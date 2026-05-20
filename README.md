# AgentAutopsy

When your agent fails, this tells you exactly why.

## Install
```bash
pip install agentautopsy
```

## Usage
```python
import agentautopsy
agentautopsy.watch()

# your agent code here — nothing else changes
```

On failure, AgentAutopsy automatically:
1. Intercepts every LLM + HTTP call
2. Detects the exact failure node
3. Prunes the trace to what matters
4. Analyzes root cause and outputs a fix
5. Verifies the fix with deterministic replay
6. Caches the fix — same failure answered instantly next time

## Output
```
[AgentAutopsy] failure detected: TimeoutError: request timed out after 30s
FAILURE NODE: llm_call to gpt-4
ROOT CAUSE: Request exceeded 30s threshold due to no timeout set
FIX: Add timeout=60 and exponential backoff retry logic
[AgentAutopsy] fix verified ✓
```

## How it works
- Zero config — one import, one function call
- Patches openai + anthropic at import time
- Stores full trace in SQLite — one file, zero infrastructure
- Cassette replay makes verification 100% deterministic
- Fix cache answers repeated failures in milliseconds

## Requirements
- Python 3.11+
- ANTHROPIC_API_KEY environment variable

## License
Apache 2.0
