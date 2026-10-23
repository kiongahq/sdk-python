# Contributing to sdk-python

Thank you for helping improve Kionga. Read [ARCHITECTURE.md](ARCHITECTURE.md) for
how the package is organized before you make a larger change.

## Development setup

Prerequisites:

- Python 3.11 or later (`requires-python = ">=3.11"`; CI uses 3.11)
- Git
- Docker, only for the pgvector memory test
- Go, any recent stable release, only to run the gitleaks secret scan locally

```bash
git clone git@github.com:kiongahq/sdk-python.git
cd sdk-python
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

The `dev` extra installs the `agents` and `huggingface` extras plus pytest,
pytest-asyncio and ruff.

## Run the checks

CI calls the shared Python workflow
([`kiongahq/.github/.github/workflows/python.yml`](https://github.com/kiongahq/.github/blob/main/.github/workflows/python.yml))
with `pip install -e ".[dev]"`. Run the same steps locally:

```bash
ruff check .
python -m pytest -q
```

Then the security gates, with the versions CI pins:

```bash
pip install --upgrade pip setuptools "pip-audit==2.10.1"
pip-audit --skip-editable                                    # known vulnerabilities in installed packages
pip install "bandit==1.9.4"
bandit -q -r . -x ./tests,./.venv,./node_modules -ll         # medium and higher static findings
go run github.com/zricethezav/gitleaks/v8@v8.30.1 git --no-banner --redact .   # secrets in the full history
```

The unit tests need no network: client tests use `httpx.MockTransport`, and agent
tests use the deterministic `mock` LLM backend. CI also runs the agent memory round trip
against a real pgvector database in a separate job. To run it locally:

```bash
docker run -d --rm --name sdk-pgvector -p 5432:5432 \
  -e POSTGRES_USER=mlaiops -e POSTGRES_PASSWORD=mlaiops-ci -e POSTGRES_DB=mlaiops \
  pgvector/pgvector:pg16
TEST_MEMORY_DSN=postgresql://mlaiops:mlaiops-ci@localhost:5432/mlaiops \
  python -m pytest -q tests/test_agents.py -k pgvector
docker stop sdk-pgvector
```

## Build and release

```bash
pip install build && python -m build      # wheel and sdist in dist/
```

CI builds the package on every push and pull request and uploads `dist/` as a
workflow artifact. To release, set `version` in `pyproject.toml`, merge, then push a
matching tag (`vX.Y.Z`). CI creates the GitHub release and attaches the wheel and
sdist.

After a release, bump the pinned SDK version where other repositories install it:
`SDK_VERSION` and `requirements-dev.txt` in
[agent-runtime](https://github.com/kiongahq/agent-runtime), and `SDK_VERSION` and
`requirements-dev.txt` in [workspace](https://github.com/kiongahq/workspace).

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/), as the history
does: `type(scope): summary` in the imperative mood, for example
`fix(sdk): normalize empty gateway collections` or
`feat(agents): support Agno, NOOA, and extensible framework adapters`. Common types
are `feat`, `fix`, `docs`, `test`, `ci` and `chore`.

## Pull requests

- Keep one logical change per pull request, with tests for new behavior.
- CI must pass. Do not skip hooks or checks.
- A new client method must match an existing gateway route. If the route is new,
  link the [platform](https://github.com/kiongahq/platform) pull request that adds it.
- Keep heavy imports out of the package root: new agent-only symbols go through the
  lazy export table in `mlaiops_sdk/__init__.py`.

## Reviewed scanner findings

When bandit flags code that you have reviewed and found safe, annotate the line.
Put the reason in a comment on the line before it, and end the flagged line with
`# nosec <rule>` and nothing after it (bandit reads any text after `nosec` as rule
IDs):

```python
# KIONGA_URL is the control plane the user configured.
with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310
```

If a test needs a fake credential that gitleaks reports, add the finding's
fingerprint (`commit:file:rule:line`, printed by gitleaks) to a `.gitleaksignore`
file at the repository root, with a comment that says why. A fingerprint pins one
exact finding, so any new secret still fails the scan.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENCE).
