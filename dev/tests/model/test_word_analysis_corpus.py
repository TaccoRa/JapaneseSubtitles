"""Regressions discovered while reviewing real Netflix/Amazon/movie SRTs."""
from types import SimpleNamespace as NS

import pytest

from model.anki_client import AnkiClient
from model.anki_ruby import AddonRubyGenerator
from model.annotation_provider import AnnotationProvider
from model.config_manager import ConfigManager
from model.cue_snapshot import CueSnapshotBuilder
from model.subtitle_manager import SubtitleManager
from model.word_database import WordDatabase, WordEntry
from furigana_splitter import counter_reading, iter_number_counter_matches


@pytest.fixture
def client():
    value = AnkiClient(ConfigManager('config.json'))
    assert value._get_tagger() is not None, 'The corpus tests require the real project tokenizer'
    yield value
    value.close()


def snapshot(client, text, segments=None, provider=None):
    return CueSnapshotBuilder(client.word_spans).build(cue_index=0, start_time=1, end_time=3,
        clean_text=text, top_segments=[], bottom_segments=segments or [(text, None)], annotation_provider=provider)


@pytest.mark.parametrize('word', ['ウイルス', 'ゲーム', 'イメージ'])
def test_loanword_lookup_and_card_identity_exclude_unidic_language_label(client, monkeypatch, word):
    text = word + 'の話です。'
    cue = snapshot(client, text)
    token = next(token for token in cue.tokens if token.surface == word)
    assert token.dictionary_lookup_key == word
    selection = cue.resolve_selection(word, ((0, 0, len(word)),))
    assert selection.preferred_lookup == word
    assert client._card_headword_from_cue_snapshot(cue, selection) == word
    monkeypatch.setattr(client, '_fetch_jisho_entries', lambda query: [dict(
        japanese=[dict(word=word, reading=word)],
        senses=[dict(english_definitions=['loanword definition'])])])
    assert 'loanword definition' in client.lookup_dictionary_entry(token.dictionary_lookup_key)


@pytest.mark.parametrize('text', ['サンジ 城ん中へ行くぞ！', '城の中へ行くぞ！'])
@pytest.mark.parametrize('split', [True, False])
def test_castle_inside_uses_shiro_reading(client, text, split):
    with AddonRubyGenerator() as generator:
        segments = generator.segments(text, split_kanji_compounds=split)
    assert ''.join(base for base, ruby in segments) == text
    assert ('城', 'しろ') in segments
    assert next(s for s in client.word_spans(text) if s['surface'] == '城')['reading'] == 'しろ'


def test_castle_interior_uses_written_dictionary_headword(client, monkeypatch):
    text = '早く城内へ急げ！ ルフィ！'
    cue = snapshot(client, text)
    token = next(token for token in cue.tokens if token.surface == '城内')
    assert token.dictionary_lookup_key == '城内'
    start = text.index('城内')
    selection = cue.resolve_selection('城内', ((0, start, start + 2),))
    assert selection.preferred_lookup == '城内'
    assert client._card_headword_from_cue_snapshot(cue, selection) == '城内'
    monkeypatch.setattr(client, '_fetch_jisho_entries', lambda query: [dict(
        japanese=[dict(word='城内', reading='じょうない')],
        senses=[dict(english_definitions=['inside of a castle'])])])
    assert 'inside of a castle' in client.lookup_dictionary_entry(token.dictionary_lookup_key)


@pytest.mark.parametrize('text', [
    '生き延びてやるぞ',  # Dr.STONE S01E01 #71
    '黙って聞きやがれ',  # Dr.STONE S01E12 #145
    '何か食べて',        # One Piece S19E440 #176
    'ゲーム名なんて言ってねえぞ',  # Hunter x Hunter S05E101 #332
    '性格悪いの お前だけになるぞ',  # DEATH NOTE S01E01 #313
    'そりゃ すげえ興味深い 深い',
    '炭化水素 ぶった切ってるだけだ',
    '俺は ５年間ずっと…',
    'ごめんね～ 気がつかなくて',
    '聞いてくれ杠 あの日の続きを',
])
@pytest.mark.parametrize('split', [False, True])
def test_real_cue_keeps_its_characters_and_source_spacing(text, split):
    with AddonRubyGenerator() as generator:
        segments = generator.segments(text, split_kanji_compounds=split)
    assert ''.join(base for base, ruby in segments) == text


