# django-spec-tasks

Guidance for working in this repository.

## What this package is

A spec invoked as a background task: a durable run record, and a worker that re-checks through the
kernel's `dispatch`. A project names a `TaskBinding`, calls `enqueue`, and reads a `SpecRun` row.

```python
run_id = enqueue(orders, "refund_order", {"order": "7"}, actor=request.user)
```

`enqueue` runs the kernel's prelude while the caller is on the stack, writes the row, and on commit asks
a backend to run that id. The worker claims the row, resolves the principal, calls `dispatch`, and
presents the result onto the row. **The queue decides where the code runs; the row decides whether it
ran.** A backend may lose a message without consequence, which is what lets one protocol hold Django
Tasks and Celery.

Three positions shape everything else:

- **The worker always re-checks.** There is no way to carry a grant across a process, because a
  permission check cannot say what it granted. A run authorized at enqueue and refused at execution has
  a name, `AUTHORIZATION_LAPSED`.
- **The kernel is read through its root and nothing else.** Every kernel name this package uses is in
  `django_service_specs.__all__`, and every import of one goes through `from django_service_specs
  import ...`. `tests/test_import_ratchet.py` holds it.
- **No spec declaration is restated.** `idempotent` and `atomic` are read from the registered spec at
  execution, never copied onto the row, so a spec whose declaration changed answers with what it
  declares now.

## Commands

| Command | Does |
| --- | --- |
| `make init` | Sync all dependency groups and install the pre-commit hooks |
| `make test` | pytest with the 100% line+branch coverage gate |
| `make lint` | `ruff check` plus `ty check` |
| `make format-check` | `ruff format --check --diff` (CI runs this; `make lint` does not) |
| `make docs-build` | `mkdocs build --strict` |
| `make release-bump VERSION=X.Y.Z` | Rewrite the version and promote the changelog section |

## Structural rules

Non-negotiable. They keep the package navigable, and they are the kernel's.

1. **One exported class or function per file.** The file is named after the exported symbol in
   `snake_case`.
2. **Private helpers used only in one file** stay in that file with a `_name` prefix.
3. **Non-exported helpers shared across files** go in that package's `utils.py`.
4. **Top-level imports only.** Lazy or function-local imports are forbidden unless the import targets a
   declared optional dependency gated behind an opt-in. Document the reason inline. A circular import is
   never the reason (rule 9).
5. **Full type annotations on every function and method signature.** `Any` is allowed only at the Django
   boundary where the type genuinely is `Any`.
6. **`__init__.py` is the only re-export point.** Only the root and `models/` re-export; a concern
   subpackage's `__init__.py` is a docstring. Internal modules import from leaf paths, never from a
   package's `__init__`.
7. **The package root is a table of contents, not a drawer.** It holds `__init__.py`, `__init__.pyi`,
   `version.py`, `apps.py`, `checks.py` and `admin.py`. Everything else lives in a subpackage named for a
   **concern**. `types/` is the one standing subpackage, for value types.
   `tests/test_package_layout.py` holds it.
8. **`types/` imports nothing else in the package.**
9. **No `if TYPE_CHECKING:` guard.** A guard hides an import cycle from the interpreter while the type
   checker still sees it. The ban is ruff's `TID251`. Break a cycle by structure.

## Constraints that look like tidy-ups

Each of these reads as an oversight and is not.

- **The root `__init__.py` serves some names lazily.** Django imports this package while it is still
  loading apps, before a model may be imported, and every module that names `SpecRun` imports it at its
  own top. So a name whose module reaches `models/` at any depth goes in `_LAZY_EXPORTS`, with its type
  in `__init__.pyi`; a plain import of one there makes the package unimportable with
  `AppRegistryNotReady`. `types/` and `binding/` reach no model, so they are imported eagerly.
- **`binding/` names no model.** That is what keeps `TaskBinding` importable before the app registry is
  ready, so the seams that take a run type it `Any`.
- **`SpecRun` routes to the default alias, and a system check refuses anything else.** The kernel's
  `atomic` opens on the default alias, and the worker's guarantee needs the row in that transaction.
- **The check names no model at import.** It reads the model from the app registry, because `apps.py`
  imports it before the registry is ready.
- **`apps.py` imports `backends/celery_backend.py` when `celery` is installed.** A Celery worker runs a
  message by looking its name up in its own registry, so the task has to be registered by importing the
  module, in the worker, at startup. A task built inside `enqueue` passes every eager test and is
  unregistered in every real worker.
- **The state table is whole from the first release, and every column lands in the first migration.** A
  state or a column added later is missing from every run before it.

## Tests

`tests/` mirrors the source tree. `pytest-asyncio` runs in auto mode. The coverage gate is **100% line
and branch**.

Never `# pragma: no cover`. If a branch cannot be reached, that is a signal to restructure the code, not
to exempt it.

- **No DRF in any environment the suite runs in.**
- **SQLite by default, Postgres on request.** `SPEC_TASKS_TEST_DATABASE=postgres` reads the `PG*`
  variables, and any other value is refused. A test that only holds on one database says so with a
  `skipif` naming why, and the `postgres` job prints each skip.
