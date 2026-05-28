---
paths:
  - pyproject.toml
  - uv.lock
  - .python-version
  - package.json
  - pnpm-lock.yaml
---

# Dependencies

This project is Python first. The Python rules below are authoritative. A short Node appendix covers the admin web app where it differs.

## One source of truth

`pyproject.toml` is the only place dependencies are declared. No `requirements.txt`, no `setup.cfg`, no `setup.py`. The lockfile records the resolved graph and is committed.

## Package manager

The project standardizes on `uv` (Astral) as it migrates off the legacy setuptools install path. Rationale: it is the fastest Python resolver and installer in active development, it owns the lockfile, and it works the same on Windows, macOS, and Linux. Until the migration completes, `pip install -e ".[dev]"` still works because the project keeps `[project.optional-dependencies]` populated alongside the modern path.

```bash
uv sync                            # install per lockfile
uv add httpx                       # add runtime dep
uv add --group dev pytest-cov      # add to PEP 735 dev group
uv lock --upgrade-package httpx    # bump one dep
uv tree                            # inspect transitive graph
```

CI installs with `uv sync --frozen` (or `pip install --no-deps -e ".[dev]"` against the resolved lockfile). A "lockfile out of date" failure in CI is a real signal, not a nuisance.

## Dependency groups (PEP 735) versus optional extras

The two `[...]`-style dependency declarations have different purposes. Use the right one.

| Declaration | Installs into | Purpose |
|---|---|---|
| `[project.dependencies]` | Always | Runtime deps for the package |
| `[project.optional-dependencies]` | When a user runs `pip install pkg[edlink]` | End-user installable extras (e.g. shipping a connector under a feature flag) |
| `[dependency-groups]` (PEP 735) | Only via `pip install --group dev` or `uv sync --group dev` | Tool-only deps (lint, test, docs) that never ship to consumers |

The POC currently keeps `[project.optional-dependencies].dev` because the existing setuptools install path uses it. When the POC moves to `uv` proper, dev tools migrate to `[dependency-groups].dev` and `.test` per PEP 735 (it is the long-term shape).

```toml
[project]
name = "edlink-rostering"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
  "fastapi>=0.115,<0.117",
  "pydantic>=2.8,<3",
  "pydantic-settings>=2.5,<3",
  "sqlalchemy[asyncio]>=2.0,<3",
  "alembic>=1.13,<2",
  "psycopg[binary,pool]>=3.2,<4",
  "httpx>=0.27,<0.28",
  "tenacity>=9,<10",
  "structlog>=24,<26",
  "pyjwt[crypto]>=2.9,<3",
  "click>=8.1,<9",
  "python-dateutil>=2.9,<3",
  "uvicorn[standard]>=0.32,<0.33",
]

[project.optional-dependencies]
dev = [
  "pytest>=8,<9",
  "pytest-asyncio>=0.24,<0.25",
  "pytest-cov>=5,<7",
  "mypy>=1.13,<2",
  "ruff>=0.7,<0.8",
  "pre-commit>=4,<5",
  "pip-audit>=2.7,<3",
]
```

## Version pinning strategy

Lower bound at the version that ships the API you depend on. Upper bound at the next major (or next leading non-zero segment for pre-1.0 packages, since semver convention places the breaking-change axis there). The lockfile records the exact resolved version for reproducibility.

| Shape | Example | Use when |
|---|---|---|
| `>=X.Y,<X+1` | `>=2.8,<3` | Stable, semver-respecting deps |
| `>=0.Y,<0.Y+1` | `>=0.115,<0.117` | Pre-1.0 deps where the leading non-zero segment is the breaking-change axis (FastAPI, uvicorn, httpx) |
| `>=X.Y` no upper | Avoid | A surprise major upgrade in CI is not the place to find out |
| `==X.Y.Z` | Avoid | Blocks security patches without payoff; the lockfile gives reproducibility |

Pre-release versions (`a`, `b`, `rc`, `dev`) never appear in production dependency declarations.

## Mandatory tooling

Every Python project in this repo has:

- **`ruff`** for lint and format. Replaces black, isort, flake8, pyupgrade, bandit (subset). One tool, one config block, one cache.
- **`mypy --strict`** for type checking. Plugins are added only when the underlying library requires one. SQLAlchemy 2.0 does not (its native PEP 484 `Mapped[T]` typing replaces the legacy plugin).
- **`pre-commit`** for hook orchestration. The hooks run `ruff check --fix`, `ruff-format`, `mypy`, and a regex check for em-dashes and AI-tell phrases per `copy-style.md`.
- **`pytest-cov`** in the test group. Coverage is reported in CI; the threshold is a guideline, not a gate.
- **`pip-audit`** in CI. Critical and high CVEs block merge. Moderate findings are tracked.

