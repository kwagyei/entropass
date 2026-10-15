"""Edge cases for --copy, the clipboard backends and the detached clearer (PR 4).

Nothing here touches the real clipboard. In-process tests replace the default ``run`` and
``popen`` of every clipboard function with a guard that fails the test, then pass fakes
explicitly. The few real-process tests put fake clipboard tools (shell scripts backed by a
file) first and alone on PATH, so the real pbcopy, xclip and friends cannot be found.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from entropass import _clearer, clipboard
from entropass.cli import main
from entropass.clipboard import Backend, ClipboardUnavailable
from entropass.passphrase import load_wordlist, parse_roll

SECRET = "Tr0ub4dor&3-correct!"
SECRET_DIGEST = hashlib.sha256(SECRET.encode()).hexdigest()
HEX64 = re.compile(r"[0-9a-f]{64}")
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="fake tools are shell scripts")


# --------------------------------------------------------------------------- guard


def _forbidden(*args: object, **kwargs: object) -> Any:
    raise AssertionError(f"a real clipboard process would have started: {args!r}")


@pytest.fixture(autouse=True)
def _no_real_clipboard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any default (real) subprocess call from the clipboard module fail the test."""
    for fn in (clipboard.copy, clipboard.read, clipboard.clear_if_unchanged):
        monkeypatch.setattr(fn, "__defaults__", (_forbidden,))
    monkeypatch.setattr(
        clipboard.spawn_clearer, "__kwdefaults__", {"platform": sys.platform, "popen": _forbidden}
    )


def which_of(*found: str) -> Callable[[str], str | None]:
    return lambda name: f"/usr/bin/{name}" if name in found else None


ALL_TOOLS = ("pbcopy", "pbpaste", "clip", "powershell", "wl-copy", "wl-paste", "xclip", "xsel")


# --------------------------------------------------------------------------- detect


