"""Decoders for the F1 23 UDP telemetry format (packet format 2023).

Every packet starts with a 29-byte header. The header's packet_id says which
body follows. Each decoder returns a plain dict so the rest of the app can
use the data without caring about byte layouts.

Sizes and layouts follow the official F1 23 UDP specification published by
Codemasters/EA. Packets the engineer doesn't need yet (car setups, lobby info,
final classification, tyre sets) are recognised but only the header is
decoded; the raw bytes are still recorded.
"""

from __future__ import annotations

import struct
from typing import Any, Callable

NUM_CARS = 22

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
HEADER = struct.Struct("<HBBBBBQfIIBB")  # 29 bytes
assert HEADER.size == 29

PACKET_MOTION = 0
PACKET_SESSION = 1
PACKET_LAP_DATA = 2
PACKET_EVENT = 3
PACKET_PARTICIPANTS = 4
PACKET_CAR_SETUPS = 5
PACKET_CAR_TELEMETRY = 6
PACKET_CAR_STATUS = 7
PACKET_FINAL_CLASSIFICATION = 8
PACKET_LOBBY_INFO = 9
PACKET_CAR_DAMAGE = 10
PACKET_SESSION_HISTORY = 11
PACKET_TYRE_SETS = 12
PACKET_MOTION_EX = 13

PACKET_NAMES = {
    PACKET_MOTION: "motion",
    PACKET_SESSION: "session",
    PACKET_LAP_DATA: "lap_data",
    PACKET_EVENT: "event",
    PACKET_PARTICIPANTS: "participants",
    PACKET_CAR_SETUPS: "car_setups",
    PACKET_CAR_TELEMETRY: "car_telemetry",
    PACKET_CAR_STATUS: "car_status",
    PACKET_FINAL_CLASSIFICATION: "final_classification",
    PACKET_LOBBY_INFO: "lobby_info",
    PACKET_CAR_DAMAGE: "car_damage",
    PACKET_SESSION_HISTORY: "session_history",
    PACKET_TYRE_SETS: "tyre_sets",
    PACKET_MOTION_EX: "motion_ex",
}

PACKET_SIZES = {
    PACKET_MOTION: 1349,
    PACKET_SESSION: 644,
    PACKET_LAP_DATA: 1131,
    PACKET_EVENT: 45,
    PACKET_PARTICIPANTS: 1306,
    PACKET_CAR_SETUPS: 1107,
    PACKET_CAR_TELEMETRY: 1352,
    PACKET_CAR_STATUS: 1239,
    PACKET_FINAL_CLASSIFICATION: 1020,
    PACKET_LOBBY_INFO: 1218,
    PACKET_CAR_DAMAGE: 953,
    PACKET_SESSION_HISTORY: 1460,
    PACKET_TYRE_SETS: 231,
    PACKET_MOTION_EX: 217,
}

# Wheel order used by every per-wheel array in the game.
WHEELS = ("RL", "RR", "FL", "FR")


class PacketError(ValueError):
    """Raised when a datagram isn't a valid F1 23 packet."""


def decode_header(data: bytes) -> dict[str, Any]:
    if len(data) < HEADER.size:
        raise PacketError(f"packet too short for header: {len(data)} bytes")
    (fmt, year, major, minor, pver, pid, uid, stime, frame, oframe,
     player, secondary) = HEADER.unpack_from(data, 0)
    return {
        "packet_format": fmt,
        "game_year": year,
        "game_major_version": major,
        "game_minor_version": minor,
        "packet_version": pver,
        "packet_id": pid,
        "session_uid": uid,
        "session_time": stime,
        "frame_identifier": frame,
        "overall_frame_identifier": oframe,
        "player_car_index": player,
        "secondary_player_car_index": secondary,
    }


# ---------------------------------------------------------------------------
# Motion (id 0) — all cars
# ---------------------------------------------------------------------------
CAR_MOTION = struct.Struct("<6f6h6f")  # 60 bytes
assert CAR_MOTION.size == 60


