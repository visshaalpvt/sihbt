# SIH 2026 Submission Tracker

Monitors problem statement submission counts on sih.gov.in, detects new
submissions, and alerts your whole team on Telegram. No AI involved — just
polling, diffing, and rule-based stats.

## Status right now

- ✅ Database (SQLite) — done, working
- ✅ Change detection + glitch protection — done, working
- ✅ Telegram notifications — done, working
- ✅ Analytics ("what's heating up / low competition") — done, working
- ⚠️ **Collector (`collector.py`) is running in DEMO MODE with fake data.**
  The real scraping/API logic depends on inspecting sih.gov.in/sih2026PS in
  DevTools first (see instructions Claude gave earlier in the chat). Once
  you send that info, `fetch_problem_statements()` gets finished for real.

You can run the entire pipeline end-to-end right now with fake data to make
sure everything works, before the real data source is wired in.

## Setup

1. Install Python 3.10+ if you don't have it.
2. In this folder, create a virtual environment and install dependencies:

   ```bash
   python -m venv venv
   source venv/bin/activate        # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and fill in your Telegram bot token
   (steps are in the docstring at the top of `notifier.py`).

4. Initialize the database:

   ```bash
   python db.py
   ```

5. Register your team members so they receive alerts:

   ```bash
   python
   >>> import db
   >>> db.add_team_member("Visshaal", "123456789")   # chat_id from Telegram
   >>> db.add_team_member("Member 2", "987654321")
   >>> exit()
   ```

6. Load the .env file and start the tracker:

   ```bash
   export $(cat .env | xargs)   # Windows: use a tool like python-dotenv instead
   python scheduler.py
   ```

   It will run forever, checking every `POLL_INTERVAL_SECONDS` (default 5 min),
   and send Telegram alerts whenever a count changes. Press Ctrl+C to stop.

## Checking analytics / leaderboard

At any time, run:

```bash
python analytics.py
```

This prints the full leaderboard with labels like `🔴 Rapidly Increasing`
and `🟢 Low Competition`, computed purely from stored history — no API calls.

## File overview

| File            | Purpose                                                             |
|-----------------|----------------------------------------------------------------------|
| `db.py`         | SQLite schema + all read/write functions                            |
| `collector.py`  | Fetches data from SIH portal (currently DEMO MODE — see top of file) |
| `notifier.py`   | Sends Telegram alerts                                                |
| `analytics.py`  | Computes stats-based labels (heating up / low competition)          |
| `scheduler.py`  | Main loop — run this to start the whole system                      |

## What's next (not built yet)

- Real collector logic (blocked on DevTools inspection of sih2026PS)
- Web dashboard (React) to visualize the leaderboard instead of reading
  `analytics.py` output in a terminal
- Deployment (Render/Railway) so this runs 24/7 without your laptop being on
- Email notifications alongside Telegram

Bring the DevTools findings back and we'll finish the collector next —
that's the only thing standing between this and running for real against
SIH 2026.
