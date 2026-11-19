import unittest

from f1engineer import simulate as S
from f1engineer import packets as P
from f1engineer import speech
from f1engineer.calls import P1, P2, P3, CallManager
from f1engineer.engineer import Engineer
from f1engineer.state import SessionState
from f1engineer.voice import (SilentPlayer, Voice, radio_effect, read_wav, tone,
                              wav_duration)


class FakeTTS:
    """Returns a beep whose length depends on the number of words."""
    name = "fake"

    def __init__(self):
        self.rendered = []

    def synthesize(self, text):
        self.rendered.append(text)
        return tone(440, 0.3 * len(text.split()), rate=8000)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def drive(state, frame=0, speed=290, throttle=1.0, brake=0.0, steer=0.0, dist=100.0,
          lap=1, driver_status=1):
    """Feed one lap-data + telemetry pair describing what the car is doing."""
    h = S.header(P.PACKET_LAP_DATA, frame / 20, frame)
    pkt = bytearray(S.lap_data(h, lap, dist, 1000, 0))
    # driver_status is byte 23 of the car's lap data block
    pkt[29 + 4 + 4 + 2 + 1 + 2 + 1 + 2 + 2 + 12 + 12] = driver_status
    state.feed(bytes(pkt))
    state.feed(S.car_telemetry(S.header(P.PACKET_CAR_TELEMETRY, frame / 20, frame),
                               speed, throttle, steer, brake))


def make(mode=None):
    state = SessionState()
    clock = Clock()
    player = SilentPlayer()
    tts = FakeTTS()
    calls = CallManager(state, Voice(tts, radio=False), player, clock=clock,
                        log=lambda m: None, mode=mode, threaded=False)
    return state, clock, player, calls


class TestSpeech(unittest.TestCase):
    def test_lap_time(self):
        self.assertEqual(speech.lap_time(89412), "1 29.4")
        self.assertEqual(speech.lap_time(65300), "1 oh 5.3")
        self.assertEqual(speech.lap_time(58200), "58.2")

    def test_delta(self):
        self.assertEqual(speech.delta(0.4), "four tenths")
        self.assertEqual(speech.delta(0.1), "a tenth")
        self.assertEqual(speech.delta(0.02), "a few hundredths")
        self.assertEqual(speech.delta(1.26), "1.3 seconds")


class TestVoice(unittest.TestCase):
    def test_radio_effect_keeps_length_and_format(self):
        wav = tone(440, 1.0, rate=22050)
        out = radio_effect(wav)
        samples, rate = read_wav(out)
        self.assertEqual(rate, 22050)
        # adds a short click before and squelch after
        self.assertAlmostEqual(wav_duration(out), 1.0 + 0.09 + 0.13, places=2)
        self.assertLessEqual(max(abs(s) for s in samples), 32767)

    def test_cache(self):
        tts = FakeTTS()
        v = Voice(tts, radio=True)
        a = v.render("Box box")
        b = v.render("Box box")
        self.assertIs(a, b)
        self.assertEqual(tts.rendered, ["Box box"])


class TestLinuxPlayer(unittest.TestCase):
    def test_prefers_pipewire(self):
        from f1engineer.voice import CommandPlayer
        have = {"pw-play", "paplay", "aplay"}
        self.assertEqual(CommandPlayer(which=lambda x: x in have).name, "pw-play")
        self.assertEqual(CommandPlayer(which=lambda x: x == "paplay").name, "paplay")
        with self.assertRaises(RuntimeError):
            CommandPlayer(which=lambda x: False)

    def test_espeak_missing_is_reported(self):
        from f1engineer.voice import EspeakTTS, TTSError
        with self.assertRaises(TTSError):
            EspeakTTS(which=lambda x: None)


