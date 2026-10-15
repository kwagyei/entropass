"""Edge cases for PR 2: boundaries, an independent entropy oracle, and the I/O contract.

The oracle here is a dynamic programme over "which classes have appeared so far", which
shares no code or formula with the inclusion-exclusion in ``entropass.entropy``. It is
exact and fast enough for length 1024, so it checks the large-L figures the brute-force
grid cannot reach.
"""

import itertools
import string
import subprocess
import sys
from collections.abc import Sequence

import pytest

from entropass.charsets import AMBIGUOUS, CharClass, build_classes
from entropass.cli import MAX_LENGTH, main
from entropass.entropy import count_valid, floor_bits
from entropass.password import count_passwords, generate_password

PRINTABLE = {chr(c) for c in range(33, 127)}


def dp_count(sizes: Sequence[int], required: Sequence[bool], length: int) -> int:
    """Count strings over disjoint classes that hit every required class, by DP."""
    need = sum(1 << i for i, r in enumerate(required) if r)
    states = {0: 1}
    for _ in range(length):
        nxt: dict[int, int] = {}
        for mask, ways in states.items():
            for i, size in enumerate(sizes):
                hit = mask | (1 << i) if required[i] else mask
                nxt[hit] = nxt.get(hit, 0) + ways * size
        states = nxt
    return states.get(need, 0)


def brute_force(sizes: Sequence[int], required: Sequence[bool], length: int) -> int:
    labelled = [i for i, size in enumerate(sizes) for _ in range(size)]
    needed = {i for i, r in enumerate(required) if r}
    return sum(needed <= set(s) for s in itertools.product(labelled, repeat=length))


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


# --- Entropy oracle ---------------------------------------------------------------------

FOUR_CLASS_GRID = [
    (sizes, required, length)
    for sizes in ([1, 1, 1, 1], [1, 2, 1, 1], [2, 1, 2, 1])
    for required in itertools.product([False, True], repeat=4)
    for length in range(0, 6)
]


@pytest.mark.parametrize(("sizes", "required", "length"), FOUR_CLASS_GRID)
def test_four_classes_match_brute_force(
    sizes: list[int], required: tuple[bool, ...], length: int
) -> None:
    """The CLI allows four classes (16 terms); the original grid stops at three."""
    req = [n for n, r in zip(sizes, required, strict=True) if r]
    assert count_valid(sum(sizes), req, length) == brute_force(sizes, required, length)


def test_dp_oracle_agrees_with_brute_force() -> None:
    for sizes, required, length in FOUR_CLASS_GRID[::7]:
        assert dp_count(sizes, required, length) == brute_force(sizes, required, length)


@pytest.mark.parametrize("length", [1, 2, 3, 4, 5, 20, 100, 1000, MAX_LENGTH])
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"exclude_ambiguous": True},
        {"symbol_chars": "!", "exclude_ambiguous": True},
        {"upper": False, "symbol_chars": "-_"},
        {"lower": False, "upper": False, "symbols": False},
    ],
)
@pytest.mark.parametrize("require", [True, False])
def test_real_alphabets_match_the_dp_oracle(
    kwargs: dict[str, object], length: int, require: bool
) -> None:
    classes = build_classes(**kwargs)  # type: ignore[arg-type]
    sizes = [len(c.chars) for c in classes]
    expected = dp_count(sizes, [require] * len(sizes), length)
    assert count_passwords(classes, length, require=require) == expected


# --- Generator --------------------------------------------------------------------------


def test_every_draw_is_from_the_whole_alphabet() -> None:
    """Rejection sampling draws from all N symbols; per-class draws would be non-uniform."""
    classes = build_classes(exclude_ambiguous=True, symbol_chars="!@#")
    alphabet_size = sum(len(c.chars) for c in classes)
    calls: list[int] = []

    def spy(n: int) -> int:
        calls.append(n)
        return len(calls) * 7 % n

    generate_password(classes, 12, randbelow=spy)
    assert calls and set(calls) == {alphabet_size}
    assert len(calls) % 12 == 0


def test_rejected_candidates_are_redrawn_whole() -> None:
    """A deterministic source that first yields a letters-only string must be redrawn."""
    classes = [CharClass("letters", "ab"), CharClass("digits", "1")]
    seq = iter([0, 1, 0, 2, 0, 1])  # "aba" (no digit) then "1ab"

    pw = generate_password(classes, 3, randbelow=lambda n: next(seq))
    assert pw == "1ab"


