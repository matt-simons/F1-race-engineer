"""The call manager decides what the engineer says, and when.

Priorities:
  P1  urgent (lock-up, safety car, penalty, anything you asked for).
      Spoken straight away; can cut off a P2/P3 call mid-sentence.
  P2  race info (lap time, position, flags). Waits for a quiet moment.
  P3  coaching. Waits for a quiet moment and counts against a per-lap budget.

A quiet moment means: on the throttle, off the brake, steering straight, and
enough time before the next braking zone to finish the sentence. The next
braking zone comes from your reference lap (best valid lap, or the last
completed lap until you have one).

Each call can have a key. Calls with the same key replace each other in the
queue, and a key on cool-down is dropped rather than repeated.
"""

from __future__ import annotations

import bisect
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .state import LapTrace, SessionState
from .voice import Player, Voice, estimate_duration, wav_duration

P1, P2, P3 = 1, 2, 3

DEFAULT_EXPIRY = {P1: 6.0, P2: 25.0, P3: 60.0}

# Coaching calls allowed per lap, by mode.
P3_BUDGET = {"practice": 3, "qualifying": 0, "race": 1}

BRAKE_ON = 0.2


@dataclass
class Call:
    text: str
    priority: int = P2
    key: str | None = None
    expires_s: float | None = None
    cooldown_s: float = 0.0
    created: float = 0.0
    audio: bytes | None = None
    duration: float = 0.0
    ready: bool = False
    seq: int = 0

    def expired(self, now: float) -> bool:
        limit = self.expires_s if self.expires_s is not None else DEFAULT_EXPIRY[self.priority]
        return now - self.created > limit


class QuietZones:
    """Works out whether there's time to talk before the next corner."""

    def __init__(self, state: SessionState):
        self.state = state
        self._straight_since: float | None = None
        self._ref: LapTrace | None = None
        self._ref_len = 0
        self._zones: list[float] = []

    def _reference(self) -> LapTrace | None:
        s = self.state
        ref = s.best_lap()
        if ref is None:
            done = [l for n, l in s.laps.items()
                    if s.lap and n < s.lap["current_lap_num"] and len(l.samples) > 50]
            ref = done[-1] if done else None
        return ref

    def braking_zones(self) -> list[float]:
        """Lap distances where braking starts on the reference lap."""
        ref = self._reference()
        if ref is None:
            return []
        if ref is not self._ref or len(ref.samples) != self._ref_len:
            zones, braking = [], False
            for smp in ref.samples:
                if smp.brake >= BRAKE_ON and not braking:
                    zones.append(smp.lap_distance)
                braking = smp.brake >= BRAKE_ON
            self._ref, self._ref_len, self._zones = ref, len(ref.samples), zones
        return self._zones

    def seconds_to_next_braking(self) -> float | None:
        s = self.state
        zones = self.braking_zones()
        if not zones or not s.lap or not s.telemetry:
            return None
        d = s.lap["lap_distance"]
        i = bisect.bisect_right(zones, d)
        if i < len(zones):
            gap = zones[i] - d
        else:
            track = (s.session or {}).get("track_length") or 0
            if not track:
                return None
            gap = track - d + zones[0]
        speed = max(s.telemetry["speed_kph"] / 3.6, 10.0)
        return gap / speed

    def _on_straight(self) -> bool:
        t = self.state.telemetry
        return bool(t) and t["throttle"] > 0.85 and t["brake"] < 0.05 and abs(t["steer"]) < 0.2

    def observe(self, now: float) -> None:
        """Called every tick so we know how long we've been on a straight."""
        if self._on_straight():
            if self._straight_since is None:
                self._straight_since = now
        else:
            self._straight_since = None

    def can_talk(self, needed_s: float, now: float) -> bool:
        s = self.state
        t = s.telemetry
        if t is None or (s.session and s.session.get("game_paused")):
            return True  # menus / paused
        if t["speed_kph"] < 60:
            return True  # pit lane, garage, spun
        self.observe(now)
        if self._straight_since is None:
            return False
        ahead = self.seconds_to_next_braking()
        if ahead is None:
            # No reference lap yet: settle for having been on a straight a moment.
            return now - self._straight_since >= 0.4
        return ahead >= needed_s + 0.3


