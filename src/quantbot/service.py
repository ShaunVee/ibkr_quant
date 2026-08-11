"""Container entrypoint: the daily scheduler AND an on-demand command listener.

One long-lived process does both jobs:

  * a BackgroundScheduler fires the pipeline on the cron schedule (07:00 SGT weekdays);
  * the main thread long-polls Telegram so you can trigger a run any time with /report.

Both paths call :func:`quantbot.scheduler.run_pipeline`, which serializes them behind a
lock — a /report sent while a run is in flight is told to wait rather than colliding on
the SQLite writer. The pipeline itself sends the brief (and any failure ping), so the
command replies here are just acknowledgements over the same code path as the schedule.

Only ONE process may poll getUpdates for a given bot token — a second poller steals
updates. This is that one process; do not scale it past 1.

Commands (accepted only from the configured TELEGRAM_CHAT_ID):
    /report   run the full pipeline now and send the brief
    /status   next scheduled run + whether a run is in progress
    /help     list commands
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from apscheduler.schedulers.background import BackgroundScheduler

from quantbot.config import load_config
from quantbot.delivery.telegram import TelegramNotifier
from quantbot.scheduler import make_trigger, run_pipeline, schedule_timezone

log = logging.getLogger("quantbot.service")

# Long-poll window for getUpdates. Telegram holds the request open until an update
# arrives or this elapses, so the loop is near-idle between commands.
_POLL_TIMEOUT_SEC = 50

_HELP = (
    "quantbot commands:\n"
    "/report — run the full brief now\n"
    "/status — next scheduled run + current state\n"
    "/help — this message"
)


def parse_command(text: str | None) -> str:
    """Normalize a message to its bare command: '/Report@MyBot now' -> '/report'.

    Returns '' for empty/non-command text.
    """
    if not text or not text.strip():
        return ""
    return text.strip().split()[0].split("@")[0].lower()


def _run_and_ack(notifier: TelegramNotifier) -> None:
    """Run the pipeline (in a worker thread), streaming stage progress and the outcome."""
    status = run_pipeline(on_progress=notifier.send)
    if status == "busy":
        notifier.send("⏳ A run is already in progress — hang tight.")
    elif status == "ok":
        notifier.send("✅ Brief sent.")
    else:  # failed — the pipeline already sent its own failure ping
        notifier.send("⚠️ Run failed. Check the logs.")


class CommandListener:
    """Long-polls Telegram and dispatches owner commands. One instance, one poller."""

    def __init__(self, token: str, chat_id: str, trigger, tz: str) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._chat_id = str(chat_id)
        self._notifier = TelegramNotifier(token, chat_id)
        self._trigger = trigger
        self._tz = tz
        self._session = requests.Session()
        self._offset: int | None = None

    # --- Telegram plumbing -------------------------------------------------

    def _get_updates(self, timeout: int) -> list[dict]:
        params = {"timeout": timeout}
        if self._offset is not None:
            params["offset"] = self._offset
        resp = self._session.get(
            f"{self._base}/getUpdates", params=params, timeout=timeout + 10
        )
        resp.raise_for_status()
        return resp.json().get("result", [])

    def _drain_backlog(self) -> None:
        """Skip commands queued while the bot was down — don't replay a stale /report."""
        updates = self._get_updates(timeout=0)
        if updates:
            self._offset = updates[-1]["update_id"] + 1
            log.info("skipped %d backlog update(s) from before start", len(updates))

    def _advertise_commands(self) -> None:
        try:
            self._session.post(
                f"{self._base}/setMyCommands",
                json={
                    "commands": [
                        {"command": "report", "description": "Run the full brief now"},
                        {"command": "status", "description": "Next run + current state"},
                        {"command": "help", "description": "List commands"},
                    ]
                },
                timeout=15,
            )
        except requests.RequestException:
            log.warning("could not register bot commands (non-fatal)", exc_info=True)

    # --- dispatch ----------------------------------------------------------

    def _handle(self, message: dict) -> None:
        chat_id = str(message.get("chat", {}).get("id", ""))
        if chat_id != self._chat_id:
            log.info("ignoring command from unauthorized chat %s", chat_id)
            return
        cmd = parse_command(message.get("text"))

        if cmd == "/report":
            self._notifier.send("🚀 Running the brief now…")
            # Off the poll thread so we keep receiving commands while it runs.
            threading.Thread(
                target=_run_and_ack, args=(self._notifier,), daemon=True
            ).start()
        elif cmd == "/status":
            self._notifier.send(self._status_text())
        elif cmd in ("/help", "/start"):
            self._notifier.send(_HELP)
        # Anything else: stay quiet (avoid nagging on unrelated messages).

    def _status_text(self) -> str:
        now = datetime.now(ZoneInfo(self._tz))
        nxt = self._trigger.get_next_fire_time(None, now)
        return f"Next scheduled run: {nxt:%Y-%m-%d %H:%M %Z}\nBot is up and listening."

    # --- main loop ---------------------------------------------------------

    def serve_forever(self) -> None:
        self._drain_backlog()
        self._advertise_commands()
        log.info("command listener ready (owner chat %s)", self._chat_id)
        while True:
            try:
                for update in self._get_updates(_POLL_TIMEOUT_SEC):
                    self._offset = update["update_id"] + 1
                    if "message" in update:
                        self._handle(update["message"])
            except requests.RequestException:
                # Network blip / Telegram hiccup: back off briefly and retry.
                log.warning("getUpdates failed; retrying shortly", exc_info=True)
                threading.Event().wait(5)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    tz = schedule_timezone()
    trigger = make_trigger()
    now = datetime.now(ZoneInfo(tz))
    log.info("scheduler armed (tz=%s); next run %s", tz, trigger.get_next_fire_time(None, now))

    scheduler = BackgroundScheduler(timezone=tz)
    # misfire_grace_time lets a run that would have fired while the host was briefly
    # down still fire on wake, instead of being silently skipped.
    scheduler.add_job(run_pipeline, trigger, id="morning_brief", misfire_grace_time=3600)
    scheduler.start()

    config = load_config()
    token = config.secrets.get("TELEGRAM_BOT_TOKEN", required=False)
    chat_id = config.secrets.get("TELEGRAM_CHAT_ID", required=False)
    if not token or not chat_id:
        # No command channel configured — still run the schedule, just don't listen.
        log.warning("TELEGRAM_BOT_TOKEN/CHAT_ID unset; on-demand /report disabled")
        try:
            threading.Event().wait()  # park forever; scheduler runs in the background
        except (KeyboardInterrupt, SystemExit):
            pass
        return 0

    try:
        CommandListener(token, chat_id, trigger, tz).serve_forever()
    except (KeyboardInterrupt, SystemExit):
        log.info("service stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
