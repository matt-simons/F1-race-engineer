"""Plain-text live readout of the session (no extra packages needed)."""

from __future__ import annotations

import os
import sys

from .packets import WHEELS
from .state import SessionState


def _ms(ms: int | None) -> str:
    if not ms:
        return "--:--.---"
    m, rem = divmod(ms, 60000)
    return f"{m}:{rem / 1000:06.3f}"


def _wheels(values, fmt="{:>4}") -> str:
    # Show as FL FR / RL RR, the way a driver thinks about the car.
    v = dict(zip(WHEELS, values))
    return f"FL {fmt.format(v['FL'])}  FR {fmt.format(v['FR'])}  RL {fmt.format(v['RL'])}  RR {fmt.format(v['RR'])}"


def render(s: SessionState, calls=None) -> str:
    lines = []
    if s.session:
        ss = s.session
        lines.append(f"{ss['track_name']}  |  {ss['session_type_name']} ({ss['session_category']})  |  "
                     f"{ss['weather_name']}  track {ss['track_temperature']}°C air {ss['air_temperature']}°C"
                     + (f"  |  SAFETY CAR: {ss['safety_car_name']}" if ss['safety_car_status'] else ""))
    else:
        lines.append("Waiting for session data… (is UDP telemetry on in the game?)")
    if s.lap:
        l = s.lap
        lines.append(f"Lap {l['current_lap_num']}  P{l['car_position']}  "
                     f"current {_ms(l['current_lap_time_ms'])}  last {_ms(l['last_lap_time_ms'])}  "
                     f"best {_ms(s.best_lap().lap_time_ms if s.best_lap() else None)}  "
                     f"dist {l['lap_distance']:7.1f} m  sector {l['sector'] + 1}"
                     + ("  INVALID" if l['current_lap_invalid'] else ""))
    if s.telemetry:
        t = s.telemetry
        lines.append(f"Speed {t['speed_kph']:3d} km/h  gear {t['gear']:>2}  rpm {t['engine_rpm']:5d}  "
                     f"throttle {t['throttle'] * 100:3.0f}%  brake {t['brake'] * 100:3.0f}%  "
                     f"steer {t['steer']:+.2f}" + ("  DRS" if t['drs'] else ""))
        lines.append("Brakes °C   " + _wheels(t['brakes_temperature']))
        lines.append("Tyre surf °C " + _wheels(t['tyres_surface_temperature']))
        lines.append("Tyre core °C " + _wheels(t['tyres_inner_temperature']))
        lines.append("Pressure    " + _wheels(t['tyres_pressure'], "{:4.1f}"))
    if s.damage:
        lines.append("Wear %      " + _wheels(s.damage['tyres_wear'], "{:4.1f}"))
    if s.motion_ex:
        lines.append("Slip ratio  " + _wheels(s.motion_ex['wheel_slip_ratio'], "{:+.2f}"))
    if s.status:
        st = s.status
        lines.append(f"Tyres {st['tyre_compound_name']} ({st['tyres_age_laps']} laps)  "
                     f"fuel {st['fuel_in_tank']:.1f} kg ({st['fuel_remaining_laps']:+.2f} laps)  "
                     f"ERS {st['ers_store_energy'] / 4e6 * 100:3.0f}%  "
                     f"TC {st['traction_control_name']}  ABS {'on' if st['anti_lock_brakes'] else 'off'}")
    if s.last_event:
        lines.append(f"Last event: {s.last_event}")
    if calls is not None:
        lines.append(f"Engineer ({calls.mode} mode, {len(calls.queue)} queued):")
        for _, c in calls.history[-5:]:
            lines.append(f"  P{c.priority}  {c.text}")
    counts = "  ".join(f"{k}:{v}" for k, v in sorted(s.packet_counts.items()))
    lines.append(f"Packets  {counts}")
    if s.errors:
        lines.append(f"Errors: {s.errors}  last: {s.last_error}")
    return "\n".join(lines)


_first = True


def draw(s: SessionState, calls=None) -> None:
    global _first
    if _first and os.name == "nt":
        os.system("")  # enables ANSI escape codes in the Windows console
    _first = False
    sys.stdout.write("\x1b[H\x1b[2J" + render(s, calls) + "\n")
    sys.stdout.flush()
