# AgentAutopsy v1.6.1

**Post-mortem debugger for AI agents — install with `pip install agentautopsy`**

## Highlights

- PyPI packaging with `setuptools.build_meta` and Python 3.8+ support
- Graceful handling when `ANTHROPIC_API_KEY` is missing (analysis returns a warning instead of crashing)
- HTTP error capture for failed OpenAI/LangChain calls (`http_error` events with traceback and URL)
- Improved replay output showing requests, errors, root cause, and run status
- LangGraph and CrewAI callback handlers
- GitHub Actions integration for automatic PR comments on test failures
- README updates: features list, before/after example, install instructions

## Install

```bash
pip install agentautopsy==1.6.1
```

## Quick start

```python
import agentautopsy

agentautopsy.watch()
# your existing agent code — nothing else changes
```

## What's included

### Core debugging

- OpenAI, Anthropic, and HTTP interceptors via `agentautopsy.watch()`
- SQLite trace store (`agentautopsy.db`) with cassette recording
- Failure detection, pruning, and AI root-cause analysis
- Fix cache, replay, and terminal reporter

### CLI & UI

- `agentautopsy runs` — list runs
- `agentautopsy replay <run_id>` — inspect a failure
- `agentautopsy fix <run_id>` — apply suggested fixes
- `agentautopsy ui` — local web dashboard

### Framework integrations

- LangChain — `get_callback_handler()`
- LangGraph — `get_langgraph_handler()`
- CrewAI — `get_crewai_handler()`

### Integrations

- Slack alerts via `AGENTAUTOPSY_SLACK_WEBHOOK`
- GitHub PR creation via `agentautopsy fix <run_id> --create-pr`
- GitHub Action at `Abhisekhpatel/AgentAutopsy@v1`

## Requirements

- Python 3.8+
- `ANTHROPIC_API_KEY` (optional for tracing; required for AI analysis)

## Full changelog

### Added

- `langgraph_handler.py` and `crewai_handler.py`
- `agentautopsy-action/` GitHub Action
- `http_error` event type with exception metadata
- CI workflow (flake8, mypy, bandit, unittest)

### Changed

- Analyzer skips Anthropic calls when API key is unset
- Replay/report formatting for HTTP failures
- Package metadata: MIT license, setuptools build backend

### Fixed

- Failed HTTP calls now create error events instead of only logging requests
- Run status set to `failed` on HTTP errors
- Windows test cleanup for temp SQLite databases

## Links

- [PyPI](https://pypi.org/project/agentautopsy/1.6.1/)
- [Repository](https://github.com/Abhisekhpatel/AgentAutopsy)
- [Issues](https://github.com/Abhisekhpatel/AgentAutopsy/issues)
