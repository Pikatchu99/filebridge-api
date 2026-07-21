# Contributing to FileBridge API

Thanks for your interest in contributing! This is a solo/portfolio project, but it follows a
proper Git workflow for practice and to keep quality gated by CI.

## Branch naming

Create a branch off `main` for every change, using one of these prefixes:

- `feature/xxx` — new functionality (e.g. `feature/file-upload-endpoint`)
- `fix/xxx` — bug fixes (e.g. `fix/serializer-validation-error`)
- `chore/xxx` — tooling, dependencies, CI, docs, and other maintenance (e.g. `chore/update-ruff-config`)

## Commit messages

This project follows [Conventional Commits](https://www.conventionalcommits.org/). Use one of
the following prefixes for your commit subject line:

- `feat:` — a new feature
- `fix:` — a bug fix
- `docs:` — documentation only changes
- `test:` — adding or correcting tests
- `refactor:` — a code change that neither fixes a bug nor adds a feature
- `chore:` — tooling, dependencies, CI, and other maintenance

Examples:

```
feat: add endpoint for listing user files
fix: correct file size validation on upload serializer
test: add coverage for expired share-link access
```

## Branch protection

`main` is protected by convention: no direct commits. All changes go through a pull request,
even though this is a solo project with no external reviewer. This keeps the history clean and
ensures CI (lint + tests) gates every merge.

## Test-driven development

Tests are expected alongside (or before) implementation. When adding or changing behavior,
write or update the relevant tests in the same PR — don't ship untested code.

## Running tests and lint locally

Run the test suite:

```bash
pytest
```

Run the linter:

```bash
ruff check .
```

Both must pass before opening a PR, and are enforced by CI on every push and pull request.
