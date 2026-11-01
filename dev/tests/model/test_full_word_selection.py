"""Full-word selection and database status must not collapse to component words."""
import re
from types import SimpleNamespace as NS

import pytest

from model.anki_client import AnkiClient
from model.annotation_provider import AnnotationProvider
from model.config_manager import ConfigManager
from model.cue_snapshot import CueSnapshotBuilder
from model.hover_layers import choose_hover_region
from model.renderer import SubtitleRenderer
from model.word_database import WordDatabase, WordEntry
from view.popup import CopyPopup


SENTENCE = 'ああ 全国模試の結果ね'
WORD = '全国模試'


def entry(word, reading='', gloss='the whole country', pos='Noun'):
    return dict(japanese=[dict(word=word, reading=reading)],
                senses=[dict(english_definitions=[gloss], parts_of_speech=[pos])])


@pytest.fixture
def client(monkeypatch):
    value = AnkiClient(ConfigManager('config.json'))
    def tokenize(text):
        readings = dict(全国='ゼンコク', 模試='モシ', 結果='ケッカ')
        return [NS(surface=surface, feature=NS(
            pos1='助詞' if surface in {'の', 'ね'} else '名詞', pos2='',
            lemma=surface, orthBase=surface, kana=readings.get(surface, surface)))
            for surface in re.findall('ああ|全国|模試|の|結果|ね', text)]
    value._tagger = tokenize
    monkeypatch.setattr(value, '_request_json', lambda *a, **k: pytest.fail('Unexpected external request'))
    monkeypatch.setattr(value, '_fetch_jisho_entries', lambda query: [entry('全国', 'ぜんこく')])
    monkeypatch.setattr(value, '_translate_deepl', lambda *a, **k: pytest.fail('Partial gloss must not be translated'))
    monkeypatch.setattr(value, '_translate_google', lambda text, **k: f'full:{text}')
    monkeypatch.setattr(value, '_translate_sentence', lambda text: f'sentence:{text}')
    monkeypatch.setattr(value, '_collect_translation_candidates', lambda *a, **k: {'word': {}, 'sentence': {}})
    monkeypatch.setattr(value, '_to_furigana_brackets', lambda text, **k: text)
    monkeypatch.setattr(value, '_get_model_field_names', lambda: {
        value.front_field, value.back_field, value.add_rubies_to_front_field,
        value.sentence_ja_field, value.sentence_de_field, value.add_rubies_to_sentence_ja_field,
        value.definition_field})
    yield value
    value.close()


def snapshot(client, text=SENTENCE, provider=None):
    return CueSnapshotBuilder(client.word_spans).build(
        cue_index=0, start_time=0, end_time=2, clean_text=text,
        top_segments=[], bottom_segments=[(text, None)], annotation_provider=provider)


@pytest.mark.parametrize('query,items,expected', [
    (WORD, [entry('全国'), entry('全国的'), entry('模試')], []),
    ('全国模試の結果', [entry('全国'), entry(WORD)], []),
    ('全国', [entry('全国民')], []),
    ('全国', [entry('全国民'), entry('全国')], ['the whole country']),
    ('ぜんこく', [entry('全国', 'ぜんこく')], ['the whole country']),
    ('全国 模試', [entry(WORD, gloss='national mock exam')], ['national mock exam']),
    ('勉強する', [entry('勉強', pos='Suru verb - included', gloss='study')], ['study']),
    ('勉強する', [entry('勉強')], []),
])
def test_dictionary_requires_a_complete_query_match(client, query, items, expected):
    assert client._extract_jisho_translation(query, items) == expected


def test_card_translation_uses_full_text_when_jisho_only_returns_a_component(client):
    assert client._translate_word(WORD) == f'full:{WORD}'
    assert client._last_jisho_full_definition == ''
    assert client._word_provider_cache[WORD] == 'google'
    assert client.lookup_dictionary_entry(WORD) == ''
    assert client.lookup_dictionary_entry(WORD, allow_translation_fallback=True) == f'{WORD} — sentence:{WORD}'


