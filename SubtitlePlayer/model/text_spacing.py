"""Space handling shared by subtitle ruby parsing and Anki exports."""

from html import unescape
import re


# These invisible format characters can mark a separator in copied subtitles,
# although Python does not classify them as whitespace.
INVISIBLE_SPACES = frozenset("\u180e\u200b\ufeff")
SPACE_ENTITY_RE = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]*|\#[0-9]+|\#[xX][0-9a-fA-F]+);")


def normalize_ruby_spacing(text: str) -> str:
    """Use ordinary spaces for ruby boundaries, preserving lines and spacing counts.

    Decode only space entities, so escaped punctuation and literal HTML-like text
    retain their meaning. Do not normalize Japanese characters with NFKC.
    """
    def decode_space(match: re.Match) -> str:
        value = unescape(match.group(0))
        if len(value) == 1 and (value.isspace() or value in INVISIBLE_SPACES):
            return value
        return match.group(0)

    value = SPACE_ENTITY_RE.sub(decode_space, str(text or ""))
    return "".join(
        " " if (ch.isspace() and ch not in "\r\n") or ch in INVISIBLE_SPACES else ch
        for ch in value
    )