- **Bindings register globally, at construction.** An autouse fixture in `tests/conftest.py` restores the
  registry after each test, so a test may build one freely and a module-level one survives.
  `TaskBinding.clear_registry()` is the one documented way to empty it.
- **Where several checks can answer one input, size the test so the one it names is the one that
  answers**, and assert which message came back.
- **A multi-condition guard is one branch arc.** Name, beside the guard, the test that fails without each
  condition.
- **A test double's shape is checked against the real producer.** No fake `DispatchResult`.

## Type checking

`ty`, scoped to `django_spec_tasks` via `[tool.ty.environment]`. The package ships `py.typed`, so
consumers get the annotations.

Never a mypy-style `# type: ignore` in the package - a pre-commit hook rejects it.

## Comment budget

`#` comments and docstrings both count, in the package and in the tests:

- A class: at most three lines (its docstring plus comments in its body outside its methods).
- A function or method: at most one line (docstring plus comments inside it, or directly
  above it).
- A module: nothing before the first import (no module docstring, no header comment); a
  package `__init__.py` with nothing to export is empty.
- Exempt: directives (`# noqa`, `# ty:`, `# vX.Y.Z` pins), migrations, and a docstring
  the code or a test reads through `__doc__`, which is data.

A docstring mkdocstrings renders into the API reference is not exempt.
Condense rather than move: a reason that does not fit is cut, not relocated to prose
elsewhere. A body left empty by a removed docstring takes `...`. The workbench's
`bin/comment-budget.py` lists the violations.

## Linting and formatting

`ruff` is the source of truth for both. Use `...` rather than `pass` for empty bodies.

`make lint` does **not** run `ruff format --check`, and CI does. Run `uv run ruff format --check` before
pushing.

## Imports inside the package

Absolute and fully qualified, never relative - `ban-relative-imports = "all"` is configured and enforced.
isort order is stdlib, third party, first party.

`from __future__ import annotations` goes at the top of every annotated file; `required-imports` enforces
it, and re-export `__init__.py` files and migrations are exempt.

The package imports Django, the standard library and `django_service_specs`. `celery` appears only in
`backends/celery_backend.py`, and `django.tasks` and `django_tasks` only in
`backends/django_tasks_backend.py`. Never `rest_framework` nor another transport.

## Compatibility floor

| | Minimum | Tested against |
| --- | --- | --- |
| Python | 3.10 | 3.10 through 3.14 |
| Django | 4.2 | 4.2, 5.0, 5.1, 5.2, 6.0, 6.1 |
| django-service-specs | 0.29.0 | the newest release, and the floor in the `floor` job |
| celery (extra) | 5.6 | the newest release, and the floor in the `floor` job |
| django-tasks (extra) | 0.12.0 | the newest release, and the floor in the `floor` job |
| PostgreSQL | whichever the Django in use supports | 17, in the `postgres` job |

The Python and Django floors are the kernel's, and cannot sit below them.

## CI and pre-commit

Ten jobs in `tests.yml`: `lint`, `docs`, `floor` (resolves every declared dependency at the bottom of
its window and runs the suite there), `floor-alone` (each install shape with no dev group, at its floors,
on a Django below 6), `extras` (each install shape at the newest versions it resolves, the backport's on
Python 3.11 so that it is what loads), `postgres` (the suite where a NUL and half a surrogate pair are
refused), `test` (the Python x Django matrix), `coverage-badge`, `secrets`, and the `tests-passed` gate
that branch protection points at. `floor-alone` and `extras` run `scripts/queue_shape.py`, which sends a
spec from `enqueue` to its row through each queue the install holds. `upstream-drift.yml` resolves the
newest versions `pyproject.toml` allows on a schedule, which is how a kernel release that breaks this
package is found.

Pre-commit runs gitleaks, the standard hygiene hooks, ruff, ty, four convention guards (no local
filesystem paths, no internal plan-step labels, no mypy-style type-ignore, no emoji or marker glyphs in
any committed file) and `check-changelog`, which refuses a changelog a merge or rebase has silently
rearranged.

## Releasing

```
make release-bump VERSION=X.Y.Z   # rewrites version.py, promotes the changelog
# edit CHANGELOG.md to fill in the new section, review the diff
# open a PR, get it reviewed, merge to main
```

Releases are **main-triggered**. The release job on `main` short-circuits to a no-op when a `vX.Y.Z` tag
for the version in source already exists on origin, so an ordinary merge costs nothing. That guard is why
the tag list starts at `v0.0.0`, a version never published: a repository with no tags reads its version
as unreleased on the first push to `main` and runs a real release attempt. `0.1.0` is the first version on
PyPI.

Publishing goes through a PyPI Trusted Publisher that names this repo, the workflow `release.yml` and the
environment `pypi`. It lives on PyPI rather than in a checkout, so renaming that workflow file or that
environment breaks the next release and nothing here fails first.