class CallManager:
    def __init__(self, state: SessionState, voice: Voice, player: Player,
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print,
                 mode: str | None = None, threaded: bool = True):
        self.state = state
        self.voice = voice
        self.player = player
        self.clock = clock
        self.log = log
        self.mode_override = mode
        self.quiet = QuietZones(state)
        self.queue: list[Call] = []
        self.last_spoken: dict[str, float] = {}
        self.last_call: Call | None = None
        self.current: Call | None = None
        self.speaking_until = 0.0
        self.p3_count: dict[int, int] = {}
        self.history: list[tuple[float, Call]] = []
        self._seq = 0
        self._lock = threading.RLock()
        self._threaded = threaded
        self._render_q: queue.Queue[Call] = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # -- public ------------------------------------------------------------
    @property
    def mode(self) -> str:
        if self.mode_override:
            return self.mode_override
        return (self.state.session or {}).get("session_category", "practice")

    def say(self, text: str, priority: int = P2, key: str | None = None,
            expires_s: float | None = None, cooldown_s: float = 0.0) -> Call | None:
        now = self.clock()
        with self._lock:
            if key and cooldown_s and now - self.last_spoken.get(key, -1e9) < cooldown_s:
                return None
            if key:
                self.queue = [c for c in self.queue if c.key != key]
            self._seq += 1
            call = Call(text, priority, key, expires_s, cooldown_s, created=now, seq=self._seq)
            self.queue.append(call)
        if self._threaded:
            self._render_q.put(call)
        else:
            self._render(call)
        return call

    def repeat_last(self) -> None:
        if self.last_call:
            self.say(self.last_call.text, P1, key="repeat")
        else:
            self.say("Nothing to repeat yet.", P1, key="repeat")

    def start(self) -> None:
        for target in (self._render_loop, self._speak_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self.player.stop()

    # -- internals -----------------------------------------------------------
    def _render(self, call: Call) -> None:
        try:
            call.audio = self.voice.render(call.text)
        except Exception as e:  # a bad synth shouldn't kill the engineer
            self.log(f"[voice] couldn't render '{call.text}': {e}")
            call.audio = None
        call.duration = wav_duration(call.audio) if call.audio else estimate_duration(call.text)
        call.ready = True

    def _render_loop(self) -> None:
        while not self._stop.is_set():
            try:
                call = self._render_q.get(timeout=0.2)
            except queue.Empty:
                continue
            self._render(call)

    def _speak_loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            time.sleep(0.05)

    def _allowed(self, call: Call, now: float) -> bool:
        if call.priority == P1:
            return True
        mode = self.mode
        lap = self.state.lap
        if mode == "qualifying" and lap and lap["driver_status"] == 1:
            return False  # nothing but urgent calls on a push lap
        if call.priority == P3:
            lap_num = lap["current_lap_num"] if lap else 0
            if self.p3_count.get(lap_num, 0) >= P3_BUDGET.get(mode, 1):
                return False
        return self.quiet.can_talk(call.duration, now)

    def tick(self) -> Call | None:
        """Run one scheduling step. Returns the call started, if any."""
        now = self.clock()
        self.quiet.observe(now)
        with self._lock:
            self.queue = [c for c in self.queue if not c.expired(now)]
            ready = sorted((c for c in self.queue if c.ready), key=lambda c: (c.priority, c.seq))
            if now < self.speaking_until:
                cur = self.current
                urgent = next((c for c in ready if c.priority == P1), None)
                if urgent and cur and cur.priority > P1:
                    self.player.stop()
                    self.log(f"[engineer] (cut off) {cur.text}")
                    return self._play(urgent, now)
                return None
            for c in ready:
                if self._allowed(c, now):
                    return self._play(c, now)
        return None

    def _play(self, call: Call, now: float) -> Call:
        self.queue.remove(call)
        if call.audio:
            self.player.play(call.audio)
        self.log(f"[engineer] {call.text}")
        self.current = call
        self.speaking_until = now + call.duration + 0.25
        if call.key:
            self.last_spoken[call.key] = now
        if call.key != "repeat":
            self.last_call = call
        if call.priority == P3:
            lap_num = self.state.lap["current_lap_num"] if self.state.lap else 0
            self.p3_count[lap_num] = self.p3_count.get(lap_num, 0) + 1
        self.history.append((now, call))
        return call