def test_no_require_length_one_draws_any_symbol() -> None:
    classes = build_classes()
    seen = set()
    for i in range(94):

        def pick(n: int, i: int = i) -> int:
            return i % n

        seen.add(generate_password(classes, 1, require=False, randbelow=pick))
    assert seen == PRINTABLE


def test_max_length_password_is_valid() -> None:
    classes = build_classes(exclude_ambiguous=True, symbol_chars="!")
    pw = generate_password(classes, MAX_LENGTH)
    assert len(pw) == MAX_LENGTH
    for cls in classes:
        assert set(pw) & set(cls.chars)
    assert set(pw) <= set("".join(c.chars for c in classes))


def test_ambiguous_exclusion_applies_to_custom_symbols() -> None:
    classes = build_classes(symbol_chars="|!|@", exclude_ambiguous=True)
    assert classes[-1] == CharClass("symbols", "!@")
    assert sum(len(c.chars) for c in classes) == 24 + 24 + 8 + 2


def test_every_punctuation_symbol_is_accepted() -> None:
    classes = build_classes(symbol_chars=string.punctuation[::-1])
    assert classes[-1].chars == string.punctuation[::-1]


# --- CLI --------------------------------------------------------------------------------


def expected_line(classes: list[CharClass], length: int, require: bool) -> str:
    sizes = [len(c.chars) for c in classes]
    shown = floor_bits(dp_count(sizes, [require] * len(sizes), length))
    names = ", ".join(c.name for c in classes)
    if len(classes) == 1:
        detail = f" ({names})"
    elif require:
        detail = f", with at least one each of {names}"
    else:
        detail = f" ({names}, none required)"
    chars = "character" if length == 1 else "characters"
    return (
        f"{shown:.1f} bits of entropy: {length} {chars} "
        f"from a {sum(sizes)}-symbol alphabet{detail}\n"
    )


def test_exclude_ambiguous_with_custom_symbols(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "password", "--symbols=|!", "--exclude-ambiguous", "-l", "8")
    pw = out.rstrip("\n")
    assert code == 0 and len(pw) == 8
    assert "!" in pw and not set(pw) & set(AMBIGUOUS)
    classes = build_classes(symbol_chars="!", exclude_ambiguous=True)
    assert err == expected_line(classes, 8, True)
    assert "57-symbol" in err


def test_no_require_length_one(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "password", "--no-require", "-l", "1")
    assert code == 0
    assert len(out) == 2 and out[0] in PRINTABLE and out[1] == "\n"
    assert err.startswith("6.5 bits of entropy: 1 character from a 94-symbol alphabet")


def test_length_one_with_one_required_class(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "password", "-l", "1", "--no-upper", "--no-digits", "--no-symbols")
    assert code == 0 and out.rstrip("\n") in string.ascii_lowercase
    assert err == "4.7 bits of entropy: 1 character from a 26-symbol alphabet (lower)\n"


def test_length_one_with_four_required_classes_is_an_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["password", "-l", "1"])
    out, err = capsys.readouterr()
    assert exc.value.code == 2 and out == "" and "at least 4" in err


def test_no_require_lifts_the_minimum_length(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "password", "--no-require", "-l", "3", "-q")
    assert code == 0 and len(out.rstrip("\n")) == 3


