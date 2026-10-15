"""Edge cases for PR 3: dice input, separators, word bounds, exact entropy, the I/O contract.

The entropy oracle uses high-precision decimal logarithms, which share no code or method
with the integer bit-length trick in ``entropass.entropy.floor_bits``.
"""

import base64
import hashlib
import io
import json
import string
import subprocess
import sys
from decimal import ROUND_FLOOR, Decimal, localcontext
from importlib.metadata import distribution
from importlib.resources import files
from pathlib import Path

import pytest

import entropass
from entropass.cli import MAX_WORDS, main
from entropass.passphrase import WORDLIST_SIZE, check_separator, load_wordlist

EFF_LARGE_SHA256 = "addd35536511597a02fa0a9ff1e5284677b8883b83e986e43f15a3db996b903e"
WORDS = frozenset(load_wordlist())
LETTERS_IN_LIST = frozenset("".join(load_wordlist()))
DICE_SUFFIX = ", assuming fair dice"


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def entropy_line(words: int) -> str:
    """The expected stderr line, from an independent decimal oracle."""
    with localcontext() as ctx:
        ctx.prec = 80
        exact = Decimal(words) * Decimal(WORDLIST_SIZE).ln() / Decimal(2).ln()
        tenths = (exact * 10).to_integral_value(rounding=ROUND_FLOOR)
        # Guard the oracle itself: never sit within 1e-30 of a 0.1 boundary.
        assert exact * 10 - tenths > Decimal("1e-30")
    noun = "word" if words == 1 else "words"
    return (
        f"{tenths / 10:.1f} bits of entropy: {words} {noun} from the EFF large wordlist "
        f"({WORDLIST_SIZE} words)"
    )


def entropass_process(*argv: str, stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "entropass", *argv],
        input=stdin,
        capture_output=True,
        timeout=60,
        check=False,
    )


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


# --- Exact entropy lines ----------------------------------------------------------------


@pytest.mark.parametrize("words", range(1, MAX_WORDS + 1))
def test_entropy_line_is_the_floor_of_n_log2_7776(
    capsys: pytest.CaptureFixture[str], words: int
) -> None:
    code, out, err = run(capsys, "passphrase", "-w", str(words))
    assert code == 0
    assert err == entropy_line(words) + "\n"
    assert len(out.rstrip("\n").split(" ")) == words


@pytest.mark.parametrize("words", [1, 6, 99, 100])
def test_dice_entropy_line_matches_and_says_fair_dice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], words: int
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("12345\n" * words))
    code, _, err = run(capsys, "passphrase", "--dice", "-w", str(words))
    assert code == 0
    assert err == entropy_line(words) + DICE_SUFFIX + "\n"


# --- -w bounds ----------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", str(MAX_WORDS)])
def test_word_bounds_are_inclusive(capsys: pytest.CaptureFixture[str], value: str) -> None:
    code, out, _ = run(capsys, "passphrase", "-q", "-w", value)
    assert code == 0 and len(out.rstrip("\n").split(" ")) == int(value)


@pytest.mark.parametrize("value", ["0", "-1", str(MAX_WORDS + 1), "1.5", "", "six", "1e2"])
def test_word_count_out_of_bounds_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], value: str
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["passphrase", "-w", value])
    out, err = capsys.readouterr()
    assert exc.value.code == 2 and out == ""
    assert "words" in err


def test_word_bounds_apply_to_dice_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as exc:
        main(["passphrase", "--dice", "-w", "0"])
    assert exc.value.code == 2 and capsys.readouterr().out == ""


# --- Separators -------------------------------------------------------------------------


@pytest.mark.parametrize("sep", ["|", " | ", "  ", "7", "42", "X", "XYZ", "..", "\\", '"', "=="])
def test_allowed_separators_join_and_split_back(
    capsys: pytest.CaptureFixture[str], sep: str
) -> None:
    code, out, err = run(capsys, "passphrase", "-w", "5", "--sep", sep)
    assert code == 0 and err.startswith("64.6 bits")
    assert out.endswith("\n") and out.count("\n") == 1
    words = out[:-1].split(sep)
    assert len(words) == 5 and set(words) <= WORDS


@pytest.mark.parametrize("sep", ["-", " - ", "x", "Q.q", "|e|", "1-2"])
def test_separators_with_wordlist_characters_are_usage_errors(
    capsys: pytest.CaptureFixture[str], sep: str
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["passphrase", f"--sep={sep}"])
    out, err = capsys.readouterr()
    assert exc.value.code == 2 and out == ""
    assert "appears in the wordlist" in err


def test_separator_rule_is_exactly_the_wordlist_alphabet() -> None:
    """Every printable ASCII character is allowed unless some EFF word contains it."""
    assert set(string.ascii_lowercase) | {"-"} == LETTERS_IN_LIST
    for code in range(32, 127):
        ch = chr(code)
        if ch in LETTERS_IN_LIST:
            with pytest.raises(ValueError, match="appears in the wordlist"):
                check_separator(ch)
        else:
            check_separator(ch)


