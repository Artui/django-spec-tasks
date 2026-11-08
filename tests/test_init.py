from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import django_spec_tasks
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.version import __version__


def test_the_root_exports_the_version() -> None:
    assert django_spec_tasks.__version__ == __version__


def test_importing_the_package_loads_no_model() -> None:
    # Django imports the package before a model may be imported, so a re-export that
    # reaches the model layer breaks every project listing the app. In a fresh process
    # with no settings at all, because this one has already loaded everything.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, django_spec_tasks; "
            "assert 'django_spec_tasks.models.spec_run' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("name", sorted(django_spec_tasks._LAZY_EXPORTS))
def test_a_lazy_export_resolves_from_its_leaf_and_is_cached(name: str) -> None:
    django_spec_tasks.__dict__.pop(name, None)
    resolved = getattr(django_spec_tasks, name)
    assert django_spec_tasks.__dict__[name] is resolved


def test_a_lazy_export_is_the_object_its_leaf_holds() -> None:
    assert django_spec_tasks.SpecRun is SpecRun


def test_no_export_shares_its_name_with_a_submodule() -> None:
    # Importing a submodule binds it on the package, and __getattr__ runs only for a name
    # that is absent, so a lazy export named after a submodule answers with the module
    # once anything has imported the leaf: the answer depends on import order.
    package = Path(django_spec_tasks.__file__).parent
    children = {path.stem for path in package.iterdir() if not path.name.startswith("__")}
    assert children.isdisjoint(django_spec_tasks.__all__)


@pytest.mark.parametrize("name, leaf", sorted(django_spec_tasks._LAZY_EXPORTS.items()))
def test_a_lazy_export_is_the_same_object_whichever_import_comes_first(
    name: str, leaf: str
) -> None:
    # In a fresh process, because this one has already imported every leaf.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import django, importlib; django.setup(); "
            f"leaf = importlib.import_module({leaf!r}).{name}; "
            f"from django_spec_tasks import {name} as root; "
            "assert root is leaf, root",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_an_unknown_name_raises_attribute_error_naming_it() -> None:
    with pytest.raises(AttributeError, match="no_such_name"):
        _ = django_spec_tasks.no_such_name


def test_dir_lists_a_lazy_name_before_it_is_first_read() -> None:
    django_spec_tasks.__dict__.pop("SpecRun", None)
    assert "SpecRun" in dir(django_spec_tasks)


def test_every_exported_name_resolves() -> None:
    for name in django_spec_tasks.__all__:
        getattr(django_spec_tasks, name)


def test_the_stub_declares_what_the_root_exports() -> None:
    # A name missing from the stub is untyped for every consumer, with nothing failing.
    stub = ast.parse(Path(django_spec_tasks.__file__).with_suffix(".pyi").read_text())
    imported = {
        alias.name for node in stub.body if isinstance(node, ast.ImportFrom) for alias in node.names
    }
    assert imported == set(django_spec_tasks.__all__)