class TestCallManager(unittest.TestCase):
    def test_p1_speaks_mid_corner(self):
        state, clock, player, calls = make()
        drive(state, speed=150, throttle=0.3, steer=0.6)
        calls.say("Front left locking.", P1)
        self.assertIsNotNone(calls.tick())
        self.assertEqual(len(player.played), 1)

    def test_p2_waits_for_straight(self):
        state, clock, player, calls = make()
        drive(state, speed=180, throttle=0.0, brake=0.9)
        calls.say("Lap time 1 29.4.", P2)
        self.assertIsNone(calls.tick())
        drive(state, speed=290)
        clock.t += 0.1
        self.assertIsNone(calls.tick())  # just got on the straight
        clock.t += 0.5
        self.assertIsNotNone(calls.tick())

    def _with_reference_lap(self, state):
        # Lap 1: braking zone at 1000 m, then cross the line to lap 2.
        for i in range(200):
            d = i * 10.0
            braking = 1000 <= d < 1100
            drive(state, frame=i, dist=d, lap=1, brake=0.9 if braking else 0.0,
                  throttle=0.0 if braking else 1.0)
        drive(state, frame=201, dist=0.0, lap=2)

    def test_p2_waits_if_braking_zone_is_close(self):
        state, clock, player, calls = make()
        self._with_reference_lap(state)
        # 290 km/h is ~80 m/s, so 950 m is under a second from the 1000 m zone.
        drive(state, frame=300, dist=950.0, lap=2)
        calls.say("Lap time 1 29.4. Plus four tenths to your best.", P2)
        self.assertIsNone(calls.tick())
        drive(state, frame=301, dist=1200.0, lap=2)
        clock.t += 1
        self.assertIsNotNone(calls.tick())

    def test_p1_interrupts_p2(self):
        state, clock, player, calls = make()
        drive(state)
        calls.tick()
        calls.say("A long piece of coaching about the next few corners.", P3)
        clock.t += 1
        self.assertEqual(calls.tick().priority, P3)
        calls.say("Safety car, safety car.", P1)
        clock.t += 0.2
        c = calls.tick()
        self.assertEqual(c.text, "Safety car, safety car.")

    def test_same_key_replaces_and_cooldown_drops(self):
        state, clock, player, calls = make()
        drive(state, speed=150, throttle=0.3, steer=0.6)  # mid-corner: P2 waits
        calls.say("P5.", P2, key="position")
        calls.say("P4.", P2, key="position")
        self.assertEqual([c.text for c in calls.queue], ["P4."])
        calls.say("Box.", P1, key="box", cooldown_s=30)
        calls.tick()
        clock.t += 5
        self.assertIsNone(calls.say("Box.", P1, key="box", cooldown_s=30))

    def test_expired_calls_dropped(self):
        state, clock, player, calls = make()
        drive(state, speed=150, throttle=0.3, steer=0.6)
        calls.say("Old news.", P2, expires_s=5)
        clock.t += 6
        calls.tick()
        self.assertEqual(calls.queue, [])

    def test_qualifying_push_lap_is_quiet(self):
        state, clock, player, calls = make(mode="qualifying")
        drive(state, driver_status=1)
        calls.tick()
        calls.say("Lap time.", P2)
        clock.t += 1
        self.assertIsNone(calls.tick())
        drive(state, driver_status=2)  # in lap
        clock.t += 1
        self.assertIsNotNone(calls.tick())

    def test_p3_budget_per_lap(self):
        state, clock, player, calls = make(mode="race")  # 1 coaching call per lap
        drive(state)
        calls.tick()
        clock.t += 1
        calls.say("Tip one.", P3, key="a")
        calls.say("Tip two.", P3, key="b")
        self.assertIsNotNone(calls.tick())
        clock.t += 5
        self.assertIsNone(calls.tick())
        drive(state, lap=2)
        clock.t += 1
        self.assertEqual(calls.tick().text, "Tip two.")


class TestEngineer(unittest.TestCase):
    def run_session(self, laps=3):
        state = SessionState()
        player = SilentPlayer()
        calls = CallManager(state, Voice(FakeTTS(), radio=False), player,
                            clock=lambda: state.session_time, log=lambda m: None,
                            threaded=False)
        eng = Engineer(state, calls)
        for _, pkt in S.fake_session(laps=laps):
            state.feed(pkt)
            eng.update()
            calls.tick()
        return state, calls, eng

    def test_full_fake_session(self):
        state, calls, eng = self.run_session()
        spoken = [c.text for _, c in calls.history]
        self.assertTrue(spoken[0].startswith("Radio check. Silverstone, practice one. "
                                             "Traction control medium, ABS off."), spoken)
        lap_calls = [c.text for _, c in calls.history if c.key == "lap_time"]
        self.assertEqual(len(lap_calls), 3, spoken)
        self.assertIn("Personal best", lap_calls[1])
        # the fake session ends with a UDP Action 1 press: repeat the last call
        self.assertEqual(calls.history[-1][1].key, "repeat")
        self.assertEqual(calls.history[-1][1].text, lap_calls[-1])

    def test_lap_time_waits_for_straight(self):
        state, calls, eng = self.run_session(laps=2)
        for t, c in calls.history:
            if c.key == "lap_time":
                # find telemetry at that moment: should be on a straight
                lap = state.laps.get(2) or state.laps.get(3)
                smp = min((s for l in state.laps.values() for s in l.samples),
                          key=lambda s: abs(s.session_time - t))
                self.assertGreater(smp.throttle, 0.85)
                self.assertEqual(smp.brake, 0.0)

    def test_buttons_repeat_and_status(self):
        state, calls, eng = self.run_session(laps=1)
        last = calls.last_call.text
        h = S.header(P.PACKET_EVENT, state.session_time, 99999)
        state.feed(S.button(h, 0x00100000))  # UDP Action 1 pressed
        state.feed(S.button(h, 0x00100000))  # still held: no second repeat
        calls.tick()
        self.assertEqual(calls.history[-1][1].text, last)
        n = len(calls.history)
        state.feed(S.button(h, 0))
        state.feed(S.button(h, 0x00200000))  # UDP Action 2: status
        calls.speaking_until = 0
        calls.tick()
        self.assertEqual(len(calls.history), n + 1)
        status = calls.history[-1][1].text
        self.assertIn("Softs", status)
        self.assertIn("percent worn", status)

    def test_safety_car_and_penalty(self):
        state = SessionState()
        calls = CallManager(state, Voice(FakeTTS(), radio=False), SilentPlayer(),
                            clock=lambda: 0.0, log=lambda m: None, threaded=False)
        eng = Engineer(state, calls)
        pkt = bytearray(S.session(S.header(P.PACKET_SESSION, 0, 0), session_type=10))
        sc_offset = 29 + 19 + 105
        pkt[sc_offset] = 1
        state.feed(bytes(pkt))
        eng.update()
        self.assertTrue(any("Safety car" in c.text for c in calls.queue))
        import struct
        state.feed(S.event(S.header(P.PACKET_EVENT, 0, 1), "PENA",
                           struct.pack("<7B", 4, 0, 0, 255, 5, 3, 0)))
        self.assertTrue(any(c.text == "5 second penalty." for c in calls.queue))


if __name__ == "__main__":
    unittest.main()
