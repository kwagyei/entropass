# entropass

[![CI](https://github.com/kwagyei/entropass/actions/workflows/ci.yml/badge.svg)](https://github.com/kwagyei/entropass/actions/workflows/ci.yml)

A password and passphrase generator for the command line that tells you, honestly and in
bits, how strong what it made actually is.

> **Work in progress.** Password generation works today. Passphrases, clipboard copy and
> the PyPI release land in the next pull requests; this README only describes what exists
> on `main`.

## Usage

```console
$ entropass
k9]Qe#vT2m!Rz_4pLw8N
130.9 bits of entropy: 20 characters from a 94-symbol alphabet, with at least one each of lower, upper, digits, symbols

$ entropass password -l 16 --no-symbols
oAPDXDd6OP7shVL9
95.1 bits of entropy: 16 characters from a 62-symbol alphabet, with at least one each of lower, upper, digits

$ entropass password --symbols='!#-_' --exclude-ambiguous -q
Y-HhEw6UmWqYkFcxtD!P
```

The password is the only thing on stdout; the entropy line goes to stderr. So
`pw=$(entropass)` captures exactly the password, and you still see its strength. (A pipe
such as `entropass | pbcopy` also gets the trailing newline.)

| Option | Default | |
|---|---|---|
| `-l`, `--length N` | 20 | 1 to 1024 |
| `--no-lower`, `--no-upper`, `--no-digits`, `--no-symbols` | all on | |
| `--symbols CHARS` | all ASCII punctuation | for sites that only accept some symbols; write `--symbols=CHARS` if they start with `-` |
| `--exclude-ambiguous` | off | drops `0 O o 1 l I \|` |
| `--no-require` | off | by default every enabled class appears at least once |
| `-q`, `--quiet` | off | no entropy line |

Install from the repository until the PyPI release:

```sh
pipx install git+https://github.com/kwagyei/entropass
```

## Design decisions

**The entropy figure is exact, not an estimate.** Most generators print
`length × log2(alphabet)`. That is wrong as soon as every character class is required,
because strings that miss a class can never be produced. entropass counts the valid strings
exactly by inclusion–exclusion over the required classes and reports `log2` of that count,
rounded *down*. For the defaults it is 130.9 bits rather than the naive 131.0. The gap is
small, but the number is true, and a test checks the count against brute-force enumeration
over a grid of small configurations.

**Every valid password is equally likely.** The usual way to force one of each class is to
place one character from each and shuffle. That is not uniform: it favours some strings
over others depending on how many characters of each class they contain, so its real
entropy is lower than it claims. entropass draws every character from the whole alphabet
and redraws the whole password if a required class is missing. A seeded chi-square test
confirms the output is uniform, and the same test fails on the shuffle shortcut, which
documents why it was rejected.

**Randomness comes from `secrets`, never `random`.** A test walks the package's syntax tree
and fails if any module imports `random`. The generator takes its random source as a
parameter, so tests can inject a seeded one without touching the package.

**No runtime dependencies.** The CLI is standard-library `argparse`. There is nothing to
audit beyond this repository.

## Known limitations

- The entropy assumes an attacker who knows entropass and the options you used, which is
  the honest assumption. It says nothing about how a site stores the password, or about
  reuse and phishing.
- Python cannot reliably wipe strings from memory, so the password lives in the process
  until it exits.
- A password printed to a terminal stays in its scrollback. Clipboard copy with automatic
  clearing is coming in a later pull request.
- Only printable ASCII is supported. That is deliberate: it survives every console encoding
  and every password form.

## Planned

- EFF diceware passphrases, including from your own physical dice rolls.
- Copy to the clipboard and clear it afterwards, only if it still holds the secret.
- Release to PyPI through GitHub Trusted Publishing.

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
