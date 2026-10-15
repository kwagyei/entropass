"""The package must never import the non-cryptographic `random` module."""

import ast
from pathlib import Path

import entropass

PACKAGE = Path(entropass.__file__).parent


def imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def test_the_scan_catches_both_import_forms() -> None:
    assert "random" in imported_modules("import random")
    assert "random" in imported_modules("from random import choice")
    assert "random" not in imported_modules("import secrets")


def test_no_module_imports_random() -> None:
    files = sorted(PACKAGE.rglob("*.py"))
    assert files, PACKAGE
    offenders = [f.name for f in files if "random" in imported_modules(f.read_text("utf-8"))]
    assert offenders == []
