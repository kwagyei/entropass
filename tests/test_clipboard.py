"""Clipboard backends, guarded clearing and the detached clearer.

Every OS tool is faked: these tests run on any platform and never touch the real
clipboard. The test marked `clipboard` at the end uses the real one, and only runs when
ENTROPASS_CLIPBOARD_TESTS=1.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from typing import Any

import pytest

from entropass import _clearer, clipboard
from entropass.clipboard import (
    Backend,
    ClipboardUnavailable,
    clear_if_unchanged,
    copy,
    detect,
    digest,
    spawn_clearer,
)

SECRET = "correct horse battery staple"
SECRET_DIGEST = hashlib.sha256(SECRET.encode()).hexdigest()


def which_of(*found: str) -> Any:
    return lambda name: f"/usr/bin/{name}" if name in found else None


class TestDetect:
    def test_macos(self) -> None:
        backend = detect("darwin", {}, which_of("pbcopy", "pbpaste"))
        assert backend.copy_cmd == ("pbcopy",)
        assert backend.paste_cmd == ("pbpaste",)

    def test_windows(self) -> None:
        backend = detect("win32", {}, which_of("clip", "powershell"))
        assert backend.copy_cmd == ("clip",)
        assert backend.paste_cmd[0] == "powershell" and "Get-Clipboard" in backend.paste_cmd

    def test_wayland(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
        backend = detect("linux", env, which_of("wl-copy", "wl-paste", "xclip"))
        assert backend.copy_cmd == ("wl-copy",)
        assert backend.paste_cmd == ("wl-paste", "--no-newline")
        assert backend.clear_cmd == ("wl-copy", "--clear")

    def test_wayland_without_wl_clipboard_falls_back_to_xwayland(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
        backend = detect("linux", env, which_of("xclip"))
        assert backend.copy_cmd == ("xclip", "-selection", "clipboard")

    def test_x11_xclip(self) -> None:
        backend = detect("linux", {"DISPLAY": ":0"}, which_of("xclip", "xsel"))
        assert backend.copy_cmd == ("xclip", "-selection", "clipboard")
        assert backend.paste_cmd == ("xclip", "-selection", "clipboard", "-o")

    def test_x11_xsel(self) -> None:
        backend = detect("linux", {"DISPLAY": ":0"}, which_of("xsel"))
        assert backend.copy_cmd == ("xsel", "--clipboard", "--input")
        assert backend.clear_cmd == ("xsel", "--clipboard", "--clear")

    @pytest.mark.parametrize(
        ("platform", "env", "found", "hint"),
        [
            ("linux", {"DISPLAY": ":0"}, (), "xclip"),
            ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, (), "wl-clipboard"),
            ("linux", {}, ("xclip", "wl-copy"), "graphical session"),
            ("darwin", {}, (), "pbcopy"),
            ("win32", {}, ("clip",), "powershell"),
            ("sunos5", {}, (), "not supported"),
        ],
    )
    def test_unavailable_says_why(
        self, platform: str, env: dict[str, str], found: Sequence[str], hint: str
    ) -> None:
        with pytest.raises(ClipboardUnavailable, match=hint):
            detect(platform, env, which_of(*found))


class FakeRun:
    """Records each command and plays back the clipboard's contents."""

    def __init__(self, pasted: bytes = b"", returncode: int = 0, raises: Exception | None = None):
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.pasted = pasted
        self.returncode = returncode
        self.raises = raises

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((cmd, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.pasted, stderr=b"")


MAC = Backend("pbcopy", copy_cmd=("pbcopy",), paste_cmd=("pbpaste",))
WAYLAND = Backend(
    "wl-copy",
    copy_cmd=("wl-copy",),
    paste_cmd=("wl-paste", "--no-newline"),
    clear_cmd=("wl-copy", "--clear"),
)


class TestCopy:
    def test_secret_goes_on_stdin_never_in_argv(self) -> None:
        run = FakeRun()
        copy(MAC, SECRET, run=run)
        [(cmd, kwargs)] = run.calls
        assert cmd == ["pbcopy"]
        assert kwargs["input"] == SECRET.encode()
        assert all(SECRET not in part for part in cmd)
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL

    @pytest.mark.parametrize(
        "run",
        [
            FakeRun(returncode=1),
            FakeRun(raises=FileNotFoundError("pbcopy")),
            FakeRun(raises=subprocess.TimeoutExpired("pbcopy", 10)),
        ],
        ids=["fails", "missing", "hangs"],
    )
    def test_failure_is_reported(self, run: FakeRun) -> None:
        with pytest.raises(ClipboardUnavailable, match="could not copy"):
            copy(MAC, SECRET, run=run)


class TestClearIfUnchanged:
    def test_clears_when_the_clipboard_still_holds_the_secret(self) -> None:
        run = FakeRun(pasted=SECRET.encode())
        assert clear_if_unchanged(MAC, SECRET_DIGEST, run=run) is True
        (paste, _), (clear, kwargs) = run.calls
        assert paste == ["pbpaste"]
        assert clear == ["pbcopy"] and kwargs["input"] == b""

    def test_uses_the_backends_own_clear_command(self) -> None:
        run = FakeRun(pasted=SECRET.encode())
        assert clear_if_unchanged(WAYLAND, SECRET_DIGEST, run=run) is True
        assert run.calls[-1][0] == ["wl-copy", "--clear"]

    def test_trailing_newline_from_the_paste_tool_still_matches(self) -> None:
        # PowerShell's Get-Clipboard ends its output with CRLF.
        run = FakeRun(pasted=SECRET.encode() + b"\r\n")
        assert clear_if_unchanged(MAC, SECRET_DIGEST, run=run) is True

    def test_leaves_something_else_alone(self) -> None:
        run = FakeRun(pasted=b"the user copied this since")
        assert clear_if_unchanged(MAC, SECRET_DIGEST, run=run) is False
        assert len(run.calls) == 1

    @pytest.mark.parametrize(
        "run",
        [FakeRun(pasted=SECRET.encode(), returncode=1), FakeRun(raises=OSError("gone"))],
        ids=["paste-fails", "paste-missing"],
    )
    def test_does_nothing_if_the_clipboard_cannot_be_read(self, run: FakeRun) -> None:
        assert clear_if_unchanged(MAC, SECRET_DIGEST, run=run) is False
        assert len(run.calls) == 1


class ClearFails(FakeRun):
    """Pasting works; the clearing command then fails to run."""

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if self.calls:
            self.calls.append((cmd, kwargs))
            raise OSError("pbcopy vanished")
        return super().__call__(cmd, **kwargs)


def test_a_clear_command_that_fails_reports_not_cleared() -> None:
    run = ClearFails(pasted=SECRET.encode())
    assert clear_if_unchanged(MAC, SECRET_DIGEST, run=run) is False
    assert len(run.calls) == 2


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.closed_with = b""

    def close_stdin(self) -> None:
        self.closed_with = self.stdin.getvalue()


class FakePopen:
    def __init__(self) -> None:
        self.args: list[str] = []
        self.kwargs: dict[str, Any] = {}
        self.process = FakeProcess()
        self.process.stdin.close = self.process.close_stdin  # type: ignore[method-assign]

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
        self.args, self.kwargs = args, kwargs
        return self.process


class TestSpawnClearer:
    def test_only_the_digest_reaches_the_clearer(self) -> None:
        popen = FakePopen()
        spawn_clearer(30, digest(SECRET), platform="darwin", popen=popen)
        assert popen.args == [sys.executable, "-m", "entropass._clearer", "--after", "30"]
        assert all(SECRET not in a and SECRET_DIGEST not in a for a in popen.args)
        assert popen.process.closed_with == SECRET_DIGEST.encode()
        assert popen.kwargs["stdin"] == subprocess.PIPE
        assert popen.kwargs["stdout"] == popen.kwargs["stderr"] == subprocess.DEVNULL

    def test_posix_clearer_outlives_the_terminal(self) -> None:
        popen = FakePopen()
        spawn_clearer(5, SECRET_DIGEST, platform="linux", popen=popen)
        assert popen.kwargs["start_new_session"] is True
        assert "creationflags" not in popen.kwargs

    def test_windows_clearer_is_detached(self) -> None:
        popen = FakePopen()
        spawn_clearer(5, SECRET_DIGEST, platform="win32", popen=popen)
        detached, new_group = 0x00000008, 0x00000200
        assert popen.kwargs["creationflags"] == detached | new_group
        assert "start_new_session" not in popen.kwargs


def test_digest_is_sha256_hex() -> None:
    assert digest(SECRET) == SECRET_DIGEST


class TestClearerProcess:
    def test_waits_then_clears_if_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        slept: list[float] = []
        seen: list[tuple[Backend, str]] = []

        def fake_clear(backend: Backend, expected: str, run: object = None) -> bool:
            seen.append((backend, expected))
            return True

        monkeypatch.setattr("sys.stdin", io.StringIO(SECRET_DIGEST + "\n"))
        monkeypatch.setattr("time.sleep", slept.append)
        monkeypatch.setattr(clipboard, "detect", lambda *a: MAC)
        monkeypatch.setattr(clipboard, "clear_if_unchanged", fake_clear)
        assert _clearer.main(["--after", "30"]) == 0
        assert slept == [30]
        assert seen == [(MAC, SECRET_DIGEST)]

    def test_no_clipboard_means_nothing_to_do(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(SECRET_DIGEST))
        monkeypatch.setattr("time.sleep", lambda s: None)

        def unavailable(*args: object) -> Backend:
            raise ClipboardUnavailable("none")

        monkeypatch.setattr(clipboard, "detect", unavailable)
        assert _clearer.main(["--after", "1"]) == 1


@pytest.mark.clipboard
@pytest.mark.skipif(
    os.environ.get("ENTROPASS_CLIPBOARD_TESTS") != "1",
    reason="uses the real clipboard; set ENTROPASS_CLIPBOARD_TESTS=1",
)
def test_real_clipboard_round_trip() -> None:
    backend = clipboard.detect(sys.platform, os.environ, shutil.which)
    copy(backend, SECRET)
    spawn_clearer(1, digest(SECRET))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        pasted = clipboard.read(backend)
        if pasted == "":
            break
        time.sleep(0.25)
    assert clipboard.read(backend) == ""
