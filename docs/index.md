# django-spec-tasks

Run a [django-service-specs](https://artui.github.io/django-service-specs/) spec as a background task:
a durable run record, and a worker that re-checks through the kernel's `dispatch`.

```python
from django_spec_tasks import enqueue

run_id = enqueue(orders, "refund_order", {"order": "7"}, actor=request.user)
```

`enqueue` runs the kernel's checks while the caller is on the stack, writes a `SpecRun` row, and asks a
backend to run that id once the transaction commits. The worker claims the row, resolves the principal,
calls `dispatch` and records the result on the row.

**The queue decides where the code runs; the row decides whether it ran.** A backend may lose a message or
deliver it twice without consequence: a second copy finds the claim taken, and a sweep asks again for a
run the queue lost. That is what lets one protocol hold Celery and Django Tasks.

## Install

```bash
pip install "django-spec-tasks[celery]"        # or
pip install "django-spec-tasks[django-tasks]"  # the backport, below Django 6.0
```

Add `"django_spec_tasks"` to `INSTALLED_APPS` and run `migrate`. Neither queue is a hard dependency, and
Django 6.0 has `django.tasks` with no extra.

## A binding

A `TaskBinding` says which specs may run in the background, and how. Collaborators are objects passed here,
never dotted paths in settings. Declare it in a `spec_tasks` module of an installed app, which the worker
finds at startup:

```python
# orders/spec_tasks.py
from django_spec_tasks import TaskBinding
from django_spec_tasks.backends.celery_backend import CeleryBackend

from orders.specs import order_specs

orders = TaskBinding(name="orders", registry=order_specs, backend=CeleryBackend())
```

Only `name`, `registry` and `backend` are required. The row records the binding's name, so do not rename one
while it has runs pending.

## What a caller reads back

```python
from django_spec_tasks import read_run

status = read_run(orders, run_id, request.user)
status.state  # RunState.SUCCEEDED
status.output  # what a JSON transport would have sent
```

`read_run` answers the run's owner, or whoever the binding's `can_read` allows, and refuses everyone else
with one `NotPermitted`, whether or not the id exists. A run ends in one state, and a refusal is never a
failure: the [worker page](worker.md) has the table.

## What it does not do

It is not a queue, a scheduler or a result store. The project owns its workers, its queues and the schedule
that runs the sweep. There is no generic `@task` decorator, no retry policy, and no chaining or fan-out.

## The pages

- [Backends](backends.md): enqueueing, and choosing where a run executes.
- [Worker](worker.md): the claim, the transaction, each ending, progress and the seams.
- [Operating](operating.md): the sweep, reading a run, pruning, the admin.
- [With django-domain-events](domain-events.md): composing with no import in either direction.
- [Reference](reference.md): every public name.
