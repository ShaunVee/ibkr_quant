"""What changed since yesterday — the memory layer's first output.

IBKR has no memory: it can't tell you a flag is *new today* vs. one that's been
grinding for a week, or that a risk you were watching just *cleared*. This diffs
today's flags against the last run and classifies each as new / persistent / cleared,
with a streak length for the persistent ones. Threshold-crossing detection on prices
lives elsewhere (it needs no persistence); this module handles the flag state machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from quantbot.models import Flag


def _key(flag: Flag) -> tuple[str, str]:
    return (flag.code, flag.symbol or "")


@dataclass(slots=True)
class FlagChange:
    flag: Flag
    status: str          # "new" | "persistent" | "cleared"
    streak: int = 1      # runs active including today; 0 for cleared


def diff(
    today_flags: list[Flag],
    prior_flags: list[Flag] | None,
    *,
    streak_of: Callable[[str, str], int] | None = None,
) -> list[FlagChange]:
    """Classify today's flags against the previous run.

    prior_flags is None when there is no earlier run to diff against (e.g. the very
    first day of tracking) — in that case we emit nothing rather than flooding the
    brief with "new" for every existing flag.
    """
    if prior_flags is None:
        return []

    prior_keys = {_key(f) for f in prior_flags}
    today_keys = {_key(f) for f in today_flags}
    changes: list[FlagChange] = []

    for f in today_flags:
        k = _key(f)
        status = "persistent" if k in prior_keys else "new"
        streak = streak_of(*k) if (streak_of and status == "persistent") else 1
        changes.append(FlagChange(flag=f, status=status, streak=streak))

    # Cleared: present last run, gone today. Reconstruct from the stored prior flag.
    for f in prior_flags:
        if _key(f) not in today_keys:
            changes.append(FlagChange(flag=f, status="cleared", streak=0))

    return changes
