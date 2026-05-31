"""Self-contained web UI for AgentAutopsy traces."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path
from typing import Any

from agentautopsy.analyzer import detect_divergence
from agentautopsy.db import get_db


def _parse_payload(raw: Any) -> dict[str, Any]:
    try:
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _row_int(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    return int(value)


def _row_float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    return float(value)


def _format_cassette(cassette: bytes | None) -> str | None:
    if not cassette:
        return None
    try:
        text = cassette.decode("utf-8")
    except UnicodeDecodeError:
        return repr(cassette)
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2, default=str)
    except (json.JSONDecodeError, TypeError):
        return text


def _metrics_suffix(
    latency_ms: int | None,
    token_input: int | None,
    token_output: int | None,
    cost_usd: float | None,
) -> str:
    parts: list[str] = []
    if latency_ms is not None:
        parts.append(f"{latency_ms}ms")
    if token_input is not None or token_output is not None:
        parts.append(f"tokens {token_input or 0}/{token_output or 0}")
    if cost_usd is not None:
        parts.append(f"${cost_usd:.6f}")
    return " · ".join(parts)


def _event_detail(
    ev_type: str,
    payload: dict[str, Any],
    cassette_size: int,
    latency_ms: int | None = None,
    token_input: int | None = None,
    token_output: int | None = None,
    cost_usd: float | None = None,
) -> str:
    if ev_type == "llm_call":
        base = f"model: {payload.get('model')}"
    elif ev_type == "llm_response":
        base = f"cassette: {cassette_size} bytes"
    elif ev_type == "http_request":
        base = f"{payload.get('method')} {payload.get('url')}"
    elif ev_type == "http_response":
        base = f"status: {payload.get('status_code')}"
    elif ev_type == "error":
        base = f"{payload.get('error_type')}: {payload.get('message')}"
    elif ev_type == "tool_call":
        base = f"{payload.get('tool')}: {payload.get('input')}"
    elif ev_type == "tool_result":
        base = f"output: {payload.get('output')}"
    elif payload:
        base = json.dumps(payload, default=str)
    else:
        base = ""

    metrics = _metrics_suffix(latency_ms, token_input, token_output, cost_usd)
    if metrics:
        return f"{base} · {metrics}" if base else metrics
    return base


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
            runs_data[run["id"]] = {
                "items": [],
                "root_cause": None,
                "summary": {
                    "total_token_input": 0,
                    "total_token_output": 0,
                    "total_tokens": 0,
                    "total_cost_usd": 0.0,
                    "total_latency_ms": 0,
                },
            }
            run["has_error"] = False
        return runs, runs_data

    for run in runs:
        run_id = run["id"]
        items: list[dict[str, Any]] = []
        raw_events: list[dict[str, Any]] = []

        pending_call: dict[str, Any] | None = None
        total_token_input = 0
        total_token_output = 0
        total_cost = 0.0
        total_latency = 0

        for row in db["events"].rows_where(
            where="run_id = ?",
            where_args=[run_id],
            order_by="timestamp",
        ):
            payload = _parse_payload(row.get("payload"))
            cassette = row.get("cassette")
            cassette_size = len(cassette) if cassette is not None else 0
            latency_ms = _row_int(row, "latency_ms")
            token_input = _row_int(row, "token_input")
            token_output = _row_int(row, "token_output")
            cost_usd = _row_float(row, "cost_usd")

            if token_input is not None:
                total_token_input += token_input
            if token_output is not None:
                total_token_output += token_output
            if cost_usd is not None:
                total_cost += cost_usd
            if latency_ms is not None:
                total_latency += latency_ms

            raw_events.append({"type": row["type"], "payload": payload})
            ev_type = row["type"]
            item = {
                "id": row["id"],
                "type": ev_type,
                "timestamp": row.get("timestamp", ""),
                "payload": payload,
                "cassette_text": _format_cassette(cassette),
                "detail": _event_detail(
                    ev_type,
                    payload,
                    cassette_size,
                    latency_ms,
                    token_input,
                    token_output,
                    cost_usd,
                ),
                "latency_ms": latency_ms,
                "token_input": token_input,
                "token_output": token_output,
                "cost_usd": cost_usd,
            }
            items.append(item)

            if ev_type == "llm_call":
                pending_call = item
            elif ev_type == "llm_response" and pending_call is not None:
                pending_call["latency_ms"] = latency_ms
                pending_call["token_input"] = token_input
                pending_call["token_output"] = token_output
                pending_call["cost_usd"] = cost_usd
                pending_call["detail"] = _event_detail(
                    "llm_call",
                    pending_call["payload"],
                    0,
                    latency_ms,
                    token_input,
                    token_output,
                    cost_usd,
                )
                pending_call["paired_response_text"] = _format_cassette(cassette)
                pending_call = None
            elif ev_type != "llm_response":
                pending_call = None

        runs_data[run_id] = {
            "items": items,
            "root_cause": _root_cause(raw_events),
            "summary": {
                "total_token_input": total_token_input,
                "total_token_output": total_token_output,
                "total_tokens": total_token_input + total_token_output,
                "total_cost_usd": round(total_cost, 6),
                "total_latency_ms": total_latency,
            },
        }

    for run in runs:
        run_data = runs_data.get(run["id"], {})
        run["has_error"] = run_data.get("root_cause") is not None
        try:
            run_data["divergences"] = detect_divergence(run["id"])
        except Exception:
            run_data["divergences"] = []
        runs_data[run["id"]] = run_data

    return runs, runs_data


def _build_html(
    runs: list[dict[str, Any]],
    runs_data: dict[str, dict[str, Any]],
) -> str:
    runs_json = json.dumps(runs)
    data_json = json.dumps(runs_data)
    api_key_json = json.dumps("AGENTAUTOPSY_API_KEY_PLACEHOLDER")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AgentAutopsy</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --bg: #09090b;
      --bg-elevated: #0f0f12;
      --surface: #141419;
      --surface-hover: #1a1a22;
      --border: rgba(255, 255, 255, 0.08);
      --border-hover: rgba(255, 255, 255, 0.14);
      --text: #fafafa;
      --muted: #a1a1aa;
      --dim: #71717a;
      --cyan: #22d3ee;
      --yellow: #facc15;
      --green: #4ade80;
      --red: #f87171;
      --purple: #a78bfa;
      --gradient: linear-gradient(135deg, #22d3ee 0%, #3b82f6 100%);
      --shadow: 0 1px 2px rgba(0,0,0,0.4), 0 8px 24px rgba(0,0,0,0.35);
      --glow: 0 0 24px rgba(34, 211, 238, 0.35), 0 0 48px rgba(99, 102, 241, 0.18);
    }}
    @keyframes replay-pulse {{
      0%, 100% {{
        box-shadow: 0 0 18px rgba(34, 211, 238, 0.45), 0 0 36px rgba(59, 130, 246, 0.25);
      }}
      50% {{
        box-shadow: 0 0 32px rgba(34, 211, 238, 0.65), 0 0 64px rgba(59, 130, 246, 0.4);
      }}
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      min-height: 100%;
      font-family: "Inter", ui-sans-serif, system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      -webkit-font-smoothing: antialiased;
    }}
    body {{
      background:
        radial-gradient(ellipse 80% 50% at 50% -20%, rgba(99, 102, 241, 0.15), transparent),
        radial-gradient(ellipse 60% 40% at 100% 0%, rgba(34, 211, 238, 0.08), transparent),
        var(--bg);
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 1rem 1.75rem;
      border-bottom: 1px solid rgba(34, 211, 238, 0.12);
      background:
        linear-gradient(180deg, rgba(34, 211, 238, 0.14) 0%, rgba(34, 211, 238, 0.04) 45%, rgba(15, 15, 18, 0.92) 100%),
        rgba(15, 15, 18, 0.85);
      box-shadow: 0 1px 0 rgba(34, 211, 238, 0.1), 0 8px 32px rgba(34, 211, 238, 0.06);
      backdrop-filter: blur(12px);
      position: sticky;
      top: 0;
      z-index: 10;
    }}
    .brand {{
      display: flex;
      flex-direction: column;
      gap: 0.15rem;
    }}
    .logo {{
      margin: 0;
      font-size: 1.35rem;
      font-weight: 700;
      letter-spacing: -0.03em;
      background: var(--gradient);
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
    }}
    .tagline {{
      margin: 0;
      font-size: 0.82rem;
      color: var(--muted);
      font-weight: 400;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 300px 1fr;
      min-height: calc(100vh - 68px);
    }}
    .sidebar {{
      border-right: 1px solid var(--border);
      background: rgba(15, 15, 18, 0.55);
      overflow-y: auto;
    }}
    .sidebar-head {{
      padding: 1.25rem 1.25rem 0.75rem;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--dim);
    }}
    #run-list {{
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
      padding: 0 0.65rem 0.85rem;
    }}
    .run-item {{
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      width: 100%;
      text-align: left;
      border: none;
      background: transparent;
      color: var(--text);
      padding: 0.85rem 1rem;
      border-radius: 999px;
      cursor: pointer;
      transition: background 0.2s ease, box-shadow 0.2s ease;
    }}
    .run-item:hover {{ background: var(--surface-hover); }}
    .run-item.active {{
      background: rgba(34, 211, 238, 0.1);
      box-shadow:
        0 0 0 1px rgba(34, 211, 238, 0.28),
        0 0 18px rgba(34, 211, 238, 0.22),
        inset 0 1px 0 rgba(34, 211, 238, 0.08);
    }}
    .status-dot {{
      width: 9px;
      height: 9px;
      border-radius: 50%;
      margin-top: 0.35rem;
      flex-shrink: 0;
      box-shadow: 0 0 8px currentColor;
    }}
    .dot-success {{ background: var(--green); color: var(--green); }}
    .dot-failed {{ background: var(--red); color: var(--red); }}
    .dot-running {{ background: var(--yellow); color: var(--yellow); }}
    .run-copy {{ min-width: 0; flex: 1; }}
    .run-id {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.72rem;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .run-meta {{
      margin-top: 0.3rem;
      font-size: 0.8rem;
      color: var(--dim);
    }}
    .detail {{
      padding: 1.75rem 2rem 2.5rem;
      overflow-y: auto;
      background:
        radial-gradient(ellipse 75% 55% at 50% 35%, rgba(30, 58, 138, 0.16) 0%, rgba(15, 23, 42, 0.06) 45%, transparent 72%),
        transparent;
    }}
    .run-body {{
      display: flex;
      align-items: flex-start;
      gap: 1.25rem;
    }}
    .run-main {{
      flex: 1;
      min-width: 0;
    }}
    .replay-panel {{
      width: 340px;
      flex-shrink: 0;
      display: none;
      position: sticky;
      top: 84px;
      max-height: calc(100vh - 100px);
      overflow-y: auto;
      padding: 1rem 1.1rem;
      border-radius: 14px;
      border: 1px solid rgba(34, 211, 238, 0.22);
      background:
        radial-gradient(ellipse 90% 60% at 50% 0%, rgba(34, 211, 238, 0.08) 0%, transparent 70%),
        var(--surface);
      box-shadow: var(--shadow), 0 0 24px rgba(34, 211, 238, 0.12);
    }}
    .replay-panel.visible {{
      display: block;
    }}
    .replay-panel-head {{
      margin: 0 0 1rem;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--cyan);
    }}
    .replay-panel-section {{
      margin-top: 0.85rem;
    }}
    .replay-panel-section:first-of-type {{
      margin-top: 0;
    }}
    .replay-panel-label {{
      font-size: 0.68rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--dim);
      margin-bottom: 0.45rem;
    }}
    .replay-panel-stat {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.82rem;
      color: var(--cyan);
      padding: 0.65rem 0.75rem;
      border-radius: 8px;
      background: rgba(34, 211, 238, 0.08);
      border: 1px solid rgba(34, 211, 238, 0.18);
    }}
    .replay-panel .prompt-block {{
      margin: 0;
      max-height: 220px;
      overflow-y: auto;
    }}
    .empty {{
      color: var(--muted);
      padding: 3rem 1rem;
      text-align: center;
      font-size: 0.95rem;
    }}
    .run-header {{
      margin-bottom: 1.5rem;
    }}
    .run-header h2 {{
      margin: 0 0 0.35rem;
      font-size: 1.15rem;
      font-weight: 600;
      letter-spacing: -0.02em;
    }}
    .run-header code {{
      font-size: 0.78rem;
      color: var(--cyan);
      background: rgba(34, 211, 238, 0.08);
      border: 1px solid rgba(34, 211, 238, 0.18);
      padding: 0.2rem 0.45rem;
      border-radius: 6px;
    }}
    .run-sub {{
      margin: 0.65rem 0 0;
      font-size: 0.85rem;
      color: var(--muted);
    }}
    .stat-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 1rem;
      margin-bottom: 1.25rem;
    }}
    .stat-card {{
      position: relative;
      overflow: hidden;
      background:
        radial-gradient(ellipse 90% 70% at 50% -10%, rgba(255, 255, 255, 0.06) 0%, transparent 65%),
        var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 1.1rem 1.25rem;
      box-shadow: var(--shadow);
      transition: border-color 0.18s ease, transform 0.18s ease;
    }}
    .stat-card::before {{
      content: "";
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 2px;
      border-radius: 14px 14px 0 0;
    }}
    .stat-grid .stat-card:nth-child(1)::before {{
      background: var(--cyan);
      box-shadow: 0 0 10px rgba(34, 211, 238, 0.7), 0 0 22px rgba(34, 211, 238, 0.35);
    }}
    .stat-grid .stat-card:nth-child(2)::before {{
      background: var(--green);
      box-shadow: 0 0 10px rgba(74, 222, 128, 0.7), 0 0 22px rgba(74, 222, 128, 0.35);
    }}
    .stat-grid .stat-card:nth-child(3)::before {{
      background: var(--purple);
      box-shadow: 0 0 10px rgba(167, 139, 250, 0.7), 0 0 22px rgba(167, 139, 250, 0.35);
    }}
    .stat-card:hover {{
      border-color: var(--border-hover);
      transform: translateY(-1px);
    }}
    .stat-label {{
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--dim);
      margin-bottom: 0.55rem;
    }}
    .stat-value {{
      font-size: 1.65rem;
      font-weight: 700;
      letter-spacing: -0.03em;
      line-height: 1.1;
    }}
    .stat-sub {{
      margin-top: 0.35rem;
      font-size: 0.78rem;
      color: var(--muted);
    }}
    .replay-btn {{
      margin-bottom: 0;
      padding: 0.72rem 1.25rem;
      border: none;
      border-radius: 10px;
      font-family: inherit;
      font-weight: 600;
      font-size: 0.88rem;
      color: #fff;
      cursor: pointer;
      background: linear-gradient(135deg, #22d3ee 0%, #3b82f6 50%, #6366f1 100%);
      box-shadow: var(--glow);
      transition: transform 0.15s ease, box-shadow 0.2s ease, filter 0.2s ease;
    }}
    .replay-btn:hover {{
      transform: translateY(-1px);
      filter: brightness(1.06);
      animation: replay-pulse 1.6s ease-in-out infinite;
    }}
    .run-actions {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.65rem;
      margin-bottom: 1.25rem;
    }}
    .divergence-section {{
      margin-bottom: 1.25rem;
    }}
    .divergence-head {{
      margin: 0 0 0.75rem;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--dim);
    }}
    .divergence-ok {{
      padding: 0.85rem 1rem;
      border-radius: 12px;
      border: 1px solid rgba(74, 222, 128, 0.28);
      background: rgba(74, 222, 128, 0.08);
      color: var(--green);
      font-size: 0.88rem;
      font-weight: 600;
    }}
    .divergence-cards {{
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
    }}
    .divergence-card {{
      padding: 0.9rem 1rem;
      border-radius: 12px;
      border: 1px solid rgba(250, 204, 21, 0.28);
      background: rgba(250, 204, 21, 0.08);
      box-shadow: var(--shadow);
    }}
    .divergence-card-title {{
      font-size: 0.86rem;
      font-weight: 600;
      color: var(--yellow);
      margin-bottom: 0.55rem;
    }}
    .divergence-card-row {{
      display: grid;
      grid-template-columns: 88px 1fr;
      gap: 0.35rem 0.75rem;
      font-size: 0.8rem;
      line-height: 1.45;
    }}
    .divergence-card-label {{
      color: var(--dim);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-size: 0.68rem;
    }}
    .divergence-card-value {{
      color: var(--muted);
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.76rem;
    }}
    .share-btn {{
      padding: 0.72rem 1.15rem;
      border: 1px solid var(--border);
      border-radius: 10px;
      font-family: inherit;
      font-weight: 600;
      font-size: 0.88rem;
      color: var(--text);
      cursor: pointer;
      background: var(--surface);
      transition: transform 0.15s ease, border-color 0.15s ease, background 0.15s ease;
    }}
    .share-btn:hover {{
      transform: translateY(-1px);
      border-color: var(--border-hover);
      background: var(--surface-hover);
    }}
    .share-btn.copied {{
      border-color: rgba(74, 222, 128, 0.35);
      color: var(--green);
      background: rgba(74, 222, 128, 0.08);
    }}
    .replay-counter {{
      margin: 0 0 1.25rem;
      min-height: 1.2rem;
      font-size: 0.82rem;
      font-weight: 600;
      color: var(--cyan);
      letter-spacing: 0.01em;
    }}
    .timeline {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
    }}
    .event {{
      padding: 1rem 1.1rem;
      border-radius: 12px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-left: 4px solid var(--dim);
      cursor: pointer;
      box-shadow: var(--shadow);
      transition:
        background 0.25s cubic-bezier(0.4, 0, 0.2, 1),
        border-color 0.25s cubic-bezier(0.4, 0, 0.2, 1),
        transform 0.25s cubic-bezier(0.4, 0, 0.2, 1),
        box-shadow 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    }}
    .event:hover {{
      background: var(--surface-hover);
      border-color: var(--border-hover);
      transform: translateY(-2px);
    }}
    .event.llm_call:hover {{
      box-shadow: var(--shadow), 0 0 0 1px rgba(34, 211, 238, 0.2), 0 0 20px rgba(34, 211, 238, 0.18);
      border-color: rgba(34, 211, 238, 0.25);
    }}
    .event.http_request:hover {{
      box-shadow: var(--shadow), 0 0 0 1px rgba(250, 204, 21, 0.2), 0 0 20px rgba(250, 204, 21, 0.15);
      border-color: rgba(250, 204, 21, 0.25);
    }}
    .event.http_response:hover {{
      box-shadow: var(--shadow), 0 0 0 1px rgba(74, 222, 128, 0.2), 0 0 20px rgba(74, 222, 128, 0.15);
      border-color: rgba(74, 222, 128, 0.25);
    }}
    .event.error:hover {{
      box-shadow: var(--shadow), 0 0 0 1px rgba(248, 113, 113, 0.25), 0 0 20px rgba(248, 113, 113, 0.18);
      border-color: rgba(248, 113, 113, 0.3);
    }}
    .event.llm_response:hover {{
      box-shadow: var(--shadow), 0 0 0 1px rgba(167, 139, 250, 0.2), 0 0 20px rgba(167, 139, 250, 0.18);
      border-color: rgba(167, 139, 250, 0.25);
    }}
    .event.expanded {{
      background: var(--surface-hover);
      border-color: var(--border-hover);
    }}
    .event .type {{
      font-weight: 600;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.82rem;
      letter-spacing: 0.01em;
    }}
    .event .summary {{
      margin-top: 0.45rem;
      color: var(--muted);
      font-size: 0.88rem;
      line-height: 1.45;
      word-break: break-word;
    }}
    .event .ts {{
      margin-top: 0.35rem;
      font-size: 0.72rem;
      color: var(--dim);
    }}
    .event.llm_call {{ border-left-color: var(--cyan); }}
    .event.llm_call .type {{ color: var(--cyan); }}
    .event.http_request {{ border-left-color: var(--yellow); }}
    .event.http_request .type {{ color: var(--yellow); }}
    .event.http_response {{ border-left-color: var(--green); }}
    .event.http_response .type {{ color: var(--green); }}
    .event.error {{ border-left-color: var(--red); }}
    .event.error .type {{ color: var(--red); }}
    .event.llm_response {{ border-left-color: var(--purple); }}
    .event.llm_response .type {{ color: var(--purple); }}
    .root-cause {{
      margin-top: 1.5rem;
      padding: 1rem 1.25rem;
      border: 1px solid rgba(248, 113, 113, 0.35);
      border-radius: 12px;
      background: rgba(248, 113, 113, 0.08);
      color: #fecaca;
      font-weight: 600;
      font-size: 0.92rem;
    }}
    .event-body {{
      max-height: 0;
      opacity: 0;
      overflow: hidden;
      transition: max-height 0.35s ease, opacity 0.25s ease, margin-top 0.25s ease;
      margin-top: 0;
    }}
    .event-body.open {{
      max-height: 4000px;
      opacity: 1;
      margin-top: 0.85rem;
    }}
    .inspect-label {{
      font-size: 0.68rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--dim);
      margin: 0.85rem 0 0.4rem;
    }}
    .inspect-label:first-child {{ margin-top: 0; }}
    .event-body pre {{
      margin: 0;
      padding: 0.9rem 1rem;
      background: #0c0c0f;
      border: 1px solid var(--border);
      border-radius: 10px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.76rem;
      line-height: 1.5;
      color: #d4d4d8;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-x: auto;
    }}
    .inspect-metrics {{
      font-size: 0.82rem;
      color: var(--muted);
      margin-top: 0.65rem;
      line-height: 1.6;
    }}
    .collapse-hint {{
      margin-top: 0.85rem;
      font-size: 0.78rem;
      color: var(--cyan);
      font-weight: 500;
    }}
    .event.replay-past {{
      opacity: 0.38;
      filter: saturate(0.6);
      transform: none !important;
    }}
    .event.replay-current {{
      border-left-width: 4px !important;
      border-left-color: var(--cyan) !important;
      background: rgba(34, 211, 238, 0.14) !important;
      opacity: 1 !important;
      box-shadow:
        0 0 0 1px rgba(34, 211, 238, 0.4),
        0 0 28px rgba(34, 211, 238, 0.45),
        0 0 56px rgba(34, 211, 238, 0.2) !important;
      transform: none !important;
    }}
    .event.replay-error-highlight {{
      border-left-color: var(--red) !important;
      background: rgba(248, 113, 113, 0.14) !important;
      opacity: 1 !important;
      box-shadow:
        0 0 0 1px rgba(248, 113, 113, 0.35),
        0 0 28px rgba(248, 113, 113, 0.35) !important;
    }}
    .prompt-viewer {{
      margin-top: 0.25rem;
    }}
    .prompt-section {{
      margin-top: 0.85rem;
    }}
    .prompt-section:first-child {{
      margin-top: 0;
    }}
    .prompt-section-head {{
      font-size: 0.68rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--dim);
      margin-bottom: 0.45rem;
    }}
    .prompt-block {{
      margin: 0;
      padding: 0.9rem 1rem;
      background: #0c0c0f;
      border: 1px solid var(--border);
      border-radius: 10px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.78rem;
      line-height: 1.55;
      color: #d4d4d8;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-x: auto;
    }}
    .prompt-block + .prompt-block {{
      margin-top: 0.5rem;
    }}
    .prompt-block.system {{
      border-left: 3px solid var(--purple);
    }}
    .prompt-block.user {{
      border-left: 3px solid var(--cyan);
    }}
    .prompt-block.assistant {{
      border-left: 3px solid var(--green);
    }}
    .prompt-empty {{
      font-size: 0.78rem;
      color: var(--dim);
      font-style: italic;
    }}
    .hl-key {{ color: #67e8f9; }}
    .hl-string {{ color: #86efac; }}
    .hl-number {{ color: #fcd34d; }}
    .hl-keyword {{ color: #f472b6; }}
    .hl-punct {{ color: #a1a1aa; }}
    .chat-section {{
      margin-top: 2rem;
      padding-top: 1.5rem;
      border-top: 1px solid var(--border);
    }}
    .chat-head {{
      margin: 0 0 1rem;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--dim);
    }}
    .chat-messages {{
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      margin-bottom: 1rem;
      max-height: 420px;
      overflow-y: auto;
      padding-right: 0.25rem;
    }}
    .chat-msg {{
      max-width: 88%;
      padding: 0.85rem 1rem;
      border-radius: 14px;
      font-size: 0.88rem;
      line-height: 1.55;
      word-break: break-word;
      white-space: pre-wrap;
    }}
    .chat-msg.user {{
      align-self: flex-end;
      background: rgba(34, 211, 238, 0.12);
      border: 1px solid rgba(34, 211, 238, 0.22);
      color: #e0f2fe;
    }}
    .chat-msg.assistant {{
      align-self: flex-start;
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--text);
      box-shadow: var(--shadow);
    }}
    .chat-msg.loading {{
      color: var(--muted);
      font-style: italic;
    }}
    .chat-compose {{
      display: flex;
      gap: 0.65rem;
      align-items: center;
    }}
    .chat-input {{
      flex: 1;
      padding: 0.72rem 1rem;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
      font-family: inherit;
      font-size: 0.88rem;
      outline: none;
      transition: border-color 0.18s ease, box-shadow 0.18s ease;
    }}
    .chat-input::placeholder {{ color: var(--dim); }}
    .chat-input:focus {{
      border-color: rgba(34, 211, 238, 0.35);
      box-shadow: 0 0 0 3px rgba(34, 211, 238, 0.1);
    }}
    .chat-ask-btn {{
      padding: 0.72rem 1.15rem;
      border: none;
      border-radius: 10px;
      font-family: inherit;
      font-weight: 600;
      font-size: 0.86rem;
      color: #fff;
      cursor: pointer;
      background: linear-gradient(135deg, #22d3ee 0%, #3b82f6 100%);
      box-shadow: 0 0 16px rgba(34, 211, 238, 0.25);
      transition: transform 0.15s ease, filter 0.15s ease, opacity 0.15s ease;
      white-space: nowrap;
    }}
    .chat-ask-btn:hover:not(:disabled) {{
      transform: translateY(-1px);
      filter: brightness(1.06);
    }}
    .chat-ask-btn:disabled {{
      opacity: 0.55;
      cursor: not-allowed;
    }}
    .chat-error {{
      margin-top: 0.75rem;
      padding: 0.75rem 1rem;
      border-radius: 10px;
      border: 1px solid rgba(248, 113, 113, 0.35);
      background: rgba(248, 113, 113, 0.08);
      color: #fecaca;
      font-size: 0.84rem;
    }}
    .chat-hint {{
      margin-top: 0.65rem;
      font-size: 0.78rem;
      color: var(--dim);
    }}
    @media (max-width: 900px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .sidebar {{ border-right: none; border-bottom: 1px solid var(--border); max-height: 220px; }}
      .stat-grid {{ grid-template-columns: 1fr; }}
      .run-body {{ flex-direction: column; }}
      .replay-panel {{
        width: 100%;
        position: static;
        max-height: none;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <h1 class="logo">AgentAutopsy</h1>
      <p class="tagline">When your agent fails, this tells you exactly why.</p>
    </div>
  </header>
  <div class="layout">
    <aside class="sidebar">
      <div class="sidebar-head">Runs</div>
      <div id="run-list"></div>
    </aside>
    <main class="detail" id="detail">
      <div class="empty">Select a run to view its event timeline.</div>
    </main>
  </div>
  <script>
    const runs = {runs_json};
    const runsData = {data_json};
    const anthropicApiKey = {api_key_json};
    const anthropicApiKeyConfigured =
      anthropicApiKey && anthropicApiKey !== "AGENTAUTOPSY_API_KEY_PLACEHOLDER";
    const CHAT_SYSTEM_PROMPT =
      "You are an AI debugging assistant. You have access to the full trace of an AI agent run. Answer questions about why it failed, what happened at each step, and how to fix it.";
    const runList = document.getElementById("run-list");
    const detail = document.getElementById("detail");
    let replayTimer = null;
    const chatHistory = {{}};
    let chatLoadingRunId = null;

    function escapeHtml(text) {{
      const div = document.createElement("div");
      div.textContent = String(text);
      return div.innerHTML;
    }}

    function formatPayload(payload) {{
      if (!payload || Object.keys(payload).length === 0) {{
        return "{{}}";
      }}
      return JSON.stringify(payload, null, 2);
    }}

    function formatMetric(value, suffix) {{
      if (value === null || value === undefined) {{
        return "—";
      }}
      return String(value) + (suffix || "");
    }}

    function extractMessageContent(content) {{
      if (content === null || content === undefined) {{
        return "";
      }}
      if (typeof content === "string") {{
        return content;
      }}
      if (Array.isArray(content)) {{
        return content.map((part) => {{
          if (typeof part === "string") {{
            return part;
          }}
          if (part && typeof part === "object") {{
            if (typeof part.text === "string") {{
              return part.text;
            }}
            if (typeof part.content === "string") {{
              return part.content;
            }}
            return JSON.stringify(part, null, 2);
          }}
          return String(part);
        }}).join("\\n");
      }}
      if (typeof content === "object") {{
        return JSON.stringify(content, null, 2);
      }}
      return String(content);
    }}

    function extractPromptSections(payload) {{
      const system = [];
      const user = [];
      const assistant = [];
      const data = payload || {{}};
      if (data.system) {{
        system.push(extractMessageContent(data.system));
      }}
      const messages = Array.isArray(data.messages) ? data.messages : [];
      messages.forEach((msg) => {{
        const role = String(msg.role || "").toLowerCase();
        const text = extractMessageContent(msg.content);
        if (!text) {{
          return;
        }}
        if (role === "system") {{
          system.push(text);
        }} else if (role === "user") {{
          user.push(text);
        }} else if (role === "assistant") {{
          assistant.push(text);
        }}
      }});
      return {{ system, user, assistant }};
    }}

    function findPairedResponse(events, callIndex) {{
      for (let i = callIndex + 1; i < events.length; i += 1) {{
        const ev = events[i];
        if (ev.type === "llm_response") {{
          return ev;
        }}
        if (ev.type === "llm_call" || ev.type === "error") {{
          break;
        }}
      }}
      return null;
    }}

    function extractAssistantResponse(ev) {{
      const cassetteText = (ev && (ev.paired_response_text || ev.cassette_text)) || "";
      if (!cassetteText || cassetteText === "(none)") {{
        return "";
      }}
      try {{
        const data = JSON.parse(cassetteText);
        if (data.choices && data.choices[0]) {{
          const message = data.choices[0].message || {{}};
          const content = extractMessageContent(message.content);
          if (content) {{
            return content;
          }}
          return JSON.stringify(data.choices[0], null, 2);
        }}
        if (Array.isArray(data.content)) {{
          const text = data.content
            .filter((block) => block && block.type === "text")
            .map((block) => block.text || "")
            .join("\\n")
            .trim();
          if (text) {{
            return text;
          }}
        }}
        if (typeof data.output_text === "string") {{
          return data.output_text;
        }}
        return JSON.stringify(data, null, 2);
      }} catch (error) {{
        return cassetteText;
      }}
    }}

    function syntaxHighlight(text) {{
      const escaped = escapeHtml(text);
      return escaped
        .replace(/"([^"\\\\]|\\\\.)*"/g, '<span class="hl-string">$&</span>')
        .replace(/\\b(true|false|null)\\b/g, '<span class="hl-keyword">$1</span>')
        .replace(/\\b-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b/g, '<span class="hl-number">$1</span>')
        .replace(/([{{}}[\\],:])/g, '<span class="hl-punct">$1</span>');
    }}

    function renderPromptBlock(text, role) {{
      if (!text) {{
        return "";
      }}
      const looksJson = text.trim().startsWith("[") || text.trim().startsWith("{{");
      const body = looksJson ? syntaxHighlight(text) : escapeHtml(text);
      return '<pre class="prompt-block ' + role + '">' + body + "</pre>";
    }}

    function buildPromptViewer(ev, events, idx) {{
      const sections = extractPromptSections(ev.payload || {{}});
      let responseText = extractAssistantResponse(ev);
      if (!responseText) {{
        const paired = findPairedResponse(events, idx);
        if (paired) {{
          responseText = extractAssistantResponse(paired);
        }}
      }}
      let html = '<div class="prompt-viewer">';
      html += '<div class="inspect-label">Prompt Viewer</div>';
      html += '<div class="prompt-section"><div class="prompt-section-head">System Prompt</div>';
      if (sections.system.length) {{
        sections.system.forEach((text) => {{
          html += renderPromptBlock(text, "system");
        }});
      }} else {{
        html += '<div class="prompt-empty">No system prompt</div>';
      }}
      html += "</div>";
      html += '<div class="prompt-section"><div class="prompt-section-head">User Messages</div>';
      if (sections.user.length) {{
        sections.user.forEach((text) => {{
          html += renderPromptBlock(text, "user");
        }});
      }} else {{
        html += '<div class="prompt-empty">No user messages</div>';
      }}
      html += "</div>";
      html += '<div class="prompt-section"><div class="prompt-section-head">Assistant Messages (request context)</div>';
      if (sections.assistant.length) {{
        sections.assistant.forEach((text) => {{
          html += renderPromptBlock(text, "assistant");
        }});
      }} else {{
        html += '<div class="prompt-empty">No prior assistant messages in request</div>';
      }}
      html += "</div>";
      html += '<div class="prompt-section"><div class="prompt-section-head">Assistant Response</div>';
      if (responseText) {{
        html += renderPromptBlock(responseText, "assistant");
      }} else {{
        html += '<div class="prompt-empty">No response recorded</div>';
      }}
      html += "</div></div>";
      return html;
    }}

    function statusDotClass(run) {{
      if ((run.status || "").toLowerCase() === "running") {{
        return "dot-running";
      }}
      if (run.has_error) {{
        return "dot-failed";
      }}
      return "dot-success";
    }}

    function toggleExpand(id) {{
      const body = document.getElementById("expand-" + id);
      const event = document.getElementById("event-" + id);
      if (!body || !event) {{
        return;
      }}
      body.classList.toggle("open");
      event.classList.toggle("expanded");
    }}

    function formatObjectText(value) {{
      if (value === null || value === undefined) {{
        return "";
      }}
      if (typeof value === "string") {{
        return value;
      }}
      try {{
        return JSON.stringify(value, null, 2);
      }} catch (error) {{
        return String(value);
      }}
    }}

    function findPairedToolResult(events, callIndex) {{
      for (let i = callIndex + 1; i < events.length; i += 1) {{
        const ev = events[i];
        if (ev.type === "tool_result") {{
          return ev;
        }}
        if (ev.type === "tool_call" || ev.type === "error") {{
          break;
        }}
      }}
      return null;
    }}

    function estimateContextSize(sections) {{
      let chars = 0;
      sections.system.forEach((text) => {{ chars += text.length; }});
      sections.user.forEach((text) => {{ chars += text.length; }});
      sections.assistant.forEach((text) => {{ chars += text.length; }});
      return {{
        chars: chars,
        tokens: Math.max(1, Math.ceil(chars / 4)),
      }};
    }}

    function renderReplayPanelSection(label, content, role) {{
      const text = formatObjectText(content);
      if (!text) {{
        return "";
      }}
      const roleClass = role ? " " + role : "";
      return (
        '<div class="replay-panel-section">' +
        '<div class="replay-panel-label">' + escapeHtml(label) + "</div>" +
        '<pre class="prompt-block' + roleClass + '">' + escapeHtml(text) + "</pre>" +
        "</div>"
      );
    }}

    function hideReplayPanel() {{
      const panel = document.getElementById("replay-panel");
      if (!panel) {{
        return;
      }}
      panel.classList.remove("visible");
      panel.innerHTML = "";
    }}

    function showReplayPanel(contentHtml) {{
      const panel = document.getElementById("replay-panel");
      if (!panel) {{
        return;
      }}
      panel.innerHTML = contentHtml;
      panel.classList.add("visible");
    }}

    function buildReplayPanelHtml(runId, eventIndex) {{
      const events = (runsData[runId] || {{ items: [] }}).items || [];
      const ev = events[eventIndex];
      if (!ev) {{
        return (
          '<div class="replay-panel-head">Step Details</div>' +
          '<div class="prompt-empty">No event data</div>'
        );
      }}

      let html =
        '<div class="replay-panel-head">Step ' +
        (eventIndex + 1) +
        " · " +
        escapeHtml(ev.type) +
        "</div>";

      if (ev.type === "llm_call") {{
        const sections = extractPromptSections(ev.payload || {{}});
        const size = estimateContextSize(sections);
        html += renderReplayPanelSection("System prompt", sections.system.join("\\n\\n"), "system");
        html += renderReplayPanelSection("User messages", sections.user.join("\\n\\n"), "user");
        html += renderReplayPanelSection(
          "Previous assistant messages",
          sections.assistant.join("\\n\\n"),
          "assistant"
        );
        html +=
          '<div class="replay-panel-section">' +
          '<div class="replay-panel-label">Full context window size</div>' +
          '<div class="replay-panel-stat">' +
          size.chars.toLocaleString() +
          " chars · ~" +
          size.tokens.toLocaleString() +
          " tokens (est.)</div></div>";
      }} else if (ev.type === "tool_call") {{
        const payload = ev.payload || {{}};
        const paired = findPairedToolResult(events, eventIndex);
        const output = paired ? (paired.payload || {{}}).output : null;
        html += renderReplayPanelSection("Tool name", payload.tool || "unknown", "");
        html += renderReplayPanelSection("Tool input", payload.input, "user");
        html += renderReplayPanelSection(
          "Tool output",
          output != null ? output : "No output recorded",
          "assistant"
        );
      }} else if (ev.type === "error") {{
        const payload = ev.payload || {{}};
        const stack = payload.stack || payload.stacktrace || payload.traceback || "";
        html += renderReplayPanelSection("Error type", payload.error_type || "Error", "");
        html += renderReplayPanelSection("Full error message", payload.message || "", "");
        html += renderReplayPanelSection(
          "Stack trace",
          stack || "Not available",
          stack ? "system" : ""
        );
      }} else {{
        html += renderReplayPanelSection("Event", ev.detail || ev.type, "");
      }}

      return html;
    }}

    function updateReplayPanel(runId, eventIndex) {{
      showReplayPanel(buildReplayPanelHtml(runId, eventIndex));
    }}

    function buildDivergenceSection(divergences) {{
      const items = divergences || [];
      let html = '<div class="divergence-section">';
      html += '<h3 class="divergence-head">Divergence</h3>';
      if (!items.length) {{
        html += '<div class="divergence-ok">No divergence detected</div>';
      }} else {{
        html += '<div class="divergence-cards">';
        items.forEach((item) => {{
          html += '<div class="divergence-card">';
          html += '<div class="divergence-card-title">' + escapeHtml(item.what_changed || "Divergence detected") + "</div>";
          html += '<div class="divergence-card-row">';
          html += '<div class="divergence-card-label">Previous</div>';
          html += '<div class="divergence-card-value">' + escapeHtml(item.previous || "—") + "</div>";
          html += '<div class="divergence-card-label">Current</div>';
          html += '<div class="divergence-card-value">' + escapeHtml(item.current || "—") + "</div>";
          html += "</div></div>";
        }});
        html += "</div>";
      }}
      html += "</div>";
      return html;
    }}

    async function shareRun(runId, button) {{
      const originalLabel = button.textContent;
      try {{
        await navigator.clipboard.writeText(runId);
        button.textContent = "Copied!";
        button.classList.add("copied");
        setTimeout(() => {{
          button.textContent = originalLabel;
          button.classList.remove("copied");
        }}, 1800);
      }} catch (error) {{
        button.textContent = "Copy failed";
        setTimeout(() => {{
          button.textContent = originalLabel;
        }}, 1800);
      }}
    }}

    function replayRun(runId) {{
      if (replayTimer) {{
        clearTimeout(replayTimer);
        replayTimer = null;
      }}
      hideReplayPanel();
      const timeline = document.getElementById("timeline-" + runId);
      const counter = document.getElementById("replay-counter-" + runId);
      if (!timeline) {{
        return;
      }}
      const eventNodes = Array.from(timeline.querySelectorAll(".event"));
      const total = eventNodes.length;
      if (!total) {{
        if (counter) {{
          counter.textContent = "";
        }}
        return;
      }}

      eventNodes.forEach((node) => {{
        node.classList.remove("replay-current", "replay-past", "replay-error-highlight");
        node.classList.remove("expanded");
        const body = node.querySelector(".event-body");
        if (body) {{
          body.classList.remove("open");
        }}
      }});

      let index = 0;
      function finishReplay(message) {{
        setCounter(message);
        replayTimer = null;
        setTimeout(hideReplayPanel, 1200);
      }}

      function setCounter(text) {{
        if (counter) {{
          counter.textContent = text;
        }}
      }}

      function showStep() {{
        eventNodes.forEach((node, i) => {{
          node.classList.remove("replay-current", "replay-error-highlight");
          if (i < index) {{
            node.classList.add("replay-past");
          }} else {{
            node.classList.remove("replay-past");
          }}
        }});

        const current = eventNodes[index];
        const step = index + 1;
        current.classList.remove("replay-past");
        current.classList.add("replay-current");
        current.scrollIntoView({{ behavior: "smooth", block: "center" }});
        setCounter("Replaying step " + step + " of " + total);
        updateReplayPanel(runId, index);

        if (current.classList.contains("error")) {{
          current.classList.remove("replay-current");
          current.classList.add("replay-error-highlight");
          finishReplay("Stopped at error — step " + step + " of " + total);
          return;
        }}

        index += 1;
        if (index >= total) {{
          eventNodes.forEach((node) => node.classList.remove("replay-current"));
          finishReplay("Replay complete — " + total + " steps");
          return;
        }}
        replayTimer = setTimeout(showStep, 800);
      }}

      showStep();
    }}

    function getRunEventsJson(runId) {{
      const data = runsData[runId] || {{ items: [] }};
      return JSON.stringify(data.items || [], null, 2);
    }}

    function buildApiMessages(runId) {{
      const eventsJson = getRunEventsJson(runId);
      const contextBlock = "Run events (full JSON):\\n" + eventsJson;
      const history = chatHistory[runId] || [];
      return history.map((msg) => {{
        if (msg.role === "user") {{
          return {{
            role: "user",
            content: contextBlock + "\\n\\nUser question: " + msg.content,
          }};
        }}
        return {{ role: "assistant", content: msg.content }};
      }});
    }}

    function showChatError(message) {{
      const errorEl = document.getElementById("chat-error");
      if (!errorEl) {{
        return;
      }}
      errorEl.textContent = message;
      errorEl.style.display = message ? "block" : "none";
    }}

    function renderChatMessages(runId) {{
      const container = document.getElementById("chat-messages");
      if (!container) {{
        return;
      }}
      const history = chatHistory[runId] || [];
      let html = "";
      history.forEach((msg) => {{
        html += '<div class="chat-msg ' + escapeHtml(msg.role) + '">' + escapeHtml(msg.content) + "</div>";
      }});
      if (chatLoadingRunId === runId) {{
        html += '<div class="chat-msg assistant loading">Thinking...</div>';
      }}
      container.innerHTML = html;
      container.scrollTop = container.scrollHeight;
    }}

    function renderChatUI(runId) {{
      const askBtn = document.getElementById("chat-ask-btn");
      const input = document.getElementById("chat-input");
      const hint = document.getElementById("chat-hint");
      if (!askBtn || !input) {{
        return;
      }}
      showChatError("");
      if (!anthropicApiKeyConfigured) {{
        askBtn.disabled = true;
        if (hint) {{
          hint.textContent = "Set ANTHROPIC_API_KEY and run agentautopsy ui to enable chat.";
        }}
      }} else {{
        askBtn.disabled = chatLoadingRunId === runId;
        if (hint) {{
          hint.textContent = "Chat history is kept for this browser session only.";
        }}
      }}
      askBtn.onclick = () => askAI(runId);
      input.onkeydown = (event) => {{
        if (event.key === "Enter" && !event.shiftKey) {{
          event.preventDefault();
          askAI(runId);
        }}
      }};
      renderChatMessages(runId);
    }}

    async function askAI(runId) {{
      const input = document.getElementById("chat-input");
      const askBtn = document.getElementById("chat-ask-btn");
      if (!input || !askBtn || chatLoadingRunId) {{
        return;
      }}
      const question = (input.value || "").trim();
      if (!question) {{
        return;
      }}
      if (!anthropicApiKeyConfigured) {{
        showChatError("Set ANTHROPIC_API_KEY and regenerate the report.");
        return;
      }}
      if (!chatHistory[runId]) {{
        chatHistory[runId] = [];
      }}
      chatHistory[runId].push({{ role: "user", content: question }});
      input.value = "";
      showChatError("");
      chatLoadingRunId = runId;
      askBtn.disabled = true;
      renderChatMessages(runId);
      try {{
        const response = await fetch("https://api.anthropic.com/v1/messages", {{
          method: "POST",
          headers: {{
            "Content-Type": "application/json",
            "x-api-key": anthropicApiKey,
            "anthropic-version": "2023-06-01",
            "anthropic-dangerous-direct-browser-access": "true",
          }},
          body: JSON.stringify({{
            model: "claude-haiku-4-5-20251001",
            max_tokens: 500,
            system: CHAT_SYSTEM_PROMPT,
            messages: buildApiMessages(runId),
          }}),
        }});
        const payload = await response.json();
        if (!response.ok) {{
          const errMsg = payload.error && payload.error.message
            ? payload.error.message
            : "Request failed (" + response.status + ")";
          throw new Error(errMsg);
        }}
        const answer = payload.content
          .filter((block) => block.type === "text")
          .map((block) => block.text)
          .join("\\n")
          .trim();
        if (!answer) {{
          throw new Error("Empty response from API");
        }}
        chatHistory[runId].push({{ role: "assistant", content: answer }});
      }} catch (error) {{
        chatHistory[runId].pop();
        showChatError(error.message || "Failed to reach Anthropic API");
      }} finally {{
        chatLoadingRunId = null;
        askBtn.disabled = !anthropicApiKeyConfigured;
        renderChatMessages(runId);
      }}
    }}

    function renderRun(runId) {{
      if (replayTimer) {{
        clearTimeout(replayTimer);
        replayTimer = null;
      }}
      hideReplayPanel();
      const data = runsData[runId] || {{ items: [], root_cause: null, summary: {{}} }};
      const events = data.items || [];
      const run = runs.find(r => r.id === runId);
      const summary = data.summary || {{}};
      let html = '<div class="run-body"><div class="run-main">';
      html += '<div class="run-header"><h2>Run timeline</h2><p><code>' + escapeHtml(runId) + '</code></p>';
      if (run) {{
        html += '<p class="run-sub">Status <strong>' + escapeHtml(run.status || "") + '</strong> · ' + escapeHtml(run.start_time || "") + '</p>';
      }}
      html += '</div>';
      html += '<div class="stat-grid">';
      html += '<div class="stat-card"><div class="stat-label">Tokens</div><div class="stat-value">' + escapeHtml(summary.total_tokens || 0) + '</div>';
      html += '<div class="stat-sub">in ' + escapeHtml(summary.total_token_input || 0) + ' / out ' + escapeHtml(summary.total_token_output || 0) + '</div></div>';
      html += '<div class="stat-card"><div class="stat-label">Cost</div><div class="stat-value">$' + escapeHtml(Number(summary.total_cost_usd || 0).toFixed(4)) + '</div>';
      html += '<div class="stat-sub">estimated USD</div></div>';
      html += '<div class="stat-card"><div class="stat-label">Latency</div><div class="stat-value">' + escapeHtml(summary.total_latency_ms || 0) + '<span style="font-size:0.95rem;font-weight:600;color:var(--muted)">ms</span></div>';
      html += '<div class="stat-sub">cumulative LLM time</div></div></div>';
      html += '<div class="run-actions">';
      html += '<button class="replay-btn" type="button" onclick="event.stopPropagation(); replayRun(\\'' + runId + '\\')">▶ Replay Run</button>';
      html += '<button class="share-btn" id="share-btn-' + escapeHtml(runId) + '" type="button" onclick="event.stopPropagation(); shareRun(\\'' + runId + '\\', this)">Share Run</button>';
      html += '</div>';
      html += '<div class="replay-counter" id="replay-counter-' + escapeHtml(runId) + '"></div>';
      html += buildDivergenceSection(data.divergences || []);
      html += '<ul class="timeline" id="timeline-' + escapeHtml(runId) + '">';
      events.forEach((ev, idx) => {{
        const cls = (ev.type || "unknown").replace(/[^a-z0-9_]/g, "_");
        const eventId = ev.id || (runId + "-" + idx);
        html += '<li class="event ' + cls + '" id="event-' + escapeHtml(eventId) + '" onclick="toggleExpand(\\'' + eventId.replace(/'/g, "\\\\'") + '\\')">';
        html += '<div class="type">[' + escapeHtml(ev.type) + ']</div>';
        if (ev.detail) {{
          html += '<div class="summary">' + escapeHtml(ev.detail) + '</div>';
        }}
        html += '<div class="ts">#' + (idx + 1) + ' · ' + escapeHtml(ev.timestamp || "") + '</div>';
        html += '<div class="event-body" id="expand-' + escapeHtml(eventId) + '">';
        if (ev.type === "llm_call") {{
          html += buildPromptViewer(ev, events, idx);
        }}
        html += '<div class="inspect-label">Payload</div>';
        html += '<pre>' + escapeHtml(formatPayload(ev.payload)) + '</pre>';
        html += '<div class="inspect-label">Cassette</div>';
        html += '<pre>' + escapeHtml(ev.cassette_text || "(none)") + '</pre>';
        html += '<div class="inspect-metrics">';
        html += 'latency_ms: ' + escapeHtml(formatMetric(ev.latency_ms, "ms")) + '<br>';
        html += 'token_input: ' + escapeHtml(formatMetric(ev.token_input)) + '<br>';
        html += 'token_output: ' + escapeHtml(formatMetric(ev.token_output)) + '<br>';
        html += 'cost_usd: ' + escapeHtml(ev.cost_usd === null || ev.cost_usd === undefined ? "—" : "$" + Number(ev.cost_usd).toFixed(6));
        html += '</div>';
        html += '<div class="collapse-hint">Click to collapse</div>';
        html += '</div></li>';
      }});
      html += '</ul>';
      if (data.root_cause) {{
        html += '<div class="root-cause">Root Cause: ' + escapeHtml(data.root_cause) + '</div>';
      }}
      html += '<div class="chat-section">';
      html += '<h3 class="chat-head">AI Debug Assistant</h3>';
      html += '<div class="chat-messages" id="chat-messages"></div>';
      html += '<div class="chat-compose">';
      html += '<input class="chat-input" id="chat-input" type="text" placeholder="Ask about this run..." />';
      html += '<button class="chat-ask-btn" id="chat-ask-btn" type="button">Ask AI</button>';
      html += '</div>';
      html += '<div class="chat-error" id="chat-error" style="display:none"></div>';
      html += '<div class="chat-hint" id="chat-hint"></div>';
      html += '</div>';
      html += '</div><aside class="replay-panel" id="replay-panel"></aside></div>';
      detail.innerHTML = html;
      renderChatUI(runId);
    }}

    if (runs.length === 0) {{
      runList.innerHTML = '<div class="empty">No runs found.</div>';
      detail.innerHTML = '<div class="empty">No data in agentautopsy.db yet.</div>';
    }} else {{
      runs.forEach((run, index) => {{
        const btn = document.createElement("button");
        btn.className = "run-item" + (index === 0 ? " active" : "");
        btn.type = "button";
        btn.innerHTML =
          '<span class="status-dot ' + statusDotClass(run) + '"></span>' +
          '<span class="run-copy"><div class="run-id">' + escapeHtml(run.id) + '</div>' +
          '<div class="run-meta">' + escapeHtml(run.status || "") + ' · ' + escapeHtml(run.start_time || "") + '</div></span>';
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
