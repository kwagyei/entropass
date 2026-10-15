import runpy
from importlib.metadata import version

import pytest

import entropass
from entropass.cli import main

# The version recorded in the installed package's metadata, independent of entropass.__version__.
INSTALLED = version("entropass")


def test_version_flag_prints_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"entropass {INSTALLED}"


def test_dunder_version_matches_package_metadata() -> None:
    assert entropass.__version__ == INSTALLED


def test_no_arguments_exits_cleanly() -> None:
    assert main([]) == 0


def test_unknown_flag_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--no-such-flag"])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_runs_as_a_module(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["entropass", "--version"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("entropass", run_name="__main__")
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"entropass {INSTALLED}"