@pytest.mark.parametrize('split', [False, True])
def test_year_duration_reading_does_not_use_aida(split):
    with AddonRubyGenerator() as generator:
        segments = generator.segments('俺は ５年間ずっと…', split_kanji_compounds=split)
    readings = ''.join(ruby or '' for base, ruby in segments if '年' in base or '間' in base)
    assert readings == 'ねんかん'


@pytest.mark.parametrize('text,selected,expected', [
    ('めんどくせえから カケラも説明しねえ', '説明', '説明する'),
    ('土器で蒸留してたんだ', '蒸留', '蒸留する'),
    ('銀は硫化水素と反応して', '反応', '反応する'),
    ('毒ガスを中和してキャッチする', '中和', '中和する'),
    ('まず 竹を蒸し焼きにする', 'に', 'に'),
    ('他のヤツを犠牲にすりゃあいい', 'に', 'に'),
    ('電話としては通じるはずだ', 'と', 'と'),
    ('どうするんだ 千空！', 'どう', 'どう'),
    ('蒸留してた', '蒸留してた', '蒸留する'),
    ('説明した結果', '説明した結果', '説明した結果'),
])
def test_contextual_headword_is_shared_by_hover_popup_and_card(client, text, selected, expected):
    cue = snapshot(client, text)
    start = text.index(selected)
    selection = cue.resolve_selection(selected, ((0, start, start+len(selected)),))
    assert selection.preferred_lookup == expected
    assert client._card_headword_from_cue_snapshot(cue, selection) == expected
    exact = [token for token in cue.tokens if token.surface == selected]
    if exact:
        assert exact[0].dictionary_lookup_key == expected


@pytest.mark.parametrize('text,rejected,retained', [
    ('一度失敗した', '一度失敗', '一度'),
    ('三人全員', '人全員', '三人'),
    ('部屋いくつ有るのかな', '部屋いくつ', 'いくつ'),
    ('硝酸プラスエタノール', '硝酸プラスエタノール', None),
    ('一輪車', None, '一輪車'),
    ('全国模試', None, '全国模試'),
    ('科学部室', None, '科学部室'),
    ('お母さん', None, 'お母さん'),
])
def test_quantity_and_operator_boundaries_keep_lexical_compounds(client, text, rejected, retained):
    compounds = {span['surface'] for span in client.word_spans(text) if span.get('compound')}
    if rejected:
        assert rejected not in compounds
    if retained:
        assert retained in compounds


def test_a_ruby_display_group_does_not_override_distinct_lexical_words(client, tmp_path):
    text = '三人全員'
    db = WordDatabase(str(tmp_path / 'corpus-words.json'))
    db.upsert(WordEntry(surface='全員', reading='ぜんいん'), save=False)
    config = {'ANNOTATION_ENABLED': True, 'ANNOTATION_MARK_LOCAL': True, 'ANNOTATION_MIN_TOKEN_LENGTH': 1}
    provider = AnnotationProvider(NS(get=lambda key: config.get(key)), db)
    cue = snapshot(client, text, segments=[(text, 'さんにんぜんいん')], provider=provider)
    marked = [segment for segment in cue.lines[0].annotated_segments if segment.meta_dict().get('annotation')]
    assert ''.join(segment.base for segment in marked) == '全員'


def test_kana_quantity_compound_does_not_inherit_a_component_status(client, tmp_path):
    db = WordDatabase(str(tmp_path / 'kana-words.json'))
    db.upsert(WordEntry(surface='いく'), save=False)
    config = {'ANNOTATION_ENABLED': True, 'ANNOTATION_MARK_LOCAL': True, 'ANNOTATION_MIN_TOKEN_LENGTH': 1}
    provider = AnnotationProvider(NS(get=lambda key: config.get(key)), db)
    cue = snapshot(client, 'いくつ', provider=provider)
    assert all(not segment.meta_dict().get('annotation') for segment in cue.lines[0].annotated_segments)