@pytest.mark.parametrize("flag", ["-l", "--length"])
def test_length_1024_is_accepted_with_exact_entropy(
    capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    code, out, err = run(capsys, "password", flag, str(MAX_LENGTH))
    assert code == 0
    assert out.endswith("\n") and len(out) == MAX_LENGTH + 1
    assert err == expected_line(build_classes(), MAX_LENGTH, True)
    assert err.startswith("6711.8 bits")


def test_length_equals_form(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run(capsys, f"--length={MAX_LENGTH}", "-q")
    assert len(out) == MAX_LENGTH + 1


@pytest.mark.parametrize("value", ["1025", "-1", "-5", "99999999999999999999", "1.5", ""])
def test_length_out_of_range_is_exit_2(capsys: pytest.CaptureFixture[str], value: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["password", "-l", value])
    out, err = capsys.readouterr()
    assert exc.value.code == 2 and out == "" and "length" in err


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["-l", "64"],
        ["--no-require", "-l", "1"],
        ["--exclude-ambiguous", "--symbols=|!"],
        ["--no-lower", "--no-upper", "--no-symbols", "-l", "6"],
        ["-l", str(MAX_LENGTH)],
    ],
)
def test_output_is_ascii_and_stdout_is_exactly_the_secret(
    capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    code, out, err = run(capsys, *argv)
    assert code == 0
    assert out.isascii() and err.isascii()
    assert out.endswith("\n") and out.count("\n") == 1
    assert set(out[:-1]) <= PRINTABLE  # no spaces, tabs, CR or control characters
    assert err.endswith("\n") and err.count("\n") == 1


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["-l", "4"],
        ["--no-require", "-l", "1"],
        ["--exclude-ambiguous", "--symbols=|!", "-l", "7"],
        ["--no-symbols", "-l", "33"],
        # Regression: float log2 showed 540.0 bits here; the truth is 539.99...
        ["--no-upper", "--no-symbols", "--exclude-ambiguous", "-l", "108"],
    ],
)
def test_displayed_bits_never_overstate(
    capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    _, _, err = run(capsys, *argv)
    shown = float(err.split(" ", 1)[0])
    length = int(argv[argv.index("-l") + 1]) if "-l" in argv else 20
    require = "--no-require" not in argv
    classes = build_classes(
        upper="--no-upper" not in argv,
        symbols="--no-symbols" not in argv,
        symbol_chars="!" if "--symbols=|!" in argv else None,
        exclude_ambiguous="--exclude-ambiguous" in argv,
    )
    count = count_passwords(classes, length, require=require)
    tenths = round(shown * 10)
    assert 2**tenths <= count**10 < 2 ** (tenths + 1)  # exact, no floating point


def test_top_level_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    out, err = capsys.readouterr()
    assert exc.value.code == 0 and err == ""
    assert out.startswith("usage: entropass")
    assert "password" in out and "--version" in out
    assert out.isascii()


@pytest.mark.parametrize("argv", [["password", "-h"], ["-l", "12", "-h"], ["--no-require", "-h"]])
def test_password_help_lists_every_option(
    capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    out, _ = capsys.readouterr()
    assert exc.value.code == 0
    assert out.startswith("usage: entropass password")
    for flag in (
        "--length",
        "--no-lower",
        "--no-upper",
        "--no-digits",
        "--no-symbols",
        "--symbols",
        "--exclude-ambiguous",
        "--no-require",
        "--quiet",
    ):
        assert flag in out, flag
    flat = " ".join(out.split())  # argparse wraps help lines to the terminal width
    assert "0 O o 1 l I |" in flat
    assert "--symbols=CHARS" in flat
    assert out.isascii()


def test_symbols_starting_with_dash_need_the_equals_form(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["password", "--symbols", "-_"])
    out, err = capsys.readouterr()
    assert exc.value.code == 2 and out == "" and "--symbols" in err


@pytest.mark.parametrize("argv", [["password", "extra"], ["help"], ["-q", "--version"]])
def test_stray_arguments_are_exit_2(capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


# --- The installed entry point, as a real process ---------------------------------------


def entropass(*argv: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "entropass", *argv], capture_output=True, check=False
    )


def test_process_stdout_is_exactly_the_secret_bytes() -> None:
    proc = entropass("password", "-l", "24")
    assert proc.returncode == 0
    secret = proc.stdout.rstrip(b"\r\n")
    assert proc.stdout in (secret + b"\n", secret + b"\r\n")
    assert len(secret) == 24 and set(secret.decode("ascii")) <= PRINTABLE
    assert secret not in proc.stderr
    assert proc.stderr.decode("ascii").startswith("1")


def test_process_exit_code_for_usage_errors() -> None:
    proc = entropass("password", "-l", "1025")
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert b"length must be between 1 and 1024" in proc.stderr


def test_process_quiet_writes_nothing_to_stderr() -> None:
    proc = entropass("-q")
    assert proc.returncode == 0 and proc.stderr == b"" and len(proc.stdout.strip()) == 20
