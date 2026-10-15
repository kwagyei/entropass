# entropass

[![CI](https://github.com/kwagyei/entropass/actions/workflows/ci.yml/badge.svg)](https://github.com/kwagyei/entropass/actions/workflows/ci.yml)

A password and passphrase generator for the command line that tells you, honestly and in
bits, how strong what it made actually is.

> **Work in progress.** This is the project scaffold: packaging, linting, type-checking and
> a CI matrix across Python 3.10–3.14 on Linux, macOS and Windows. The generators land in
> the next pull requests; this README only describes what exists on `main`.

## Planned

- Passwords from chosen character classes, with **exact** entropy when every class is
  required (counted by inclusion–exclusion, not the naive `length × log2(alphabet)`).
- EFF diceware passphrases, including from your own physical dice rolls.
- Copy to the clipboard and clear it afterwards, only if it still holds the secret.
- Randomness from `secrets`, never `random`. No runtime dependencies.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install --upgrade pip          # dependency groups need pip >= 25.1
.venv/bin/pip install --group dev
.venv/bin/pip install .
.venv/bin/pytest --cov
.venv/bin/ruff check && .venv/bin/ruff format --check && .venv/bin/mypy
```

## Licence

MIT — see [LICENSE](https://github.com/kwagyei/entropass/blob/main/LICENSE).
