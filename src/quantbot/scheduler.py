"""Cron trigger + a serialized pipeline runner, shared by the container service.

The daily schedule (see src/quantbot/service.py) and any on-demand /report both call
:func:`run_pipeline`. It is serialized behind a lock: the SQLite DB has a single writer,
so two overlapping runs could collide. A run requested while another is in flight returns
``"busy"`` rather than piling on.

Each run shells out to a *fresh* ``python -m quantbot.pipeline`` process, so one bad
morning — a hang or an unhandled crash — is isolated and cannot take the scheduler (or
the command listener) down with it.

Schedule and timezone come from the environment (defaults = 07:00 Asia/Singapore,
Mon-Fri), so you can retune without rebuilding the image:

    QUANTBOT_SCHEDULE_DAYS   cron day_of_week   (default "mon-fri")
    QUANTBOT_SCHEDULE_HOUR   hour               (default "7")
    QUANTBOT_SCHEDULE_MINUTE minute             (default "0")
    QUANTBOT_TZ / TZ         IANA timezone      (default "Asia/Singapore")
    QUANTBOT_RUN_TIMEOUT_SEC per-run hard cap   (default 900)
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable

from apscheduler.triggers.cron import CronTrigger

from quantbot.pipeline import PROGRESS_PREFIX

log = logging.getLogger("quantbot.scheduler")

# One run is bounded so a stuck provider call can't wedge things forever. Matches the
# intent of the old systemd TimeoutStartSec.
RUN_TIMEOUT_SEC = int(os.environ.get("QUANTBOT_RUN_TIMEOUT_SEC", "900"))

# Serializes every pipeline run (scheduled or on-demand) — SQLite has one writer.
_run_lock = threading.Lock()


def schedule_timezone() -> str:
    """IANA timezone the schedule fires in. TZ pins it independent of host clock."""
    return os.environ.get("QUANTBOT_TZ") or os.environ.get("TZ") or "Asia/Singapore"


def make_trigger() -> CronTrigger:
    """Build the cron trigger from the environment (defaults: 07:00 SGT, Mon-Fri)."""
    return CronTrigger(
        day_of_week=os.environ.get("QUANTBOT_SCHEDULE_DAYS", "mon-fri"),
        hour=os.environ.get("QUANTBOT_SCHEDULE_HOUR", "7"),
        minute=os.environ.get("QUANTBOT_SCHEDULE_MINUTE", "0"),
        timezone=schedule_timezone(),
    )


def run_pipeline(on_progress: Callable[[str], None] | None = None) -> str:
    """Run one full pipeline, serialized against any other run.

    If ``on_progress`` is given, each stage marker the pipeline prints is forwarded to
    it (used to stream live progress to Telegram on an on-demand /report). Returns
    ``"ok"`` (exit 0), ``"failed"`` (non-zero / timeout / crash), or ``"busy"`` (another
    run already holds the lock — this request was skipped).
    """
    if not _run_lock.acquire(blocking=False):
        log.info("pipeline run requested but one is already in progress; skipping")
        return "busy"
    try:
        log.info("pipeline run starting")
        return _stream_pipeline(on_progress)
    except subprocess.TimeoutExpired:
        log.error("pipeline run timed out after %ds", RUN_TIMEOUT_SEC)
        return "failed"
    except Exception:  # noqa: BLE001 - never let one bad run kill the caller
        log.exception("pipeline run raised")
        return "failed"
    finally:
        _run_lock.release()


def _stream_pipeline(on_progress: Callable[[str], None] | None) -> str:
    """Run the pipeline subprocess, echoing its stdout and forwarding stage markers.

    A watchdog kills the run if it overruns RUN_TIMEOUT_SEC. stderr is inherited so the
    real logs still land in `docker logs`; stdout is read here for the markers.
    """
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "quantbot.pipeline", "--stage", "all"],
        stdout=subprocess.PIPE,
        text=True,
    )
    watchdog = threading.Timer(RUN_TIMEOUT_SEC, proc.kill)
    watchdog.start()
    try:
        for line in proc.stdout:  # streams until the process exits
            line = line.rstrip("\n")
            if line.startswith(PROGRESS_PREFIX):
                msg = line[len(PROGRESS_PREFIX):]
                log.info("progress: %s", msg)
                if on_progress is not None:
                    try:
                        on_progress(msg)
                    except Exception:  # noqa: BLE001 - a failed ping must not abort the run
                        log.warning("progress callback failed", exc_info=True)
            elif line:
                log.info("pipeline: %s", line)
        proc.wait()
    finally:
        watchdog.cancel()
    log.info("pipeline run finished (exit %d)", proc.returncode)
    return "ok" if proc.returncode == 0 else "failed"
