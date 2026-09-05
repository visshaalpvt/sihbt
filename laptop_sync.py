"""
laptop_sync.py — Run this ON YOUR LAPTOP (not on Render).

It scrapes the SIH site locally (your IP isn't blocked, unlike Render's),
then pushes the results to your deployed app's /sync endpoint so everyone
on the team sees fresh data on the shared Render URL.

SETUP (one-time, on your laptop):
    pip install -r requirements.txt
    playwright install chromium

USAGE:
    python laptop_sync.py                 # sync once
    python laptop_sync.py --loop          # sync every SYNC_INTERVAL_SECONDS forever
    python laptop_sync.py --loop --interval 1800   # every 30 min instead of default

You need two things set as environment variables (or edit the defaults below):
    RENDER_URL    -> e.g. https://sihbt.onrender.com
    SYNC_SECRET   -> must match the SYNC_SECRET env var you set on Render
"""

import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

import collector  # reuses your existing, already-working scraper

RENDER_URL = os.environ.get("RENDER_URL", "https://sihbt.onrender.com").rstrip("/")
SYNC_SECRET = os.environ.get("SYNC_SECRET", "")
DEFAULT_INTERVAL = int(os.environ.get("SYNC_INTERVAL_SECONDS", "1800"))  # 30 min


def sync_once():
    if not SYNC_SECRET:
        print("[laptop_sync] ERROR: SYNC_SECRET not set. Set it as an env var "
              "(must match the SYNC_SECRET you set on Render).")
        return False

    print("[laptop_sync] Scraping SIH portal locally...")
    ps_list = collector.fetch_problem_statements()
    print(f"[laptop_sync] Scraped {len(ps_list)} problem statements.")

    if not ps_list:
        print("[laptop_sync] No data scraped, skipping push.")
        return False

    print(f"[laptop_sync] Pushing to {RENDER_URL}/sync ...")
    try:
        resp = requests.post(
            f"{RENDER_URL}/sync",
            headers={"X-Sync-Secret": SYNC_SECRET},
            json={"problem_statements": ps_list},
            timeout=60,
        )
        resp.raise_for_status()
        print(f"[laptop_sync] Success: {resp.json()}")
        return True
    except requests.exceptions.HTTPError as e:
        print(f"[laptop_sync] Server rejected sync: {e.response.status_code} {e.response.text[:300]}")
        return False
    except Exception as e:
        print(f"[laptop_sync] Failed to push: {e}")
        return False


if __name__ == "__main__":
    loop_mode = "--loop" in sys.argv
    interval = DEFAULT_INTERVAL
    if "--interval" in sys.argv:
        idx = sys.argv.index("--interval")
        interval = int(sys.argv[idx + 1])

    if not loop_mode:
        sync_once()
    else:
        print(f"[laptop_sync] Looping every {interval}s. Ctrl+C to stop.")
        while True:
            sync_once()
            print(f"[laptop_sync] Sleeping {interval}s...")
            time.sleep(interval)
