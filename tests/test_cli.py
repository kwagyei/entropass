import errno
import io
import runpy
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

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


class ClosedPipe(io.StringIO):
    """A stdout whose reader has gone away."""

    def __init__(self, fd: int, error: OSError) -> None:
        super().__init__()
        self._fd = fd
        self._error = error

    def write(self, s: str) -> int:
        raise self._error

    def fileno(self) -> int:
        return self._fd


@pytest.mark.parametrize(
    "error",
    [BrokenPipeError(errno.EPIPE, "Broken pipe"), OSError(errno.EINVAL, "Invalid argument")],
    ids=["posix", "windows"],
)
def test_closed_pipe_fails_quietly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    error: OSError,
) -> None:
    with (tmp_path / "stdout").open("w") as target:
        monkeypatch.setattr("sys.stdout", ClosedPipe(target.fileno(), error))
        assert main(["password", "-q"]) == 1
    assert capsys.readouterr().err == ""


def test_other_os_errors_are_not_swallowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with (tmp_path / "stdout").open("w") as target:
        monkeypatch.setattr("sys.stdout", ClosedPipe(target.fileno(), OSError(errno.EIO, "I/O")))
        with pytest.raises(OSError, match="I/O"):
            main(["password"])


# --help and --version are included because argparse writes them itself: the closed pipe
# only surfaces at the final flush, which main() must also catch.
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe semantics")
@pytest.mark.parametrize("argv", [["-q", "-l", "1024"], ["--help"], ["--version"]])
def test_closed_pipe_in_a_real_process(argv: list[str]) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "entropass", *argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    proc.stdout.close()
    err = proc.stderr.read()
    assert proc.wait(timeout=30) == 1
    assert err == b""


class TestPassphraseCommand:
    def test_default_is_six_words_with_spaces(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, err = run(capsys, "passphrase")
        assert code == 0
        assert out.endswith("\n") and out.count("\n") == 1
        assert len(out.split(" ")) == 6
        assert err == "77.5 bits of entropy: 6 words from the EFF large wordlist (7776 words)\n"

    def test_words_and_separator(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, err = run(capsys, "passphrase", "-w", "5", "--sep", ".")
        assert len(out.rstrip("\n").split(".")) == 5
        assert err.startswith("64.6 bits of entropy: 5 words")

    def test_one_word(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, err = run(capsys, "passphrase", "-w", "1")
        assert " " not in out.rstrip("\n")
        assert err.startswith("12.9 bits of entropy: 1 word from")

    def test_quiet(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, err = run(capsys, "passphrase", "-q")
        assert out and err == ""

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (["-w", "0"], "words"),
            (["-w", "101"], "words"),
            (["-w", "x"], "words"),
            (["--sep", "-"], "appears in the wordlist"),
            (["--sep", ""], "separator"),
        ],
    )
    def test_bad_options_are_usage_errors(
        self, capsys: pytest.CaptureFixture[str], argv: list[str], message: str
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["passphrase", *argv])
        out, err = capsys.readouterr()
        assert exc.value.code == 2 and out == ""
        assert message in err

    def test_help_credits_eff(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main(["passphrase", "--help"])
        out = capsys.readouterr().out
        assert "Electronic Frontier Foundation" in out and "CC BY 4.0" in out


class TestDice:
    def piped(self, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(text))

    def test_piped_rolls_choose_the_words(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.piped(monkeypatch, "11111\n6 6 6 6 6\n66665\n")
        code, out, err = run(capsys, "passphrase", "--dice", "-w", "3")
        assert code == 0
        assert out == "abacus zoom zoology\n"
        assert err.endswith(
            "38.7 bits of entropy: 3 words from the EFF large wordlist "
            "(7776 words), assuming fair dice\n"
        )

    def test_piped_blank_lines_are_skipped(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.piped(monkeypatch, "\n11111\n\n11111\n")
        _, out, _ = run(capsys, "passphrase", "--dice", "-w", "2", "-q")
        assert out == "abacus abacus\n"

    def test_piped_bad_roll_fails_without_echoing_it(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.piped(monkeypatch, "11111\n12347\n")
        code, out, err = run(capsys, "passphrase", "--dice", "-w", "2")
        assert code == 1 and out == ""
        assert "roll 2" in err and "12347" not in err

    def test_piped_too_few_rolls(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.piped(monkeypatch, "11111\n")
        code, out, err = run(capsys, "passphrase", "--dice", "-w", "2")
        assert code == 1 and out == ""
        assert "expected 2 rolls" in err

    def test_terminal_rolls_are_read_without_echo_and_reprompted(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr("sys.stdin", _Tty())
        answers = iter(["66666", "7", "11111"])
        prompts: list[str] = []

        def fake_getpass(prompt: str = "") -> str:
            prompts.append(prompt)
            return next(answers)

        monkeypatch.setattr("entropass.cli.getpass.getpass", fake_getpass)
        code, out, err = run(capsys, "passphrase", "--dice", "-w", "2")
        assert code == 0 and out == "zoom abacus\n"
        assert prompts[0].startswith("Word 1 of 2")
        assert prompts[1] == prompts[2] and prompts[1].startswith("Word 2 of 2")
        assert "five dice" in err and "7\n" not in err


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True