@pytest.mark.parametrize('selected', [WORD, '全国模試の結果'])
def test_popup_selection_reaches_card_fields_unchanged(client, selected):
    cue = snapshot(client)
    start, end = SENTENCE.index(selected), SENTENCE.index(selected) + len(selected)
    indexes = {'sel.first': f'1.{start}', 'sel.last': f'1.{end}'}
    widget = NS(get=lambda *a: selected, index=lambda key: indexes.get(key, key),
                compare=lambda left, op, right: left == right)
    popup = object.__new__(CopyPopup)
    popup._entry_widget, popup._cue_snapshot = widget, cue
    popup._line_segments = [[(SENTENCE, None)]]
    popup._rebuild_word_hits(SENTENCE)
    region = popup._selected_text_region()
    assert region['base'] == region['lookup'] == selected
    popup._dictionary_lookup = client.lookup_dictionary_entry
    assert popup._hover_dictionary_text(region) == f'{selected} — sentence:{selected}'

    pending, prepared = [], []
    def add(**payload):
        prepared.append(client.prepare_note_from_selection(
            payload['selected_text'], payload['subtitle_text'], copy_existing_media=False,
            cue_snapshot=payload['cue_snapshot'], selection_spans=payload['selection_spans']))
    popup._on_add_anki = add
    popup._add_selection_to_anki_from_popup(NS(after=lambda delay, fn: pending.append(fn)), SENTENCE)
    # The action owns the selected text and spans even if the UI changes before dispatch.
    indexes.update({'sel.first': '1.3', 'sel.last': '1.5'})
    pending.pop()()
    result = prepared[0]
    assert result['selection_surface_text'] == result['selection_lookup_text'] == selected
    assert result['fields'][client.add_rubies_to_front_field] == selected
    assert result['fields'][client.back_field] == f'full:{selected}'
    assert result['fields'][client.sentence_ja_field] == SENTENCE
    assert result['cue_selection_spans'] == ((0, start, end),)


@pytest.mark.parametrize('text,surfaces,expected', [
    ('食べた後', [('食べ', '動詞', '食べる'), ('た', '助動詞', 'た'), ('後', '名詞', '後')], '食べた後'),
    ('走って帰る', [('走っ', '動詞', '走る'), ('て', '助詞', 'て'), ('帰る', '動詞', '帰る')], '走って帰る'),
    ('食べた', [('食べ', '動詞', '食べる'), ('た', '助動詞', 'た')], '食べる'),
])
def test_phrase_without_snapshot_keeps_all_content_words(client, text, surfaces, expected):
    client._tagger = lambda text: [NS(surface=s, feature=NS(pos1=p, lemma=lemma, orthBase=lemma))
                                 for s, p, lemma in surfaces]
    assert client._card_headword_for_anki(text) == expected


@pytest.mark.parametrize('anchor_span,point', [(None, 35), ((3, 5), 35), ((5, 7), 55), ((3, 4), 35)])
def test_hover_uses_whole_compound_despite_ruby_or_token_boundaries(client, anchor_span, point):
    cue = snapshot(client)
    regions = [dict(token.as_mapping(), base=token.surface, char_start=token.start,
                    char_end=token.end, bbox=(token.start*10, 0, token.end*10, 20))
               for token in cue.tokens if token.start*10 <= point < token.end*10]
    # The same chooser serves the canvas and popup hit tests.
    anchor = dict(char_start=anchor_span[0], char_end=anchor_span[1]) if anchor_span else None
    assert choose_hover_region(regions, anchor)['base'] == WORD
    assert SubtitleRenderer._region_at_point(regions, point, 10, anchor=anchor)['base'] == WORD
    renderer = object.__new__(SubtitleRenderer)
    renderer._dictionary_lookup = client.lookup_dictionary_entry
    assert renderer._hover_dictionary_text(choose_hover_region(regions, anchor)) == f'{WORD} — sentence:{WORD}'
    popup = object.__new__(CopyPopup)
    popup._entry_widget, popup._cue_snapshot = None, cue
    popup._word_hits = [dict(region, start=f'1.{region["char_start"]}', end=f'1.{region["char_end"]}')
                        for region in regions]
    popup._segment_bbox = lambda widget, start, end: (int(start[2:])*10, 0, (int(end[2:])-int(start[2:]))*10, 20)
    assert popup._region_at_pointer(None, popup._word_hits, point, 10, anchor=anchor)['base'] == WORD
    popup._dictionary_lookup = client.lookup_dictionary_entry
    assert popup._hover_dictionary_text(choose_hover_region(regions, anchor)) == f'{WORD} — sentence:{WORD}'


