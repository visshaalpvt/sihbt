"""
analytics.py — "Which PS is heating up / which has low competition"

This is pure statistics, NOT AI. No API calls, no cost, no latency, nothing
that can time out. It runs instantly on data already in the database.

Core ideas:
  - percentile rank: where does this PS's count sit relative to all others?
  - velocity: how fast is its count increasing (submissions per hour)?
  - labels: simple rule-based thresholds turn numbers into human-readable tags
"""

from datetime import datetime, timedelta, timezone
from statistics import mean, median

import db


def _percentile_rank(value, all_values):
    if not all_values:
        return 0
    below_or_equal = sum(1 for v in all_values if v <= value)
    return round(100 * below_or_equal / len(all_values), 1)


def _velocity_per_hour(ps_id, window_hours=6):
    """Submissions per hour over the last `window_hours`, based on snapshot history."""
    history = db.get_snapshot_history(ps_id, limit=500)
    if len(history) < 2:
        return 0.0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    in_window = [
        h for h in history
        if datetime.fromisoformat(h["checked_at"]) >= cutoff
    ]
    if len(in_window) < 2:
        return 0.0

    in_window.sort(key=lambda h: h["checked_at"])
    oldest, newest = in_window[0], in_window[-1]
    delta_count = newest["count"] - oldest["count"]
    delta_hours = (
        datetime.fromisoformat(newest["checked_at"])
        - datetime.fromisoformat(oldest["checked_at"])
    ).total_seconds() / 3600
    # Guard against near-simultaneous checks producing a division by a tiny
    # number (which would make velocity explode to a meaningless huge value).
    # Require at least 3 minutes of real spread before trusting the rate.
    MIN_WINDOW_HOURS = 3 / 60
    if delta_hours < MIN_WINDOW_HOURS:
        return 0.0
    return round(delta_count / delta_hours, 2)


def build_leaderboard():
    """
    Returns a dict with overall stats plus a per-PS breakdown, each tagged
    with a plain-language label — this is what feeds the dashboard and any
    '/status' command you might add later.
    """
    all_ps = db.get_all_problem_statements()
    if not all_ps:
        return {"summary": {}, "problem_statements": []}

    counts = [ps["current_count"] for ps in all_ps]
    avg_count = round(mean(counts), 2)
    median_count = round(median(counts), 2)

    enriched = []
    velocities = {}
    for ps in all_ps:
        velocities[ps["ps_id"]] = _velocity_per_hour(ps["ps_id"])

    avg_velocity = round(mean(velocities.values()), 2) if velocities else 0

    for ps in all_ps:
        pct = _percentile_rank(ps["current_count"], counts)
        vel = velocities[ps["ps_id"]]

        label = "🟡 Average"
        if vel > max(2 * avg_velocity, 1):
            label = "🔴 Rapidly Increasing"
        elif pct <= 25:
            label = "🟢 Low Competition"
        elif pct >= 75:
            label = "🔥 High Competition"

        enriched.append({
            **ps,
            "percentile": pct,
            "velocity_per_hour": vel,
            "label": label,
        })

    enriched.sort(key=lambda p: p["current_count"], reverse=True)

    return {
        "summary": {
            "total_ps": len(all_ps),
            "total_submissions": sum(counts),
            "average_per_ps": avg_count,
            "median_per_ps": median_count,
            "average_velocity_per_hour": avg_velocity,
        },
        "problem_statements": enriched,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(build_leaderboard(), indent=2))
