![AgentAutopsy](assets/logo.png)

[![PyPI version](https://img.shields.io/pypi/v/agentautopsy?color=blue)](https://pypi.org/project/agentautopsy/)
[![Downloads](https://static.pepy.tech/badge/agentautopsy)](https://pepy.tech/project/agentautopsy)
[![GitHub stars](https://img.shields.io/github/stars/Abhisekhpatel/AgentAutopsy?style=social)](https://github.com/Abhisekhpatel/AgentAutopsy)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

The post-mortem debugger for AI agents. When your agent fails silently — AgentAutopsy tells you exactly why.

## Install

```bash
pip install agentautopsy
```

## Quick Start

```python
import agentautopsy
agentautopsy.watch()
# your existing agent code — nothing else changes
```

```bash
agentautopsy ui
```

## Features

### MCP Post-Mortem Tracing

Traces every MCP tool call with schema validation, failure reports, and downstream contamination detection.

### Schema Drift Detector

Compares tool schemas against SQLite baselines and alerts when fields are added, removed, renamed, or change type.

### DVR Fork and Replay

Records every step of an agent run so you can rewind, replay from any point, or fork with different input.

### Swarm Tracing (50+ agents)

Links parent and child agent runs with causality thread IDs and a live topology graph across large swarms.

### AI Chat on your trace

Ask why a run failed, which step broke, and how to fix it — directly in the Web UI against your recorded trace.

### Works with LangChain, CrewAI, AutoGen, LlamaIndex

Zero-config `watch()` for OpenAI and Anthropic; dedicated handlers for LangChain, LangGraph, and CrewAI.

## MCPAutopsy

```python
from agentautopsy import MCPAutopsy

MCPAutopsy.start("my-mcp-server")
# or: agentautopsy.watch_mcp("my-mcp-server")
```

## SchemaDriftDetector

```python
from agentautopsy import SchemaDriftDetector

SchemaDriftDetector().watch()  # also enabled automatically by agentautopsy.watch()
```

## DVRReplay

```python
from agentautopsy import DVRReplay

dvr = DVRReplay()
dvr.replay(run_id, from_step=3)
dvr.fork(run_id, at_step=3, new_input={"query": "fixed prompt"})
```

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests, and pull request guidelines.

## License

MIT — see [LICENSE](LICENSE).