class TestDetectEdgeCases:
    @pytest.mark.parametrize(
        "env",
        [
            {},
            {"WAYLAND_DISPLAY": "", "DISPLAY": ""},
            {"WAYLAND_DISPLAY": ""},
            {"DISPLAY": ""},
            {"XDG_SESSION_TYPE": "wayland"},  # a session type alone is not a display
        ],
        ids=["no-vars", "both-empty", "wayland-empty", "display-empty", "session-type-only"],
    )
    def test_empty_or_missing_display_is_no_session(self, env: dict[str, str]) -> None:
        with pytest.raises(ClipboardUnavailable, match="graphical session"):
            clipboard.detect("linux", env, which_of(*ALL_TOOLS))

    def test_empty_wayland_value_with_x11_uses_x_tools(self) -> None:
        env = {"WAYLAND_DISPLAY": "", "DISPLAY": ":0"}
        backend = clipboard.detect("linux", env, which_of(*ALL_TOOLS))
        assert backend.name == "xclip"

    def test_wayland_with_empty_display_uses_wl_clipboard(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ""}
        assert clipboard.detect("linux", env, which_of(*ALL_TOOLS)).name == "wl-copy"

    def test_wayland_only_never_uses_x_tools(self) -> None:
        # Without DISPLAY there is no XWayland, so xclip could not reach a server.
        env = {"WAYLAND_DISPLAY": "wayland-0"}
        with pytest.raises(ClipboardUnavailable, match="wl-clipboard"):
            clipboard.detect("linux", env, which_of("xclip", "xsel"))

    def test_wayland_needs_both_wl_copy_and_wl_paste(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-0"}
        with pytest.raises(ClipboardUnavailable, match="wl-clipboard"):
            clipboard.detect("linux", env, which_of("wl-copy"))
        with pytest.raises(ClipboardUnavailable, match="wl-clipboard"):
            clipboard.detect("linux", env, which_of("wl-paste"))

    def test_xwayland_half_installed_wl_clipboard_falls_back_to_xsel(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
        assert clipboard.detect("linux", env, which_of("wl-copy", "xsel")).name == "xsel"

    def test_wayland_preferred_over_x11_when_both_work(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":1"}
        assert clipboard.detect("linux", env, which_of(*ALL_TOOLS)).name == "wl-copy"

    def test_xclip_preferred_over_xsel(self) -> None:
        assert clipboard.detect("linux", {"DISPLAY": ":0"}, which_of(*ALL_TOOLS)).name == "xclip"

    def test_x11_only_ignores_wl_clipboard(self) -> None:
        with pytest.raises(ClipboardUnavailable, match="xclip or xsel"):
            clipboard.detect("linux", {"DISPLAY": ":0"}, which_of("wl-copy", "wl-paste"))

    def test_xwayland_with_nothing_installed_names_wl_clipboard_too(self) -> None:
        # ADR-0006: "name the package to install (wl-clipboard, xclip or xsel)". In a
        # Wayland session with XWayland, wl-clipboard is the package to suggest first.
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
        with pytest.raises(ClipboardUnavailable) as exc:
            clipboard.detect("linux", env, which_of())
        assert "wl-clipboard" in str(exc.value)

    @pytest.mark.parametrize("platform", ["freebsd14", "openbsd7", "netbsd10", "freebsd8"])
    def test_bsd_uses_x11_tools(self, platform: str) -> None:
        backend = clipboard.detect(platform, {"DISPLAY": ":0"}, which_of("xsel"))
        assert backend.name == "xsel"

    @pytest.mark.parametrize("platform", ["freebsd14", "openbsd7"])
    def test_bsd_wayland(self, platform: str) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-0"}
        assert clipboard.detect(platform, env, which_of(*ALL_TOOLS)).name == "wl-copy"

    @pytest.mark.parametrize("platform", ["freebsd14", "openbsd7"])
    def test_bsd_headless(self, platform: str) -> None:
        with pytest.raises(ClipboardUnavailable, match="graphical session"):
            clipboard.detect(platform, {}, which_of(*ALL_TOOLS))

    def test_macos_ignores_display_variables(self) -> None:
        env = {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}  # e.g. XQuartz
        assert clipboard.detect("darwin", env, which_of(*ALL_TOOLS)).name == "pbcopy"

    def test_macos_needs_pbpaste_too(self) -> None:
        with pytest.raises(ClipboardUnavailable, match="pbpaste"):
            clipboard.detect("darwin", {}, which_of("pbcopy"))

    def test_windows_needs_clip_too(self) -> None:
        with pytest.raises(ClipboardUnavailable, match="clip"):
            clipboard.detect("win32", {}, which_of("powershell"))

    def test_windows_ignores_display_variables(self) -> None:
        env = {"DISPLAY": ":0"}
        assert clipboard.detect("win32", env, which_of(*ALL_TOOLS)).name == "clip"

    @pytest.mark.parametrize("platform", ["cygwin", "msys", "aix", "emscripten", "wasi", ""])
    def test_other_platforms_are_unsupported(self, platform: str) -> None:
        with pytest.raises(ClipboardUnavailable, match="not supported"):
            clipboard.detect(platform, {"DISPLAY": ":0"}, which_of(*ALL_TOOLS))

    def test_accepts_a_read_only_mapping(self) -> None:
        env: Mapping[str, str] = MappingProxyType({"DISPLAY": ":0"})
        assert clipboard.detect("linux", env, which_of("xclip")).name == "xclip"

    def test_only_looks_up_command_names_never_a_secret(self) -> None:
        asked: list[str] = []

        def which(name: str) -> str | None:
            asked.append(name)
            return None

        with pytest.raises(ClipboardUnavailable):
            clipboard.detect("linux", {"WAYLAND_DISPLAY": "w", "DISPLAY": ":0"}, which)
        assert set(asked) <= set(ALL_TOOLS)

    def test_backends_match_adr_table(self) -> None:
        assert clipboard._WINDOWS.paste_cmd == (
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-Clipboard",
        )
        assert clipboard._XCLIP.paste_cmd == ("xclip", "-selection", "clipboard", "-o")
        assert clipboard._XSEL.paste_cmd == ("xsel", "--clipboard", "--output")
        assert clipboard._WL_COPY.clear_cmd == ("wl-copy", "--clear")

    def test_backend_is_frozen(self) -> None:
        with pytest.raises(FrozenInstanceError):
            clipboard._PBCOPY.copy_cmd = ("sh", "-c", "evil")  # type: ignore[misc]


# --------------------------------------------------------------------------- fake run

BACKENDS = [
    clipboard._PBCOPY,
    clipboard._WINDOWS,
    clipboard._WL_COPY,
    clipboard._XCLIP,
    clipboard._XSEL,
]


class Board:
    """A fake clipboard driven through a backend's commands, recording every call."""

    def __init__(self, backend: Backend, content: bytes = b"", *, suffix: bytes = b"") -> None:
        self.backend = backend
        self.content = content
        self.suffix = suffix  # what the paste tool appends, e.g. CRLF from PowerShell
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.events: list[str] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((list(cmd), kwargs))
        command = tuple(cmd)
        if command == self.backend.paste_cmd:
            self.events.append("paste")
            return subprocess.CompletedProcess(cmd, 0, stdout=self.content + self.suffix)
        if command == self.backend.clear_cmd:
            self.events.append("clear")
            self.content = b""
        elif command == self.backend.copy_cmd:
            self.events.append("copy")
            self.content = kwargs["input"]
        else:  # pragma: no cover - would mean an unexpected command
            raise AssertionError(f"unexpected command {cmd!r}")
        return subprocess.CompletedProcess(cmd, 0, stdout=None)


class FakeStdin(io.BytesIO):
    def __init__(self) -> None:
        super().__init__()
        self.written = b""
        self.was_closed = False

    def close(self) -> None:
        self.written = self.getvalue()
        self.was_closed = True
        super().close()


class FakeProcess:
    def __init__(self) -> None:
        self.stdin: io.BytesIO = FakeStdin()


class FakePopen:
    def __init__(self, events: list[str] | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.processes: list[FakeProcess] = []
        self.events = events

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append((list(args), kwargs))
        if self.events is not None:
            self.events.append("spawn")
        process = FakeProcess()
        self.processes.append(process)
        return process

    @property
    def stdin_bytes(self) -> bytes:
        [process] = self.processes
        stdin = process.stdin
        assert isinstance(stdin, FakeStdin) and stdin.was_closed
        return stdin.written


def _strings_in(value: object) -> Iterator[str]:
    """Every string reachable in a call's argv or kwargs (bytes decoded)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, bytes):
        yield value.decode("latin-1")
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _strings_in(k)
            yield from _strings_in(v)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings_in(item)


# --------------------------------------------------------------------------- copy, read, clear


class TestCopyEdgeCases:
    @pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
    def test_every_backend_gets_the_secret_on_stdin_only(self, backend: Backend) -> None:
        board = Board(backend)
        clipboard.copy(backend, SECRET, run=board)
        [(cmd, kwargs)] = board.calls
        assert tuple(cmd) == backend.copy_cmd
        assert kwargs["input"] == SECRET.encode("ascii")  # no trailing newline
        assert not any(SECRET in s for s in _strings_in(cmd))
        assert "env" not in kwargs and not kwargs.get("shell", False)
        assert board.content == SECRET.encode()

    @pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
    def test_tool_output_goes_to_devnull_so_forking_tools_cannot_hang_it(
        self, backend: Backend
    ) -> None:
        # xclip, xsel and wl-copy fork a child that keeps serving the selection. A pipe on
        # stdout/stderr would be held open by that child.
        board = Board(backend)
        clipboard.copy(backend, SECRET, run=board)
        _, kwargs = board.calls[0]
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert "capture_output" not in kwargs

    def test_copy_has_a_timeout_and_never_raises_on_status(self) -> None:
        board = Board(clipboard._PBCOPY)
        clipboard.copy(clipboard._PBCOPY, SECRET, run=board)
        _, kwargs = board.calls[0]
        assert kwargs["timeout"] == clipboard.TIMEOUT
        assert 0 < clipboard.TIMEOUT <= 30
        assert kwargs["check"] is False

    def test_failure_message_never_contains_the_secret(self) -> None:
        def broken(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            raise subprocess.TimeoutExpired(cmd, 10, output=kwargs["input"])

        with pytest.raises(ClipboardUnavailable) as exc:
            clipboard.copy(clipboard._PBCOPY, SECRET, run=broken)
        assert SECRET not in str(exc.value)
        assert exc.value.__cause__ is None and exc.value.__suppress_context__

    @pytest.mark.parametrize("code", [1, 2, 127, -9])
    def test_any_nonzero_status_is_a_failure(self, code: int) -> None:
        def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(cmd, code)

        with pytest.raises(ClipboardUnavailable, match="pbcopy"):
            clipboard.copy(clipboard._PBCOPY, SECRET, run=run)

    @pytest.mark.parametrize(
        "exc", [PermissionError("denied"), FileNotFoundError("x"), OSError(8, "exec format")]
    )
    def test_os_errors_are_reported(self, exc: OSError) -> None:
        def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            raise exc

        with pytest.raises(ClipboardUnavailable, match="could not copy"):
            clipboard.copy(clipboard._XSEL, SECRET, run=run)


class TestReadEdgeCases:
    def test_read_has_a_timeout_and_captures_output(self) -> None:
        board = Board(clipboard._XCLIP, SECRET.encode())
        assert clipboard.read(clipboard._XCLIP, run=board) == SECRET
        _, kwargs = board.calls[0]
        assert kwargs["timeout"] == clipboard.TIMEOUT and kwargs["capture_output"] is True
        assert kwargs["check"] is False

    def test_hanging_paste_tool_reads_as_none(self) -> None:
        def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            raise subprocess.TimeoutExpired(cmd, 10)

        assert clipboard.read(clipboard._PBCOPY, run=run) is None

    def test_non_utf8_clipboard_does_not_crash(self) -> None:
        board = Board(clipboard._PBCOPY, b"\xff\xfe\x00binary")
        assert isinstance(clipboard.read(clipboard._PBCOPY, run=board), str)

    def test_empty_clipboard_reads_as_empty_string(self) -> None:
        assert clipboard.read(clipboard._PBCOPY, run=Board(clipboard._PBCOPY)) == ""


class TestClearEdgeCases:
    @pytest.mark.parametrize("suffix", [b"\n", b"\r\n", b"\r", b"\n\n", b"\r\n\r\n"])
    def test_paste_tool_line_endings_still_match(self, suffix: bytes) -> None:
        board = Board(clipboard._WINDOWS, SECRET.encode(), suffix=suffix)
        assert clipboard.clear_if_unchanged(clipboard._WINDOWS, SECRET_DIGEST, run=board)
        assert board.content == b""

    @pytest.mark.parametrize(
        "content",
        [
            SECRET + " ",
            " " + SECRET,
            "\n" + SECRET,
            SECRET + "\t",
            SECRET[:-1],
            SECRET + SECRET,
            SECRET.upper(),
            "",
        ],
        ids=[
            "trailing-space",
            "leading-space",
            "leading-newline",
            "tab",
            "prefix",
            "doubled",
            "case",
            "empty",
        ],
    )
    def test_anything_but_the_secret_is_left_alone(self, content: str) -> None:
        board = Board(clipboard._PBCOPY, content.encode())
        assert not clipboard.clear_if_unchanged(clipboard._PBCOPY, SECRET_DIGEST, run=board)
        assert board.events == ["paste"]
        assert board.content == content.encode()

    def test_expects_a_digest_not_the_secret(self) -> None:
        board = Board(clipboard._PBCOPY, SECRET.encode())
        assert not clipboard.clear_if_unchanged(clipboard._PBCOPY, SECRET, run=board)
        assert board.events == ["paste"]

    def test_compares_with_constant_time_compare(self, monkeypatch: pytest.MonkeyPatch) -> None:
        compared: list[tuple[str, str]] = []
        real = hmac.compare_digest

        def spy(a: str, b: str) -> bool:
            compared.append((a, b))
            return bool(real(a, b))

        monkeypatch.setattr(hmac, "compare_digest", spy)
        board = Board(clipboard._PBCOPY, SECRET.encode())
        assert clipboard.clear_if_unchanged(clipboard._PBCOPY, SECRET_DIGEST, run=board)
        assert compared == [(SECRET_DIGEST, SECRET_DIGEST)]

    @pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
    def test_clear_command_per_backend(self, backend: Backend) -> None:
        board = Board(backend, SECRET.encode())
        assert clipboard.clear_if_unchanged(backend, SECRET_DIGEST, run=board)
        cmd, kwargs = board.calls[-1]
        assert tuple(cmd) == (backend.clear_cmd or backend.copy_cmd)
        assert kwargs["input"] == b""
        assert kwargs["timeout"] == clipboard.TIMEOUT
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert board.content == b""

    def test_secret_never_in_argv_while_clearing(self) -> None:
        board = Board(clipboard._XSEL, SECRET.encode())
        clipboard.clear_if_unchanged(clipboard._XSEL, SECRET_DIGEST, run=board)
        for cmd, _ in board.calls:
            assert not any(SECRET in s or SECRET_DIGEST in s for s in cmd)

    def test_hanging_clear_reports_not_cleared(self) -> None:
        def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            if tuple(cmd) == clipboard._PBCOPY.paste_cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=SECRET.encode())
            raise subprocess.TimeoutExpired(cmd, 10)

        assert not clipboard.clear_if_unchanged(clipboard._PBCOPY, SECRET_DIGEST, run=run)

    def test_failing_clear_status_reports_not_cleared(self) -> None:
        def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            if tuple(cmd) == clipboard._PBCOPY.paste_cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=SECRET.encode())
            return subprocess.CompletedProcess(cmd, 1)

        assert not clipboard.clear_if_unchanged(clipboard._PBCOPY, SECRET_DIGEST, run=run)


class TestWindowsCreationFlags:
    def test_tool_calls_hide_the_console_window_on_windows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("entropass.clipboard.sys.platform", "win32")
        board = Board(clipboard._WINDOWS)
        clipboard.copy(clipboard._WINDOWS, SECRET, run=board)
        clipboard.clear_if_unchanged(clipboard._WINDOWS, SECRET_DIGEST, run=board)
        assert board.events == ["copy", "paste", "copy"]
        for _, kwargs in board.calls:
            assert kwargs["creationflags"] == CREATE_NO_WINDOW

    @pytest.mark.parametrize("platform", ["darwin", "linux", "freebsd14"])
    def test_no_creationflags_elsewhere(
        self, monkeypatch: pytest.MonkeyPatch, platform: str
    ) -> None:
        # creationflags is Windows-only; passing it on POSIX raises ValueError.
        monkeypatch.setattr("entropass.clipboard.sys.platform", platform)
        board = Board(clipboard._PBCOPY)
        clipboard.copy(clipboard._PBCOPY, SECRET, run=board)
        clipboard.clear_if_unchanged(clipboard._PBCOPY, SECRET_DIGEST, run=board)
        for _, kwargs in board.calls:
            assert "creationflags" not in kwargs

    def test_clearer_is_detached_with_new_group_and_no_session_flag(self) -> None:
        popen = FakePopen()
        clipboard.spawn_clearer(30, SECRET_DIGEST, platform="win32", popen=popen)
        [(_, kwargs)] = popen.calls
        assert kwargs["creationflags"] & CREATE_NO_WINDOW
        assert not kwargs["creationflags"] & DETACHED_PROCESS
        assert kwargs["creationflags"] & CREATE_NEW_PROCESS_GROUP
        assert "start_new_session" not in kwargs
        assert kwargs["close_fds"] is True


class TestSpawnClearerEdgeCases:
    @pytest.mark.parametrize("platform", ["darwin", "linux", "win32", "freebsd14"])
    def test_argv_has_no_secret_and_no_digest(self, platform: str) -> None:
        popen = FakePopen()
        clipboard.spawn_clearer(86400, SECRET_DIGEST, platform=platform, popen=popen)
        [(args, kwargs)] = popen.calls
        assert args == [sys.executable, "-m", "entropass._clearer", "--after", "86400"]
        assert not any(HEX64.search(s) for s in _strings_in(args))
        assert not any(SECRET_DIGEST in s for s in _strings_in(kwargs))
        assert "env" not in kwargs and not kwargs.get("shell", False)
        assert popen.stdin_bytes == SECRET_DIGEST.encode("ascii")

    def test_stdin_is_a_pipe_and_output_is_discarded(self) -> None:
        popen = FakePopen()
        clipboard.spawn_clearer(1, SECRET_DIGEST, platform="linux", popen=popen)
        [(_, kwargs)] = popen.calls
        assert kwargs["stdin"] == subprocess.PIPE
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["start_new_session"] is True and kwargs["close_fds"] is True

    def test_does_not_wait_for_the_clearer(self) -> None:
        class NoWait(FakeProcess):
            def wait(self, timeout: float | None = None) -> int:
                raise AssertionError("the CLI must not wait for the clearer")

            def communicate(self, *args: object, **kwargs: object) -> Any:
                raise AssertionError("the CLI must not wait for the clearer")

        def popen(args: list[str], **kwargs: Any) -> NoWait:
            return NoWait()

        clipboard.spawn_clearer(30, SECRET_DIGEST, platform="linux", popen=popen)


# --------------------------------------------------------------------------- _clearer.main


class TestClearerMain:
    def _wire(
        self, monkeypatch: pytest.MonkeyPatch, stdin_text: str, order: list[str]
    ) -> list[tuple[Backend, str]]:
        seen: list[tuple[Backend, str]] = []

        class Stdin(io.StringIO):
            def read(self, size: int | None = -1) -> str:
                order.append("read")
                return super().read(size)

        def fake_detect(platform: str, env: Mapping[str, str], which: object) -> Backend:
            order.append("detect")
            assert platform == sys.platform and env is os.environ
            return clipboard._PBCOPY

        def fake_clear(backend: Backend, expected: str, run: object = None) -> bool:
            order.append("clear")
            seen.append((backend, expected))
            return False

        monkeypatch.setattr("sys.stdin", Stdin(stdin_text))
        monkeypatch.setattr("time.sleep", lambda s: order.append(f"sleep {s}"))
        monkeypatch.setattr(clipboard, "detect", fake_detect)
        monkeypatch.setattr(clipboard, "clear_if_unchanged", fake_clear)
        return seen

    def test_reads_digest_first_then_sleeps_then_detects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        order: list[str] = []
        seen = self._wire(monkeypatch, SECRET_DIGEST, order)
        assert _clearer.main(["--after", "7"]) == 0
        assert order == ["read", "sleep 7", "detect", "clear"]
        assert seen == [(clipboard._PBCOPY, SECRET_DIGEST)]

    @pytest.mark.parametrize("framing", ["{}\n", "{}\r\n", "  {}  ", "\n{}\n\n"])
    def test_whitespace_around_the_digest_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch, framing: str
    ) -> None:
        seen = self._wire(monkeypatch, framing.format(SECRET_DIGEST), [])
        _clearer.main(["--after", "1"])
        assert seen[0][1] == SECRET_DIGEST

    def test_not_cleared_still_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch, SECRET_DIGEST, [])
        assert _clearer.main(["--after", "1"]) == 0

    @pytest.mark.parametrize("argv", [[], ["--after"], ["--after", "soon"], ["--after", "1.5"]])
    def test_bad_arguments_are_a_usage_error(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str]
    ) -> None:
        self._wire(monkeypatch, SECRET_DIGEST, [])
        with pytest.raises(SystemExit) as exc:
            _clearer.main(argv)
        assert exc.value.code == 2


# --------------------------------------------------------------------------- CLI, in process


class Harness:
    """Wire the CLI to the real copy() and spawn_clearer() with fake run and Popen."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, backend: Backend = clipboard._PBCOPY):
        self.events: list[str] = []
        self.environ_before = dict(os.environ)
        self.board = Board(backend)
        self.board.events = self.events
        self.popen = FakePopen(self.events)
        self.detect_calls: list[tuple[str, Mapping[str, str], object]] = []
        self.backend = backend
        self.unavailable: str | None = None

        def detect(platform: str, env: Mapping[str, str], which: object) -> Backend:
            self.detect_calls.append((platform, env, which))
            if self.unavailable is not None:
                raise ClipboardUnavailable(self.unavailable)
            return backend

        monkeypatch.setattr(clipboard, "detect", detect)
        monkeypatch.setattr(clipboard.copy, "__defaults__", (self.board,))
        monkeypatch.setattr(
            clipboard.spawn_clearer,
            "__kwdefaults__",
            {"platform": sys.platform, "popen": self.popen},
        )

    @property
    def copied(self) -> str:
        return self.board.content.decode("ascii")

    def assert_secret_only_on_copy_stdin(self, secret: str) -> None:
        for cmd, kwargs in self.board.calls:
            assert not any(secret in s for s in _strings_in(cmd))
            others = {k: v for k, v in kwargs.items() if k != "input"}
            assert not any(secret in s for s in _strings_in(others))
        for args, kwargs in self.popen.calls:
            assert not any(secret in s for s in _strings_in(args))
            assert not any(secret in s for s in _strings_in(kwargs))
        # Compared as a whole: a short secret can occur by chance inside an unrelated value
        # (a one-word passphrase inside "Program Files", say).
        assert dict(os.environ) == self.environ_before


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


class TestCliCopy:
    @pytest.mark.parametrize(
        "argv",
        [
            ["--copy"],
            ["-c"],
            ["password", "-c", "-l", "64"],
            ["password", "-c", "--no-symbols", "--exclude-ambiguous"],
            ["passphrase", "--copy"],
            ["passphrase", "-c", "-w", "2", "--sep", "."],
            ["passphrase", "-c", "--sep", " | "],
        ],
    )
    def test_full_copy_path_keeps_the_secret_off_argv_and_out_of_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        argv: list[str],
    ) -> None:
        h = Harness(monkeypatch)
        code, out, err = run(capsys, *argv)
        assert code == 0 and out == ""
        secret = h.copied
        assert secret and secret not in err
        assert h.events == ["copy", "spawn"]
        assert h.popen.stdin_bytes == clipboard.digest(secret).encode()
        assert clipboard.digest(secret) not in err
        h.assert_secret_only_on_copy_stdin(secret)

    def test_detect_gets_the_real_platform_env_and_which(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h = Harness(monkeypatch)
        run(capsys, "-c")
        [(platform, env, which)] = h.detect_calls
        assert platform == sys.platform and env is os.environ
        assert which is shutil.which

    def test_combined_short_flags(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h = Harness(monkeypatch)
        code, out, err = run(capsys, "password", "-qc")
        assert (code, out, err) == (0, "", "") and h.copied

    @pytest.mark.parametrize("command", ["password", "passphrase"])
    def test_quiet_success_prints_nothing_at_all(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        command: str,
    ) -> None:
        h = Harness(monkeypatch)
        assert run(capsys, command, "-c", "-q", "--clear-after", "0") == (0, "", "")
        assert h.events == ["copy"]

    def test_quiet_still_prints_errors(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h = Harness(monkeypatch)
        h.unavailable = "install xclip or xsel to use the clipboard"
        code, out, err = run(capsys, "password", "-c", "-q")
        assert code == 1 and out == ""
        assert err == "entropass: error: install xclip or xsel to use the clipboard\n"

    @pytest.mark.parametrize(
        "failure",
        [
            subprocess.CompletedProcess(["pbcopy"], 1),
            subprocess.TimeoutExpired(["pbcopy"], 10),
            FileNotFoundError(2, "No such file", "pbcopy"),
            PermissionError(13, "denied"),
        ],
        ids=["status-1", "timeout", "missing", "permission"],
    )
    @pytest.mark.parametrize("quiet", [False, True], ids=["loud", "quiet"])
    def test_copy_failure_exits_1_without_secret_and_without_clearer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        failure: object,
        quiet: bool,
    ) -> None:
        h = Harness(monkeypatch)
        seen: list[bytes] = []

        def broken(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            seen.append(kwargs["input"])
            if isinstance(failure, BaseException):
                raise failure
            assert isinstance(failure, subprocess.CompletedProcess)
            return failure

        monkeypatch.setattr(clipboard.copy, "__defaults__", (broken,))
        argv = ["passphrase", "-c", *(["-q"] if quiet else [])]
        code, out, err = run(capsys, *argv)
        [secret] = seen
        assert code == 1 and out == ""
        assert err.startswith("entropass: error: could not copy with pbcopy")
        assert secret.decode() not in err and "Traceback" not in err
        assert "bits" not in err and "Copied" not in err
        assert h.popen.calls == []

    def test_unavailable_never_generates_output_or_spawns(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h = Harness(monkeypatch)
        h.unavailable = "there is no graphical session, so no clipboard"
        code, out, err = run(capsys, "passphrase", "--copy", "--clear-after", "5")
        assert code == 1 and out == ""
        assert h.board.calls == [] and h.popen.calls == []
        assert err.count("\n") == 1  # one line: the error, nothing else

    @pytest.mark.parametrize("value", ["0", "1", "86400", "00", "+5"])
    def test_clear_after_accepted_values(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        value: str,
    ) -> None:
        h = Harness(monkeypatch)
        code, _, err = run(capsys, "-c", "--clear-after", value)
        assert code == 0
        seconds = int(value)
        if seconds == 0:
            assert h.events == ["copy"] and "will not be cleared" in err
        else:
            [(args, _)] = h.popen.calls
            assert args[-2:] == ["--after", str(seconds)]
            assert f"clears in {seconds} s" in err

    @pytest.mark.parametrize("value", ["-1", "86401", "1.5", "", "30s", "1e3", "inf", "0x10"])
    def test_clear_after_rejected_values_exit_2_before_anything_runs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        value: str,
    ) -> None:
        h = Harness(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            main(["passphrase", "-c", f"--clear-after={value}"])
        assert exc.value.code == 2
        out, err = capsys.readouterr()
        assert out == "" and "--clear-after" in err
        assert h.events == []

    def test_clear_after_without_copy_is_a_usage_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h = Harness(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            main(["password", "--clear-after", "5"])
        out, err = capsys.readouterr()
        assert exc.value.code == 2 and out == ""
        assert "--clear-after only applies with --copy" in err
        assert h.detect_calls == [] and h.events == []

    def test_default_delay_is_30(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h = Harness(monkeypatch)
        run(capsys, "-c")
        [(args, _)] = h.popen.calls
        assert args[-2:] == ["--after", "30"]

    def test_each_run_copies_a_fresh_secret(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h = Harness(monkeypatch)
        run(capsys, "-c", "-q")
        first = h.copied
        run(capsys, "-c", "-q")
        assert h.copied != first

    def test_clearer_that_cannot_start_is_reported_without_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The copy worked, but the clearer could not start (python moved by an upgrade,
        # fork limit, ...). The user should learn the clipboard will NOT be cleared.
        h = Harness(monkeypatch)

        def cannot_start(args: list[str], **kwargs: Any) -> FakeProcess:
            raise FileNotFoundError(2, "No such file or directory", args[0])

        monkeypatch.setattr(
            clipboard.spawn_clearer,
            "__kwdefaults__",
            {"platform": sys.platform, "popen": cannot_start},
        )
        try:
            code = main(["passphrase", "-c"])
        except OSError as exc:
            pytest.fail(f"main() raised {exc!r} instead of reporting it")
        out, err = capsys.readouterr()
        assert out == "" and h.copied not in err
        assert code == 1 and "entropass:" in err and "clear" in err

    def test_clearer_dying_before_reading_is_not_mistaken_for_a_closed_stdout(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # BrokenPipeError from the clearer's stdin reaches main()'s closed-stdout handler,
        # which then exits 1 silently and points stdout at /dev/null.
        h = Harness(monkeypatch)
        dup2_calls: list[tuple[int, int]] = []

        class DeadStdin(io.BytesIO):
            def write(self, data: Any, /) -> int:
                raise BrokenPipeError(32, "Broken pipe")

        class DeadProcess(FakeProcess):
            def __init__(self) -> None:
                self.stdin = DeadStdin()

        def popen(args: list[str], **kwargs: Any) -> DeadProcess:
            return DeadProcess()

        monkeypatch.setattr(
            clipboard.spawn_clearer, "__kwdefaults__", {"platform": sys.platform, "popen": popen}
        )
        monkeypatch.setattr(os, "dup2", lambda a, b: dup2_calls.append((a, b)))

        class Stdout(io.StringIO):
            def fileno(self) -> int:
                return 1

        monkeypatch.setattr("sys.stdout", Stdout())
        code = main(["passphrase", "-c"])
        err = capsys.readouterr().err
        assert h.copied not in err
        assert dup2_calls == [], "stdout was redirected although stdout was never closed"
        assert code == 1 and "clear" in err


class TestCliCopyWithDice:
    ROLLS = ("11111", "6 6 6 6 6", "35142", "2 4 6 1 3")

    def _expected(self, sep: str = " ") -> str:
        words = load_wordlist()
        return sep.join(words[parse_roll(r)] for r in self.ROLLS)

    def test_piped_dice_are_copied_not_printed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h = Harness(monkeypatch)
        monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(self.ROLLS) + "\n"))
        code, out, err = run(capsys, "passphrase", "--dice", "-w", "4", "-c", "--sep", ".")
        assert code == 0 and out == ""
        assert h.copied == self._expected(".")
        assert h.popen.stdin_bytes == clipboard.digest(self._expected(".")).encode()
        assert "assuming fair dice" in err and h.copied not in err
        for roll in self.ROLLS:
            assert roll not in err
            h.assert_secret_only_on_copy_stdin(roll)
        h.assert_secret_only_on_copy_stdin(h.copied)

    def test_clearer_stdin_is_its_own_pipe_not_the_dice_input(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h = Harness(monkeypatch)
        monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(self.ROLLS)))
        run(capsys, "passphrase", "--dice", "-w", "4", "-c")
        [(_, kwargs)] = h.popen.calls
        assert kwargs["stdin"] == subprocess.PIPE

    def test_bad_rolls_with_copy_never_touch_the_clipboard(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h = Harness(monkeypatch)
        monkeypatch.setattr("sys.stdin", io.StringIO("11111\n77777\n"))
        code, out, err = run(capsys, "passphrase", "--dice", "-w", "2", "-c")
        assert code == 1 and out == "" and "77777" not in err
        assert h.detect_calls == [] and h.events == []

    def test_dice_on_a_terminal_with_quiet_copy(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        class Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        h = Harness(monkeypatch)
        rolls = iter(self.ROLLS)
        monkeypatch.setattr("sys.stdin", Tty())
        monkeypatch.setattr("entropass.cli.getpass.getpass", lambda prompt="": next(rolls))
        assert run(capsys, "passphrase", "--dice", "-w", "4", "-c", "-q") == (0, "", "")
        assert h.copied == self._expected()


# --------------------------------------------------------------------------- real processes

FAKE_TOOL = """#!/bin/sh
dir=${0%/*}
name=${0##*/}
printf '%s %s\\n' "$name" "$*" >> "$dir/log"
case "$name $*" in
  "pbpaste "|"xclip -selection clipboard -o"|"xsel --clipboard --output"|\\
  "wl-paste --no-newline") /bin/cat "$dir/board" ;;
  "wl-copy --clear"|"xsel --clipboard --clear") : > "$dir/board" ;;
  *) /bin/cat > "$dir/board" ;;
esac
"""


class FakeTools:
    """Fake clipboard tools on a PATH of their own, backed by the file ``board``."""

    def __init__(self, root: Path) -> None:
        self.bin = root / "bin"
        self.bin.mkdir()
        tool = self.bin / "fake-tool"
        tool.write_text(FAKE_TOOL)
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        for name in ("pbcopy", "pbpaste", "xclip", "xsel", "wl-copy", "wl-paste"):
            (self.bin / name).symlink_to(tool)
        self.board.write_bytes(b"")
        self.home = root / "home"
        self.home.mkdir()

    @property
    def board(self) -> Path:
        return self.bin / "board"

    @property
    def log(self) -> str:
        path = self.bin / "log"
        return path.read_text() if path.exists() else ""

    def env(self) -> dict[str, str]:
        # PATH holds only the fakes, so no real clipboard tool can be found. DISPLAY makes
        # Linux pick the fake xclip; macOS picks the fake pbcopy.
        return {"PATH": str(self.bin), "HOME": str(self.home), "DISPLAY": ":99"}


@pytest.fixture
def tools(tmp_path: Path) -> FakeTools:
    fake = FakeTools(tmp_path)
    # Belt and braces: make sure detection in a child would resolve to the fakes.
    for name in ("pbcopy", "pbpaste", "xclip"):
        assert shutil.which(name, path=str(fake.bin)) == str(fake.bin / name)
    return fake


def _wait_for(condition: Callable[[], bool], timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


@POSIX_ONLY
class TestRealClearerProcess:
    def _spawn(self, tools: FakeTools, after: int, digest: str, cwd: Path) -> int:
        procs: list[subprocess.Popen[bytes]] = []

        def popen(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
            # spawn_clearer sets cwd to the installed package; the test's cwd is unused.
            proc = subprocess.Popen(args, env=tools.env(), **kwargs)
            procs.append(proc)
            return proc

        clipboard.spawn_clearer(after, digest, popen=popen)
        [proc] = procs
        return proc.wait(timeout=30)

    def test_clears_when_unchanged(self, tools: FakeTools, tmp_path: Path) -> None:
        tools.board.write_bytes(SECRET.encode())
        assert self._spawn(tools, 1, SECRET_DIGEST, tmp_path) == 0
        assert tools.board.read_bytes() == b""
        assert SECRET not in tools.log and SECRET_DIGEST not in tools.log

    def test_leaves_new_content_alone(self, tools: FakeTools, tmp_path: Path) -> None:
        tools.board.write_bytes(b"something the user copied later")
        assert self._spawn(tools, 0, SECRET_DIGEST, tmp_path) == 0
        assert tools.board.read_bytes() == b"something the user copied later"
        assert "paste" in tools.log or "-o" in tools.log

    def test_no_clipboard_exits_1(self, tools: FakeTools, tmp_path: Path) -> None:
        for name in ("pbcopy", "pbpaste", "xclip", "xsel", "wl-copy", "wl-paste"):
            (tools.bin / name).unlink()
        assert self._spawn(tools, 0, SECRET_DIGEST, tmp_path) == 1


@POSIX_ONLY
class TestRealCli:
    def _cli(self, tools: FakeTools, cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "entropass", *argv],
            env=tools.env(),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_copy_then_guarded_clear_end_to_end(self, tools: FakeTools, tmp_path: Path) -> None:
        result = self._cli(tools, tmp_path, "passphrase", "--copy", "--clear-after", "2")
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        secret = tools.board.read_text()
        assert len(secret.split(" ")) == 6
        assert secret not in result.stderr
        assert _wait_for(lambda: tools.board.read_bytes() == b""), "clipboard never cleared"
        assert secret not in tools.log and clipboard.digest(secret) not in tools.log
        assert "Traceback" not in result.stderr

    def test_new_copy_survives_the_clearer(self, tools: FakeTools, tmp_path: Path) -> None:
        result = self._cli(tools, tmp_path, "password", "-c", "-q", "--clear-after", "1")
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
        tools.board.write_bytes(b"user's own text")
        # Wait until the clearer has pasted (it logs), then a little longer.
        assert _wait_for(lambda: tools.log.count("\n") >= 2)
        time.sleep(0.5)
        assert tools.board.read_bytes() == b"user's own text"

    def test_no_clipboard_tools_fails_cleanly(self, tools: FakeTools, tmp_path: Path) -> None:
        for name in ("pbcopy", "pbpaste", "xclip", "xsel", "wl-copy", "wl-paste"):
            (tools.bin / name).unlink()
        result = self._cli(tools, tmp_path, "password", "--copy")
        assert result.returncode == 1 and result.stdout == ""
        assert result.stderr.startswith("entropass: error:")
        assert "Traceback" not in result.stderr

    def test_forking_tool_does_not_hang_copy(self, tools: FakeTools) -> None:
        # Like xclip and wl-copy: the tool forks a child that keeps running.
        forker = tools.bin / "forker"
        pidfile = tools.bin / "child.pid"
        forker.write_text(
            f"#!/bin/sh\n/bin/cat > /dev/null\n/bin/sleep 30 &\necho $! > {pidfile}\nexit 0\n"
        )
        forker.chmod(0o755)
        backend = Backend("forker", (str(forker),), (str(forker),))
        start = time.monotonic()
        try:
            clipboard.copy(backend, SECRET, run=subprocess.run)
            assert time.monotonic() - start < 5
        finally:
            if pidfile.exists():
                os.kill(int(pidfile.read_text()), 15)

    def test_hanging_tool_times_out(
        self, tools: FakeTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hanger = tools.bin / "hanger"
        hanger.write_text("#!/bin/sh\nexec /bin/sleep 30\n")
        hanger.chmod(0o755)
        monkeypatch.setattr(clipboard, "TIMEOUT", 1)
        backend = Backend("hanger", (str(hanger),), (str(hanger),))
        start = time.monotonic()
        with pytest.raises(ClipboardUnavailable, match="hanger"):
            clipboard.copy(backend, SECRET, run=subprocess.run)
        assert clipboard.read(backend, run=subprocess.run) is None
        assert time.monotonic() - start < 10

    def test_clearer_cannot_be_shadowed_by_the_working_directory(
        self, tools: FakeTools, tmp_path: Path
    ) -> None:
        # `python -m entropass._clearer` puts the current directory first on sys.path, so an
        # `entropass/` package in the directory where the user runs the CLI is what runs.
        script = Path(sys.executable).parent / "entropass"
        if not script.exists():
            pytest.skip("console script not installed next to the interpreter")
        workdir = tmp_path / "work"
        shadow = workdir / "entropass"
        shadow.mkdir(parents=True)
        marker = tmp_path / "shadow-ran"
        (shadow / "__init__.py").write_text("")
        (shadow / "_clearer.py").write_text(
            f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\n"
        )
        result = subprocess.run(
            [str(script), "password", "-c", "-q", "--clear-after", "1"],
            env=tools.env(),
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        _wait_for(lambda: marker.exists() or tools.board.read_bytes() == b"")
        assert not marker.exists(), "a module from the working directory ran as the clearer"
        assert tools.board.read_bytes() == b""
