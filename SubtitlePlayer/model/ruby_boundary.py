"""Shared Anki bracket-ruby boundaries for snapshots and generated text."""

OPENING = "[(（{｛<＜「『【・･"


def starts_katakana(text):
    return bool(text) and ("ァ" <= text[0] <= "ヿ" or "ｦ" <= text[0] <= "ﾟ")


def needs_ruby_boundary(segments, index, previous_output, sentence_spacing=True):
    base, ruby = segments[index]
    if not ruby or not base or not previous_output or previous_output[-1].isspace() or previous_output[-1] in OPENING:
        return False
    code = ord(base[0])
    boundary = code == 0x3005 or 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF or base[0].isdigit()
    if sentence_spacing and starts_katakana(base):
        previous_katakana = False
        for prev_base, prev_ruby in reversed(segments[:index]):
            if not prev_base or prev_base in ("・", "･"):
                continue
            previous_katakana = bool(prev_ruby and starts_katakana(prev_base))
            break
        boundary = boundary or not previous_katakana
    return boundary