@pytest.mark.parametrize('known,expected', [('捕まる', True), ('捕まえる', False)])
def test_tsukamatte_marks_the_te_form_only_from_its_own_verb(client, tmp_path, known, expected):
    db = WordDatabase(str(tmp_path / 'te-words.json'))
    db.upsert(WordEntry(surface=known), save=False)
    config = {'ANNOTATION_ENABLED': True, 'ANNOTATION_MARK_LOCAL': True, 'ANNOTATION_MIN_TOKEN_LENGTH': 1}
    provider = AnnotationProvider(NS(get=lambda key: config.get(key)), db)
    segments = provider.annotate_segments([('捕まってたまるか', None)], client.word_spans)
    marked = ''.join(segment[0] for segment in segments
                     if len(segment) > 2 and segment[2].get('status') == 'local_known')
    assert marked == ('捕まって' if expected else '')


def test_contextual_suru_headword_is_used_by_direct_database_status(client, tmp_path):
    db = WordDatabase(str(tmp_path / 'suru-words.json'))
    db.upsert(WordEntry(surface='説明する', reading='せつめいする'), save=False)
    config = {'ANNOTATION_ENABLED': True, 'ANNOTATION_MARK_LOCAL': True, 'ANNOTATION_MIN_TOKEN_LENGTH': 1}
    provider = AnnotationProvider(NS(get=lambda key: config.get(key)), db)
    cue = snapshot(client, '説明した', provider=provider)
    selection = cue.resolve_selection('説明', ((0, 0, 2),))
    status = provider.describe_selection('説明', selection.tokens)
    assert 'Direct database: local known' in status
    assert 'Lookup: 説明する' in status


@pytest.mark.parametrize('slashes', ['\\', '\\\\'])
@pytest.mark.parametrize('escape', ['N', 'n'])
def test_exported_ass_line_escape_in_srt_is_not_analyzed_as_a_word(slashes, escape):
    manager = object.__new__(SubtitleManager)
    manager.config = NS(get=lambda key: None)
    source = '休憩してくれ' + slashes + escape + 'お昼にしよう'
    assert manager._clean_text(source) == '休憩してくれ\nお昼にしよう'
    assert manager._clean_text('お昼' + slashes + 'hにしよう') == 'お昼 にしよう'


@pytest.mark.parametrize('text,reading', [
    ('1分', 'いっぷん'), ('10分', 'じゅっぷん'), ('30分', 'さんじゅっぷん'),
    ('８時間', 'はちじかん'), ('４時間', 'よじかん'), ('４時', 'よじ'),
    ('４回戦', 'よんかいせん'), ('10月５日', 'じゅうがついつか'),
    ('3月1日', 'さんがつついたち'), ('一月二十四日', 'いちがつにじゅうよっか'),
    ('1個', 'いっこ'), ('3階', 'さんがい'), ('8歳', 'はっさい'),
])
def test_time_date_and_counter_units_keep_their_contextual_reading(text, reading):
    with AddonRubyGenerator() as generator:
        segments = generator.segments(text)
    assert ''.join(base for base, ruby in segments) == text
    assert ''.join(ruby or base for base, ruby in segments) == reading


def test_multiple_counter_and_date_matches_stay_in_source_order():
    text = '２つと１匹を８時間後の10月５日に'
    matches = list(iter_number_counter_matches(text))
    assert [match.group(0) for match, ruby in matches] == ['２つ', '１匹', '８時間', '10月５日']
    with AddonRubyGenerator() as generator:
        assert ''.join(base for base, ruby in generator.segments(text)) == text


@pytest.mark.parametrize('counter,reading', [('分', 'じゅういっぷん'), ('本', 'じゅういっぽん'),
    ('人', 'じゅういちにん'), ('回', 'じゅういっかい')])
def test_compound_numbers_do_not_reuse_standalone_irregular_people_readings(counter, reading):
    assert counter_reading('11', counter) == reading


@pytest.mark.parametrize('text,noun,adjective,reading', [
    ('そんなことしたら 性格悪いのお 前だけになるぞ', '性格', '悪', 'わる'),
    ('性格悪かった', '性格', '悪', 'わる'),
    ('攻撃力高い', '攻撃力', '高', 'たか'),
])
def test_ruby_keeps_the_following_adjective_stem_separate_from_a_noun(text, noun, adjective, reading):
    with AddonRubyGenerator() as generator:
        segments = generator.segments(text)
    assert ''.join(base for base, ruby in segments) == text
    assert (adjective, reading) in segments
    assert not any(noun in base and adjective in base for base, ruby in segments)


