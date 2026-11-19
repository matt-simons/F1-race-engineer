"""The race engineer: watches the session and queues calls.

Milestone 2 covers the basics a real engineer says every session: radio
check, lap times against your best, track-limit deletions, position changes,
flags, penalties, and answers to the controller buttons. The rules engine
(lock-ups, temps, wear) and Haiku coaching plug in here later.
"""

from __future__ import annotations

from typing import Any

from . import speech
from .calls import P1, P2, CallManager
from .state import SessionState

# Phrases rendered at start-up so they play with no delay.
PRERENDER = [
    "Safety car, safety car. No overtaking.",
    "Virtual safety car. Watch your delta.",
    "Green flag, green flag. Back to racing.",
    "Red flag, red flag. Session stopped.",
    "Track limits, that lap's deleted.",
    "Nothing to repeat yet.",
    "DRS enabled.",
]

PENALTY_TYPES = {0: "Drive-through penalty.", 1: "Stop-go penalty.", 2: "Grid penalty.",
                 5: "That's a warning.", 6: "We've been disqualified.",
                 17: "That's a black and white flag. Careful."}


class Engineer:
    def __init__(self, state: SessionState, calls: CallManager,
                 repeat_button: str = "UDP_ACTION_1", status_button: str = "UDP_ACTION_2"):
        self.state = state
        self.calls = calls
        self.repeat_button = repeat_button
        self.status_button = status_button
        self._buttons_held = 0
        self._radio_check_done = False
        self._safety_car = 0
        self._invalid_called_lap: int | None = None
        self._position: int | None = None
        self._position_pending: tuple[int, float] | None = None
        state.subscribe(self._on_state_event)

    # -- hooks from the state --------------------------------------------
    def _on_state_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "session_start":
            self._radio_check_done = False
            self._safety_car = 0
            self._position = None
            self._invalid_called_lap = None
            self._buttons_held = 0
        elif kind == "lap_complete":
            self._lap_complete(payload["lap"])
        elif kind == "button":
            self._button(payload["button_status"])
        elif kind == "event":
            self._event(payload)

    def update(self) -> None:
        """Call after every packet: checks things that change gradually."""
        s = self.state
        if not self._radio_check_done and s.session and s.status:
            self._radio_check()
        if s.session:
            sc = s.session["safety_car_status"]
            if sc != self._safety_car:
                self._safety_car_change(self._safety_car, sc)
                self._safety_car = sc
        if s.lap:
            lap = s.lap
            n = lap["current_lap_num"]
            if lap["current_lap_invalid"] and self._invalid_called_lap != n \
                    and lap["driver_status"] in (1, 4):
                self._invalid_called_lap = n
                self.calls.say("Track limits, that lap's deleted.", P2, key="invalid")
            if self.calls.mode == "race":
                self._track_position(lap["car_position"])

    # -- individual calls ------------------------------------------------
    def _radio_check(self) -> None:
        self._radio_check_done = True
        ss, st = self.state.session, self.state.status
        session = speech.SESSION_SPOKEN.get(ss["session_type_name"], "the session")
        tc = st["traction_control_name"]
        abs_ = "on" if st["anti_lock_brakes"] else "off"
        self.calls.say(f"Radio check. {ss['track_name']}, {session}. "
                       f"Traction control {tc}, ABS {abs_}. Let's go.",
                       P2, key="radio_check", expires_s=120)

    def _lap_complete(self, lap) -> None:
        if not lap.lap_time_ms:
            return
        others = [l for l in self.state.laps.values()
                  if l is not lap and l.lap_time_ms and not l.invalid]
        prev_best = min((l.lap_time_ms for l in others), default=None)
        t = speech.lap_time(lap.lap_time_ms)
        mode = self.calls.mode
        if lap.invalid:
            text = f"{t}, but it's deleted."
        elif prev_best is None:
            text = f"Lap time {t}."
        elif lap.lap_time_ms < prev_best:
            text = f"{t}. Personal best, {speech.delta((prev_best - lap.lap_time_ms) / 1000)} quicker."
        else:
            text = f"{t}. Plus {speech.delta((lap.lap_time_ms - prev_best) / 1000)} to your best."
        if mode == "race" and self.state.lap:
            text += f" {speech.position(self.state.lap['car_position'])}."
        # In qualifying, hold the lap time until the push lap is over.
        expires = 150 if mode == "qualifying" else 30
        self.calls.say(text, P2, key="lap_time", expires_s=expires)

    def _track_position(self, pos: int) -> None:
        now = self.calls.clock()
        if self._position is None:
            self._position = pos
            return
        if pos == self._position:
            self._position_pending = None
            return
        if not self._position_pending or self._position_pending[0] != pos:
            self._position_pending = (pos, now)
            return
        if now - self._position_pending[1] >= 2.0:  # ignore flicker side by side
            gained = pos < self._position
            self._position = pos
            self._position_pending = None
            self.calls.say(f"{'Good job, ' if gained else ''}{speech.position(pos)}.", P2,
                           key="position", expires_s=15)

    def _safety_car_change(self, old: int, new: int) -> None:
        if new == 1:
            self.calls.say("Safety car, safety car. No overtaking.", P1, key="sc")
        elif new == 2:
            self.calls.say("Virtual safety car. Watch your delta.", P1, key="sc")
        elif new == 0 and old in (1, 2):
            self.calls.say("Green flag, green flag. Back to racing.", P1, key="sc")

    def _event(self, e: dict[str, Any]) -> None:
        code = e["code"]
        me = self.state.player_idx
        if code == "PENA" and e.get("vehicle_idx") == me:
            ptype = e.get("penalty_type")
            if ptype == 4 and e.get("time_s"):
                text = f"{e['time_s']} second penalty."
            else:
                text = PENALTY_TYPES.get(ptype)
            if text:
                self.calls.say(text, P1 if ptype != 5 else P2, key="penalty")
        elif code == "RDFL":
            self.calls.say("Red flag, red flag. Session stopped.", P1, key="red_flag")
        elif code == "CHQF" and self.calls.mode == "race":
            self.calls.say("Chequered flag. Great job, bring it home.", P1, key="chequered")
        elif code == "DRSE" and self.calls.mode == "race":
            self.calls.say("DRS enabled.", P2, key="drs")
        elif code == "FTLP" and e.get("vehicle_idx") == me and self.calls.mode == "race":
            self.calls.say("That's the fastest lap. Purple.", P2, key="fastest_lap")

    # -- push-to-talk ------------------------------------------------------
    def _button(self, mask: int) -> None:
        from .packets import BUTTONS
        pressed = mask & ~self._buttons_held  # only react to new presses
        self._buttons_held = mask
        names = {BUTTONS[b] for b in BUTTONS if pressed & b}
        if self.repeat_button in names:
            self.calls.repeat_last()
        if self.status_button in names:
            self.calls.say(self.status_text(), P1, key="status")

    def status_text(self) -> str:
        s = self.state
        parts = []
        mode = self.calls.mode
        if s.lap:
            if mode == "race":
                pos = s.lap["car_position"]
                parts.append(f"{speech.position(pos)}.")
                if pos > 1 and s.lap["delta_to_car_in_front_ms"]:
                    parts.append(f"Gap ahead {speech.gap(s.lap['delta_to_car_in_front_ms'])}.")
            else:
                best = s.best_lap()
                if best:
                    parts.append(f"Best {speech.lap_time(best.lap_time_ms)}.")
        if s.status:
            st = s.status
            compound = speech.COMPOUND_SPOKEN.get(st["tyre_compound_name"], "tyres")
            tyre = f"{compound[0].upper()}{compound[1:]}, {st['tyres_age_laps']} laps"
            if s.damage:
                wear = sum(s.damage["tyres_wear"]) / 4
                tyre += f", {wear:.0f} percent worn"
            parts.append(tyre + ".")
            if mode == "race":
                fuel = st["fuel_remaining_laps"]
                parts.append(f"Fuel {'plus' if fuel >= 0 else 'minus'} {abs(fuel):.1f} laps.")
            parts.append(f"Battery {st['ers_store_energy'] / 4e6 * 100:.0f} percent.")
        return " ".join(parts) or "No data yet."