Boilerplate to drop in `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py313"
extend-exclude = ["alembic/versions"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM", "RUF", "ASYNC", "S", "PT"]
ignore = ["E501", "S101", "B008"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S", "PT011"]
"alembic/versions/**" = ["E", "F", "I", "UP", "B"]

[tool.ruff.lint.isort]
known-first-party = ["edlink_rostering"]

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.13"
strict = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "--cov=edlink_rostering --cov-report=term-missing"
```

## Adding a dep

1. `uv add <name>` (runtime) or `uv add --group dev <name>` (tool). If still on legacy setuptools install, edit `pyproject.toml` directly and run `pip install -e ".[dev]"`.
2. Run `bash scripts/lint.sh`, `bash scripts/typecheck.sh`, `bash scripts/test.sh`. All exit 0.
3. Run `uv run pip-audit` (or `pip-audit`) to confirm no known CVEs.
4. Check the license is acceptable (see below).
5. If the dep is in the stack-anchor list, update `docs/STACK_REFERENCES.md`.

## License policy

| License | Production runtime | Dev tool |
|---|---|---|
| MIT, BSD, Apache 2.0, ISC, MPL 2.0, PSF | Yes | Yes |
| LGPL | Case by case (dynamic linking only) | Yes |
| GPL family | No | Yes |
| AGPL | No | No |
| Custom commercial | Legal review | Legal review |

Check with `pip-licenses` or `uv pip list --format json | jq`.

## Native dependencies and wheels

Python deps with native components (psycopg, cryptography, numpy, pyarrow) require wheels. Prefer the `[binary]` extra (e.g. `psycopg[binary,pool]`) over source builds. Docker base images are pinned to versions with manylinux-compatible wheels available.

When a wheel is not available for the target platform, document the build toolchain explicitly in the Dockerfile and the local-dev setup. Do not let "works on my machine" slide on this.

## Reproducibility

The reproducible install is the product of:

1. **Python version** pinned in `requires-python` in `pyproject.toml` and in `.python-version` for `uv python install`.
2. **Lockfile** committed, regenerated only when deps change.
3. **Resolver version** pinned in CI (e.g. `astral-sh/setup-uv@v3` with `version: "0.5.4"`).
4. **Base image** pinned by SHA-256 digest, not by floating tag.

Drift between local and CI is a bug category, not a workflow nuance.

## Renovate or Dependabot

Automated PRs are welcome. Auto-merge is not. A human reviews every dep update, even patches, against the changelog and the test results.

## Dep hygiene

Before adding a dep, ask:

1. Does the standard library or a current dep already cover this?
2. Is the package actively maintained (release in the last six months, sane open-issue rate)?
3. Is the transitive footprint small? `uv tree` (or `pipdeptree`) shows the graph.
4. Will it survive the next minor Python release? Test against the dev image first.

Tiny single-purpose packages are a supply-chain attack surface. Inline the snippet or pick a fatter, audited dep.

## Private packages

If the application publishes an internal canonical-model package, the registry is private (Azure Artifacts, GitHub Packages). No internal package is ever published to public PyPI.

## Submodules and forks

In order: contribute upstream, maintain a public fork with a written policy, vendor the snippet with attribution. Pinning a git submodule or a private fork is a last resort and gets an ADR.

## Cross-references

- `.claude/rules/scripts.md`, dev-loop commands and their IntelliJ run configs
- `.claude/rules/security.md`, security audit in CI
- `docs/STACK_REFERENCES.md`, version anchor for the major stack items

---

## Node appendix

The admin web app uses `pnpm`. `package.json` is the source of truth. `pnpm-lock.yaml` is committed. `engines.node` pins the Node major. ESLint and Prettier are the lint and format tools (TypeScript-native, mature). The rest of the principles above (lockfile-first, no auto-merge, license policy, no AGPL) apply identically.

```json
{
  "name": "@edlink-rostering/web",
  "type": "module",
  "engines": { "node": ">=22" },
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "test": "vitest",
    "test:e2e": "playwright test",
    "lint": "eslint src --max-warnings=0",
    "fmt": "prettier --write src"
  }
}
```
