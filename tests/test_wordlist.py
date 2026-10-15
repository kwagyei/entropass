"""The bundled EFF wordlist is exactly EFF's file, read from the installed package."""

import hashlib
import itertools
from importlib.resources import files

from entropass.passphrase import WORDLIST_SIZE, load_wordlist

# Re-derive with:
#   curl -s https://www.eff.org/files/2016/07/18/eff_large_wordlist.txt | shasum -a 256
EFF_LARGE_SHA256 = "addd35536511597a02fa0a9ff1e5284677b8883b83e986e43f15a3db996b903e"

RAW = (files("entropass") / "data" / "eff_large_wordlist.txt").read_bytes()


def test_file_is_byte_for_byte_effs() -> None:
    assert hashlib.sha256(RAW).hexdigest() == EFF_LARGE_SHA256


def test_codes_are_every_five_dice_roll_in_order() -> None:
    lines = RAW.decode("ascii").splitlines()
    codes = [line.split("\t")[0] for line in lines]
    assert codes == ["".join(p) for p in itertools.product("123456", repeat=5)]


def test_words_are_unique_lowercase_and_line_up_with_their_codes() -> None:
    words = load_wordlist()
    assert len(words) == len(set(words)) == WORDLIST_SIZE == 6**5
    assert all(set(w) <= set("abcdefghijklmnopqrstuvwxyz-") for w in words)
    lines = RAW.decode("ascii").splitlines()
    assert [line.split("\t")[1] for line in lines] == list(words)


def test_hyphenated_words_are_why_hyphen_is_not_a_separator() -> None:
    assert [w for w in load_wordlist() if "-" in w] == ["drop-down", "felt-tip", "t-shirt"]


def test_licence_file_ships_with_the_list() -> None:
    notice = (files("entropass") / "data" / "EFF-WORDLIST-LICENSE.txt").read_text("utf-8")
    assert "Electronic Frontier Foundation" in notice
    assert "creativecommons.org/licenses/by/4.0" in notice
