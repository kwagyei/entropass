"""Clipboard copy through the operating system's own tools, and a guarded clear.

No dependencies: each platform already ships a command-line clipboard tool. The secret
only ever reaches those tools on stdin, never in argv, where other users could read it.
"""

from __future__ import annotations

import hashlib
import hmac
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Run = Callable[..., "subprocess.CompletedProcess[bytes]"]
Which = Callable[[str], "str | None"]

# The directory that contains the installed `entropass` package (site-packages).
PACKAGE_ROOT = str(Path(__file__).resolve().parent.parent)

TIMEOUT = 10  # seconds; a clipboard tool that hangs longer than this has failed

# Windows process-creation flags, spelled out because subprocess only defines them there.
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


class ClipboardUnavailable(Exception):
    """There is no usable clipboard, or the copy failed. The message says why."""


@dataclass(frozen=True)
class Backend:
    name: str
    copy_cmd: tuple[str, ...]
    paste_cmd: tuple[str, ...]
    # Command that empties the clipboard; None means "run copy_cmd with empty input".
    clear_cmd: tuple[str, ...] | None = None


_PBCOPY = Backend("pbcopy", ("pbcopy",), ("pbpaste",))
_WINDOWS = Backend(
    "clip", ("clip",), ("powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard")
)
_WL_COPY = Backend(
    "wl-copy", ("wl-copy",), ("wl-paste", "--no-newline"), clear_cmd=("wl-copy", "--clear")
)
_XCLIP = Backend(
    "xclip", ("xclip", "-selection", "clipboard"), ("xclip", "-selection", "clipboard", "-o")
)
_XSEL = Backend(
    "xsel",
    ("xsel", "--clipboard", "--input"),
    ("xsel", "--clipboard", "--output"),
    clear_cmd=("xsel", "--clipboard", "--clear"),
)


def detect(platform: str, env: Mapping[str, str], which: Which) -> Backend:
    """Choose the clipboard tool for this platform and session, or say what is missing."""

    def available(backend: Backend) -> bool:
        return all(which(cmd[0]) for cmd in (backend.copy_cmd, backend.paste_cmd))

    if platform == "darwin":
        if available(_PBCOPY):
            return _PBCOPY
        raise ClipboardUnavailable("pbcopy and pbpaste were not found")
    if platform == "win32":
        if available(_WINDOWS):
            return _WINDOWS
        raise ClipboardUnavailable("clip and powershell are needed to use the clipboard")
    if platform.startswith("linux") or "bsd" in platform:
        wayland, x11 = bool(env.get("WAYLAND_DISPLAY")), bool(env.get("DISPLAY"))
        if not (wayland or x11):
            raise ClipboardUnavailable("there is no graphical session, so no clipboard")
        if wayland and available(_WL_COPY):
            return _WL_COPY
        if x11:
            for backend in (_XCLIP, _XSEL):
                if available(backend):
                    return backend
        if wayland and x11:
            raise ClipboardUnavailable("install wl-clipboard, xclip or xsel to use the clipboard")
        if wayland:
            raise ClipboardUnavailable("install wl-clipboard to use the clipboard")
        raise ClipboardUnavailable("install xclip or xsel to use the clipboard")
    raise ClipboardUnavailable(f"the clipboard is not supported on {platform}")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _no_window() -> dict[str, Any]:
    # Keep clip and powershell from flashing a console window from the detached clearer.
    return {"creationflags": _CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def copy(backend: Backend, secret: str, run: Run = subprocess.run) -> None:
    """Put ``secret`` on the clipboard, passing it on stdin."""
    try:
        result = run(
            list(backend.copy_cmd),
            input=secret.encode("ascii"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT,
            check=False,
            **_no_window(),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ClipboardUnavailable(f"could not copy with {backend.name}") from None
    if result.returncode != 0:
        raise ClipboardUnavailable(f"could not copy with {backend.name}")


def read(backend: Backend, run: Run = subprocess.run) -> str | None:
    """The clipboard's text, or None if it cannot be read."""
    try:
        result = run(
            list(backend.paste_cmd),
            capture_output=True,
            timeout=TIMEOUT,
            check=False,
            **_no_window(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    # Paste tools may add a line ending (PowerShell adds CRLF); secrets never end in one.
    return result.stdout.decode("utf-8", errors="replace").rstrip("\r\n")


def clear_if_unchanged(backend: Backend, expected: str, run: Run = subprocess.run) -> bool:
    """Empty the clipboard only if it still holds the secret whose digest is ``expected``.

    Anything the user copied since is left alone, and so is a clipboard that cannot be
    read. Returns whether the clipboard was cleared.
    """
    current = read(backend, run=run)
    if current is None or not hmac.compare_digest(digest(current), expected):
        return False
    try:
        result = run(
            list(backend.clear_cmd or backend.copy_cmd),
            input=b"",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT,
            check=False,
            **_no_window(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def spawn_clearer(
    after: int,
    secret_digest: str,
    *,
    platform: str = sys.platform,
    popen: Callable[..., Any] = subprocess.Popen,
) -> None:
    """Start a detached process that clears the clipboard after ``after`` seconds.

    It receives only the digest of the secret, on stdin, and is detached so that it
    survives the terminal closing while the CLI returns at once. Raises OSError if it
    cannot be started.

    It runs from the installed package's own directory, not the user's: `python -m`
    puts the working directory first on sys.path, so a `time.py` or `entropass/` where
    the user happened to run the command would otherwise be imported by the clearer.
    """
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": PACKAGE_ROOT,
    }
    if platform == "win32":
        # A hidden console of its own, apart from the terminal's. DETACHED_PROCESS would
        # leave a venv's python.exe redirector without one, and its child would open a
        # visible console window.
        kwargs["creationflags"] = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = popen([sys.executable, "-m", "entropass._clearer", "--after", str(after)], **kwargs)
    process.stdin.write(secret_digest.encode("ascii"))
    process.stdin.close()
