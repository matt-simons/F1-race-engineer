import os
import socket
import tempfile
import threading
import time
import unittest

from f1engineer import packets as P
from f1engineer import simulate as S
from f1engineer.listener import listen
from f1engineer.recording import Recorder, read_recording
from f1engineer.state import SessionState


def h(pid, player=0):
    return S.header(pid, 12.5, 100, player)


class TestSizes(unittest.TestCase):
    def test_built_packets_match_spec_sizes(self):
        built = {
            P.PACKET_MOTION: S.motion(h(0)),
            P.PACKET_SESSION: S.session(h(1)),
            P.PACKET_LAP_DATA: S.lap_data(h(2)),
            P.PACKET_EVENT: S.button(h(3), 1),
            P.PACKET_PARTICIPANTS: S.participants(h(4)),
            P.PACKET_CAR_TELEMETRY: S.car_telemetry(h(6)),
            P.PACKET_CAR_STATUS: S.car_status(h(7)),
            P.PACKET_CAR_DAMAGE: S.car_damage(h(10)),
            P.PACKET_SESSION_HISTORY: S.session_history(h(11)),
            P.PACKET_MOTION_EX: S.motion_ex(h(13)),
        }
        for pid, data in built.items():
            self.assertEqual(len(data), P.PACKET_SIZES[pid], P.PACKET_NAMES[pid])


class TestDecode(unittest.TestCase):
    def test_header(self):
        hd = P.decode_header(S.header(6, 42.0, 7, player=3))
        self.assertEqual(hd["packet_format"], 2023)
        self.assertEqual(hd["packet_id"], 6)
        self.assertEqual(hd["player_car_index"], 3)
        self.assertAlmostEqual(hd["session_time"], 42.0)

    def test_wrong_format_rejected(self):
        bad = bytearray(S.car_telemetry(h(6)))
        bad[0:2] = (2022).to_bytes(2, "little")
        with self.assertRaises(P.PacketError):
            P.decode(bytes(bad))

    def test_wrong_size_rejected(self):
        with self.assertRaises(P.PacketError):
            P.decode(S.car_telemetry(h(6))[:-1])

    def test_car_telemetry(self):
        _, b = P.decode(S.car_telemetry(h(6), speed=287, throttle=0.75, brake=0.25, steer=-0.5,
                                        gear=6, brakes=(400, 410, 650, 660)))
        c = b["cars"][0]
        self.assertEqual(c["speed_kph"], 287)
        self.assertAlmostEqual(c["throttle"], 0.75)
        self.assertAlmostEqual(c["brake"], 0.25)
        self.assertAlmostEqual(c["steer"], -0.5)
        self.assertEqual(c["gear"], 6)
        self.assertEqual(c["brakes_temperature"], (400, 410, 650, 660))
        self.assertEqual(len(b["cars"]), 22)

    def test_lap_data(self):
        _, b = P.decode(S.lap_data(h(2), lap_num=4, lap_distance=1234.5, current_ms=45678,
                                   last_ms=91234, position=3, sector=1, s1=61234))
        c = b["cars"][0]
        self.assertEqual(c["current_lap_num"], 4)
        self.assertAlmostEqual(c["lap_distance"], 1234.5)
        self.assertEqual(c["last_lap_time_ms"], 91234)
        self.assertEqual(c["car_position"], 3)
        self.assertEqual(c["sector1_time_ms"], 61234)

    def test_session(self):
        _, b = P.decode(S.session(h(1), track_id=7, session_type=10))
        self.assertEqual(b["track_name"], "Silverstone")
        self.assertEqual(b["session_category"], "race")
        self.assertEqual(b["track_temperature"], 30)
        self.assertEqual(b["ai_difficulty"], 90)

    def test_car_status_assists(self):
        _, b = P.decode(S.car_status(h(7), tc=1, abs_on=0, fuel=33.5))
        c = b["cars"][0]
        self.assertEqual(c["traction_control_name"], "medium")
        self.assertFalse(c["anti_lock_brakes"])
        self.assertAlmostEqual(c["fuel_in_tank"], 33.5)
        self.assertEqual(c["tyre_compound_name"], "soft")

    def test_damage(self):
        _, b = P.decode(S.car_damage(h(10), wear=(10.0, 11.0, 12.0, 13.0)))
        self.assertEqual(b["cars"][0]["tyres_wear"], (10.0, 11.0, 12.0, 13.0))

    def test_motion_ex(self):
        _, b = P.decode(S.motion_ex(h(13), slip=(0.1, 0.2, -0.3, 0.0)))
        self.assertAlmostEqual(b["wheel_slip_ratio"][2], -0.3, places=5)

    def test_button_event(self):
        _, b = P.decode(S.button(h(3), 0x00100000 | 0x00000080))
        self.assertEqual(b["code"], "BUTN")
        self.assertIn("UDP_ACTION_1", b["buttons"])
        self.assertIn("DPAD_DOWN", b["buttons"])

    def test_fastest_lap_event(self):
        import struct
        _, b = P.decode(S.event(h(3), "FTLP", struct.pack("<Bf", 5, 88.5)))
        self.assertEqual(b["vehicle_idx"], 5)
        self.assertAlmostEqual(b["lap_time_s"], 88.5)

    def test_participants(self):
        _, b = P.decode(S.participants(h(4), names=("Me", "Max")))
        self.assertEqual(b["cars"][0]["name"], "Me")
        self.assertEqual(b["cars"][1]["name"], "Max")

    def test_session_history(self):
        _, b = P.decode(S.session_history(h(11), laps=[(90000, True), (89000, False)]))
        self.assertEqual(b["num_laps"], 2)
        self.assertTrue(b["laps"][0]["lap_valid"])
        self.assertFalse(b["laps"][1]["lap_valid"])

    def test_player_index_respected(self):
        state = SessionState()
        state.feed(S.car_status(S.header(7, 0, 0, player=5), tc=2))
        self.assertEqual(state.player_idx, 5)
        self.assertEqual(state.status["traction_control_name"], "full")


