# API reference

Everything below is exported from `django_spec_tasks` directly, except the two backends, which stay on their
own paths because each imports an optional queue. Kernel names a binding reads, such as `SpecRegistry` and
`resolve_principal`, are documented on
[the kernel's site](https://artui.github.io/django-service-specs/reference/).

## System checks

Registered by the app's config, so a project gets them by listing `"django_spec_tasks"` in `INSTALLED_APPS`.

- **`django_spec_tasks.E001`**: a database router sends `SpecRun` away from the default alias, for reads or for writes. The kernel's
  transaction opens on the default alias, and the worker needs the row inside it.

## Management commands

- **`sweep_spec_runs`**: see [`sweep_runs`][django_spec_tasks.sweep.sweep_runs.sweep_runs].
- **`prune_spec_runs --older-than DAYS`**: see [`prune_runs`][django_spec_tasks.sweep.prune_runs.prune_runs].

## Calling

::: django_spec_tasks.caller.enqueue.enqueue

::: django_spec_tasks.read.read_run.read_run

::: django_spec_tasks.read.state_of.state_of

## Binding

::: django_spec_tasks.binding.task_binding.TaskBinding

## Backends

::: django_spec_tasks.types.task_backend.TaskBackend

::: django_spec_tasks.backends.celery_backend.CeleryBackend

::: django_spec_tasks.backends.django_tasks_backend.DjangoTasksBackend

## The worker

::: django_spec_tasks.worker.run_spec.run_spec

::: django_spec_tasks.worker.run_state_for.run_state_for

## Operating

::: django_spec_tasks.sweep.sweep_runs.sweep_runs

::: django_spec_tasks.sweep.prune_runs.prune_runs

## Admin

::: django_spec_tasks.admin.SpecRunAdmin

## Types and the row

::: django_spec_tasks.models.spec_run.SpecRun

::: django_spec_tasks.types.run_state.RunState

::: django_spec_tasks.types.terminal_states.TERMINAL_STATES

::: django_spec_tasks.types.run_status.RunStatus

::: django_spec_tasks.types.sweep_report.SweepReport
