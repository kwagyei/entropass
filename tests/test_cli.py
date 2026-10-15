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


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_bare_command_prints_a_default_password(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys)
    assert code == 0
    assert out.endswith("\n") and len(out.rstrip("\n")) == 20
    assert "bits" in err


def test_options_without_a_command_go_to_password(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "-l", "12", "-q")
    assert code == 0 and len(out.rstrip("\n")) == 12


def test_stdout_holds_only_the_secret(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "password", "-l", "32")
    assert code == 0
    assert out.count("\n") == 1
    password = out.rstrip("\n")
    assert len(password) == 32 and " " not in password
    assert password not in err


def test_entropy_line_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    _, _, err = run(capsys, "password")
    assert err == (
        "130.9 bits of entropy: 20 characters from a 94-symbol alphabet, "
        "with at least one each of lower, upper, digits, symbols\n"
    )


def test_entropy_line_without_requirement(capsys: pytest.CaptureFixture[str]) -> None:
    _, _, err = run(capsys, "password", "--no-require", "--no-symbols", "-l", "10")
    assert err == (
        "59.5 bits of entropy: 10 characters from a 62-symbol alphabet "
        "(lower, upper, digits, none required)\n"
    )


def test_quiet_suppresses_the_entropy_line(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "password", "-q")
    assert code == 0 and out and err == ""


def test_options_reach_the_generator(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run(
        capsys,
        "password",
        "-l",
        "40",
        "--no-upper",
        "--no-digits",
        "--symbols=-_",
        "--exclude-ambiguous",
    )
    password = out.rstrip("\n")
    assert set(password) <= set("abcdefghijkmnpqrstuvwxyz-_")
    assert set(password) & set("-_")


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["password", "-l", "0"], "length"),
        (["password", "-l", "abc"], "length"),
        (["password", "-l", "1025"], "length"),
        (["password", "-l", "3"], "at least 4"),
        (["password", "--no-lower", "--no-upper", "--no-digits", "--no-symbols"], "at least one"),
        (["password", "--symbols", "abc"], "symbols"),
        (["password", "--symbols", "|", "--exclude-ambiguous"], "symbols"),
        (["password", "--no-symbols", "--symbols", "!"], "symbols"),
    ],
)
def test_bad_options_are_usage_errors(
    capsys: pytest.CaptureFixture[str], argv: list[str], message: str
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    out, err = capsys.readouterr()
    assert exc.value.code == 2
    assert out == ""
    assert message in err