def _decode_motion(data: bytes) -> dict[str, Any]:
    cars = []
    for v in CAR_MOTION.iter_unpack(data[29:29 + CAR_MOTION.size * NUM_CARS]):
        cars.append({
            "world_position": v[0:3],
            "world_velocity": v[3:6],
            # Normalised direction vectors are sent as int16 / 32767.
            "world_forward_dir": tuple(x / 32767.0 for x in v[6:9]),
            "world_right_dir": tuple(x / 32767.0 for x in v[9:12]),
            "g_force_lateral": v[12],
            "g_force_longitudinal": v[13],
            "g_force_vertical": v[14],
            "yaw": v[15],
            "pitch": v[16],
            "roll": v[17],
        })
    return {"cars": cars}


# ---------------------------------------------------------------------------
# Session (id 1)
# ---------------------------------------------------------------------------
SESSION_HEAD = struct.Struct("<BbbBHBbBHHBBBBBB")  # 19 bytes
MARSHAL_ZONE = struct.Struct("<fb")  # 5 bytes
FORECAST = struct.Struct("<BBBbbbbB")  # 8 bytes
SESSION_TAIL = struct.Struct("<BBIIIBBB9BBBIB4BBBB")
assert SESSION_HEAD.size == 19 and MARSHAL_ZONE.size == 5 and FORECAST.size == 8

SESSION_TYPES = {
    0: "unknown", 1: "P1", 2: "P2", 3: "P3", 4: "short_practice",
    5: "Q1", 6: "Q2", 7: "Q3", 8: "short_qualifying", 9: "one_shot_qualifying",
    10: "race", 11: "race_2", 12: "race_3", 13: "time_trial",
}
WEATHER = {0: "clear", 1: "light_cloud", 2: "overcast", 3: "light_rain",
           4: "heavy_rain", 5: "storm"}
SAFETY_CAR = {0: "none", 1: "full", 2: "virtual", 3: "formation_lap"}
TRACKS = {
    0: "Melbourne", 1: "Paul Ricard", 2: "Shanghai", 3: "Bahrain", 4: "Catalunya",
    5: "Monaco", 6: "Montreal", 7: "Silverstone", 8: "Hockenheim", 9: "Hungaroring",
    10: "Spa", 11: "Monza", 12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi",
    15: "Texas", 16: "Brazil", 17: "Austria", 18: "Sochi", 19: "Mexico",
    20: "Baku", 21: "Sakhir Short", 22: "Silverstone Short", 23: "Texas Short",
    24: "Suzuka Short", 25: "Hanoi", 26: "Zandvoort", 27: "Imola", 28: "Portimao",
    29: "Jeddah", 30: "Miami", 31: "Las Vegas", 32: "Losail",
}


def session_category(session_type: int) -> str:
    """Map the game's session type to the engineer's mode."""
    if session_type in (1, 2, 3, 4, 13):
        return "practice"
    if session_type in (5, 6, 7, 8, 9):
        return "qualifying"
    if session_type in (10, 11, 12):
        return "race"
    return "practice"


def _decode_session(data: bytes) -> dict[str, Any]:
    off = 29
    h = SESSION_HEAD.unpack_from(data, off)
    off += SESSION_HEAD.size
    zones = []
    for i in range(21):
        start, flag = MARSHAL_ZONE.unpack_from(data, off + i * 5)
        zones.append({"zone_start": start, "zone_flag": flag})
    off += 21 * 5
    num_zones = h[15]
    safety_car, network_game, num_forecast = struct.unpack_from("<BBB", data, off)
    off += 3
    forecasts = []
    for i in range(56):
        f = FORECAST.unpack_from(data, off + i * 8)
        if i < num_forecast:
            forecasts.append({
                "session_type": f[0], "time_offset_min": f[1], "weather": f[2],
                "track_temperature": f[3], "track_temperature_change": f[4],
                "air_temperature": f[5], "air_temperature_change": f[6],
                "rain_percentage": f[7],
            })
    off += 56 * 8
    t = SESSION_TAIL.unpack_from(data, off)
    return {
        "weather": h[0],
        "weather_name": WEATHER.get(h[0], "unknown"),
        "track_temperature": h[1],
        "air_temperature": h[2],
        "total_laps": h[3],
        "track_length": h[4],
        "session_type": h[5],
        "session_type_name": SESSION_TYPES.get(h[5], "unknown"),
        "session_category": session_category(h[5]),
        "track_id": h[6],
        "track_name": TRACKS.get(h[6], f"track_{h[6]}"),
        "formula": h[7],
        "session_time_left": h[8],
        "session_duration": h[9],
        "pit_speed_limit": h[10],
        "game_paused": h[11],
        "is_spectating": h[12],
        "spectator_car_index": h[13],
        "marshal_zones": zones[:num_zones],
        "safety_car_status": safety_car,
        "safety_car_name": SAFETY_CAR.get(safety_car, "unknown"),
        "network_game": network_game,
        "weather_forecast": forecasts,
        "forecast_accuracy": t[0],
        "ai_difficulty": t[1],
        "pit_window_ideal_lap": t[5],
        "pit_window_latest_lap": t[6],
        "pit_rejoin_position": t[7],
        "steering_assist": t[8],
        "braking_assist": t[9],
        "gearbox_assist": t[10],
        "pit_assist": t[11],
        "pit_release_assist": t[12],
        "ers_assist": t[13],
        "drs_assist": t[14],
        "dynamic_racing_line": t[15],
        "dynamic_racing_line_type": t[16],
        "game_mode": t[17],
        "rule_set": t[18],
        "time_of_day": t[19],
        "session_length": t[20],
        "num_safety_car_periods": t[25],
        "num_virtual_safety_car_periods": t[26],
        "num_red_flag_periods": t[27],
    }


