# With django-domain-events

A project that records its events with [django-domain-events](https://pypi.org/project/django-domain-events/)
can start a run from an event, fire an event from inside a run, and fire one when a run finishes. None of
it needs an import in either package, and neither lists the other as a dependency or an extra.

They fit because both keep their record in a database row written inside whatever transaction the caller
holds. An event and a run are each "intent, committed with the business change", so three seams on the
binding and a few lines in the project are all the joining there is:

| Seam | What it is for here |
| --- | --- |
| `carry=` | records the caller's correlation id on the row at enqueue |
| `around_run=` | restores it, and the principal, in the worker around the service |
| `on_finish=` | fires an event inside the transaction of the run's terminal write |

**What was run.** The recipe has been run against django-domain-events 0.14.1 by a throwaway probe on
Django 6.1 and SQLite, not by a project: an event starting a run, a service and a finishing hook firing
inside the worker's transaction, a lost claim, a killed worker, a replay, and attribution through
`attributed`. The delayed run was read from the source and not run. The seams' own transaction behaviour
is held by this package's tests, with a plain model standing in for the event log. Every domain-events
name in the snippets is in `django_domain_events.__all__` at 0.14.1.

## An event starts a run

A durable receiver calls `enqueue` from inside its delivery.

```python
from django_domain_events import receiver
from django_spec_tasks import enqueue


@receiver(OrderPaid, key="orders.review_refund")
def review_refund(event: OrderPaid) -> None:
    enqueue(orders, "review_refund", {"order": event.order_id}, actor=None)
```

domain-events runs a durable receiver in the same transaction that records its acknowledgement, and
`enqueue` writes the run row in the transaction it finds. So the row commits with the acknowledgement, and
the backend is asked only after that commit.

- A receiver that raises leaves no run and asks no backend: the row rolls back with the delivery, and
  the delivery is retried by domain-events as any failed one is.
- A delivery whose acknowledgement is lost (the claim lapsed to another relay) leaves no run either.
- A delivered event gives one run and one message. **No dedup key is needed for that**, on one condition.

**The one-database condition.** `SpecRun` and the event log must be on the same database. `SpecRun` is
always on the default alias, and a system check refuses a router that sends it elsewhere. If the event log
is written to another alias, the run row and the acknowledgement are two commits: a crash between them
re-delivers the event and makes a second run. Give the receiver a dedup key of its own then, or move the log
to the default alias.

**A replay is a new run.** `replay_events` reopens a finished delivery, the receiver runs again, and
`enqueue` writes another row. That is what a replay asks for: the event is owed again, and what an
event is owed is a run. It is a statement here and not a guard, because a project that wants a replay to be
a no-op can make the spec idempotent, and one that wants it to repeat the work has nothing to undo.

**An event's actor is attribution, not authorization.** The receiver chooses the principal itself, or
passes `actor=None` and lets the spec answer for a system run. The event is never handed to the permission
check as a grant. `None` is admitted only by a spec whose permissions admit it, and a gated spec refuses
it at `enqueue` with `PermissionCheckDenied`. Whoever fired the event may have acted with a role the worker
will not find, and the worker re-checks everything `enqueue` did, so a run is authorized now or it is
`AUTHORIZATION_LAPSED`.

Inside a receiver `current_scope()` holds only what an enclosing `attributed` block set, so a run started
from a receiver carries a correlation id only if the receiver opened such a block.

## A service fires inside a run

`fire()` writes into the transaction it finds. Under the worker's outer transaction, which a spec with
`atomic=True` gets, the event commits with the run's terminal state, and a worker killed before the commit
leaves neither the event nor the service's work, only a `RUNNING` row for the sweep.

```python
from dataclasses import dataclass

from django_domain_events import event, fire
from django_service_specs import ServiceSpec, Unrestricted


@event
@dataclass(frozen=True)
class OrderRefunded:
    order_id: str


def refund_order(*, order: str) -> None:
    ...  # the work
    fire(OrderRefunded(order_id=order))


refund = ServiceSpec(service=refund_order, atomic=True, permissions=[Unrestricted()])
```

Under `atomic=False`, `fire()` warns that it ran outside a transaction, as it would anywhere: the event
commits when it is written, and the terminal state is a second fact. A spec that wants its events tied to
its work declares `atomic=True` or opens its own transaction.

A worker has no request, so an event fired there carries no actor and no correlation id. Filling the two
seams is enough:

```python
from uuid import UUID

from django_domain_events import attributed, current_scope
from django_spec_tasks import TaskBinding


def carry_correlation(actor):
    correlation = current_scope().correlation_id
    return {"correlation_id": str(correlation)} if correlation else {}


def attribute(run, principal):
    correlation = run.carried.get("correlation_id")
    return attributed(
        actor=principal,
        correlation_id=UUID(correlation) if correlation else None,
    )


orders = TaskBinding(
    name="orders",
    registry=registry,
    backend=backend,
    carry=carry_correlation,
    around_run=attribute,
)
```

`carry` is called by `enqueue`, in the caller's scope, and the mapping is stored on the row, so it must
survive JSON: a value JSON would change is refused there. `around_run` is called in the worker with the
row and the resolved principal, and is entered before `dispatch` and left after `present`. Anything the
service fires between them has the actor and the correlation id. This is the same shape as
domain-events' own `AROUND_DELIVERY` hook for receivers.

`attributed` mints a correlation id when it is given none, so a run enqueued with no ambient correlation
still fires its events under one id of its own.

## A run finishing fires an event

`on_finish(run)` runs inside the transaction of the run's terminal write, after `around_run` has left. An
event fired there exists if and only if the state committed.

```python
@event
@dataclass(frozen=True)
class RunFinished:
    run_id: str
    spec: str
    state: str


def announce(run):
    correlation = run.carried.get("correlation_id")
    with attributed(correlation_id=UUID(correlation) if correlation else None):
        fire(RunFinished(run_id=str(run.pk), spec=run.spec_name, state=str(run.state)))


orders = TaskBinding(..., carry=carry_correlation, around_run=attribute, on_finish=announce)
```

What that guarantees:

- **A refusal fires it too.** Under `atomic=True` the service's work rolls back first, then the refused
  state and the event commit together in a transaction of their own.
- **A lost claim fires nothing.** A worker whose lease lapsed finds the terminal write matching no row, so
  the write does not happen, `on_finish` is not called, and under `atomic=True` what the service fired
  rolls back as well. Under `atomic=False` what the service fired stands, and no finish event exists.
- **The sweep fires it for its endings.** A run the sweep ends `ABANDONED` or `SPEC_UNAVAILABLE` calls the
  same hook, in the transaction of its write, on a row of the same shape as the worker's.
- **A hook that raises takes the write with it.** The event it fired before raising is gone, the run stays
  `RUNNING`, and the sweep answers it.

An event fired from `on_finish` is outside `around_run`, so it has no actor from that seam. `announce`
above takes its correlation from the row.

## A delayed run

domain-events lets a receiver declare when its delivery runs: `due=` takes a `timedelta`, counted from
the event, or a callable returning an aware datetime. A receiver that calls `enqueue` with a `due` is a
delayed spec run, and nothing in either package is a scheduler.

```python
from datetime import timedelta


@receiver(OfferSent, key="offers.expire", due=timedelta(hours=48))
def expire_offer(event: OfferSent) -> None:
    enqueue(offers, "expire_offer", {"offer": event.offer_id}, actor=None)
```

Periodic work stays with the project's own scheduler, which calls `enqueue` or `sweep_runs`.

## Why a run is not a delivery

A design that runs each spec as a delivery, with the worker a receiver and no `SpecRun`, looks smaller. It
is not the same thing:

- **A lapsed lease re-runs without asking.** domain-events owes any claimed delivery whose lease has
  lapsed, and nothing in that path reads whether repeating the receiver is safe. A receiver killed
  mid-call is called again. This package has a state table so that a lost worker is read against
  `idempotent`: an idempotent run goes back to `PENDING`, any other is `ABANDONED`.
- **It needs a relay process.** A deployment whose only queue is Celery or Django Tasks owns none.
- **It still needs a row.** A delivery has no place for the presented result or the refusal, which are what
  a caller reads back.
- **It couples the packages.** Neither lists the other, and the recipe keeps it so; a run that was a
  delivery would need one to import the other's worker.

So the packages meet at a row each: an event can start a run, and a run can fire an event, and neither
owns the other's record.
