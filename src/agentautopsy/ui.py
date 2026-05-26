"""Self-contained web UI for AgentAutopsy traces."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path
from typing import Any

from agentautopsy.db import get_db


def _parse_payload(raw: Any) -> dict[str, Any]:
    try:
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _event_detail(ev_type: str, payload: dict[str, Any], cassette_size: int) -> str:
    if ev_type == "llm_call":
        return f"model: {payload.get('model')}"
    if ev_type == "llm_response":
        return f"cassette: {cassette_size} bytes"
    if ev_type == "http_request":
        return f"{payload.get('method')} {payload.get('url')}"
    if ev_type == "http_response":
        return f"status: {payload.get('status_code')}"
    if ev_type == "error":
        return f"{payload.get('error_type')}: {payload.get('message')}"
    if ev_type == "tool_call":
        return f"{payload.get('tool')}: {payload.get('input')}"
    if ev_type == "tool_result":
        return f"output: {payload.get('output')}"
    if payload:
        return json.dumps(payload, default=str)
    return ""


def _root_cause(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event["type"] == "error":
            payload = event["payload"]
            error_type = payload.get("error_type", "Error")
            message = payload.get("message", "")
            return f"{error_type} — {message}"
    return None


def _load_data(db: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not db["runs"].exists():
        return [], {}

    runs = [
        {
            "id": row["id"],
            "start_time": row.get("start_time", ""),
            "status": row.get("status", ""),
        }
        for row in db["runs"].rows_where(order_by="start_time desc")
    ]

    runs_data: dict[str, dict[str, Any]] = {}
    if not db["events"].exists():
        for run in runs:
            runs_data[run["id"]] = {"items": [], "root_cause": None}
        return runs, runs_data

    for run in runs:
        run_id = run["id"]
        items: list[dict[str, Any]] = []
        raw_events: list[dict[str, Any]] = []

        for row in db["events"].rows_where(
            where="run_id = ?",
            where_args=[run_id],
            order_by="timestamp",
        ):
            payload = _parse_payload(row.get("payload"))
            cassette = row.get("cassette")
            cassette_size = len(cassette) if cassette is not None else 0
            raw_events.append({"type": row["type"], "payload": payload})
            items.append(
                {
                    "type": row["type"],
                    "timestamp": row.get("timestamp", ""),
                    "detail": _event_detail(row["type"], payload, cassette_size),
                }
            )

        runs_data[run_id] = {
            "items": items,
            "root_cause": _root_cause(raw_events),
        }

    return runs, runs_data


def _build_html(
    runs: list[dict[str, Any]], runs_data: dict[str, dict[str, Any]]
) -> str:
    runs_json = json.dumps(runs)
    data_json = json.dumps(runs_data)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AgentAutopsy</title>
  <style>
    :root {{
      --bg: #0f1419;
      --panel: #1a2332;
      --border: #2d3a4d;
      --text: #e6edf3;
      --muted: #8b949e;
      --cyan: #56d4dd;
      --yellow: #e3b341;
      --green: #3fb950;
      --red: #f85149;
      --blue: #79c0ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }}
    header {{
      padding: 1rem 1.5rem;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
    }}
    header h1 {{ margin: 0; font-size: 1.25rem; }}
    header p {{ margin: 0.25rem 0 0; color: var(--muted); font-size: 0.9rem; }}
    .layout {{
      display: grid;
      grid-template-columns: 320px 1fr;
      min-height: calc(100vh - 72px);
    }}
    .runs {{
      border-right: 1px solid var(--border);
      background: var(--panel);
      overflow-y: auto;
    }}
    .runs h2 {{
      margin: 0;
      padding: 1rem 1rem 0.5rem;
      font-size: 0.85rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
    }}
    .run-item {{
      display: block;
      width: 100%;
      text-align: left;
      border: none;
      border-bottom: 1px solid var(--border);
      background: transparent;
      color: var(--text);
      padding: 0.85rem 1rem;
      cursor: pointer;
    }}
    .run-item:hover {{ background: #243044; }}
    .run-item.active {{ background: #2d3f56; border-left: 3px solid var(--cyan); }}
    .run-id {{ font-family: ui-monospace, monospace; font-size: 0.75rem; color: var(--muted); }}
    .run-meta {{ margin-top: 0.35rem; font-size: 0.85rem; }}
    .detail {{ padding: 1.5rem; overflow-y: auto; }}
    .empty {{ color: var(--muted); padding: 2rem; }}
    .timeline {{ list-style: none; margin: 0; padding: 0; }}
    .event {{
      padding: 0.75rem 1rem;
      margin-bottom: 0.5rem;
      border-radius: 8px;
      background: #121a24;
      border-left: 4px solid var(--muted);
    }}
    .event .type {{
      font-weight: 600;
      font-family: ui-monospace, monospace;
      font-size: 0.85rem;
    }}
    .event .summary {{
      margin-top: 0.35rem;
      color: var(--muted);
      font-size: 0.9rem;
      word-break: break-word;
    }}
    .event .ts {{
      margin-top: 0.25rem;
      font-size: 0.75rem;
      color: #6e7681;
    }}
    .event.llm_call {{ border-left-color: var(--cyan); }}
    .event.llm_call .type {{ color: var(--cyan); }}
    .event.http_request {{ border-left-color: var(--yellow); }}
    .event.http_request .type {{ color: var(--yellow); }}
    .event.http_response {{ border-left-color: var(--green); }}
    .event.http_response .type {{ color: var(--green); }}
    .event.error {{ border-left-color: var(--red); }}
    .event.error .type {{ color: var(--red); }}
    .event.llm_response {{ border-left-color: var(--blue); }}
    .event.llm_response .type {{ color: var(--blue); }}
    .root-cause {{
      margin-top: 1.5rem;
      padding: 1rem 1.25rem;
      border: 1px solid var(--red);
      border-radius: 8px;
      background: #2a1518;
      color: var(--red);
      font-weight: 600;
    }}
    .run-header {{
      margin-bottom: 1rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--border);
    }}
    .run-header code {{ font-size: 0.8rem; color: var(--cyan); }}
  </style>
</head>
<body>
  <header>
    <h1>AgentAutopsy</h1>
    <p>When your agent fails, this tells you exactly why.</p>
  </header>
  <div class="layout">
    <aside class="runs">
      <h2>Runs</h2>
      <div id="run-list"></div>
    </aside>
    <main class="detail" id="detail">
      <div class="empty">Select a run to view its event timeline.</div>
    </main>
  </div>
  <script>
    const runs = {runs_json};
    const runsData = {data_json};
    const runList = document.getElementById("run-list");
    const detail = document.getElementById("detail");

    function escapeHtml(text) {{
      const div = document.createElement("div");
      div.textContent = String(text);
      return div.innerHTML;
    }}

    function renderRun(runId) {{
      const data = runsData[runId] || {{ items: [], root_cause: null }};
      const events = data.items || [];
      const run = runs.find(r => r.id === runId);
      let html = '<div class="run-header"><h2>Run timeline</h2><p><code>' + escapeHtml(runId) + '</code></p>';
      if (run) {{
        html += '<p>Status: <strong>' + escapeHtml(run.status || "") + '</strong> · Started: ' + escapeHtml(run.start_time || "") + '</p>';
      }}
      html += '</div><ul class="timeline">';
      events.forEach((ev, idx) => {{
        const cls = (ev.type || "unknown").replace(/[^a-z0-9_]/g, "_");
        html += '<li class="event ' + cls + '"><div class="type">[' + escapeHtml(ev.type) + ']</div>';
        if (ev.detail) {{
          html += '<div class="summary">' + escapeHtml(ev.detail) + '</div>';
        }}
        html += '<div class="ts">#' + (idx + 1) + ' · ' + escapeHtml(ev.timestamp || "") + '</div></li>';
      }});
      html += '</ul>';
      if (data.root_cause) {{
        html += '<div class="root-cause">Root Cause: ' + escapeHtml(data.root_cause) + '</div>';
      }}
      detail.innerHTML = html;
    }}

    if (runs.length === 0) {{
      runList.innerHTML = '<div class="empty" style="padding:1rem">No runs found.</div>';
      detail.innerHTML = '<div class="empty">No data in agentautopsy.db yet.</div>';
    }} else {{
      runs.forEach((run, index) => {{
        const btn = document.createElement("button");
        btn.className = "run-item" + (index === 0 ? " active" : "");
        btn.type = "button";
        btn.innerHTML = '<div class="run-id">' + escapeHtml(run.id) + '</div><div class="run-meta">' +
          escapeHtml(run.status || "") + ' · ' + escapeHtml(run.start_time || "") + '</div>';
        btn.addEventListener("click", () => {{
          document.querySelectorAll(".run-item").forEach(el => el.classList.remove("active"));
          btn.classList.add("active");
          renderRun(run.id);
        }});
        runList.appendChild(btn);
      }});
      renderRun(runs[0].id);
    }}
  </script>
</body>
</html>
"""


def start_ui() -> Path:
    """Build a self-contained HTML report and open it in the browser."""
    db = get_db()
    runs, runs_data = _load_data(db)
    html = _build_html(runs, runs_data)
    output_path = Path.cwd() / "agentautopsy_report.html"
    output_path.write_text(html, encoding="utf-8")
    webbrowser.open(output_path.resolve().as_uri())
    return output_path
