# Operating

Four things keep a deployment honest: a **sweep** that answers runs the queue lost, a
**read side** that says who may see a run, **pruning** that deletes old rows, and an **admin** that
shows them. None of them is scheduled by this package. A project runs the sweep and the prune the way
it runs anything periodic: a Celery beat entry, cron, or its platform's scheduler.

## The sweep

```python
from django_spec_tasks.sweep.sweep_runs import sweep_runs

report = sweep_runs()  # SweepReport(reasked=0, requeued=1, abandoned=0, unavailable=0, errors=0)
```

or, from a scheduler that runs commands:

```bash
python manage.py sweep_spec_runs
python manage.py sweep_spec_runs --limit 200
```

The command prints how many rows moved where: `Swept spec runs: 1 requeued, 0 abandoned, 0 spec
unavailable, 3 asked again, 0 errors.`

A queue may lose a message, so the row is the record and the sweep is how a lost run is found.

| Row | Outcome |
| --- | --- |
| `PENDING` past its binding's `grace` | the backend is asked again; the claim stops a second run |
| `RUNNING` past `lease_expires_at`, the spec declares `idempotent=True`, `attempts < max_attempts` | back to `PENDING`, and the backend is asked |
| the same, `attempts >= max_attempts` | `ABANDONED`: a run that keeps killing its worker must not loop |
| the same, `idempotent` is `False` or undeclared | `ABANDONED` |
| any of the above, the binding or the spec is gone | `SPEC_UNAVAILABLE` |

`idempotent` is read from the registered spec when the sweep runs, never from the row, so a spec whose
declaration changed answers with what it declares now. **Undeclared is not `True`**: a spec that says
nothing is abandoned, not retried.

A row whose binding this process does not know has no `grace` of its own, so a pending one waits out
the default (`TaskBinding.grace`'s default of ten minutes) before it is ended. A sweep running on code from
before a binding was added would otherwise end every run of it the moment it was written. Keep the
process that sweeps on the same code as the one that enqueues.

### What a lapse leaves behind

It depends on the spec:

- Under `atomic=True` the database effects rolled back with the worker, so `idempotent` decides only
  whether *external* effects (an email, a call to a payment provider) may happen twice.
- Under `atomic=False` the work may have happened. `ABANDONED` there means *unknown*: the sweep cannot
  tell whether the run finished its work and died before it wrote the state.

A worker that is slow rather than dead is refused at its own terminal write once the sweep has moved the
row. Under `atomic=True` its effects roll back; under `atomic=False` they stand. So size a binding's
`lease` to its longest run.

### Guarantees

- **Every move is a compare-and-swap on `(state, attempts)`.** Two sweeps agree on one transition, and
  a worker that claimed the row after the sweep read it is not overwritten.
- **`on_finish` runs for `ABANDONED` and `SPEC_UNAVAILABLE`**, in the transaction with the state write, so
  every terminal state fires it. If it raises, that row's write rolls back, the error is logged and
  counted in `errors`, and the sweep carries on; the row is answered by the next sweep.
- **A backend that raises** leaves its row as it was (a requeued row stays `PENDING`) and is counted in
  `errors`. The sweep asks again next time.
- **A sweep is bounded.** Rows are taken oldest first, `batch_size` at a time, and `limit` caps how many
  one sweep looks at.

## Reading a run

```python
from django_spec_tasks.read.read_run import read_run

status = read_run(orders, run_id, request.user)
```

`read_run` returns a frozen `RunStatus`: `id`, `state`, `kind`, `output`, `progress`,
`progress_message`, `refusal_code`, `refusal_detail`, `created_at`, `finished_at`. It never carries
`params`, `carried`, `traceback`, `actor_pk` or `refusal_class`. The call's arguments and its presented
output are as sensitive as the call, so:

1. the run's owner reads it, when the principal is not deactivated; or
2. the binding's `can_read(principal, run)` says yes.

Anything else raises `NotPermitted`. So do an unknown id, a malformed one, and a run of another binding,
with the same message, so an id cannot be probed for whether it exists. A deactivated principal is
refused on both paths. A system run has no owner, so only `can_read` reads it.

`state_of(binding, run_id)` is for trusted callers: the state alone, with no principal, or `None`
for an id that is not that binding's.

## Pruning

```bash
python manage.py prune_spec_runs --older-than 30
```

Deletes runs in a terminal state that finished more than the given number of days ago, in batches. It
never deletes a `PENDING` or `RUNNING` row, however old. There is no default age, so the argument is
required: how long to keep a run is the project's decision.

## Admin

`SpecRun` is registered read-only. Nobody adds, changes or deletes a run, and there are no actions
(an admin action skips the change permission). The list shows state, spec, binding, actor, attempts
and times, and filters by state and binding. `params`, `carried` and `output` are hidden because they
hold what the call was given and returned; the `traceback` shows, for triage.

To show the payloads, subclass the admin and set the flag:

```python
from django.contrib import admin

from django_spec_tasks.admin import SpecRunAdmin
from django_spec_tasks.models.spec_run import SpecRun


class PayloadSpecRunAdmin(SpecRunAdmin):
    show_payloads = True


admin.site.unregister(SpecRun)
admin.site.register(SpecRun, PayloadSpecRunAdmin)
```

## Reference

::: django_spec_tasks.sweep.sweep_runs.sweep_runs

::: django_spec_tasks.types.sweep_report.SweepReport

::: django_spec_tasks.sweep.prune_runs.prune_runs

::: django_spec_tasks.read.read_run.read_run

::: django_spec_tasks.read.state_of.state_of

::: django_spec_tasks.types.run_status.RunStatus