def test_explicit_noun_adjective_phrase_selection_keeps_the_complete_expression(client):
    text = 'そんなことしたら 性格悪いのお 前だけになるぞ'
    cue = snapshot(client, text)
    start = text.index('性格悪い')
    selection = cue.resolve_selection('性格悪い', ((0, start, start+4),))
    assert selection.preferred_lookup == '性格悪い'
    assert client._card_headword_from_cue_snapshot(cue, selection) == '性格悪い'


@pytest.mark.parametrize('surface', ['性格悪い', '性格悪かった', '性格悪くない', '性格 悪い'])
def test_lexical_adjective_expression_is_the_hover_and_card_target(client, surface):
    from model.hover_layers import choose_hover_region
    text = 'そんなことしたら ' + surface + 'の お前だけになるぞ'
    cue = snapshot(client, text)
    start = text.index(surface)
    expression = next(token for token in cue.tokens if token.compound and token.surface == surface)
    assert expression.dictionary_lookup_key == '性格悪い'
    for point in (start, start+2 if surface[2] != ' ' else start+3):
        regions = [dict(token.as_mapping(), base=token.surface, char_start=token.start, char_end=token.end)
                   for token in cue.tokens if token.start <= point < token.end]
        region = choose_hover_region(regions)
        assert region['base'] == surface and region['lookup'] == '性格悪い'
    selection = cue.resolve_selection(surface, ((0, start, start+len(surface)),))
    assert selection.preferred_lookup == '性格悪い'
    assert client._card_headword_from_cue_snapshot(cue, selection) == '性格悪い'


def test_component_knowledge_does_not_mark_the_lexical_adjective_expression(client, tmp_path):
    db = WordDatabase(str(tmp_path / 'expression-words.json'))
    for word in ('性格', '悪い'):
        db.upsert(WordEntry(surface=word), save=False)
    config = {'ANNOTATION_ENABLED': True, 'ANNOTATION_MARK_LOCAL': True, 'ANNOTATION_MIN_TOKEN_LENGTH': 1}
    provider = AnnotationProvider(NS(get=lambda key: config.get(key)), db)
    cue = snapshot(client, '性格悪い', provider=provider)
    assert all(not segment.meta_dict().get('annotation') for segment in cue.lines[0].annotated_segments)
    db.upsert(WordEntry(surface='性格悪い', reading='せいかくわるい'), save=False)
    provider.refresh()
    cue = snapshot(client, '性格悪い', provider=provider)
    assert all(segment.meta_dict().get('annotation') for segment in cue.lines[0].annotated_segments)


def test_hovered_expression_prepares_a_card_for_the_whole_dictionary_headword(client, monkeypatch):
    text = '性格悪いの お前だけになるぞ'
    cue = snapshot(client, text)
    monkeypatch.setattr(client, '_translate_word', lambda word: 'gemein' if word == '性格悪い' else pytest.fail(word))
    monkeypatch.setattr(client, '_translate_sentence', lambda text: '')
    monkeypatch.setattr(client, '_collect_translation_candidates', lambda *a, **k: {})
    monkeypatch.setattr(client, '_to_furigana_brackets', lambda text, **k: text)
    monkeypatch.setattr(client, '_get_model_field_names', lambda: {
        client.front_field, client.add_rubies_to_front_field, client.back_field, client.sentence_ja_field,
        client.sentence_de_field, client.add_rubies_to_sentence_ja_field})
    monkeypatch.setattr(client, '_invoke', lambda *a, **k: pytest.fail('No live Anki access'))
    result = client.prepare_note_from_selection('性格悪い', cue_snapshot=cue,
        selection_spans=((0, 0, 4),), copy_existing_media=False)
    assert result['selection_lookup_text'] == '性格悪い'
    assert result['fields'][client.add_rubies_to_front_field] == '性格悪い'
    assert result['fields'][client.back_field] == 'gemein'
