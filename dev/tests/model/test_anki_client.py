from SubtitlePlayer.model.config_manager import ConfigManager
import SubtitlePlayer.model.anki_client as anki_client_module
from SubtitlePlayer.model.anki_client import AnkiClient


def test_extract_jisho_translation_list():
    # basic sanity check that we return a list of definitions
    client = AnkiClient(ConfigManager("config.json"))
    fake_entries = [
        {
            "japanese": [{"word": "試験", "reading": "しけん"}],
            "senses": [{"english_definitions": ["test", "exam", "try"]}],
        }
    ]
    defs = client._extract_jisho_translation("試験", fake_entries)
    assert isinstance(defs, list)
    assert defs == ["test", "exam", "try"]


def test_build_fields_with_definition(monkeypatch):
    config = ConfigManager("config.json")
    client = AnkiClient(config)

    # pretend a jisho lookup already produced a full definition string
    client._last_jisho_full_definition = "one, two, three, four"

    # ensure the model appears to have a Definition field
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
        client.definition_field,
    })

    fields = client._build_note_fields(
        selected="選択",
        subtitle="",
        word_translation="one, two, three",
        sentence_translation="",
    )
    assert fields[client.back_field] == "one, two, three"
    assert fields[client.definition_field] == "one, two, three, four"


def test_translation_entries_are_deduped_case_insensitive():
    client = AnkiClient(ConfigManager("config.json"))
    assert client._dedupe_translation_entries("house / House, sleep; Sleep|room") == "house, sleep, room"


def test_build_fields_dedupes_word_and_definition(monkeypatch):
    config = ConfigManager("config.json")
    client = AnkiClient(config)
    client._last_jisho_full_definition = "house / House, shelter"

    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
        client.definition_field,
    })

    fields = client._build_note_fields(
        selected="\u5bb6",
        subtitle="",
        word_translation="house / House, home",
        sentence_translation="",
    )
    assert fields[client.back_field] == "house, home"
    assert fields[client.definition_field] == "house, shelter"


def test_kanji_mora_splitting_defaults_to_whole_mecab_token():
    config = ConfigManager("config.json")
    config.config["ANKI_SPLIT_KANJI_MORAS"] = False
    client = AnkiClient(config)
    assert client._split_surface_and_reading("\u6f22\u5b57", "\u304b\u3093\u3058") == [
        ("\u6f22\u5b57", "\u304b\u3093\u3058")
    ]


def test_kanji_mora_splitting_can_be_enabled():
    config = ConfigManager("config.json")
    config.config["ANKI_SPLIT_KANJI_MORAS"] = True
    client = AnkiClient(config)
    assert client._split_surface_and_reading("\u6f22\u5b57", "\u304b\u3093\u3058") == [
        ("\u6f22", "\u304b\u3093"),
        ("\u5b57", "\u3058"),
    ]


def test_bracket_text_spaces_before_kanji_ruby_segments():
    client = AnkiClient(ConfigManager("config.json"))
    rendered = client._segments_to_bracket_text(
        [
            ("\u98df", "\u305f"),
            ("\u3079\u308b", None),
            ("\u6f22", "\u304b\u3093"),
            ("\u5b57", "\u3058"),
        ],
        sentence_spacing=True,
    )
    assert rendered == "\u98df[\u305f]\u3079\u308b \u6f22[\u304b\u3093] \u5b57[\u3058]"


def test_word_spans_add_noun_compound_before_parts():
    client = AnkiClient(ConfigManager("config.json"))

    class Feature:
        def __init__(self, pos1, kana, lemma=""):
            self.pos1 = pos1
            self.pos2 = ""
            self.kana = kana
            self.lemma = lemma

    class Token:
        def __init__(self, surface, pos1, kana, lemma=""):
            self.surface = surface
            self.feature = Feature(pos1, kana, lemma)

    client._tagger = lambda _text: [
        Token("\u7279\u306b", "\u526f\u8a5e", "\u30c8\u30af\u30cb"),
        Token("\u7cbe\u795e", "\u540d\u8a5e", "\u30bb\u30a4\u30b7\u30f3"),
        Token("\u69cb\u9020", "\u540d\u8a5e", "\u30b3\u30a6\u30be\u30a6"),
        Token("\u306b", "\u52a9\u8a5e", "\u30cb"),
    ]

    spans = client.word_spans("\u7279\u306b \u7cbe\u795e\u69cb\u9020\u306b")

    assert spans[0]["surface"] == "\u7cbe\u795e\u69cb\u9020"
    assert spans[0]["lookup"] == "\u7cbe\u795e\u69cb\u9020"
    assert all(span["lookup"] != "\u7cbe\u795e\u69cb\u9020\u306b" for span in spans)


def test_lookup_dictionary_entry_falls_back_to_translation_only_when_requested(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))
    monkeypatch.setattr(client, "_fetch_jisho_entries", lambda _query: [])
    monkeypatch.setattr(client, "_translate_sentence", lambda _query: "mental structure")

    assert client.lookup_dictionary_entry("\u7cbe\u795e\u69cb\u9020") == ""
    assert (
        client.lookup_dictionary_entry(
            "\u7cbe\u795e\u69cb\u9020",
            allow_translation_fallback=True,
        )
        == "\u7cbe\u795e\u69cb\u9020 \u2014 mental structure"
    )


