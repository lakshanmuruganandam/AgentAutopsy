# AgentAutopsy

Deterministic replay debugger for AI agents.

Replay failures exactly as they happened.
Find the divergence node.
Get replay-verified fixes.

![Demo](./assets/demo.png)

![Python](https://img.shields.io/badge/python-3.11+-blue) ![License](https://img.shields.io/badge/license-Apache%202.0-green) ![Zero Config](https://img.shields.io/badge/config-zero-brightgreen) ![SQLite](https://img.shields.io/badge/storage-local%20SQLite-lightgrey)

## Install
```bash
pip install git+https://github.com/Abhisekhpatel/AgentAutopsy.git
```

## Setup

Windows: `set ANTHROPIC_API_KEY=your-key-here`

Mac/Linux: `export ANTHROPIC_API_KEY=your-key-here`

Get your free key at console.anthropic.com

## Quick start

Create test_agent.py and paste this:

```python
import agentautopsy
agentautopsy.watch()
from openai import OpenAI
client = OpenAI(api_key="fake-key")
response = client.chat.completions.create(model="gpt-4", messages=[{"role": "user", "content": "hello"}])
```

Run: `python test_agent.py`

## Usage

```python
import agentautopsy
agentautopsy.watch()
# your existing agent code here — nothing else changes
```

AgentAutopsy automatically intercepts every LLM call, detects failures, finds root cause, outputs a verified fix, and caches it for next time.

## Works with

OpenAI, Anthropic, LangChain, any framework using openai or anthropic

## Requirements

Python 3.11+, ANTHROPIC_API_KEY

## License

Apache 2.0
