"""Turn numbers into the way an engineer says them on the radio."""

from __future__ import annotations

_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

SESSION_SPOKEN = {
    "P1": "practice one", "P2": "practice two", "P3": "practice three",
    "short_practice": "practice", "Q1": "Q1", "Q2": "Q2", "Q3": "Q3",
    "short_qualifying": "qualifying", "one_shot_qualifying": "one-shot qualifying",
    "race": "the race", "race_2": "race two", "race_3": "race three",
    "time_trial": "time trial", "unknown": "the session",
}

COMPOUND_SPOKEN = {"soft": "softs", "medium": "mediums", "hard": "hards",
                   "inter": "inters", "wet": "wets", "super_soft": "super softs"}


def lap_time(ms: int) -> str:
    """89412 -> '1 29.4', 65300 -> '1 oh 5.3', 58200 -> '58.2'."""
    tenths = round(ms / 100)
    m, rem = divmod(tenths, 600)
    s = rem / 10
    if m == 0:
        return f"{s:.1f}"
    if s < 10:
        return f"{m} oh {s:.1f}"
    return f"{m} {s:.1f}"


def delta(seconds: float) -> str:
    """0.4 -> 'four tenths', 0.1 -> 'a tenth', 1.26 -> '1.3 seconds', 0.04 -> 'a few hundredths'."""
    d = abs(seconds)
    if d < 0.05:
        return "a few hundredths"
    tenths = round(d * 10)
    if tenths < 10:
        return "a tenth" if tenths == 1 else f"{_WORDS[tenths]} tenths"
    return f"{d:.1f} seconds"


def gap(ms: int) -> str:
    return f"{ms / 1000:.1f}"


def position(p: int) -> str:
    return f"P{p}"