assert 29 + 19 + 105 + 3 + 448 + SESSION_TAIL.size == PACKET_SIZES[PACKET_SESSION]

# ---------------------------------------------------------------------------
# Lap data (id 2) — all cars
# ---------------------------------------------------------------------------
LAP_DATA = struct.Struct("<IIHBHBHHfff15BHHB")  # 50 bytes
assert LAP_DATA.size == 50

PIT_STATUS = {0: "none", 1: "pitting", 2: "in_pit_area"}
DRIVER_STATUS = {0: "in_garage", 1: "flying_lap", 2: "in_lap", 3: "out_lap", 4: "on_track"}


def _decode_lap_data(data: bytes) -> dict[str, Any]:
    cars = []
    for v in LAP_DATA.iter_unpack(data[29:29 + LAP_DATA.size * NUM_CARS]):
        cars.append({
            "last_lap_time_ms": v[0],
            "current_lap_time_ms": v[1],
            "sector1_time_ms": v[2] + v[3] * 60000,
            "sector2_time_ms": v[4] + v[5] * 60000,
            "delta_to_car_in_front_ms": v[6],
            "delta_to_race_leader_ms": v[7],
            "lap_distance": v[8],
            "total_distance": v[9],
            "safety_car_delta": v[10],
            "car_position": v[11],
            "current_lap_num": v[12],
            "pit_status": v[13],
            "num_pit_stops": v[14],
            "sector": v[15],  # 0 = sector 1
            "current_lap_invalid": bool(v[16]),
            "penalties_s": v[17],
            "total_warnings": v[18],
            "corner_cutting_warnings": v[19],
            "num_unserved_drive_through_pens": v[20],
            "num_unserved_stop_go_pens": v[21],
            "grid_position": v[22],
            "driver_status": v[23],
            "result_status": v[24],
            "pit_lane_timer_active": bool(v[25]),
            "pit_lane_time_in_lane_ms": v[26],
            "pit_stop_timer_ms": v[27],
            "pit_stop_should_serve_pen": bool(v[28]),
        })
    tt_pb, tt_rival = struct.unpack_from("<BB", data, 29 + LAP_DATA.size * NUM_CARS)
    return {"cars": cars, "time_trial_pb_car_idx": tt_pb, "time_trial_rival_car_idx": tt_rival}


# ---------------------------------------------------------------------------
# Event (id 3)
# ---------------------------------------------------------------------------
# Button flags sent with the BUTN event. Xbox names in brackets.
BUTTONS = {
    0x00000001: "A",
    0x00000002: "Y",
    0x00000004: "B",
    0x00000008: "X",
    0x00000010: "DPAD_LEFT",
    0x00000020: "DPAD_RIGHT",
    0x00000040: "DPAD_UP",
    0x00000080: "DPAD_DOWN",
    0x00000100: "MENU",
    0x00000200: "LB",
    0x00000400: "RB",
    0x00000800: "LT",
    0x00001000: "RT",
    0x00002000: "LEFT_STICK_CLICK",
    0x00004000: "RIGHT_STICK_CLICK",
    0x00008000: "RIGHT_STICK_LEFT",
    0x00010000: "RIGHT_STICK_RIGHT",
    0x00020000: "RIGHT_STICK_UP",
    0x00040000: "RIGHT_STICK_DOWN",
    0x00080000: "SPECIAL",
}
for _i in range(12):
    BUTTONS[0x00100000 << _i] = f"UDP_ACTION_{_i + 1}"


