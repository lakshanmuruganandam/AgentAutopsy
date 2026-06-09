# Contributing to AgentAutopsy

Thank you for your interest in contributing. AgentAutopsy is a post-mortem debugger for AI agents, and community help makes it better for everyone.

## Ways to contribute

- Report bugs with reproducible steps and trace output
- Suggest features or improvements in GitHub issues
- Fix bugs or add features via pull requests
- Improve documentation, examples, and tests
- Share feedback from real agent debugging workflows

## Getting started

### Prerequisites

- Python 3.8+
- Git
- An `ANTHROPIC_API_KEY` (optional, for AI analysis features during development)

### Setup

```bash
git clone https://github.com/Abhisekhpatel/AgentAutopsy.git
cd AgentAutopsy
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -e .
pip install flake8 mypy bandit
```

### Run tests and checks

Match what CI runs before opening a PR:

```bash
flake8 src/agentautopsy/ --count --select=E9,F63,F7,F82 --show-source --statistics
mypy src/agentautopsy/ --ignore-missing-imports
bandit -c pyproject.toml -r src/agentautopsy/ -ll
python -m unittest discover tests -v
```

## Pull request guidelines

1. **Fork and branch** — Create a feature branch from `master` (e.g. `fix/http-error-replay`).
2. **Keep changes focused** — One logical change per PR when possible.
3. **Add or update tests** — Bug fixes should include a test; new behavior should be covered where practical.
4. **Match existing style** — Follow patterns in `src/agentautopsy/` (typing, naming, minimal scope).
5. **Do not commit secrets** — Never commit API keys, `.env` files, `agentautopsy.db`, or generated HTML reports.
6. **Do not commit build artifacts** — Do not add `dist/`, `build/`, or `*.egg-info/` directories.
7. **Update docs** — Update `README.md` if user-facing behavior changes.

## Code structure

| Path | Purpose |
|------|---------|
| `src/agentautopsy/` | Main package |
| `tests/` | Unit tests |
| `agentautopsy-action/` | GitHub Action for CI failure analysis |
| `.github/workflows/` | CI configuration |

## Reporting bugs

Include:

- Python version
- AgentAutopsy version (`pip show agentautopsy`)
- Framework used (OpenAI SDK, LangChain, LangGraph, CrewAI, etc.)
- Steps to reproduce
- Expected vs. actual behavior
- Relevant CLI output or `agentautopsy replay <run_id>` output

## Feature requests

Open an issue describing:

- The problem you are trying to solve
- Your proposed approach (if any)
- Why it fits AgentAutopsy’s scope as a local, zero-config agent debugger

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](https://opensource.org/licenses/MIT).

## Questions

Open a [GitHub issue](https://github.com/Abhisekhpatel/AgentAutopsy/issues) for questions or discussion before large changes.
