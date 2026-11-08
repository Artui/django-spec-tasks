# Enqueueing, and where a run executes

`enqueue` is the caller's half. A backend is the other: it asks a queue to call the worker with a run's id.

```python
from django_spec_tasks import enqueue

run_id = enqueue(orders, "refund_order", {"order": "7"}, actor=request.user)
```

It returns the run's id as a string. `actor` is required: pass `None` for a system run, which the spec admits or refuses by its own permissions. Leaving the argument out is a `TypeError`, so an unauthenticated call cannot become a system run by omission.

## What enqueue refuses

Everything that can be refused while the caller is still on the stack is, and a refusal writes no row and sends nothing. A refusal spends no query: the kernel's prelude (`check_call`) reads the call and the principal and touches no table.

| The call | Raises |
| --- | --- |
| a spec name the binding's registry does not hold | `LookupError`, naming the binding and the name |
| a spec with a `file` parameter, at any depth | `TypeError`, naming the parameter |
| an `actor` with no primary key, such as `AnonymousUser` | `TypeError`, naming `actor=None` |
| a deactivated `actor` | `PrincipalUnavailable` |
| an unknown, missing, mistyped or out-of-choices argument | `InvalidArguments` |
| a principal the spec's class-level permissions refuse | `NotPermitted` |
| a parameter named like one of the binding's pool seeds | `ImproperlyConfigured` |
| a parameter or carried value that JSON would change | `TypeError`, naming the key |

The check is the kernel's own, run with the binding's `pool_seeds`, so a seed collision is refused here exactly as the worker would refuse it. Nothing it grants crosses to the worker: the worker checks again.

**The Validator does not run at enqueue.** A stock one queries, needs the row it acts on, and its answer would not hold by the time a worker ran. The worker runs the whole of it.

### A file cannot cross

An upload is a live object, and a queue carries JSON. Save it to storage first and declare a parameter that holds its key:

```python
key = default_storage.save(name, upload)
enqueue(imports, "import_orders", {"upload_key": key}, actor=request.user)
```

### What JSON would change

A queue stores JSON, and what it hands a worker is what survived. Django Tasks accepts some values that do not survive: a tuple comes back a list, bytes a string, a mapping with integer keys keeps them in one backend and loses them in the next. `enqueue` compares each parameter with its own round trip and refuses the difference, so the parameters a worker sees are the ones the caller wrote. Pass what a JSON body would carry.

`binding.carry(actor)`, when set, is held to the same rule. Its result is recorded on the row and offered to the call context as `carried`, here and again in the worker, so a seed resolver reads the same value in both.

## When the message is sent

The row is written in the caller's transaction and the backend is asked on commit:

- inside `atomic`, nothing is sent before the block commits;
- if the block rolls back, nothing is sent and there is no row;
- a backend that raises at commit (a broker that is down) does not cancel the callbacks the project registered after it, and leaves the row `PENDING`. The row is what makes the lost message recoverable, so the sweep asks again.

The callback is registered with `robust=True` on the alias the row was written to.

## Choosing a backend

Name the backend on the binding. It is never chosen by what imports: `django.tasks` imports on any Django 6, and its default backend runs a task inline on the caller's thread, so a choice by availability would pick that in a project running Celery.

### Django Tasks

```python
from django_spec_tasks.backends.django_tasks_backend import DjangoTasksBackend

orders = TaskBinding(name="orders", registry=registry, backend=DjangoTasksBackend())
```

`DjangoTasksBackend(alias="default", queue=None)` sends to the `TASKS` entry `alias`, and to `queue` on it when one is named. The framework is `django.tasks` from Django 6.0 and the `django-tasks` backport below it; install the backport with the `django-tasks` extra.

The module defines its task at the top level, as Django Tasks requires: a worker finds a task again by its dotted path, which must resolve to the task itself. The task is built against the `default` entry of `TASKS`, as any `@task` is, so `TASKS` needs one.

`django.tasks` on its own is not a deployment. Core ships `ImmediateBackend`, which runs the task on the caller's thread, and `DummyBackend`, which runs nothing; it has no worker and no durable backend. For production run `django-tasks-db` and its `db_worker`, or use Celery.

### Celery

```python
from django_spec_tasks.backends.celery_backend import CeleryBackend

orders = TaskBinding(name="orders", registry=registry, backend=CeleryBackend())
```

`CeleryBackend(queue=None)` sends the run, with the run's id as Celery's `task_id` so a log line names the run. Install the `celery` extra.

The task is registered under the name `django_spec_tasks.run_spec`, spelled out and never derived from the module path, because messages already on a broker carry it. It is registered by importing the module, which the app does in `ready()` whenever `celery` is installed: a worker finds a message's task in its own registry, so a task built later, inside a call, would pass every test that sends and runs in one process and be missing from every real worker. Route it with `task_routes`, keyed by that name.

The package sets nothing else, each because the row already answers it:

| Not set | Why |
| --- | --- |
| retries | a re-run is the sweep's fresh enqueue of the same id, the same in either world |
| `acks_late` | your acknowledgement and visibility timeout apply; a second copy finds the claim taken |
| result backend | the result is on the row (`ignore_result=True`) |
| beat | the package schedules nothing |
| routing | no queue by default; `task_routes` decides |

## What your project owns

The package asks a queue to call the worker; the deployment is yours.

| | Django Tasks | Celery |
| --- | --- | --- |
| the worker process | `db_worker`, or your backend's | `celery worker` |
| its queues | `TASKS` entries and `QUEUES` | `task_routes`, `-Q` |
| the scheduler for the sweep | a cron job or your scheduler | Celery beat, or cron |
