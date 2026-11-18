"""Live picture of the session, built from decoded packets.

Keeps the latest value of everything the engineer cares about for the player
car, plus a per-lap trace of the player's inputs keyed by lap distance. Later
milestones (rules engine, corner model, Haiku coach) read from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from . import packets as P


@dataclass
class TraceSample:
    session_time: float
    lap_distance: float
    speed_kph: int
    throttle: float
    brake: float
    steer: float
    gear: int
    engine_rpm: int
    brakes_temperature: tuple
    tyres_surface_temperature: tuple
    tyres_inner_temperature: tuple
    wheel_slip_ratio: tuple | None
    g_lat: float | None
    g_long: float | None
    pos_x: float | None
    pos_z: float | None


@dataclass
class LapTrace:
    lap_num: int
    samples: list[TraceSample] = field(default_factory=list)
    lap_time_ms: int | None = None
    invalid: bool = False
    sector1_ms: int | None = None
    sector2_ms: int | None = None


Listener = Callable[[str, dict[str, Any]], None]


class SessionState:
    def __init__(self) -> None:
        self.session_uid: int | None = None
        self.player_idx: int = 0
        self.session: dict[str, Any] | None = None
        self.participants: dict[str, Any] | None = None
        self.lap: dict[str, Any] | None = None           # player
        self.all_laps: list[dict[str, Any]] | None = None
        self.telemetry: dict[str, Any] | None = None     # player
        self.status: dict[str, Any] | None = None        # player
        self.damage: dict[str, Any] | None = None        # player
        self.motion: dict[str, Any] | None = None        # player
        self.motion_ex: dict[str, Any] | None = None     # player
        self.history: dict[str, Any] | None = None       # player
        self.last_event: dict[str, Any] | None = None
        self.session_time: float = 0.0
        self.laps: dict[int, LapTrace] = {}
        self.packet_counts: dict[str, int] = {}
        self.errors: int = 0
        self.last_error: str | None = None
        self._listeners: list[Listener] = []

    # -- events for later milestones ------------------------------------
    def subscribe(self, fn: Listener) -> None:
        """fn(kind, payload) is called for 'lap_complete', 'event',
        'button', 'session_start'."""
        self._listeners.append(fn)

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        for fn in self._listeners:
            fn(kind, payload)

    # -- ingest ----------------------------------------------------------
    def feed(self, data: bytes) -> None:
        try:
            header, body = P.decode(data)
        except P.PacketError as e:
            self.errors += 1
            self.last_error = str(e)
            return
        name = P.PACKET_NAMES[header["packet_id"]]
        self.packet_counts[name] = self.packet_counts.get(name, 0) + 1
        if body is None:
            return

        if header["session_uid"] != self.session_uid:
            self._reset(header["session_uid"])
        self.player_idx = header["player_car_index"]
        self.session_time = header["session_time"]
        i = self.player_idx
        pid = header["packet_id"]

        if pid == P.PACKET_SESSION:
            self.session = body
        elif pid == P.PACKET_PARTICIPANTS:
            self.participants = body
        elif pid == P.PACKET_LAP_DATA:
            self._on_lap_data(body["cars"])
        elif pid == P.PACKET_CAR_TELEMETRY:
            self.telemetry = body["cars"][i]
            self._append_sample()
        elif pid == P.PACKET_CAR_STATUS:
            self.status = body["cars"][i]
        elif pid == P.PACKET_CAR_DAMAGE:
            self.damage = body["cars"][i]
        elif pid == P.PACKET_MOTION:
            self.motion = body["cars"][i]
        elif pid == P.PACKET_MOTION_EX:
            self.motion_ex = body
        elif pid == P.PACKET_SESSION_HISTORY:
            if body["car_idx"] == i:
                self.history = body
                self._apply_history()
        elif pid == P.PACKET_EVENT:
            self.last_event = body
            if body["code"] == "BUTN":
                self._emit("button", body)
            else:
                self._emit("event", body)

    def _reset(self, uid: int) -> None:
        listeners = self._listeners
        counts = self.packet_counts
        self.__init__()
        self._listeners = listeners
        self.packet_counts = counts
        self.session_uid = uid
        self._emit("session_start", {"session_uid": uid})

    def _on_lap_data(self, cars: list[dict[str, Any]]) -> None:
        new = cars[self.player_idx]
        prev = self.lap
        self.all_laps = cars
        self.lap = new
        if prev and new["current_lap_num"] != prev["current_lap_num"]:
            done = self.laps.get(prev["current_lap_num"])
            if done is not None:
                done.lap_time_ms = new["last_lap_time_ms"]
                done.invalid = done.invalid or prev["current_lap_invalid"]
                done.sector1_ms = prev["sector1_time_ms"] or None
                done.sector2_ms = prev["sector2_time_ms"] or None
                self._emit("lap_complete", {"lap": done})
        trace = self.laps.get(new["current_lap_num"])
        if trace is not None and new["current_lap_invalid"]:
            trace.invalid = True

    def _apply_history(self) -> None:
        # Session history is the game's authoritative lap list; use it to
        # fill in validity and times for laps we traced.
        for n, h in enumerate(self.history["laps"], start=1):
            trace = self.laps.get(n)
            if trace and h["lap_time_ms"]:
                trace.lap_time_ms = h["lap_time_ms"]
                trace.invalid = not h["lap_valid"]

    def _append_sample(self) -> None:
        if not self.lap or not self.telemetry:
            return
        lap_num = self.lap["current_lap_num"]
        dist = self.lap["lap_distance"]
        if dist < 0:  # before the line on an out lap
            return
        trace = self.laps.setdefault(lap_num, LapTrace(lap_num))
        t = self.telemetry
        m, mx = self.motion, self.motion_ex
        trace.samples.append(TraceSample(
            session_time=self.session_time,
            lap_distance=dist,
            speed_kph=t["speed_kph"],
            throttle=t["throttle"],
            brake=t["brake"],
            steer=t["steer"],
            gear=t["gear"],
            engine_rpm=t["engine_rpm"],
            brakes_temperature=t["brakes_temperature"],
            tyres_surface_temperature=t["tyres_surface_temperature"],
            tyres_inner_temperature=t["tyres_inner_temperature"],
            wheel_slip_ratio=mx["wheel_slip_ratio"] if mx else None,
            g_lat=m["g_force_lateral"] if m else None,
            g_long=m["g_force_longitudinal"] if m else None,
            pos_x=m["world_position"][0] if m else None,
            pos_z=m["world_position"][2] if m else None,
        ))

    # -- convenience -----------------------------------------------------
    def best_lap(self) -> LapTrace | None:
        valid = [l for l in self.laps.values() if l.lap_time_ms and not l.invalid]
        return min(valid, key=lambda l: l.lap_time_ms) if valid else None

    def driver_name(self, idx: int) -> str:
        if self.participants and idx < len(self.participants["cars"]):
            return self.participants["cars"][idx]["name"] or f"Car {idx}"
        return f"Car {idx}"
