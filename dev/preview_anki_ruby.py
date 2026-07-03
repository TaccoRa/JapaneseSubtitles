from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
SUBTITLE_PLAYER = ROOT / "SubtitlePlayer"
if str(SUBTITLE_PLAYER) not in sys.path:
    sys.path.insert(0, str(SUBTITLE_PLAYER))

from model.anki_client import AnkiClient  # noqa: E402
from model.config_manager import ConfigManager  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview Anki ruby output without creating an Anki note.")
    parser.add_argument("--word", default="", help="Selected word/headword to render")
    parser.add_argument("--sentence", default="", help="Japanese sentence field to render")
    parser.add_argument("--split-kanji-compounds", action="store_true", help="Use advanced per-kanji compound split mode")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    config = ConfigManager(str(ROOT / "config.json"))
    config.config["ANKI_SPLIT_KANJI_MORAS"] = bool(args.split_kanji_compounds)
    client = AnkiClient(config)

    word = args.word or ""
    sentence = args.sentence or ""
    result = {
        "split_kanji_compounds": bool(args.split_kanji_compounds),
        "word_raw": word,
        "word_ruby": client._to_furigana_brackets(
            word,
            collapse_inline_reading=False,
            sentence_spacing=False,
        ) if word else "",
        "sentence_raw": sentence,
        "sentence_ruby": client._to_furigana_brackets(
            sentence,
            collapse_inline_reading=True,
            sentence_spacing=True,
        ) if sentence else "",
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if word:
            print(f"word raw : {result['word_raw']}")
            print(f"word ruby: {result['word_ruby']}")
        if sentence:
            print(f"sent raw : {result['sentence_raw']}")
            print(f"sent ruby: {result['sentence_ruby']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
