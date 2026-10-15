# entropass

[![CI](https://github.com/kwagyei/entropass/actions/workflows/ci.yml/badge.svg)](https://github.com/kwagyei/entropass/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/entropass)](https://pypi.org/project/entropass/)
[![Python](https://img.shields.io/pypi/pyversions/entropass)](https://pypi.org/project/entropass/)

A password and passphrase generator for the command line that tells you, honestly and in
bits, how strong what it made actually is.

```sh
pipx install entropass        # or: pip install entropass
```

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

$ entropass passphrase
crusader manlike sherry unwelcome vocalize litigate
77.5 bits of entropy: 6 words from the EFF large wordlist (7776 words)

$ entropass passphrase --dice -w 3 --sep .
Word 1 of 3: roll five dice and enter them (e.g. 4 1 6 2 3):
Word 2 of 3: roll five dice and enter them (e.g. 4 1 6 2 3):
Word 3 of 3: roll five dice and enter them (e.g. 4 1 6 2 3):
abacus.zoom.humorous
38.7 bits of entropy: 3 words from the EFF large wordlist (7776 words), assuming fair dice

$ entropass passphrase --copy
Copied the passphrase to the clipboard. It clears in 30 s if it still holds it.
77.5 bits of entropy: 6 words from the EFF large wordlist (7776 words)
```

The secret is the only thing on stdout; the entropy line goes to stderr. So
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

| `passphrase` option | Default | |
|---|---|---|
| `-w`, `--words N` | 6 | 1 to 100; each word adds 12.9 bits |
| `--sep TEXT` | a space | printable ASCII with no lowercase letters and no `-` (see below) |
| `--dice` | off | use your own dice: five per word, typed without echo, or piped one roll per line |
| `-q`, `--quiet` | off | no entropy line |

Both commands also take:

| Option | Default | |
|---|---|---|
| `-c`, `--copy` | off | copy to the clipboard instead of printing |
| `--clear-after SECS` | 30 | then clear it, if it still holds the secret; `0` never clears |

The clipboard uses the tools your system already has: `pbcopy` on macOS, `clip` and
PowerShell on Windows, and `wl-clipboard`, `xclip` or `xsel` on Linux.

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

**The wordlist is EFF's file, byte for byte.** It ships unmodified, so anyone can compare
it with EFF's copy, and a test pins its SHA-256 and checks that its codes are every roll of
five dice in order. The test reads the file from the *installed* package, so a wheel that
left it out would fail CI rather than fail on a user's machine.

**Separators may not contain any character that appears in a word.** Four EFF words contain
a hyphen (`drop-down`, `felt-tip`, `t-shirt`, `yo-yo`). If `-` were allowed, a passphrase
could in principle split into words in more than one way, and the entropy would be
slightly less than claimed. None of the four happens to collide with this list, but the
rule makes the guarantee hold without having to check every list.

**Dice rolls are read, never passed as options.** In `--dice` mode the rolls *are* the
passphrase, so they are not accepted as command-line arguments, which other users can see
in `ps` and which shell history keeps. On a terminal each roll is typed without echo and
re-prompted if invalid. From a pipe a bad roll fails the run without being echoed back.

**The clipboard is cleared only if it still holds the secret.** `--copy` hands the secret
to the system's clipboard tool on stdin, never as an argument. It then starts a small
detached process and returns at once. That process receives only the SHA-256 digest of the
secret, never the secret itself. After the delay it reads the clipboard and clears it only
if the digest still matches, so something you copied in the meantime is never wiped. It
survives closing the terminal. If no clipboard is available, entropass fails rather than
falling back to printing the secret into the scrollback.

**No runtime dependencies.** The CLI is standard-library `argparse`. There is nothing to
audit beyond this repository.

## Known limitations

- The entropy assumes an attacker who knows entropass and the options you used, which is
  the honest assumption. It says nothing about how a site stores the password, or about
  reuse and phishing.
- Python cannot reliably wipe strings from memory, so the password lives in the process
  until it exits.
- A secret printed to a terminal stays in its scrollback; use `--copy` to avoid that.
- Clipboard managers and history keep their own copies, which entropass cannot remove:
  Windows clipboard history (Win+V) and cloud clipboard, macOS Universal Clipboard, KDE
  Klipper, and third-party managers. The system tools have no way to mark a copy as
  sensitive.
- The clear runs late if the computer sleeps through the delay (it happens after waking),
  and not at all after a restart or logout before the delay ends. A copy made in the few milliseconds between the clearer's check and its
  clear would be lost.
- SSH and other sessions without a graphical desktop have no clipboard, so `--copy` fails
  there. On Wayland, desktops that don't let background programs read the clipboard (some
  GNOME versions) will skip the clear, because the clearer cannot check what is there.
- `--dice` entropy assumes fair dice and honest rolling; entropass cannot check either.
- `--dice` hides typed rolls only when stdin is a real terminal. Some Windows terminals,
  such as Git Bash's mintty without winpty, do not present as one, so the rolls are then
  read as piped input, with echo on and no prompts. Use PowerShell or cmd there.
- Only printable ASCII is supported. That is deliberate: it survives every console encoding
  and every password form.

## Planned

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

### Releasing

Bump `version` in `pyproject.toml` in a pull request, merge it, then tag that commit:

```sh
git tag v0.1.0 && git push origin v0.1.0
```

[`release.yml`](.github/workflows/release.yml) then:
1. builds the package once and checks that the tag matches the version;
2. publishes it to TestPyPI;
3. installs it back from TestPyPI and runs it;
4. waits for a maintainer to approve the `pypi` environment;
5. publishes the same files to PyPI and creates the GitHub release.

Publishing uses PyPI Trusted Publishing, so no API token is stored anywhere. Every action
in both workflows is pinned to a commit SHA, with its version in a comment.

## Credits and licence

entropass is MIT-licensed; see [LICENSE](https://github.com/kwagyei/entropass/blob/main/LICENSE).

The passphrase words are the [EFF Large Wordlist for Passphrases](https://www.eff.org/dice)
by Joseph Bonneau and the Electronic Frontier Foundation, included unmodified under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) (EFF's licence for its original
material, per [eff.org/copyright](https://www.eff.org/copyright)). See
[EFF-WORDLIST-LICENSE.txt](https://github.com/kwagyei/entropass/blob/main/src/entropass/data/EFF-WORDLIST-LICENSE.txt).
