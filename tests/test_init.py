from __future__ import annotations

import django_spec_tasks
from django_spec_tasks.version import __version__


def test_the_root_exports_the_version() -> None:
    assert django_spec_tasks.__version__ == __version__
