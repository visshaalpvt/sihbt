"""
app.py — Entry point for deployment (Render).
Wraps scheduler.py's loop in a background thread + serves a dashboard.
"""

import os
import threading

from flask import Flask, render_template_string

import db
import scheduler
import analytics

app = Flask(__name__)

_scheduler_lock = threading.Lock()
_scheduler_started = False


def start_scheduler_background():
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
        db.init_db()
        thread = threading.Thread(target=scheduler.main, daemon=True, name="sih-scheduler")
        thread.start()
        print("[app] Scheduler started in background thread.")


DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="60">
  <title>SIH 2026 Tracker</title>
  <style>
    body { font-family: -apple-system, Segoe UI, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }
    h1 { font-size: 22px; margin-bottom: 4px; }
    .sub { color: #94a3b8; font-size: 13px; margin-bottom: 20px; }
    .summary { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }
    .card { background: #1e293b; border-radius: 10px; padding: 14px 18px; min-width: 130px; }
    .card .n { font-size: 22px; font-weight: 700; }
    .card .l { font-size: 12px; color: #94a3b8; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; color: #94a3b8; font-weight: 600; padding: 8px; border-bottom: 1px solid #334155; }
    td { padding: 8px; border-bottom: 1px solid #1e293b; }
    tr:hover { background: #1e293b; }
    .label { padding: 2px 8px; border-radius: 20px; font-size: 11px; white-space: nowrap; }
  </style>
</head>
<body>
  <h1>SIH 2026 Submission Tracker</h1>
  <div class="sub">Auto-refreshes every 60s &middot; scheduler runs independently in the background</div>

  <div class="summary">
    <div class="card"><div class="n">{{ data.summary.get('total_ps', 0) }}</div><div class="l">Problem Statements</div></div>
    <div class="card"><div class="n">{{ data.summary.get('total_submissions', 0) }}</div><div class="l">Total Submissions</div></div>
    <div class="card"><div class="n">{{ data.summary.get('average_per_ps', 0) }}</div><div class="l">Avg / PS</div></div>
    <div class="card"><div class="n">{{ data.summary.get('average_velocity_per_hour', 0) }}</div><div class="l">Avg Velocity / hr</div></div>
  </div>

  <table>
    <tr>
      <th>PS ID</th><th>Title</th><th>Organization</th><th>Count</th><th>Percentile</th><th>Velocity/hr</th><th>Status</th>
    </tr>
    {% for ps in data.problem_statements %}
    <tr>
      <td>{{ ps.ps_id }}</td>
      <td>{{ ps.title }}</td>
      <td>{{ ps.organization }}</td>
      <td>{{ ps.current_count }}</td>
      <td>{{ ps.percentile }}%</td>
      <td>{{ ps.velocity_per_hour }}</td>
      <td>{{ ps.label }}</td>
    </tr>
    {% endfor %}
  </table>

  {% if not data.problem_statements %}
    <p style="color:#94a3b8;">No data yet -- the scheduler runs its first check shortly after startup. Refresh in a minute.</p>
  {% endif %}
</body>
</html>
"""


@app.route("/")
def dashboard():
    data = analytics.build_leaderboard()
    return render_template_string(DASHBOARD_TEMPLATE, data=data)


@app.route("/health")
def health():
    return {"status": "ok"}, 200


start_scheduler_background()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)