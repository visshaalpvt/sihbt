"""
app.py — Entry point for deployment (Render).
Wraps scheduler.py's loop in a background thread + serves a dashboard.
"""

import os
import threading

from flask import Flask, render_template_string, request
import json
import requests

import db
import scheduler
import analytics

app = Flask(__name__)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "minimax/minimax-m3:free")
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
  <title>SIH 2026 Tracker Assistant</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: 'Segoe UI', -apple-system, sans-serif;
      background: linear-gradient(135deg, #f5f7fa 0%, #e8ecf3 100%);
      margin: 0; min-height: 100vh;
      display: flex; align-items: center; justify-content: center;
      padding: 24px;
    }
    .card {
      background: #ffffff; border-radius: 20px;
      box-shadow: 0 10px 40px rgba(0,0,0,0.08);
      padding: 32px; max-width: 640px; width: 100%;
    }
    .header { text-align: center; margin-bottom: 24px; }
    .header h1 {
      font-size: 24px; margin: 0 0 6px 0;
      background: linear-gradient(90deg, #6366f1, #8b5cf6);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    .header p { color: #64748b; font-size: 13px; margin: 0; }
    #chatLog {
      min-height: 280px; max-height: 420px; overflow-y: auto;
      background: #f8fafc; border-radius: 14px; padding: 16px;
      margin-bottom: 16px; font-size: 14px; line-height: 1.5;
    }
    .msg-user, .msg-bot {
      margin-bottom: 12px; padding: 10px 14px; border-radius: 12px; max-width: 85%;
    }
    .msg-user {
      background: #6366f1; color: white; margin-left: auto;
      border-bottom-right-radius: 4px;
    }
    .msg-bot {
      background: #eef2ff; color: #1e293b; margin-right: auto;
      border-bottom-left-radius: 4px; white-space: pre-wrap;
    }
    .input-row { display: flex; gap: 10px; }
    #chatInput {
      flex: 1; padding: 12px 16px; border-radius: 12px;
      border: 1px solid #e2e8f0; font-size: 14px; outline: none;
    }
    #chatInput:focus { border-color: #6366f1; }
    button {
      padding: 12px 22px; border-radius: 12px; border: none;
      background: linear-gradient(90deg, #6366f1, #8b5cf6);
      color: white; font-weight: 600; cursor: pointer; font-size: 14px;
    }
    button:hover { opacity: 0.9; }
    .placeholder { color: #94a3b8; font-size: 13px; text-align: center; padding: 40px 0; }
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>SIH 2026 Tracker Assistant</h1>
      <p>Ask anything about submission counts, competition, or trends</p>
    </div>
    <div id="chatLog">
      <div class="placeholder">Try: "which PS has low competition?" or "how many hardware problem statements?"</div>
    </div>
    <div class="input-row">
      <input id="chatInput" type="text" placeholder="Type your question...">
      <button onclick="sendChat()">Ask</button>
    </div>
  </div>

  <script>
    async function sendChat() {
      const input = document.getElementById('chatInput');
      const log = document.getElementById('chatLog');
      const question = input.value.trim();
      if (!question) return;

      const placeholder = log.querySelector('.placeholder');
      if (placeholder) placeholder.remove();

      const userDiv = document.createElement('div');
      userDiv.className = 'msg-user';
      userDiv.textContent = question;
      log.appendChild(userDiv);
      input.value = '';

      const thinkingDiv = document.createElement('div');
      thinkingDiv.className = 'msg-bot';
      thinkingDiv.id = 'thinking';
      thinkingDiv.textContent = 'Thinking...';
      log.appendChild(thinkingDiv);
      log.scrollTop = log.scrollHeight;

      try {
        const res = await fetch('/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question})
        });
        const data = await res.json();
        document.getElementById('thinking').remove();
        const botDiv = document.createElement('div');
        botDiv.className = 'msg-bot';
        botDiv.textContent = data.answer;
        log.appendChild(botDiv);
      } catch (e) {
        document.getElementById('thinking').remove();
        const errDiv = document.createElement('div');
        errDiv.className = 'msg-bot';
        errDiv.textContent = 'Something went wrong. Try again.';
        log.appendChild(errDiv);
      }
      log.scrollTop = log.scrollHeight;
    }
    document.getElementById('chatInput').addEventListener('keypress', e => {
      if (e.key === 'Enter') sendChat();
    });
  </script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_TEMPLATE)


@app.route("/health")
def health():
    return {"status": "ok"}, 200


SYNC_SECRET = os.environ.get("SYNC_SECRET", "")


@app.route("/sync", methods=["POST"])
def sync():
    """Accepts scraped PS data (pushed from a laptop/machine that isn't
    blocked by the SIH site's WAF) and writes it into this app's own
    database, exactly as if the local scheduler had fetched it itself."""
    if not SYNC_SECRET:
        return {"error": "SYNC_SECRET not configured on server"}, 500
    if request.headers.get("X-Sync-Secret", "") != SYNC_SECRET:
        return {"error": "unauthorized"}, 401

    payload = request.json or {}
    ps_list = payload.get("problem_statements", [])
    if not ps_list:
        return {"error": "no problem_statements in payload"}, 400

    sync_id = db.log_sync_start()
    try:
        events = []
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
                events.append(event)
        db.log_sync_end(sync_id, "success", ps_count=len(ps_list))
        return {"status": "ok", "ps_count": len(ps_list), "events": len(events)}
    except Exception as e:
        db.log_sync_end(sync_id, "failed", error_message=str(e))
        return {"error": str(e)}, 500


@app.route("/chat", methods=["POST"])
def chat():
    question = (request.json or {}).get("question", "").strip()
    if not question:
        return {"answer": "Ask me something about the tracker data!"}, 400

    data = analytics.build_leaderboard()
    all_ps = data.get("problem_statements", [])

    category_counts = {}
    for ps in all_ps:
        cat = (ps.get("category") or "Unknown").strip() or "Unknown"
        category_counts[cat] = category_counts.get(cat, 0) + 1

    compact_lines = [
        f"{ps['ps_id']} | {ps.get('category','?')} | {ps.get('organization','?')} | count={ps['current_count']} | {ps.get('label','')}"
        for ps in all_ps
    ]
    compact_data = "\n".join(compact_lines)[:12000]

    system_prompt = (
        "You are a helpful assistant for a hackathon team's SIH 2026 tracker.\n"
        f"Exact category counts (trust these numbers exactly, don't recount): {category_counts}\n"
        f"Summary: {data.get('summary', {})}\n\n"
        "Per-PS data (ps_id | category | organization | count | status):\n"
        f"{compact_data}\n\n"
        "Answer using only this data. For 'how many' questions, use the exact "
        "category counts given above rather than counting rows yourself. "
        "Give ONLY the final answer in plain sentences -- never show your "
        "reasoning steps, thinking process, or a numbered scan of the list."
    )

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                "max_tokens": 300,
            },
            timeout=30,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"]
    except requests.exceptions.HTTPError as e:
        answer = f"OpenRouter error {e.response.status_code}: {e.response.text[:300]}"
    except Exception as e:
        answer = f"Something went wrong talking to the AI: {e}"

    return {"answer": answer}


start_scheduler_background()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)