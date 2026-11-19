"""Text to speech and audio playback.

Speech engines, best first:
  piper  free neural voice, runs locally. `pip install piper-tts`, then
         `python -m f1engineer download-voice` to fetch a voice model.
  sapi   the built-in Windows voice. Nothing to install; more robotic.
  print  no audio, just prints the call (for testing).

Every engine returns 16-bit mono WAV bytes, so the radio effect and the
player work the same whatever the engine.
"""

from __future__ import annotations

import array
import base64
import io
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path
from typing import Protocol

DEFAULT_VOICE_DIR = Path("voices")
DEFAULT_VOICE = "en_GB-alan-medium"
VOICE_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------
def wav_duration(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return w.getnframes() / float(w.getframerate())


def read_wav(wav_bytes: bytes) -> tuple[array.array, int]:
    """Return (int16 mono samples, sample rate)."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("only 16-bit WAV is supported")
    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()
    if ch > 1:
        samples = array.array("h", samples[::ch])
    return samples, rate


def write_wav(samples: array.array, rate: int) -> bytes:
    out = array.array("h", samples)
    if sys.byteorder == "big":
        out.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(out.tobytes())
    return buf.getvalue()


def tone(freq: float, seconds: float, rate: int = 22050, volume: float = 0.3) -> bytes:
    n = int(seconds * rate)
    s = array.array("h", (int(32767 * volume * math.sin(2 * math.pi * freq * i / rate))
                          for i in range(n)))
    return write_wav(s, rate)


# ---------------------------------------------------------------------------
# Radio effect: band-pass to ~300-3400 Hz, light drive, hiss, and a click
# before and a squelch after, like a team radio.
# ---------------------------------------------------------------------------
def radio_effect(wav_bytes: bytes, seed: int = 0) -> bytes:
    x, rate = read_wav(wav_bytes)
    rng = random.Random(seed)
    dt = 1.0 / rate
    rc_hp = 1.0 / (2 * math.pi * 300)
    rc_lp = 1.0 / (2 * math.pi * 3400)
    a_hp = rc_hp / (rc_hp + dt)
    a_lp = dt / (rc_lp + dt)

    def burst(seconds: float, level: float) -> list[float]:
        return [rng.uniform(-level, level) for _ in range(int(seconds * rate))]

    head = burst(0.03, 0.25) + [0.0] * int(0.06 * rate)
    tail = [0.0] * int(0.05 * rate) + burst(0.08, 0.12)
    out: list[float] = list(head)
    prev_x = prev_hp = lp1 = lp2 = 0.0
    for s in x:
        v = s / 32768.0
        hp = a_hp * (prev_hp + v - prev_x)
        prev_x, prev_hp = v, hp
        lp1 += a_lp * (hp - lp1)
        lp2 += a_lp * (lp1 - lp2)
        driven = math.tanh(lp2 * 2.2) * 0.85
        out.append(driven + rng.uniform(-0.008, 0.008))
    out.extend(tail)
    return write_wav(array.array("h", (int(max(-1.0, min(1.0, v)) * 32767) for v in out)), rate)


# ---------------------------------------------------------------------------
# Speech engines
# ---------------------------------------------------------------------------
class TTSError(RuntimeError):
    pass


class TTS(Protocol):
    name: str

    def synthesize(self, text: str) -> bytes | None: ...


class PiperTTS:
    """Piper neural voice, loaded once and kept in memory (fast)."""

    name = "piper"

    def __init__(self, model_path: str | Path):
        try:
            from piper import PiperVoice  # type: ignore
        except ImportError as e:
            raise TTSError("piper-tts isn't installed (pip install piper-tts)") from e
        model_path = Path(model_path)
        if not model_path.exists():
            raise TTSError(f"voice model not found: {model_path} "
                           f"(run: python -m f1engineer download-voice)")
        self._voice = PiperVoice.load(str(model_path))
        self._lock = threading.Lock()

    def synthesize(self, text: str) -> bytes:
        buf = io.BytesIO()
        with self._lock, wave.open(buf, "wb") as w:
            # piper-tts 1.3+ uses synthesize_wav; older releases use synthesize.
            if hasattr(self._voice, "synthesize_wav"):
                self._voice.synthesize_wav(text, w)
            else:
                self._voice.synthesize(text, w)
        return buf.getvalue()


_SAPI_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 1
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(22050, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
while (($line = [Console]::In.ReadLine()) -ne $null) {
  $ms = New-Object System.IO.MemoryStream
  $s.SetOutputToAudioStream($ms, $fmt)
  $s.Speak($line)
  $s.SetOutputToNull()
  [Console]::Out.WriteLine([Convert]::ToBase64String($ms.ToArray()))
  [Console]::Out.Flush()
}
"""


class SapiTTS:
    """Built-in Windows voice via one long-running PowerShell process."""

    name = "sapi"

    def __init__(self):
        if os.name != "nt":
            raise TTSError("the Windows voice is only available on Windows")
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            raise TTSError("PowerShell not found")
        # Pass the script as -EncodedCommand so stdin is free for the text lines.
        encoded = base64.b64encode(_SAPI_SCRIPT.encode("utf-16-le")).decode("ascii")
        self._proc = subprocess.Popen(
            [ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._lock = threading.Lock()

    def synthesize(self, text: str) -> bytes:
        clean = " ".join(text.split())
        with self._lock:
            self._proc.stdin.write(clean + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
        if not line:
            raise TTSError("Windows voice stopped responding")
        raw = base64.b64decode(line.strip())
        # SetOutputToAudioStream gives raw PCM; wrap it as WAV.
        samples = array.array("h")
        samples.frombytes(raw[: len(raw) // 2 * 2])
        return write_wav(samples, 22050)


class PrintTTS:
    """No audio: prints calls. Used for tests and when nothing else works."""

    name = "print"

    def synthesize(self, text: str) -> None:
        return None


def make_tts(engine: str = "auto", model: str | Path | None = None,
             log=print) -> TTS:
    model = Path(model) if model else DEFAULT_VOICE_DIR / f"{DEFAULT_VOICE}.onnx"
    order = {"auto": ["piper", "sapi", "print"], "piper": ["piper"],
             "sapi": ["sapi"], "print": ["print"]}[engine]
    errors = []
    for name in order:
        try:
            if name == "piper":
                return PiperTTS(model)
            if name == "sapi":
                return SapiTTS()
            return PrintTTS()
        except TTSError as e:
            errors.append(f"{name}: {e}")
            log(f"[voice] {name} unavailable — {e}")
    raise TTSError("; ".join(errors))


def download_voice(name: str = DEFAULT_VOICE, dest: Path = DEFAULT_VOICE_DIR, log=print) -> Path:
    """Download a Piper voice (e.g. en_GB-alan-medium) from the official voices repo."""
    lang_region, speaker, quality = name.split("-", 2)
    lang = lang_region.split("_")[0]
    dest.mkdir(parents=True, exist_ok=True)
    for suffix in (".onnx", ".onnx.json"):
        url = f"{VOICE_BASE_URL}/{lang}/{lang_region}/{speaker}/{quality}/{name}{suffix}"
        target = dest / f"{name}{suffix}"
        log(f"Downloading {url}")
        with urllib.request.urlopen(url) as r, open(target, "wb") as f:
            shutil.copyfileobj(r, f)
    log(f"Saved to {dest / (name + '.onnx')}")
    return dest / f"{name}.onnx"


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------
class Player(Protocol):
    def play(self, wav_bytes: bytes) -> None: ...
    def stop(self) -> None: ...


class WinsoundPlayer:
    """Windows playback with the standard library. Non-blocking; stop() cuts it off."""

    def __init__(self):
        import winsound  # type: ignore
        self._ws = winsound
        self._dir = Path(tempfile.mkdtemp(prefix="f1engineer_"))
        self._n = 0

    def play(self, wav_bytes: bytes) -> None:
        # winsound can't play from memory asynchronously, so use a temp file.
        self._n = (self._n + 1) % 4
        path = self._dir / f"call{self._n}.wav"
        path.write_bytes(wav_bytes)
        self._ws.PlaySound(str(path), self._ws.SND_FILENAME | self._ws.SND_ASYNC
                           | self._ws.SND_NODEFAULT)

    def stop(self) -> None:
        self._ws.PlaySound(None, 0)


class SilentPlayer:
    """Plays nothing (tests, or non-Windows machines without aplay)."""

    def __init__(self):
        self.played: list[bytes] = []

    def play(self, wav_bytes: bytes) -> None:
        self.played.append(wav_bytes)

    def stop(self) -> None:
        pass


class AplayPlayer:
    """Linux playback for development."""

    def __init__(self):
        if not shutil.which("aplay"):
            raise RuntimeError("aplay not found")
        self._p: subprocess.Popen | None = None

    def play(self, wav_bytes: bytes) -> None:
        self.stop()
        self._p = subprocess.Popen(["aplay", "-q", "-"], stdin=subprocess.PIPE)
        self._p.stdin.write(wav_bytes)
        self._p.stdin.close()

    def stop(self) -> None:
        if self._p and self._p.poll() is None:
            self._p.terminate()


def make_player() -> Player:
    if os.name == "nt":
        return WinsoundPlayer()
    try:
        return AplayPlayer()
    except RuntimeError:
        return SilentPlayer()


# ---------------------------------------------------------------------------
# Voice = engine + effect + cache
# ---------------------------------------------------------------------------
class Voice:
    """Turns text into ready-to-play audio, with caching so repeated and
    pre-rendered phrases cost nothing."""

    def __init__(self, tts: TTS, radio: bool = True, cache_size: int = 200):
        self.tts = tts
        self.radio = radio
        self._cache: dict[str, bytes | None] = {}
        self._cache_size = cache_size
        self._lock = threading.Lock()

    def render(self, text: str) -> bytes | None:
        with self._lock:
            if text in self._cache:
                return self._cache[text]
        wav = self.tts.synthesize(text)
        if wav is not None and self.radio:
            wav = radio_effect(wav, seed=hash(text) & 0xFFFF)
        with self._lock:
            if len(self._cache) >= self._cache_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[text] = wav
        return wav

    def prerender(self, phrases) -> None:
        for p in phrases:
            try:
                self.render(p)
            except Exception:
                pass


def estimate_duration(text: str) -> float:
    """Rough speaking time when there's no audio (print engine)."""
    return 0.4 + 0.33 * len(text.split())