def test_lookup_dictionary_entry_caches_hover_result(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))
    calls = {"count": 0}
    fake_entries = [
        {
            "japanese": [{"word": "\u8a66\u9a13", "reading": "\u3057\u3051\u3093"}],
            "senses": [{"english_definitions": ["test", "exam"]}],
        }
    ]

    def fake_fetch(query):
        calls["count"] += 1
        return fake_entries

    monkeypatch.setattr(client, "_fetch_jisho_entries", fake_fetch)

    assert client.lookup_dictionary_entry("\u8a66\u9a13") == "\u8a66\u9a13 [\u3057\u3051\u3093] \u2014 test, exam"
    assert client.lookup_dictionary_entry("\u8a66\u9a13") == "\u8a66\u9a13 [\u3057\u3051\u3093] \u2014 test, exam"
    assert calls["count"] == 1


def test_translate_hover_selection_prefers_deepl(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))
    calls = []

    def fake_deepl(text, source_lang, target_lang):
        calls.append(("deepl", text, source_lang, target_lang))
        return "mentale Struktur"

    def fake_google(text, source_lang, target_lang):
        calls.append(("google", text, source_lang, target_lang))
        return "google result"

    monkeypatch.setattr(client, "_translate_deepl", fake_deepl)
    monkeypatch.setattr(client, "_translate_google", fake_google)

    assert client.translate_hover_selection("\u7cbe\u795e\u69cb\u9020", provider="deepl") == "\u7cbe\u795e\u69cb\u9020 \u2014 mentale Struktur"
    assert calls == [("deepl", "\u7cbe\u795e\u69cb\u9020", "ja", client.sentence_target_lang)]


def test_translate_hover_selection_google_provider_uses_google_only(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))
    calls = []

    def fake_deepl(text, source_lang, target_lang):
        calls.append(("deepl", text, source_lang, target_lang))
        return ""

    def fake_google(text, source_lang, target_lang):
        calls.append(("google", text, source_lang, target_lang))
        return "mentale Struktur"

    monkeypatch.setattr(client, "_translate_deepl", fake_deepl)
    monkeypatch.setattr(client, "_translate_google", fake_google)

    assert client.translate_hover_selection("\u7cbe\u795e\u69cb\u9020", provider="google") == "\u7cbe\u795e\u69cb\u9020 \u2014 mentale Struktur"
    assert calls == [
        ("google", "\u7cbe\u795e\u69cb\u9020", "ja", client.sentence_target_lang),
    ]


def test_tagger_load_is_lazy(monkeypatch):
    calls = {"count": 0}

    def fake_tagger():
        calls["count"] += 1
        return lambda _text: []

    monkeypatch.setattr(anki_client_module, "Tagger", fake_tagger)

    client = AnkiClient(ConfigManager("config.json"))
    assert calls["count"] == 0

    assert client._get_tagger() is not None
    assert client._get_tagger() is not None
    assert calls["count"] == 1


def test_word_spans_cache_avoids_retokenizing():
    client = AnkiClient(ConfigManager("config.json"))
    calls = {"count": 0}

    class Feature:
        pos1 = "\u540d\u8a5e"
        pos2 = ""
        kana = "\u30ab\u30f3\u30b8"
        lemma = ""

    class Token:
        surface = "\u6f22\u5b57"
        feature = Feature()

    def fake_tagger(_text):
        calls["count"] += 1
        return [Token()]

    client._tagger = fake_tagger

    first = client.word_spans("\u6f22\u5b57")
    first[0]["surface"] = "changed"
    second = client.word_spans("\u6f22\u5b57")

    assert calls["count"] == 1
    assert second[0]["surface"] == "\u6f22\u5b57"


def test_collect_translation_candidates_uses_existing_caches(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))

    def fail(*_args, **_kwargs):
        raise AssertionError("candidate collection should not call translators")

    monkeypatch.setattr(client, "_translate_google", fail)
    monkeypatch.setattr(client, "_translate_deepl", fail)
    monkeypatch.setattr(client, "_translate_word_with_jisho", fail)

    selected = "\u6f22\u5b57"
    subtitle = "\u6f22\u5b57\u3092\u8aad\u3080"
    client._jisho_word_translation_cache[selected] = "Kanji"
    client._google_translation_cache[
        client._translation_cache_key(selected, "ja", client.word_target_lang)
    ] = "google word"
    client._word_provider_cache[selected] = "jisho"
    client._deepl_translation_cache[
        client._translation_cache_key(subtitle, "JA", "DE")
    ] = "deepl sentence"
    client._sentence_provider_cache[subtitle] = "deepl"

    candidates = client._collect_translation_candidates(
        selected,
        subtitle,
        word_translation="Kanji",
        sentence_translation="deepl sentence",
    )

    assert candidates["word"] == {"jisho": "Kanji", "google": "google word"}
    assert candidates["sentence"] == {"deepl": "deepl sentence", "google": ""}


def test_ensure_decks_batches_and_caches():
    client = AnkiClient(ConfigManager("config.json"))
    calls = []

    def fake_invoke(action, params=None):
        calls.append((action, params))
        if action == "multi":
            return [{"result": None, "error": None} for _ in params["actions"]]
        return None

    client._invoke = fake_invoke

    client._ensure_decks(("Main", "Main::Reading", "Main", ""))
    client._ensure_decks(("Main", "Main::Reading"))
    client._ensure_deck("Main::Reverse")

    assert calls == [
        (
            "multi",
            {
                "actions": [
                    {"action": "createDeck", "params": {"deck": "Main"}},
                    {"action": "createDeck", "params": {"deck": "Main::Reading"}},
                ]
            },
        ),
        ("createDeck", {"deck": "Main::Reverse"}),
    ]
