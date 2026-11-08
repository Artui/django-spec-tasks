# django-spec-tasks

[![CI](https://github.com/Artui/django-spec-tasks/workflows/tests/badge.svg)](https://github.com/Artui/django-spec-tasks/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/django-spec-tasks.svg)](https://pypi.org/project/django-spec-tasks/)
[![Python versions](https://img.shields.io/pypi/pyversions/django-spec-tasks.svg)](https://pypi.org/project/django-spec-tasks/)
[![Django versions](https://img.shields.io/pypi/djversions/django-spec-tasks.svg)](https://pypi.org/project/django-spec-tasks/)
[![Docs](https://img.shields.io/badge/docs-artui.github.io-blue.svg)](https://artui.github.io/django-spec-tasks/)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Artui/django-spec-tasks/gh-pages/coverage.json)](https://github.com/Artui/django-spec-tasks/actions/workflows/tests.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/django-spec-tasks.svg)](https://github.com/Artui/django-spec-tasks/blob/main/LICENSE)

Run a [django-service-specs](https://github.com/Artui/django-service-specs) spec as a background task:
a durable run record, and a worker that re-checks through `dispatch`.

```bash
pip install "django-spec-tasks[celery]"        # or
pip install "django-spec-tasks[django-tasks]"  # the backport, below Django 6.0
```

```python
from django_spec_tasks import TaskBinding, enqueue, read_run
from django_spec_tasks.backends.celery_backend import CeleryBackend

orders = TaskBinding(name="orders", registry=order_specs, backend=CeleryBackend())

run_id = enqueue(orders, "refund_order", {"order": "7"}, actor=request.user)
status = read_run(orders, run_id, request.user)
```

The queue decides where the code runs; the row decides whether it ran. A backend may lose a message or deliver
it twice without consequence, which is what lets one protocol hold Celery and Django Tasks.

Read [the documentation](https://artui.github.io/django-spec-tasks/).
