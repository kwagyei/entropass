"""Detached process that clears the clipboard later, if it still holds the secret.

Started by ``entropass --copy`` as ``python -m entropass._clearer --after N``. It reads the
SHA-256 digest of the secret on stdin, never the secret itself.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from collections.abc import Sequence

from entropass import clipboard


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="entropass._clearer")
    parser.add_argument("--after", type=int, required=True)
    args = parser.parse_args(argv)
    expected = sys.stdin.read().strip()

    time.sleep(args.after)
    try:
        backend = clipboard.detect(sys.platform, os.environ, shutil.which)
    except clipboard.ClipboardUnavailable:
        return 1
    clipboard.clear_if_unchanged(backend, expected)
    return 0


if __name__ == "__main__":
    sys.exit(main())