def test_hyphen_free_split_is_unique_so_join_is_injective() -> None:
    """With an allowed separator, splitting recovers the word list (incl. hyphenated words)."""
    sample = ["drop-down", "felt", "tip", "yo-yo", "t-shirt", "down", "shirt"]
    for sep in (" ", ".", "|", "7", "XY"):
        assert sep.join(sample).split(sep) == sample


def test_dice_mode_checks_the_separator_before_reading_rolls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = io.StringIO("11111\n")
    monkeypatch.setattr("sys.stdin", stdin)
    with pytest.raises(SystemExit) as exc:
        main(["passphrase", "--dice", "-w", "1", "--sep=-"])
    assert exc.value.code == 2 and capsys.readouterr().out == ""
    assert stdin.tell() == 0  # no roll was consumed


# --- Dice from a pipe -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "11111\r\n66666\r\n",  # Windows pipe
        "11111\r66666\r",  # bare CR
        "11111 \t\n\t6 6 6 6 6  \n",  # trailing and leading whitespace
        "11111\n66666",  # no final newline
        "\r\n11111\r\n\r\n66666\r\n\r\n",  # blank CRLF lines
        "1,1,1,1,1\n6, 6, 6, 6, 6\n",
    ],
)
def test_piped_line_variants_are_accepted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    code, out, _ = run(capsys, "passphrase", "--dice", "-w", "2", "-q")
    assert (code, out) == (0, "abacus zoom\n")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "read 0"),
        ("\n\n  \n", "read 0"),
        ("11111\n22222\n33333\n", "read 3"),
        ("11111\n", "read 1"),
    ],
)
def test_piped_wrong_number_of_rolls_fails_without_echo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], text: str, message: str
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    code, out, err = run(capsys, "passphrase", "--dice", "-w", "2")
    assert code == 1 and out == ""
    assert "expected 2 rolls" in err and message in err
    for roll in ("11111", "22222", "33333"):
        assert roll not in err


@pytest.mark.parametrize("bad", ["\ufeff11111", "1\u00a01 1 1 1", "\uff11" * 5, "12 3 4", "654321"])
def test_piped_malformed_rolls_fail_with_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(f"22222\n{bad}\n"))
    code, out, err = run(capsys, "passphrase", "--dice", "-w", "2")
    assert code == 1 and out == ""
    assert "roll 2" in err and "22222" not in err and bad.strip() not in err


def test_dice_mode_uses_no_software_randomness(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*_: object, **__: object) -> list[str]:
        raise AssertionError("dice mode must not call the generator")

    monkeypatch.setattr("entropass.cli.generate_passphrase", forbidden)
    monkeypatch.setattr("sys.stdin", io.StringIO("66665\n"))
    assert run(capsys, "passphrase", "--dice", "-w", "1", "-q")[:2] == (0, "zoology\n")


def test_piped_undecodable_bytes_fail_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0005: bad piped input fails with exit 1 and a message, not a traceback."""
    stdin = io.TextIOWrapper(io.BytesIO(b"\xff\xfe\n11111\n"), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stdin)
    code, out, err = run(capsys, "passphrase", "--dice", "-w", "2")
    assert code == 1 and out == ""
    assert err.startswith("entropass: error:")


def test_closed_stdin_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`entropass passphrase --dice <&-` leaves sys.stdin as None."""
    monkeypatch.setattr("sys.stdin", None)
    code, out, err = run(capsys, "passphrase", "--dice", "-w", "1")
    assert code == 1 and out == ""
    assert err.startswith("entropass: error:")


# --- Dice on a terminal -----------------------------------------------------------------


def _fake_terminal(monkeypatch: pytest.MonkeyPatch, answers: list[str | BaseException]) -> _Tty:
    tty = _Tty()
    monkeypatch.setattr("sys.stdin", tty)
    replies = iter(answers)

    def fake_getpass(prompt: str = "") -> str:
        reply = next(replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr("entropass.cli.getpass.getpass", fake_getpass)
    return tty


def test_terminal_accepts_whitespace_and_never_reads_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tty = _fake_terminal(monkeypatch, [" 1 1 1 1 1 \r", "", "6,6,6,6,6"])
    code, out, err = run(capsys, "passphrase", "--dice", "-w", "2")
    assert (code, out) == (0, "abacus zoom\n")
    assert tty.tell() == 0
    assert "Word" not in out and "Word" not in err  # prompts go through getpass only


def test_terminal_reprompt_does_not_echo_the_rejected_roll(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_terminal(monkeypatch, ["123456", "11111"])
    code, out, err = run(capsys, "passphrase", "--dice", "-w", "1", "-q")
    assert (code, out) == (0, "abacus\n")
    assert "123456" not in err and "five dice" in err


@pytest.mark.parametrize("after", [0, 1])
def test_terminal_eof_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], after: int
) -> None:
    """Ctrl-D at a getpass prompt raises EOFError: expect exit 1, no traceback, no secret."""
    _fake_terminal(monkeypatch, [*["11111"] * after, EOFError()])
    code, out, err = run(capsys, "passphrase", "--dice", "-w", "2")
    assert code == 1 and out == ""
    assert "11111" not in err


