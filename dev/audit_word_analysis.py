"""Read-only audit of real subtitle word boundaries and their application."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'SubtitlePlayer'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import srt
from model.anki_client import AnkiClient
from model.anki_ruby import AddonRubyGenerator
from model.annotation_provider import AnnotationProvider
from model.config_manager import ConfigManager
from model.cue_snapshot import CueSnapshotBuilder
from model.hover_layers import choose_hover_region
from model.subtitle_manager import SubtitleManager
from model.word_database import WordDatabase


def selected_files(root):
    folders = defaultdict(list)
    for file in sorted(root.rglob('*.srt')):
        folders[file.parent].append(file)
    for files in folders.values():
        indexes = [0, len(files)-1]
        if len(files) > 15:
            indexes.insert(1, len(files)//2)
        yield from (files[i] for i in dict.fromkeys(indexes))


def audit(root, output, max_cues_per_file=0):
    config = ConfigManager(str(ROOT / 'config.json'))
    client = AnkiClient(config)
    if client._get_tagger() is None:
        raise RuntimeError('Run with the project .venv: the real Japanese tokenizer is required')
    manager = object.__new__(SubtitleManager)
    manager.config = config
    manager._ruby_stats = Counter()
    manager._ruby_generator_failed = False
    db = WordDatabase(str(ROOT / 'annotation_words.json'))
    provider = AnnotationProvider(config, db)
    builder = CueSnapshotBuilder(client.word_spans)
    report = dict(max_cues_per_file=max_cues_per_file, files=[], totals=Counter(), issues=[], review_candidates=[], examples=[])
    seen_candidates, seen_examples = Counter(), Counter()

    def record(kind, location, **details):
        report['totals'][kind] += 1
        if sum(issue['kind'] == kind and issue['file'] == location['file'] for issue in report['issues']) < 200:
            report['issues'].append(dict(kind=kind, **location, **details))

    with AddonRubyGenerator() as generator:
        manager._ruby_generator = generator
        for file in selected_files(root):
            file_counts = Counter()
            relative = file.relative_to(ROOT).as_posix()
            print('Checking', relative, flush=True)
            try:
                subtitles = manager.load_subtitles(str(file))
            except Exception as error:
                record('parse_error', dict(file=relative), error=str(error))
                continue
            file_counts['source_cues'] = len(subtitles)
            if max_cues_per_file and len(subtitles) > max_cues_per_file:
                indexes = [0] if max_cues_per_file == 1 else sorted({
                    round(i*(len(subtitles)-1)/(max_cues_per_file-1)) for i in range(max_cues_per_file)})
                subtitles = [subtitles[i] for i in indexes]
            for sub in subtitles:
                file_counts['cues'] += 1
                clean = manager._clean_text(sub.content)
                if not re.search('[ぁ-ヺ一-鿿]', clean):
                    continue
                lines = [line for line in clean.splitlines() if line.strip()]
                if not lines:
                    continue
                file_counts['japanese_cues'] += 1
                if len(lines) > 2:
                    file_counts['more_than_two_rows'] += 1
                displayed = [lines[0]] if len(lines) == 1 else [lines[0], ' '.join(lines[1:])]
                parsed = [manager._parse_ruby_segments(line) for line in displayed]
                cue = builder.build(cue_index=sub.index, start_time=sub.start.total_seconds(),
                    end_time=sub.end.total_seconds(), clean_text=clean,
                    top_segments=parsed[0] if len(parsed) > 1 else [],
                    bottom_segments=parsed[-1], annotation_provider=provider)
                location = dict(file=relative, cue=sub.index, seconds=sub.start.total_seconds(), text=cue.plain_text)
                for line in cue.lines:
                    original = displayed[line.index]
                    expected = SubtitleManager.RUBY_PATTERN.sub(lambda m: m[1], original)
                    if line.text != expected:
                        record('visible_text_changed', location, expected=expected, actual=line.text)
                    if ''.join(seg.base for seg in line.annotated_segments) != line.text:
                        record('annotation_text_changed', location)
                    base_tokens = [token for token in line.tokens if not token.compound]
                    for token in line.tokens:
                        file_counts['tokens'] += 1
                        if token.compound:
                            file_counts['compounds'] += 1
                        if line.text[token.start:token.end] != token.surface:
                            record('token_offset_mismatch', location, token=token.as_mapping())
                        selection = cue.resolve_selection(token.surface, ((line.index, token.start, token.end),))
                        lookup = client._card_headword_from_cue_snapshot(cue, selection)
                        if token.compound and lookup != token.surface:
                            record('compound_card_changed', location, surface=token.surface, lookup=lookup)
                        if token.annotation is not None:
                            file_counts['annotated_tokens'] += 1
                        parts = [part for part in base_tokens if token.start <= part.start and part.end <= token.end]
                        if token.compound and (len(parts) >= 3 or len(token.surface) >= 8):
                            folder = file.parent.name
                            if seen_candidates[folder] < 18:
                                report['review_candidates'].append(dict(**location, surface=token.surface,
                                    parts=[part.surface for part in parts], reading=token.reading, lookup=lookup))
                                seen_candidates[folder] += 1
                    for token in base_tokens:
                        candidates = [dict(t.as_mapping(), base=t.surface, char_start=t.start, char_end=t.end)
                                      for t in line.tokens if t.start <= token.start < t.end]
                        region = choose_hover_region(candidates, dict(char_start=token.start, char_end=token.end))
                        if region is None:
                            record('missing_hover', location, surface=token.surface)
                            continue
                        surface = str(region['base'])
                        spans = ((line.index, int(region['char_start']), int(region['char_end'])),)
                        selection = cue.resolve_selection(surface, spans)
                        card = client._card_headword_from_cue_snapshot(cue, selection)
                        if not card:
                            card = client._card_headword_for_anki(surface, cue.plain_text)
                        popup = selection.preferred_lookup
                        if card != popup:
                            file_counts['lookup_differences'] += 1
                            if sum(issue['kind'] == 'lookup_difference' for issue in report['issues']) < 200:
                                report['issues'].append(dict(kind='lookup_difference', **location, surface=surface,
                                    hover=region['lookup'], popup=popup, card=card, parts=[p.surface for p in selection.tokens]))
                    categories = []
                    if any(token.compound for token in line.tokens): categories.append('compound')
                    if any(token.pos1 == '動詞' for token in line.tokens): categories.append('verb')
                    if any(token.pos2 == '固有名詞' for token in line.tokens): categories.append('name')
                    if re.search('[0-9０-９一二三四五六七八九十]+[匹本人回年時分]', line.text): categories.append('counter')
                    for category in categories:
                        key = (file.parent.name, category)
                        if seen_examples[key] < 3:
                            report['examples'].append(dict(**location, category=category,
                                ruby=[(seg.base, seg.ruby) for seg in line.segments],
                                words=[(t.surface, t.dictionary_lookup_key, t.reading, t.compound) for t in line.tokens]))
                            seen_examples[key] += 1
            report['files'].append(dict(file=relative, **file_counts))
            report['totals'].update(file_counts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(relative, dict(file_counts), flush=True)
    client.close()
    report['totals'] = dict(report['totals'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['totals'], ensure_ascii=False))
    print('Report:', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT / 'subs')
    parser.add_argument('--output', type=Path, default=ROOT / 'dev/reviews/word-analysis-audit.json')
    parser.add_argument('--max-cues-per-file', type=int, default=0,
                        help='Evenly sample this many cues per file; zero checks the entire file')
    parser.add_argument('--trace-after', type=int, default=0, help='Diagnostic stack dump interval')
    args = parser.parse_args()
    if args.trace_after:
        import faulthandler
        faulthandler.dump_traceback_later(args.trace_after, repeat=True)
    audit(args.root.resolve(), args.output.resolve(), args.max_cues_per_file)