def buttons_from_mask(mask: int) -> list[str]:
    return [name for bit, name in BUTTONS.items() if mask & bit]


def _decode_event(data: bytes) -> dict[str, Any]:
    code = data[29:33].decode("ascii", errors="replace")
    d = data[33:45]
    out: dict[str, Any] = {"code": code}
    if code == "FTLP":
        idx, lap_time = struct.unpack_from("<Bf", d)
        out.update(vehicle_idx=idx, lap_time_s=lap_time)
    elif code in ("RTMT",):
        idx, reason = struct.unpack_from("<BB", d)
        out.update(vehicle_idx=idx, reason=reason)
    elif code in ("TMPT", "RCWN", "DTSV", "SGSV"):
        out.update(vehicle_idx=d[0])
    elif code == "PENA":
        v = struct.unpack_from("<7B", d)
        out.update(penalty_type=v[0], infringement_type=v[1], vehicle_idx=v[2],
                   other_vehicle_idx=v[3], time_s=v[4], lap_num=v[5], places_gained=v[6])
    elif code == "SPTP":
        idx, speed, is_overall, is_personal, fastest_idx, fastest_speed = \
            struct.unpack_from("<BfBBBf", d)
        out.update(vehicle_idx=idx, speed=speed, is_overall_fastest=bool(is_overall),
                   is_driver_fastest=bool(is_personal),
                   fastest_vehicle_idx=fastest_idx, fastest_speed=fastest_speed)
    elif code == "STLG":
        out.update(num_lights=d[0])
    elif code == "FLBK":
        frame, stime = struct.unpack_from("<If", d)
        out.update(flashback_frame=frame, flashback_session_time=stime)
    elif code == "BUTN":
        (mask,) = struct.unpack_from("<I", d)
        out.update(button_status=mask, buttons=buttons_from_mask(mask))
    elif code == "OVTK":
        out.update(overtaking_vehicle_idx=d[0], overtaken_vehicle_idx=d[1])
    # SSTA, SEND, DRSE, DRSD, CHQF, LGOT, RDFL carry no extra data.
    return out


# ---------------------------------------------------------------------------
# Participants (id 4)
# ---------------------------------------------------------------------------
PARTICIPANT = struct.Struct("<7B48s3B")  # 58 bytes
assert PARTICIPANT.size == 58


def _decode_participants(data: bytes) -> dict[str, Any]:
    num_active = data[29]
    cars = []
    for v in PARTICIPANT.iter_unpack(data[30:30 + PARTICIPANT.size * NUM_CARS]):
        name = v[7].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        cars.append({
            "ai_controlled": bool(v[0]), "driver_id": v[1], "network_id": v[2],
            "team_id": v[3], "my_team": bool(v[4]), "race_number": v[5],
            "nationality": v[6], "name": name, "your_telemetry": v[8],
            "show_online_names": v[9], "platform": v[10],
        })
    return {"num_active_cars": num_active, "cars": cars}


# ---------------------------------------------------------------------------
# Car telemetry (id 6) — all cars
# ---------------------------------------------------------------------------
CAR_TELEMETRY = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")  # 60 bytes
assert CAR_TELEMETRY.size == 60


