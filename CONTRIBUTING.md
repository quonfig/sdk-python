# Contributing to `quonfig` (Python SDK)

Thanks for your interest in contributing! This guide covers the basics of getting set up,
running tests, and sending pull requests.

## Reporting Issues

Before opening a new issue, please check the
[issue list](https://github.com/quonfig/sdk-python/issues) to see if it has already been
reported or fixed.

When filing a bug, include:

- The version of `quonfig` you're running (`pip show quonfig`)
- Python version (`python --version`) — we test against Python 3.11
- A minimal reproduction (a snippet, or ideally a failing test) and the actual vs. expected
  behavior

For security issues, please follow [SECURITY.md](./SECURITY.md) instead of filing a public
issue.

## Local Development

The SDK is a plain Poetry project. Clone, install, and you're ready:

```sh
git clone https://github.com/quonfig/sdk-python.git
cd sdk-python
poetry install
```

### Lint

```sh
poetry run ruff check .
```

### Typecheck

```sh
poetry run mypy quonfig
```

### Test

```sh
poetry run pytest
```

Some tests exercise the integration suite that lives in the sibling
[`integration-test-data`](https://github.com/quonfig/integration-test-data) repo. The CI
workflow checks out both repos side-by-side; for local runs, only the unit-level tests are
required.

## Sending Pull Requests

- Open a draft PR early if you'd like feedback before finishing the implementation.
- Add a test for any behavior change. Bug fixes should include a regression test that fails
  without the fix.
- We follow semver — any breaking change must be called out in the PR description.
- Keep commits focused. If a PR touches both a feature and an unrelated cleanup, split them.
- If you change `pyproject.toml`, regenerate `poetry.lock` (`poetry lock`) and stage it in the
  same commit so CI's frozen install does not fail.

The CI pipeline (`.github/workflows/test.yaml`) runs `ruff check`, `mypy`, and `pytest` on
every push and pull request — please make sure all three pass locally before requesting
review.

## Releases

Releases are automated by `.github/workflows/release.yaml`. Releasing is currently
maintainer-only; if your change is ready to ship, leave a note on the PR and a maintainer
will cut the release.

Thanks again for contributing!
