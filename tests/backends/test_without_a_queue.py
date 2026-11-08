"""Neither queue is a dependency: the package has to import, and enqueue, with neither installed."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import django_spec_tasks

_REPOSITORY = Path(django_spec_tasks.__file__).parent.parent

# ``None`` in ``sys.modules`` makes an import of the name raise ``ImportError``, which is what
# a missing distribution does, and makes ``find_spec`` answer that there is none.
_WITH_NO_QUEUE = """
import sys
for name in ("celery", "django_tasks"):
    sys.modules[name] = None
import django
django.setup()
import django_spec_tasks
from django_spec_tasks.enqueue.enqueue import enqueue
loaded = sorted(m for m in sys.modules if m.startswith("django_spec_tasks.backends"))
print(loaded)
"""


def test_the_package_and_enqueue_import_with_neither_queue_installed() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _WITH_NO_QUEUE],
        cwd=_REPOSITORY,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "tests.conftest_settings"},
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"