def test_terminal_ctrl_c_exits_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C at a getpass prompt: expect the conventional 130, nothing on stdout.

    The interrupt is caught here so that an escaping KeyboardInterrupt fails this test
    instead of stopping the whole pytest session.
    """
    _fake_terminal(monkeypatch, ["11111", KeyboardInterrupt()])
    try:
        code, out, _ = run(capsys, "passphrase", "--dice", "-w", "2")
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt escaped main(): the user sees a traceback")
    assert code == 130 and out == ""


# --- stdout contract in a real process --------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "stdin"),
    [
        (["passphrase"], b""),
        (["passphrase", "-w", "100", "--sep", " | "], b""),
        (["passphrase", "--dice", "-w", "2"], b"11111\r\n66666\r\n"),
        (["passphrase", "--dice", "-w", "3", "--sep", "."], b"11111\n6 6 6 6 6\n66665"),
    ],
)
def test_stdout_is_one_ascii_line_holding_only_the_passphrase(
    argv: list[str], stdin: bytes
) -> None:
    proc = entropass_process(*argv, stdin=stdin)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    out.decode("ascii")  # raises if not ASCII
    line = out.removesuffix(b"\n").removesuffix(b"\r")
    assert out.endswith(b"\n") and b"\n" not in line and b"\r" not in line
    assert b"bits" not in out and b"Word" not in out
    assert b"bits of entropy" in proc.stderr
    if "--dice" in argv:
        sep = argv[argv.index("--sep") + 1] if "--sep" in argv else " "
        expected = ["abacus", "zoom", "zoology"][: int(argv[argv.index("-w") + 1])]
        assert line.decode("ascii") == sep.join(expected)


def test_piped_bad_roll_in_a_real_process_prints_nothing_on_stdout() -> None:
    proc = entropass_process("passphrase", "--dice", "-w", "2", stdin=b"11111\r\n77777\r\n")
    assert proc.returncode == 1 and proc.stdout == b""
    assert b"Traceback" not in proc.stderr and b"77777" not in proc.stderr


# --- The wordlist as installed ----------------------------------------------------------


def test_wordlist_is_read_from_the_imported_package() -> None:
    resource = files("entropass") / "data" / "eff_large_wordlist.txt"
    package_dir = Path(entropass.__file__).resolve().parent
    assert Path(str(resource)).resolve() == package_dir / "data" / "eff_large_wordlist.txt"
    assert hashlib.sha256(resource.read_bytes()).hexdigest() == EFF_LARGE_SHA256


def test_installed_record_pins_the_same_hash() -> None:
    """The wheel's RECORD carries the file hash; it must equal EFF's (non-editable installs)."""
    dist = distribution("entropass")
    direct = dist.read_text("direct_url.json")
    if direct and json.loads(direct).get("dir_info", {}).get("editable"):
        pytest.skip("editable install: RECORD does not list package data")
    entries = {str(f): f for f in dist.files or []}
    for name in (
        "entropass/data/eff_large_wordlist.txt",
        "entropass/data/EFF-WORDLIST-LICENSE.txt",
    ):
        assert name in entries, f"{name} missing from the installed wheel"
    record = entries["entropass/data/eff_large_wordlist.txt"].hash
    assert record is not None and record.mode == "sha256"
    digest = base64.urlsafe_b64decode(record.value + "=" * (-len(record.value) % 4))
    assert digest.hex() == EFF_LARGE_SHA256


def test_wordlist_is_cached_and_immutable() -> None:
    first = load_wordlist()
    assert first is load_wordlist() and isinstance(first, tuple)
    assert all(w.isascii() and w == w.strip() and len(w) >= 3 for w in first)


def test_metadata_declares_both_licences() -> None:
    meta = distribution("entropass").metadata
    assert meta["License-Expression"] == "MIT AND CC-BY-4.0"
    assert any("EFF-WORDLIST-LICENSE" in f for f in meta.get_all("License-File") or [])


def test_licence_notice_records_provenance_and_pin() -> None:
    notice = (files("entropass") / "data" / "EFF-WORDLIST-LICENSE.txt").read_text("utf-8")
    assert "unmodified" in notice and EFF_LARGE_SHA256 in notice
    assert "Joseph Bonneau" in notice and "CC BY 4.0" in notice
    assert "eff.org/files/2016/07/18/eff_large_wordlist.txt" in notice
