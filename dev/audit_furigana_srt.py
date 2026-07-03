from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
SUBTITLE_PLAYER = ROOT / "SubtitlePlayer"
if str(SUBTITLE_PLAYER) not in sys.path:
    sys.path.insert(0, str(SUBTITLE_PLAYER))

import srt  # noqa: E402
from furigana_splitter import bracket_text  # noqa: E402
from model.anki_ruby import AddonRubyGenerator  # noqa: E402


JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
KANJI_RE = re.compile(r"[\u3400-\u9fff]")
HTML_RE = re.compile(r"<[^>]+>")
ASS_POSITION_RE = re.compile(r"\{\\an\d+\}")
BAD_RUBY_START = set("\u3083\u3085\u3087\u3041\u3043\u3045\u3047\u3049\u308e\u3063\u30fc\u3093")


def clean_srt_text(text: str) -> str:
    text = ASS_POSITION_RE.sub("", text or "")
    text = HTML_RE.sub("", text)
    text = text.replace("\u3000", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def subtitle_files(root: Path, files_per_folder: int) -> list[Path]:
    files: list[Path] = []
    direct_files = sorted(root.glob("*.srt"))
    if direct_files:
        picks = [direct_files[0]]
        if files_per_folder > 1 and len(direct_files) > 1:
            picks.append(direct_files[len(direct_files) // 2])
        if files_per_folder > 2 and len(direct_files) > 2:
            picks.append(direct_files[-1])
        files.extend(dict.fromkeys(picks[:files_per_folder]))

    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        srt_files = sorted(folder.glob("*.srt"))
        if not srt_files:
            continue
        picks = [srt_files[0]]
        if files_per_folder > 1 and len(srt_files) > 1:
            picks.append(srt_files[len(srt_files) // 2])
        if files_per_folder > 2 and len(srt_files) > 2:
            picks.append(srt_files[-1])
        files.extend(dict.fromkeys(picks[:files_per_folder]))
    return list(dict.fromkeys(files))


def suspicious_segments(segments: list[tuple[str, str | None]]) -> list[tuple[str, str, str]]:
    suspicious: list[tuple[str, str, str]] = []
    for index, (base, ruby) in enumerate(segments):
        if not ruby:
            continue
        has_kanji = bool(KANJI_RE.search(base or ""))
        if has_kanji and ruby[0] in BAD_RUBY_START:
            suspicious.append((base, ruby, "bad-start"))
        if len(base or "") == 1 and has_kanji and len(ruby) >= 7:
            suspicious.append((base, ruby, "long-single-kanji"))
        if index + 1 < len(segments):
            next_base, next_ruby = segments[index + 1]
            if has_kanji and len(ruby) >= 2 and next_ruby is None and next_base and next_base.startswith(ruby):
                suspicious.append((base, ruby, "duplicated-inline-reading"))
    return suspicious


def parse_subtitles(path: Path) -> list[srt.Subtitle]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    return list(srt.parse(raw))


def render_sentence(generator: AddonRubyGenerator, sentence: str, *, split_kanji_compounds: bool) -> str:
    return bracket_text(generator.segments(sentence, split_kanji_compounds=split_kanji_compounds))


def print_sentence(
    sentence: str,
    *,
    split_kanji_compounds: bool,
    compare_split_modes: bool,
) -> int:
    with AddonRubyGenerator() as generator:
        if compare_split_modes:
            print(f"sentence: {sentence}")
            print(f"default : {render_sentence(generator, sentence, split_kanji_compounds=False)}")
            print(f"split   : {render_sentence(generator, sentence, split_kanji_compounds=True)}")
        else:
            print(render_sentence(generator, sentence, split_kanji_compounds=split_kanji_compounds))
    return 0


def audit(
    root: Path,
    files_per_folder: int,
    max_examples_per_file: int,
    *,
    file: Path | None = None,
    contains: str = "",
    split_kanji_compounds: bool = False,
    compare_split_modes: bool = False,
) -> int:
    files = [file] if file is not None else subtitle_files(root, files_per_folder)
    if not files:
        print(f"No .srt files found under {root}")
        return 1

    total_lines = 0
    total_suspicious = 0
    with AddonRubyGenerator() as generator:
        for path in files:
            examples = 0
            suspicious_count = 0
            try:
                subtitles = parse_subtitles(path)
            except Exception as exc:
                print(f"\n{path}\n  parse failed: {exc}")
                continue

            print(f"\n{path}")
            for sub in subtitles:
                text = clean_srt_text(sub.content)
                if not text or not JAPANESE_RE.search(text) or not KANJI_RE.search(text):
                    continue
                if contains and contains not in text:
                    continue
                total_lines += 1
                try:
                    segments = generator.segments(text, split_kanji_compounds=split_kanji_compounds)
                except Exception as exc:
                    print(f"  #{sub.index} generator failed: {exc}")
                    suspicious_count += 1
                    total_suspicious += 1
                    continue

                flags = suspicious_segments(segments)
                if flags:
                    suspicious_count += 1
                    total_suspicious += 1

                if examples < max_examples_per_file or flags:
                    if compare_split_modes:
                        rendered = (
                            f"default: {render_sentence(generator, text, split_kanji_compounds=False)}\n"
                            f"    split  : {render_sentence(generator, text, split_kanji_compounds=True)}"
                        )
                    else:
                        rendered = bracket_text(segments)
                    marker = " !" if flags else "  "
                    print(f"{marker}#{sub.index} {text}")
                    print(f"    {rendered}")
                    if flags:
                        print(f"    flags: {flags[:4]}")
                    examples += 1

            print(f"  suspicious lines: {suspicious_count}")

    print(f"\nChecked {len(files)} files, {total_lines} subtitle lines, suspicious lines: {total_suspicious}")
    return 1 if total_suspicious else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit furigana output on real SRT files.")
    parser.add_argument("--root", default=str(ROOT / "subs"), help="SRT root folder")
    parser.add_argument("--file", default="", help="Audit one exact .srt file")
    parser.add_argument("--sentence", default="", help="Render one sentence directly")
    parser.add_argument("--contains", default="", help="Only audit SRT lines containing this text")
    parser.add_argument("--split-kanji-compounds", action="store_true", help="Use advanced per-kanji compound split mode")
    parser.add_argument("--compare-split-modes", action="store_true", help="Print default and advanced split output")
    parser.add_argument("--files-per-folder", type=int, default=2)
    parser.add_argument("--max-examples-per-file", type=int, default=6)
    args = parser.parse_args()
    if args.sentence:
        return print_sentence(
            args.sentence,
            split_kanji_compounds=bool(args.split_kanji_compounds),
            compare_split_modes=bool(args.compare_split_modes),
        )
    file_arg = Path(args.file) if args.file else None
    return audit(
        Path(args.root),
        max(1, args.files_per_folder),
        max(0, args.max_examples_per_file),
        file=file_arg,
        contains=args.contains,
        split_kanji_compounds=bool(args.split_kanji_compounds),
        compare_split_modes=bool(args.compare_split_modes),
    )


if __name__ == "__main__":
    raise SystemExit(main())
