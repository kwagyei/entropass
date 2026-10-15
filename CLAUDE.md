# entropass

Password and passphrase generator CLI, published to PyPI. Public repo:
github.com/kwagyei/entropass.

Project notes: ~/Vault/Projects/code-projects/

## Stack

- Python, `requires-python >=3.10`; CI matrix 3.10–3.14 × Linux/macOS/Windows
- src layout (`src/entropass/`), hatchling build, no runtime dependencies
- stdlib `argparse`; dev tools in `[dependency-groups] dev` (pip >= 25.1)

## Commands

```sh
python3.13 -m venv .venv && .venv/bin/pip install --upgrade pip
.venv/bin/pip install --group dev && .venv/bin/pip install .   # reinstall after src changes
.venv/bin/pytest --cov          # coverage floor 90%, branch coverage
.venv/bin/ruff check && .venv/bin/ruff format --check && .venv/bin/mypy
.venv/bin/python -m build && .venv/bin/twine check --strict dist/*
```

Tests run against the installed package, not the checkout, so a file missing from the
wheel fails the tests. Reinstall with `pip install .` after changing `src/`.

## Conventions

- Randomness only from `secrets`. Never import `random` in `src/` (a test enforces it
  from PR 2); tests may inject a seeded `randbelow`.
- Pure functions take their dependencies as parameters (`randbelow`, `run`, `platform`,
  `env`, `which`). `cli.py` is the only module that prints.
- stdout carries only the secret; the entropy line goes to stderr. Secrets never go in
  argv or environment variables.
- Type hints everywhere; mypy strict covers `src` and `tests`.
- Entropy shown is floored to 0.1 bit and must equal log2 of the exact number of outcomes.

## Settled decisions

- Exact entropy by inclusion–exclusion; uniform generation by rejection sampling.
- EFF large wordlist bundled verbatim (`src/entropass/data/`, `.gitattributes` keeps its
  bytes) with a SHA-256 pin; separators may not contain any word character, default a
  space (four words contain hyphens). Licence metadata `"MIT AND CC-BY-4.0"`: EFF licenses
  its original material CC BY 4.0 (eff.org/copyright).
- Exit codes: 0 ok, 1 bad run-time input (dice), no clipboard or closed pipe, 130 Ctrl-C,
  2 bad options.
- With `--copy` the secret is never printed, even if copying fails.
- Clipboard tests fake every OS tool; the one real round trip (`-m clipboard`) runs only
  with `ENTROPASS_CLIPBOARD_TESTS=1`, set on the macOS CI runners. Don't run it on
  Kwabena's Mac: it overwrites his clipboard.
- Clipboard via OS tools on stdin; a detached `python -m entropass._clearer` holds only a
  SHA-256 digest and clears only if the clipboard is unchanged.
- Releases: `v*` tag → TestPyPI → PyPI after manual approval, Trusted Publishing, no tokens.
