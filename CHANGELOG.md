# Changelog

## [Unreleased]

### Added
- `SpecRun`, the durable record of one run, with `RunState` and `TERMINAL_STATES`. All its columns are in the first migration, and a system check refuses a router that sends it away from the default alias.
- `TaskBackend`, the one-method protocol a queue implements.
- `TaskBinding`, the frozen object that chooses a run's registry, backend, principal resolver and bounds. Bindings register under their name, a duplicate raises naming it, and `AppConfig.ready` discovers each app's `spec_tasks` module.
- `enqueue(binding, spec_name, params, *, actor)`: refuses what cannot cross (a spec with a `file` parameter, a principal with no primary key, a parameter or carried value JSON would change) and runs the kernel's prelude with the binding's pool seeds while the caller is on the stack; a refusal writes no row and sends nothing.
- The run row is written in the caller's transaction and the backend is asked on commit with `robust=True` on the row's alias, so a broker that is down at commit cancels no later callback and leaves the run `PENDING`.
- `DjangoTasksBackend(alias="default", queue=None)`: sends to Django Tasks (`django.tasks` from Django 6.0, the `django-tasks` backport below it).
- `CeleryBackend(queue=None)`: a task registered under the stable name `django_spec_tasks.run_spec`, `ignore_result`, the run id as Celery's `task_id`; registered when the app imports the module at startup.
- `sweep_runs` and the `sweep_spec_runs` command re-ask the backend for pending runs it seems to have lost and end lapsed running ones: back to `PENDING` when the spec declares `idempotent=True` with attempts to spare, otherwise `ABANDONED`; `SPEC_UNAVAILABLE` when the binding or spec is gone. Every move is a compare-and-swap on `(state, attempts)`, and `on_finish` runs for the states the sweep writes. The project schedules it; the package ships no schedule.
- `read_run` returns a frozen `RunStatus` to a run's owner or to whoever `can_read` allows, and never carries `params`, `carried`, `traceback`, `actor_pk` or `refusal_class`; a stranger, an unknown id, a malformed id and another binding's run raise the same `NotPermitted`. `state_of` answers the state alone for trusted callers.
- `prune_runs` and the `prune_spec_runs --older-than DAYS` command delete finished runs in batches and never a pending or running one; there is no default age.
- `SpecRun` is registered read-only in the admin: no add, change, delete or actions; `params`, `carried` and `output` are hidden unless a subclass sets `show_payloads`.

[Unreleased]: https://github.com/Artui/django-spec-tasks/compare/v0.0.0...HEAD