def _decode_car_telemetry(data: bytes) -> dict[str, Any]:
    cars = []
    for v in CAR_TELEMETRY.iter_unpack(data[29:29 + CAR_TELEMETRY.size * NUM_CARS]):
        cars.append({
            "speed_kph": v[0],
            "throttle": v[1],   # 0..1
            "steer": v[2],      # -1 (full left) .. 1 (full right)
            "brake": v[3],      # 0..1
            "clutch": v[4],
            "gear": v[5],       # -1 reverse, 0 neutral
            "engine_rpm": v[6],
            "drs": bool(v[7]),
            "rev_lights_percent": v[8],
            "rev_lights_bit_value": v[9],
            "brakes_temperature": v[10:14],         # °C, RL RR FL FR
            "tyres_surface_temperature": v[14:18],  # °C
            "tyres_inner_temperature": v[18:22],    # °C
            "engine_temperature": v[22],
            "tyres_pressure": v[23:27],             # PSI
            "surface_type": v[27:31],
        })
    mfd, mfd2, suggested = struct.unpack_from("<BBb", data, 29 + CAR_TELEMETRY.size * NUM_CARS)
    return {"cars": cars, "mfd_panel_index": mfd,
            "mfd_panel_index_secondary": mfd2, "suggested_gear": suggested}


# ---------------------------------------------------------------------------
# Car status (id 7) — all cars
# ---------------------------------------------------------------------------
CAR_STATUS = struct.Struct("<5B3f2H2BH3Bb3fB3fB")  # 55 bytes
assert CAR_STATUS.size == 55

TRACTION_CONTROL = {0: "off", 1: "medium", 2: "full"}
TYRE_COMPOUND_VISUAL = {16: "soft", 17: "medium", 18: "hard", 7: "inter", 8: "wet",
                        15: "wet", 19: "super_soft", 20: "soft", 21: "medium", 22: "hard"}
ERS_MODES = {0: "none", 1: "medium", 2: "hotlap", 3: "overtake"}


def _decode_car_status(data: bytes) -> dict[str, Any]:
    cars = []
    for v in CAR_STATUS.iter_unpack(data[29:29 + CAR_STATUS.size * NUM_CARS]):
        cars.append({
            "traction_control": v[0],
            "traction_control_name": TRACTION_CONTROL.get(v[0], "unknown"),
            "anti_lock_brakes": bool(v[1]),
            "fuel_mix": v[2],
            "front_brake_bias": v[3],
            "pit_limiter": bool(v[4]),
            "fuel_in_tank": v[5],
            "fuel_capacity": v[6],
            "fuel_remaining_laps": v[7],
            "max_rpm": v[8],
            "idle_rpm": v[9],
            "max_gears": v[10],
            "drs_allowed": bool(v[11]),
            "drs_activation_distance": v[12],
            "actual_tyre_compound": v[13],
            "visual_tyre_compound": v[14],
            "tyre_compound_name": TYRE_COMPOUND_VISUAL.get(v[14], f"compound_{v[14]}"),
            "tyres_age_laps": v[15],
            "vehicle_fia_flags": v[16],  # -1 invalid, 0 none, 1 green, 2 blue, 3 yellow
            "engine_power_ice": v[17],
            "engine_power_mguk": v[18],
            "ers_store_energy": v[19],  # joules, max 4 MJ
            "ers_deploy_mode": v[20],
            "ers_harvested_this_lap_mguk": v[21],
            "ers_harvested_this_lap_mguh": v[22],
            "ers_deployed_this_lap": v[23],
            "network_paused": bool(v[24]),
        })
    return {"cars": cars}


# ---------------------------------------------------------------------------
# Car damage (id 10) — all cars
# ---------------------------------------------------------------------------
CAR_DAMAGE = struct.Struct("<4f4B4B18B")  # 42 bytes
assert CAR_DAMAGE.size == 42


def _decode_car_damage(data: bytes) -> dict[str, Any]:
    cars = []
    for v in CAR_DAMAGE.iter_unpack(data[29:29 + CAR_DAMAGE.size * NUM_CARS]):
        cars.append({
            "tyres_wear": v[0:4],     # percent, RL RR FL FR
            "tyres_damage": v[4:8],   # percent
            "brakes_damage": v[8:12],
            "front_left_wing_damage": v[12],
            "front_right_wing_damage": v[13],
            "rear_wing_damage": v[14],
            "floor_damage": v[15],
            "diffuser_damage": v[16],
            "sidepod_damage": v[17],
            "drs_fault": bool(v[18]),
            "ers_fault": bool(v[19]),
            "gearbox_damage": v[20],
            "engine_damage": v[21],
            "engine_mguh_wear": v[22],
            "engine_es_wear": v[23],
            "engine_ce_wear": v[24],
            "engine_ice_wear": v[25],
            "engine_mguk_wear": v[26],
            "engine_tc_wear": v[27],
            "engine_blown": bool(v[28]),
            "engine_seized": bool(v[29]),
        })
    return {"cars": cars}


