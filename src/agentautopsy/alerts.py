"""Slack alerting for AgentAutopsy failures."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def send_slack_alert(
    webhook_url: str,
    run_id: str,
    error: str,
    root_cause: str,
    fix: str,
) -> None:
    """Send a formatted Slack alert when an agent run fails."""
    payload = {
        "text": "❌ Agent Run Failed",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "❌ Agent Run Failed", "emoji": True},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Run ID:*\n`{run_id}`"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Error:*\n{error}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Root Cause:*\n{root_cause or 'Unknown'}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Fix:*\n{fix or 'No fix suggested'}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Link to run:*\nRun `agentautopsy ui` to inspect this run.",
                },
            },
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status >= 400:
            raise urllib.error.HTTPError(
                webhook_url,
                response.status,
                f"Slack webhook returned {response.status}",
                response.headers,
                None,
            )
