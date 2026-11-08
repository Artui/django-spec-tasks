from __future__ import annotations

import ast
from pathlib import Path

import django_spec_tasks

_PACKAGE = Path(django_spec_tasks.__file__).parent
_ROOT_FILES = {
    "__init__.py",
    "__init__.pyi",
    "admin.py",
    "apps.py",
    "checks.py",
    "py.typed",
    "version.py",
}
# The two whose ``__init__.py`` has a job: Django finds a model through ``models``.
_REEXPORTING = {_PACKAGE, _PACKAGE / "models"}


def test_the_root_holds_only_what_the_framework_names_and_the_version() -> None:
    files = {path.name for path in _PACKAGE.iterdir() if path.is_file()}
    assert files <= _ROOT_FILES


def test_a_concern_subpackage_re_exports_nothing() -> None:
    inits = [path for path in _PACKAGE.rglob("__init__.py") if path.parent not in _REEXPORTING]
    assert inits
    for init in inits:
        body = ast.parse(init.read_text()).body
        assert ast.get_docstring(ast.Module(body=body, type_ignores=[])) is not None or not body
        assert len(body) <= 1, init