class TestSessionAndRecording(unittest.TestCase):
    def test_fake_session_builds_laps(self):
        state = SessionState()
        events = []
        state.subscribe(lambda kind, payload: events.append(kind))
        for _, pkt in S.fake_session(laps=3):
            state.feed(pkt)
        self.assertEqual(state.errors, 0)
        self.assertEqual(sorted(state.laps), [1, 2, 3])
        self.assertEqual(events.count("lap_complete"), 3)
        self.assertIn("button", events)
        best = state.best_lap()
        self.assertEqual(best.lap_num, 3)
        lap2 = state.laps[2]
        self.assertTrue(any(s.wheel_slip_ratio and s.wheel_slip_ratio[2] < -0.3
                            for s in lap2.samples))
        # samples ordered by distance within a lap
        d = [s.lap_distance for s in state.laps[1].samples]
        self.assertEqual(d, sorted(d))

    def test_recording_round_trip(self):
        pkts = [p for _, p in S.fake_session(laps=1)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.f1rec")
            with Recorder(path) as rec:
                for i, p in enumerate(pkts):
                    rec.write(p, float(i))
            back = [d for _, d in read_recording(path)]
        self.assertEqual(back, pkts)

    def test_truncated_recording_reads_what_it_can(self):
        pkts = [p for _, p in S.fake_session(laps=1)][:50]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.f1rec")
            with Recorder(path) as rec:
                for p in pkts:
                    rec.write(p)
            import gzip
            with open(path, "rb") as f:
                raw = gzip.decompress(f.read())
            with gzip.open(path, "wb") as f:
                f.write(raw[:-100])
            back = list(read_recording(path))
        self.assertEqual(len(back), 49)

    def test_unclosed_recording_is_readable(self):
        pkts = [p for _, p in S.fake_session(laps=1)][:200]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.f1rec")
            rec = Recorder(path)
            for p in pkts:
                rec.write(p)
            rec._f.flush()  # simulate a crash after a periodic flush
            back = list(read_recording(path))
            rec.close()
        self.assertEqual(len(back), 200)

    def test_udp_listener_receives_packets(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        got = []

        def handler(data, t):
            got.append(data)
            if len(got) >= 5:
                raise KeyboardInterrupt

        def run():
            try:
                listen(handler, host="127.0.0.1", port=port)
            except KeyboardInterrupt:
                pass

        th = threading.Thread(target=run, daemon=True)
        th.start()
        time.sleep(0.2)
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        pkt = S.car_telemetry(h(6))
        for _ in range(5):
            out.sendto(pkt, ("127.0.0.1", port))
        th.join(timeout=3)
        out.close()
        self.assertEqual(len(got), 5)
        self.assertEqual(got[0], pkt)


if __name__ == "__main__":
    unittest.main()
