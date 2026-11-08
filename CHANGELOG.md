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

[Unreleased]: https://github.com/Artui/django-spec-tasks/compare/v0.0.0...HEAD