@pytest.mark.parametrize('known_parts', [('全', '国', '模', '試'), ('全国', '模試'), (WORD,)])
def test_compound_database_status_does_not_come_from_components(client, tmp_path, known_parts):
    config = {'ANNOTATION_ENABLED': True, 'ANNOTATION_MARK_LOCAL': True, 'ANNOTATION_MIN_TOKEN_LENGTH': 1,
              'ANNOTATION_RUBY_MODE': 'hide_mature'}
    db = WordDatabase(str(tmp_path / 'words.json'))
    for part in known_parts:
        db.upsert(WordEntry(surface=part, reading='ぜんこくもし' if part == WORD else ''), save=False)
    provider = AnnotationProvider(NS(get=lambda key: config.get(key)), db)
    cue = snapshot(client, provider=provider)
    annotated = [segment for segment in cue.lines[0].annotated_segments if segment.meta_dict().get('annotation')]
    if known_parts == (WORD,):
        assert ''.join(segment.base for segment in annotated) == WORD
        assert all(segment.meta_dict()['status'] == 'local_known' for segment in annotated)
    else:
        assert annotated == []
        assert all(token.annotation is None for token in cue.tokens)
    # Standalone component words still have their own status; the diagnostic
    # view can report these separately without coloring the whole compound.
    assert provider.match_token(dict(surface=known_parts[0], lookup=known_parts[0])) is not None
    status = provider.describe_selection(WORD, cue.resolve_selection(WORD).tokens)
    assert ('Direct database: not found' in status) == (known_parts != (WORD,))
    assert 'Reading: ぜんこくもし\n' in status


@pytest.mark.parametrize('parts', [('全', '国', '模', '試'), ('全国', '模試'), (WORD,)])
def test_split_ruby_does_not_reintroduce_component_status(client, tmp_path, parts):
    settings = {'ANNOTATION_ENABLED': True, 'ANNOTATION_MARK_LOCAL': True, 'ANNOTATION_MIN_TOKEN_LENGTH': 1,
                'ANNOTATION_RUBY_MODE': 'hide_mature'}
    db = WordDatabase(str(tmp_path / 'split-words.json'))
    for part in parts:
        db.upsert(WordEntry(surface=part), save=False)
    provider = AnnotationProvider(NS(get=lambda key: settings.get(key)), db)
    segments = [('ああ ', None), ('全', 'ぜん'), ('国', 'こく'), ('模', 'も'), ('試', 'し'), ('の結果ね', None)]
    output, tokens, intervals = provider.analyze_segments(segments, client.word_spans)
    assert ''.join(segment[0] for segment in output) == SENTENCE
    if parts == (WORD,):
        assert [(start, end, match.status) for start, end, match in intervals] == [(3, 7, 'local_known')]
    else:
        assert intervals == []
        assert output == segments


def test_component_status_still_applies_to_a_separate_word(client, tmp_path):
    settings = {'ANNOTATION_ENABLED': True, 'ANNOTATION_MARK_LOCAL': True, 'ANNOTATION_MIN_TOKEN_LENGTH': 1}
    db = WordDatabase(str(tmp_path / 'standalone-words.json'))
    db.upsert(WordEntry(surface='全国'), save=False)
    provider = AnnotationProvider(NS(get=lambda key: settings.get(key)), db)
    text = '全国模試と全国'
    output, tokens, intervals = provider.analyze_segments([(text, None)], client.word_spans)
    assert [(start, end) for start, end, match in intervals] == [(5, 7)]
    assert ''.join(segment[0] for segment in output if len(segment) == 3) == '全国'
