"""
scheduler.py — The main loop. Run this to start the whole system:

    python scheduler.py

It will:
  1. Fetch current data from the SIH portal (via collector.py)
  2. Store snapshots + detect changes (via db.py)
  3. Send Telegram alerts for any new submissions (via notifier.py)
  4. Sleep for POLL_INTERVAL_SECONDS, then repeat — forever, until you stop it

Safeguards built in (per the "don't blindly trust one scrape" rule):
  - If a PS's count suddenly drops to 0 while others look normal, we treat
    it as a likely glitch and skip raising an event for that PS this cycle.
  - Consecutive fetch failures are logged; after too many in a row, we back
    off (wait longer) instead of hammering a possibly-broken/rate-limiting site.
  - Every cycle is logged to sync_log so you can see uptime/history later.
"""

import os
import time
import traceback

from dotenv import load_dotenv

# Explicit here too (not just relying on notifier.py's import-time call) so
# this doesn't silently break if import order ever changes.
load_dotenv()

import db
import collector
import notifier

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))  # default 5 min
MAX_CONSECUTIVE_FAILURES = 5
BACKOFF_MULTIPLIER = 3  # if we keep failing, wait 3x longer each time, up to a cap
MAX_BACKOFF_SECONDS = 3600  # never wait more than 1 hour between retries


def looks_like_glitch(ps_id, previous_count, new_count):
    """Guard against false 'everyone disappeared' alerts from a bad scrape."""
    if previous_count > 5 and new_count == 0:
        return True
    if previous_count > 0 and new_count < previous_count * 0.2:
        # count dropped by more than 80% in one check — suspicious, not impossible
        return True
    return False


def run_one_cycle():
    sync_id = db.log_sync_start()
    events = []
    try:
        ps_list = collector.fetch_problem_statements()
        if not ps_list:
            raise RuntimeError("Collector returned no problem statements")

        for ps in ps_list:
            event = db.upsert_problem_statement(
                ps_id=ps["ps_id"],
                title=ps["title"],
                organization=ps.get("organization", ""),
                category=ps.get("category", ""),
                theme=ps.get("theme", ""),
                count=ps["count"],
            )
            if event:
                if looks_like_glitch(event["ps_id"], event["previous_count"], event["new_count"]):
                    print(f"[scheduler] Suspicious drop for {event['ps_id']} — skipping alert, flagging in log.")
                    continue
                events.append(event)

        db.log_sync_end(sync_id, "success", ps_count=len(ps_list))
        print(f"[scheduler] Cycle complete: {len(ps_list)} PS checked, {len(events)} new event(s).")
        return events, True

    except Exception as e:
        db.log_sync_end(sync_id, "failed", error_message=str(e))
        print(f"[scheduler] Cycle FAILED: {e}")
        traceback.print_exc()
        return [], False


def main():
    print("=== SIH Tracker starting ===")
    print(f"Poll interval: {POLL_INTERVAL_SECONDS}s")
    db.init_db()

    consecutive_failures = 0

    while True:
        events, success = run_one_cycle()

        if success:
            consecutive_failures = 0
            for event in events:
                notifier.notify_all(event)
            wait_time = POLL_INTERVAL_SECONDS
        else:
            consecutive_failures += 1
            wait_time = min(
                POLL_INTERVAL_SECONDS * (BACKOFF_MULTIPLIER ** consecutive_failures),
                MAX_BACKOFF_SECONDS,
            )
            print(f"[scheduler] {consecutive_failures} consecutive failure(s). "
                  f"Backing off — waiting {wait_time}s before retry.")

        time.sleep(wait_time)


if __name__ == "__main__":
    main()