# ---------------------------------------------------------------------------
# Session history (id 11) — one car per packet
# ---------------------------------------------------------------------------
LAP_HISTORY = struct.Struct("<IHBHBHBB")  # 14 bytes
STINT_HISTORY = struct.Struct("<BBB")
assert LAP_HISTORY.size == 14


def _decode_session_history(data: bytes) -> dict[str, Any]:
    head = struct.unpack_from("<7B", data, 29)
    num_laps, num_stints = head[1], head[2]
    laps = []
    off = 36
    for i in range(min(num_laps, 100)):
        v = LAP_HISTORY.unpack_from(data, off + i * 14)
        laps.append({
            "lap_time_ms": v[0],
            "sector1_ms": v[1] + v[2] * 60000,
            "sector2_ms": v[3] + v[4] * 60000,
            "sector3_ms": v[5] + v[6] * 60000,
            "valid_flags": v[7],  # bit0 lap, bit1 S1, bit2 S2, bit3 S3
            "lap_valid": bool(v[7] & 0x01),
        })
    off += 100 * 14
    stints = []
    for i in range(min(num_stints, 8)):
        end, actual, visual = STINT_HISTORY.unpack_from(data, off + i * 3)
        stints.append({"end_lap": end, "actual_compound": actual, "visual_compound": visual})
    return {
        "car_idx": head[0], "num_laps": num_laps, "num_tyre_stints": num_stints,
        "best_lap_time_lap_num": head[3], "best_sector1_lap_num": head[4],
        "best_sector2_lap_num": head[5], "best_sector3_lap_num": head[6],
        "laps": laps, "tyre_stints": stints,
    }


# ---------------------------------------------------------------------------
# Motion Ex (id 13) — player car only
# ---------------------------------------------------------------------------
MOTION_EX = struct.Struct("<47f")  # 188 bytes
assert 29 + MOTION_EX.size == PACKET_SIZES[PACKET_MOTION_EX]


def _decode_motion_ex(data: bytes) -> dict[str, Any]:
    v = MOTION_EX.unpack_from(data, 29)
    return {
        "suspension_position": v[0:4],
        "suspension_velocity": v[4:8],
        "suspension_acceleration": v[8:12],
        "wheel_speed": v[12:16],        # m/s, RL RR FL FR
        "wheel_slip_ratio": v[16:20],   # <0 locking, >0 spinning
        "wheel_slip_angle": v[20:24],
        "wheel_lat_force": v[24:28],
        "wheel_long_force": v[28:32],
        "height_of_cog_above_ground": v[32],
        "local_velocity": v[33:36],
        "angular_velocity": v[36:39],
        "angular_acceleration": v[39:42],
        "front_wheels_angle": v[42],
        "wheel_vert_force": v[43:47],
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
DECODERS: dict[int, Callable[[bytes], dict[str, Any]]] = {
    PACKET_MOTION: _decode_motion,
    PACKET_SESSION: _decode_session,
    PACKET_LAP_DATA: _decode_lap_data,
    PACKET_EVENT: _decode_event,
    PACKET_PARTICIPANTS: _decode_participants,
    PACKET_CAR_TELEMETRY: _decode_car_telemetry,
    PACKET_CAR_STATUS: _decode_car_status,
    PACKET_CAR_DAMAGE: _decode_car_damage,
    PACKET_SESSION_HISTORY: _decode_session_history,
    PACKET_MOTION_EX: _decode_motion_ex,
}


def decode(data: bytes) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Decode one datagram. Returns (header, body). Body is None for packet
    types that aren't decoded yet. Raises PacketError on bad input."""
    header = decode_header(data)
    if header["packet_format"] != 2023:
        raise PacketError(
            f"packet format {header['packet_format']} — set UDP Format to 2023 in game")
    pid = header["packet_id"]
    expected = PACKET_SIZES.get(pid)
    if expected is None:
        raise PacketError(f"unknown packet id {pid}")
    if len(data) != expected:
        raise PacketError(
            f"{PACKET_NAMES[pid]} packet is {len(data)} bytes, expected {expected}")
    decoder = DECODERS.get(pid)
    return header, (decoder(data) if decoder else None)
