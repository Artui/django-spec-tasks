# The worker

`run_spec(run_id)` runs one row: it claims it, calls the spec through the kernel's `dispatch`, and
records how the call ended, in one transaction when the spec says `atomic=True`. **Progress under
`atomic=True` is invisible until that transaction commits, unless the binding names a
`progress_using` alias:** a report written on the connection holding the transaction cannot be read
from any other, which is the point of the transaction and the cost of writing to the row.

Both backends call this function with one string and read nothing back. It takes no retries and
returns no result: whether a run may run again is read from the row.

## The steps

1. **Read.** A row that is not `PENDING` ends the call here. A redelivered message costs one query and
   no run, whatever the spec declares.
2. **Find.** The binding by name and the spec in its registry. Either missing ends the row
   `SPEC_UNAVAILABLE`, from `PENDING`, with `attempts` unchanged, and `{"message": ...}` in
   `refusal_detail` naming what is missing.
3. **Claim.** One conditional `UPDATE` to `RUNNING`, committed alone before anything runs. Losing it
   ends the call. Two copies of a message cannot both win, and a copy cannot run a row another
   attempt finished.
4. **Resolve.** `actor_pk` null means the principal is `None`, never `AnonymousUser`, and a spec gated
   on a principal refuses it. Otherwise `binding.resolve_actor(actor_pk)`.
5. **Run.** `dispatch`, then `present`, inside `around_run`. Everything enqueue checked is checked again,
   and the Validator runs for the first time.
6. **Record.** A write conditioned on `(pk, state, attempts)`, then `on_finish`, in one transaction.

The call is a task call: `call_transport(call_context)` reads `"task"`, and whatever `carry` recorded at
enqueue is at `call_context["carried"]`, where a `PoolSeeds` resolver can read it.

## The transaction

| Spec | What wraps what |
| --- | --- |
| `ServiceSpec` with `atomic=True` | one transaction around `around_run`, `dispatch`, `present`, the terminal write and `on_finish` |
| `ServiceSpec` with `atomic=False` | no outer transaction; the terminal write and `on_finish` take their own |
| `SelectorSpec` | nothing to roll back, so as `atomic=False` |

So under `atomic=True` the service's work and the run's state are one fact. A worker dying before the
commit leaves no row of the service's, and a `RUNNING` run the sweep answers.

A refusal or an exception rolls that transaction back first, then writes its state and runs `on_finish`
in a transaction of its own, so the state and what it fires still commit together.

**The window that remains.** An `atomic=False` spec's committed work and its terminal write are two
facts. A worker dying between them leaves a `RUNNING` row the sweep ends `ABANDONED`, with the work done.
That is what `atomic=False` means, and it is the right choice for a call one transaction should not span,
such as an agent run.

**A lost claim commits nothing.** Past its lease a run may be handed to another worker or ended by the
sweep. The terminal write then matches no row: it logs a warning and writes nothing, nothing fires, and
under `atomic=True` the service's work rolls back with it. Under `atomic=False` the work stands. So a
binding's `lease` is sized to its longest run.

## How a run ends

| Raised | State | Why |
| --- | --- | --- |
| `NotPermitted`, `PermissionCheckDenied` | `AUTHORIZATION_LAPSED` | authorized at enqueue, refused now: access changed, the code did not break |
| `PrincipalUnavailable` | `AUTHORIZATION_LAPSED` | deleted, malformed or deactivated in the window |
| `InvalidArguments` | `INVALID` | the Validator runs only here |
| `DispatchError`, and any later subclass | `INVALID` | a refusal of the call |
| `ServiceValidationError` | `INVALID` | the service's word for bad input |
| `ServiceNotFound` | `REFUSED` | the target vanished |
| `ActionUnavailable`, `ServiceConflict` | `CONFLICT` | terminal; `ActionUnavailable` leaves its code |
| `AdditionalInputRequired` | `INPUT_REQUIRED` | nobody to ask; the remedy is a new enqueue carrying the answer |
| `ServiceError`, and any later subclass | `REFUSED` | the operation refused on its own terms |

`run_state_for(error)` is this table. Anything else is `FAILED`, and `FAILED` is never the answer to a
refusal.

Every row of the table records:

| Column | Holds |
| --- | --- |
| `refusal_class` | the class's name, a project's own subclass included |
| `refusal_code` | its `code`, where it has one |
| `refusal_detail` | `{"message": ..., "errors": ..., "schema": ...}` |

`errors` is the field errors of an `InvalidArguments` or a `ServiceValidationError`, and `schema` is what
an `AdditionalInputRequired` asks for; each is null otherwise. A refusal carrying a detail no JSON column
could store keeps its state and its message, with both null.

A `FAILED` run has `traceback` and leaves the refusal columns null: a reader is shown `refusal_detail` and
never `traceback`, so a fault's words stay where only an operator looks.

## The result

`SUCCEEDED` records `kind`, and `output`, which is `present(spec, result)`: what a JSON transport would
have sent, stamped with `package_version` since it is a contract. `instance_pk` keeps the target's identity.
`kind` is `"instance"`, `"list"` or `"not_found"`. A required target or row that is not there is a result
rather than a refusal, so it ends `SUCCEEDED` with `kind="not_found"` and no output, on a selector and on a
service spec alike. A `ServiceNotFound` a service raises is a refusal: it ends `REFUSED`.

**The bound.** Past `max_output_bytes`, measured as the column writes it, the run ends `OUTPUT_TOO_LARGE`
with `{"size": ..., "limit": ...}` in `refusal_detail`, and `output` stays null. Under `atomic=True` the work
rolls back with it, so the caller reads a clean refusal. Under `atomic=False` the work stands, and the state
says the result was not stored. Output is never truncated.

Output JSON cannot hold, which is a presenter's bug, ends `FAILED`, as any exception nobody declared does.

## Progress

A service declaring `progress` gets a reporter that writes to the row, for as long as this attempt holds it.
With a `total`, the `progress` column holds the fraction done; without one, the service's own unit.
`message` lands in `progress_message`, and `meta` is dropped.

| Spec | `progress_using` | Read from another connection |
| --- | --- | --- |
| `atomic=False`, or a selector | unset | at once |
| `atomic=True` | unset | only after the run commits, and never if it rolls back |
| `atomic=True` | an alias | at once, on a database that lets a second connection write |

A report that cannot be written is logged and dropped: progress never ends a run. SQLite has one writer, so
under `atomic=True` an alias's write waits for the service's and fails once the service has written anything.

Progress increases within one dispatch. A claim clears both columns, so an idempotent spec run again
starts from nothing rather than from what the attempt before it reported.

## The seams

`around_run(run, principal)` is entered before `dispatch` and left after `present`, and is handed what the
call raises. It may not swallow it: with no result to record, a run whose seam swallowed its exception ends
`FAILED` rather than `SUCCEEDED` with nothing to show.

`on_finish(run)` runs inside the terminal transaction and reads the row as a reader would. Raising from it is
a bug in the project: the terminal write rolls back with it, the exception reaches the worker, and the sweep
answers the run.

## Exceptions the worker does not catch

A `BaseException` is the worker dying: it passes through untouched, rolling back whatever transaction is
open, and leaves the row `RUNNING`. So does anything raised while recording the ending, since nothing could
record that either.
