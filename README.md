# AgentAutopsy

[![PyPI version](https://img.shields.io/pypi/v/agentautopsy?color=blue)](https://pypi.org/project/agentautopsy/)
[![PyPI downloads](https://img.shields.io/pypi/dm/agentautopsy?color=green)](https://pypi.org/project/agentautopsy/)
[![GitHub stars](https://img.shields.io/github/stars/Abhisekhpatel/AgentAutopsy?style=social)](https://github.com/Abhisekhpatel/AgentAutopsy)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

> When your agent fails, this tells you exactly why.

## Install

```bash
pip install agentautopsy
```

## Features

- **Interceptor** — Captures OpenAI, Anthropic, and HTTP traffic automatically with zero code changes (`agentautopsy.watch()`)
- **Web UI** — Browse runs, inspect events, and debug failures in a local dashboard (`agentautopsy ui`)
- **Auto-fix** — Generates and applies patch suggestions for failed runs (`agentautopsy fix <run_id>`)
- **GitHub PR** — Opens a pull request with the proposed fix (`agentautopsy fix <run_id> --create-pr`)
- **Slack alerts** — Posts failure notifications to your channel when a run fails
- **Prompt diffing** — Compares prompts in the current run against the previous run
- **Replay** — Step through failed runs event-by-event in the UI and CLI (`agentautopsy replay <run_id>`)
- **Multi-agent graph** — Visualizes agent handoffs and parent/child run chains (`agentautopsy agents`)

## Why this exists

Every time an AI agent fails, you get a useless stack trace.
No context. No reason. No fix.
AgentAutopsy gives you the exact failure step, root cause,
and a verified fix — automatically.

![demo](assets/demo.gif)

![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-Apache%202.0-green)
![Zero Config](https://img.shields.io/badge/config-zero-brightgreen)
![Works with](https://img.shields.io/badge/works%20with-OpenAI%20%2B%20Anthropic-orange)

## CLI

agentautopsy runs        # see all agent runs
agentautopsy replay <id> # replay any failure
agentautopsy stats       # fix cache stats

## GitHub Actions

Add AgentAutopsy to your test workflow so failed Python tests get an automatic root-cause analysis and a suggested fix posted on the pull request.

Create or update `.github/workflows/test.yml`:

```yaml
name: Tests

on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: AgentAutopsy
        uses: Abhisekhpatel/AgentAutopsy@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          test_command: pytest
```

**Inputs**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `anthropic_api_key` | yes | — | Anthropic API key for analysis |
| `github_token` | yes | — | Token with `pull-requests: write` (use `secrets.GITHUB_TOKEN`) |
| `test_command` | no | `pytest` | Shell command run before analysis |

On test failure the action runs `agentautopsy analyze` (via the bundled entrypoint), then posts **root cause** and **fix** as a PR comment. Store `ANTHROPIC_API_KEY` in repository secrets.

## Examples

```python
# Basic usage
import agentautopsy
agentautopsy.watch()

# LangChain
from agentautopsy import get_callback_handler
handler = get_callback_handler()
agent.run(input, config={"callbacks": [handler]})

# LangGraph
from agentautopsy import get_langgraph_handler
handler = get_langgraph_handler()
graph.invoke(input, config={"callbacks": [handler]})

# CrewAI
from agentautopsy import get_crewai_handler
handler = get_crewai_handler()
crew = Crew(agents=[...], callbacks=[handler])
```

```bash
# Slack alerts
export AGENTAUTOPSY_SLACK_WEBHOOK=https://hooks.slack.com/...

# Web UI
agentautopsy ui

# CLI
agentautopsy runs
agentautopsy replay <run_id>
agentautopsy stats
```

## LangGraph

```python
import agentautopsy
from agentautopsy import get_langgraph_handler

agentautopsy.watch()
handler = get_langgraph_handler()

# Pass the handler into LangGraph invoke config
result = graph.invoke(
    {"messages": [("user", "research competitors")]},
    config={"callbacks": [handler]},
)
```

The handler records node start/end, edge traversals, state updates between nodes, tool and LLM activity, and any graph errors in `agentautopsy.db`.

## CrewAI

```python
import agentautopsy
from agentautopsy import get_crewai_handler
from crewai import Crew

agentautopsy.watch()
handler = get_crewai_handler()

crew = Crew(agents=[researcher, writer], tasks=[...], callbacks=[handler])
crew.kickoff()

# Or use step_callback on Crew / Agent (supported by current CrewAI releases)
crew = Crew(agents=[...], step_callback=handler.step_callback)
```

The handler records task start/end, tool usage, agent handoffs, final crew output, and errors.

## Usage

```python
import agentautopsy
agentautopsy.watch()
# your existing agent code here — nothing else changes
```

AgentAutopsy automatically intercepts every LLM call, detects failures, finds root cause, outputs a verified fix, and caches it for next time.

## Why AgentAutopsy vs LangSmith / Helicone?

| Feature | AgentAutopsy | LangSmith | Helicone |
|---------|-------------|-----------|----------|
| Works offline | ✅ | ❌ | ❌ |
| Zero config | ✅ | ❌ | ❌ |
| Replay failed runs | ✅ | partial | ❌ |
| AI debug assistant | ✅ | ❌ | ❌ |
| Prompt diffing | ✅ | partial | ❌ |
| Divergence detection | ✅ | ❌ | ❌ |
| Free and open source | ✅ | partial | ✅ |
| No cloud required | ✅ | ❌ | ❌ |

## Setup

Windows: `set ANTHROPIC_API_KEY=your-key-here`
Mac/Linux: `export ANTHROPIC_API_KEY=your-key-here`
Get your free key at console.anthropic.com

Set `AGENTAUTOPSY_SLACK_WEBHOOK=your-webhook-url` and AgentAutopsy will automatically alert your Slack channel when any agent fails.

## Quick start

```bash
pip install agentautopsy
```

Create test_agent.py and paste this:

```python
import agentautopsy
agentautopsy.watch()
```

Run: `python test_agent.py`

## Works with

OpenAI, Anthropic, LangChain, LangGraph, CrewAI, any framework using openai or anthropic

## Requirements

Python 3.11+, ANTHROPIC_API_KEY

## License

Apache 2.0

## Roadmap

- [ ] VS Code extension
- [x] GitHub Actions integration  
- [ ] Multi-agent tracing
- [ ] Auto-fix applier
- [x] LangChain support
- [x] LangGraph support
- [x] CrewAI support
- [x] Slack alerts
- [x] Web UI
- [x] Prompt diffing
- [x] Divergence detection
