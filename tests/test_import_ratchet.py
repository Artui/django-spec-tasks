from __future__ import annotations

import ast
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import django_spec_tasks

_PACKAGE = Path(django_spec_tasks.__file__).parent
_CELERY_MODULE = Path("backends/celery_backend.py")
_TASKS_MODULE = Path("backends/django_tasks_backend.py")


def _imported_modules(tree: ast.Module) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def _refusal(module: str, source: Path) -> str | None:
    top = module.split(".")[0]
    if top in sys.stdlib_module_names or top == "django_spec_tasks":
        return None
    if top == "django_service_specs":
        return None if module == top else "the kernel is read through its root"
    if top == "celery":
        return None if source == _CELERY_MODULE else "celery belongs to its backend"
    if top == "django_tasks" or module.startswith("django.tasks"):
        return None if source == _TASKS_MODULE else "the Tasks framework belongs to its backend"
    if top == "django":
        return None
    return "not a dependency of this package"


def imports_out_of_bounds(root: Path) -> list[str]:
    """Every import under ``root`` this package may not make, as ``path: module: why``."""
    return [
        f"{source}: {module}: {why}"
        for path in sorted(root.rglob("*.py*"))
        if path.suffix in {".py", ".pyi"}
        for source in [path.relative_to(root)]
        for module in _imported_modules(ast.parse(path.read_text()))
        for why in [_refusal(module, source)]
        if why is not None
    ]


def _copy_with(tmp_path: Path, relative: str, line: str) -> Path:
    # A copy of the real package with one file changed, so each test is the ratchet refusing it.
    root = tmp_path / "copy"
    shutil.copytree(_PACKAGE, root, ignore=shutil.ignore_patterns("__pycache__"))
    target = root / relative
    target.parent.mkdir(exist_ok=True)
    target.write_text(f"{target.read_text() if target.exists() else ''}\n{line}\n")
    return root


def test_the_package_imports_only_what_it_may() -> None:
    assert imports_out_of_bounds(_PACKAGE) == []


def test_the_ratchet_reads_the_real_package() -> None:
    # An empty walk would pass the test above with nothing checked.
    assert len(list(_PACKAGE.rglob("*.py"))) > 5


@pytest.mark.parametrize(
    ("relative", "line", "why"),
    [
        ("apps.py", "import rest_framework", "not a dependency"),
        ("apps.py", "from rest_framework import views", "not a dependency"),
        ("apps.py", "import django_spec_rest", "not a dependency"),
        ("apps.py", "import django_spec_mcp", "not a dependency"),
        ("apps.py", "def later() -> None:\n    import rest_framework", "not a dependency"),
        ("apps.py", "import celery", "celery belongs"),
        ("backends/django_tasks_backend.py", "import celery", "celery belongs"),
        ("backends/celery_backend.py", "import django_tasks", "the Tasks framework belongs"),
        ("apps.py", "from django.tasks import task", "the Tasks framework belongs"),
        ("apps.py", "from django_service_specs.dispatch.dispatch import dispatch", "its root"),
    ],
)
def test_the_ratchet_refuses_a_copy_importing(
    tmp_path: Path, relative: str, line: str, why: str
) -> None:
    refused = imports_out_of_bounds(_copy_with(tmp_path, relative, line))
    assert len(refused) == 1, refused
    assert refused[0].startswith(relative)
    assert why in refused[0]


@pytest.mark.parametrize(
    ("relative", "line"),
    [
        ("backends/celery_backend.py", "from celery import shared_task"),
        ("backends/django_tasks_backend.py", "from django.tasks import task"),
        ("backends/django_tasks_backend.py", "from django_tasks import task"),
        ("apps.py", "from django_service_specs import dispatch"),
        ("apps.py", "from django.db import transaction"),
    ],
)
def test_the_ratchet_accepts_a_copy_importing(tmp_path: Path, relative: str, line: str) -> None:
    assert imports_out_of_bounds(_copy_with(tmp_path, relative, line)) == []
