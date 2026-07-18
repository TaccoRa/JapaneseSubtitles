from SubtitlePlayer.model.config_manager import ConfigManager
import SubtitlePlayer.model.anki_client as anki_client_module
from SubtitlePlayer.model.anki_client import AnkiClient, AnkiConnectRequestError


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


def test_mixed_kanji_kana_splits_visible_kana_boundaries_by_default():
    config = ConfigManager("config.json")
    config.config["ANKI_SPLIT_KANJI_MORAS"] = False
    client = AnkiClient(config)

    assert client._split_surface_and_reading("\u4e57\u308a\u63db\u3048\u308b", "\u306e\u308a\u304b\u3048\u308b") == [
        ("\u4e57", "\u306e"),
        ("\u308a", None),
        ("\u63db", "\u304b"),
        ("\u3048\u308b", None),
    ]
    assert client._split_surface_and_reading("\u716e\u3048\u5207\u3089\u306a\u3044", "\u306b\u3048\u304d\u3089\u306a\u3044") == [
        ("\u716e", "\u306b"),
        ("\u3048", None),
        ("\u5207", "\u304d"),
        ("\u3089\u306a\u3044", None),
    ]


def test_anki_counter_reading_spans_number_and_counter():
    client = AnkiClient(ConfigManager("config.json"))

    assert (
        client._to_furigana_brackets(
            "\uff11 \u5339",
            collapse_inline_reading=True,
            sentence_spacing=True,
        )
        == "\uff11 \u5339[\u3044\u3063\u3074\u304d]"
    )


def test_anki_counter_ruby_adds_boundary_space_after_plain_prefix():
    client = AnkiClient(ConfigManager("config.json"))

    assert (
        client._to_furigana_brackets(
            "\u305f\u3063\u305f\uff11\u4eba",
            collapse_inline_reading=False,
            sentence_spacing=False,
        )
        == "\u305f\u3063\u305f \uff11\u4eba[\u3072\u3068\u308a]"
    )


def test_katakana_word_gets_hiragana_ruby():
    client = AnkiClient(ConfigManager("config.json"))

    assert client._split_surface_and_reading(
        "\u30e0\u30c1\u30e3\u30af\u30c1\u30e3",
        "\u30e0\u30c1\u30e3\u30af\u30c1\u30e3",
    ) == [("\u30e0\u30c1\u30e3\u30af\u30c1\u30e3", "\u3080\u3061\u3083\u304f\u3061\u3083")]


def test_card_headword_for_selection_uses_verb_dictionary_form():
    client = AnkiClient(ConfigManager("config.json"))

    class Feature:
        def __init__(self, pos1, lemma):
            self.pos1 = pos1
            self.lemma = lemma
            self.orthBase = lemma
            self.formBase = ""

    class Token:
        def __init__(self, surface, pos1, lemma):
            self.surface = surface
            self.feature = Feature(pos1, lemma)

    def fake_tagger(text):
        if text == "\u8cab\u3044\u305f":
            return [
                Token("\u8cab\u3044", "\u52d5\u8a5e", "\u8cab\u304f"),
                Token("\u305f", "\u52a9\u52d5\u8a5e", "\u305f"),
            ]
        if text == "\u8cab\u3044":
            return [Token("\u8cab\u3044", "\u52d5\u8a5e", "\u8cab\u304f")]
        return [Token(text, "\u540d\u8a5e", text)]

    client._tagger = fake_tagger

    assert client._card_headword_for_selection("\u8cab\u3044\u305f") == "\u8cab\u304f"
    assert client._card_headword_for_selection("\u8cab\u3044") == "\u8cab\u304f"
    assert client._card_headword_for_selection("\u8cab") == "\u8cab"


def test_card_headword_for_selection_uses_suru_for_sahen_compounds():
    client = AnkiClient(ConfigManager("config.json"))

    class Feature:
        def __init__(self, pos1, lemma, orth_base="", ctype="", pos2="", pos3=""):
            self.pos1 = pos1
            self.pos2 = pos2
            self.pos3 = pos3
            self.lemma = lemma
            self.orthBase = orth_base or lemma
            self.formBase = self.orthBase
            self.cType = ctype

    class Token:
        def __init__(self, surface, feature):
            self.surface = surface
            self.feature = feature

    def fake_tagger(text):
        if text == "\u52c9\u5f37\u3057\u305f":
            return [
                Token("\u52c9\u5f37", Feature("\u540d\u8a5e", "\u52c9\u5f37", pos3="\u30b5\u5909\u53ef\u80fd")),
                Token(
                    "\u3057",
                    Feature("\u52d5\u8a5e", "\u70ba\u308b", "\u3059\u308b", "\u30b5\u884c\u5909\u683c", "\u975e\u81ea\u7acb\u53ef\u80fd"),
                ),
                Token("\u305f", Feature("\u52a9\u52d5\u8a5e", "\u305f")),
            ]
        if text == "\u611b\u3057\u305f":
            return [
                Token(
                    "\u611b\u3057",
                    Feature("\u52d5\u8a5e", "\u611b\u3059\u308b", "\u611b\u3059\u308b", "\u30b5\u884c\u5909\u683c"),
                ),
                Token("\u305f", Feature("\u52a9\u52d5\u8a5e", "\u305f")),
            ]
        if text == "\u79c1\u306f\u52c9\u5f37\u3057\u305f":
            return [
                Token("\u79c1", Feature("\u4ee3\u540d\u8a5e", "\u79c1")),
                Token("\u306f", Feature("\u52a9\u8a5e", "\u306f")),
                Token("\u52c9\u5f37", Feature("\u540d\u8a5e", "\u52c9\u5f37", pos3="\u30b5\u5909\u53ef\u80fd")),
                Token(
                    "\u3057",
                    Feature("\u52d5\u8a5e", "\u70ba\u308b", "\u3059\u308b", "\u30b5\u884c\u5909\u683c", "\u975e\u81ea\u7acb\u53ef\u80fd"),
                ),
                Token("\u305f", Feature("\u52a9\u52d5\u8a5e", "\u305f")),
            ]
        return []

    client._tagger = fake_tagger

    assert client._card_headword_for_selection("\u52c9\u5f37\u3057\u305f") == "\u52c9\u5f37\u3059\u308b"
    assert client._card_headword_for_selection("\u611b\u3057\u305f") == "\u611b\u3059\u308b"
    assert (
        client._card_headword_for_selection("\u79c1\u306f\u52c9\u5f37\u3057\u305f")
        == "\u79c1\u306f\u52c9\u5f37\u3057\u305f"
    )


def test_add_from_selection_uses_dictionary_form_for_verb_card(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))

    class Feature:
        pos1 = "\u52d5\u8a5e"
        lemma = "\u8cab\u304f"
        orthBase = "\u8cab\u304f"
        formBase = ""

    class Token:
        surface = "\u8cab\u3044"
        feature = Feature()

    client._tagger = lambda _text: [Token()]
    translate_calls = []
    notes = []

    monkeypatch.setattr(client, "_translate_word", lambda text: translate_calls.append(text) or "durchdringen")
    monkeypatch.setattr(client, "_translate_sentence", lambda _text: "")
    monkeypatch.setattr(client, "_to_furigana_brackets", lambda text, **_kwargs: f"ruby:{text}")
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
        client.definition_field,
    })
    monkeypatch.setattr(client, "_route_new_cards", lambda _note_id: {"reading": [], "reverse": [], "unrouted": []})

    def fake_invoke(action, params=None):
        if action == "multi":
            return [{"result": None, "error": None} for _ in params["actions"]]
        if action == "addNote":
            notes.append(params["note"])
            return 123
        return None

    monkeypatch.setattr(client, "_invoke", fake_invoke)

    result = client.add_from_selection("\u8cab\u3044\u305f", subtitle_text="")

    assert translate_calls == ["\u8cab\u304f"]
    assert result["selection_surface_text"] == "\u8cab\u3044\u305f"
    assert result["selection_lookup_text"] == "\u8cab\u304f"
    fields = notes[0]["fields"]
    assert fields[client.add_rubies_to_front_field] == "\u8cab\u304f"
    assert fields[client.front_field] == "ruby:\u8cab\u304f"
    assert fields[client.back_field] == "durchdringen"


def test_add_from_selection_adds_suru_marker_tag(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))

    class Feature:
        def __init__(self, pos1, lemma, orth_base="", ctype="", pos2="", pos3=""):
            self.pos1 = pos1
            self.pos2 = pos2
            self.pos3 = pos3
            self.lemma = lemma
            self.orthBase = orth_base or lemma
            self.formBase = self.orthBase
            self.cType = ctype

    class Token:
        def __init__(self, surface, feature):
            self.surface = surface
            self.feature = feature

    client._tagger = lambda _text: [
        Token("\u52c9\u5f37", Feature("\u540d\u8a5e", "\u52c9\u5f37", pos3="\u30b5\u5909\u53ef\u80fd")),
        Token(
            "\u3057",
            Feature("\u52d5\u8a5e", "\u70ba\u308b", "\u3059\u308b", "\u30b5\u884c\u5909\u683c", "\u975e\u81ea\u7acb\u53ef\u80fd"),
        ),
        Token("\u305f", Feature("\u52a9\u52d5\u8a5e", "\u305f")),
    ]
    monkeypatch.setattr(client, "_translate_word", lambda _text: "study")
    monkeypatch.setattr(client, "_translate_sentence", lambda _text: "")
    monkeypatch.setattr(client, "_to_furigana_brackets", lambda text, **_kwargs: f"ruby:{text}")
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
        client.definition_field,
    })
    monkeypatch.setattr(client, "_route_new_cards", lambda _note_id: {"reading": [], "reverse": [], "unrouted": []})
    notes = []

    def fake_invoke(action, params=None):
        if action == "multi":
            return [{"result": None, "error": None} for _ in params["actions"]]
        if action == "addNote":
            notes.append(params["note"])
            return 456
        return None

    monkeypatch.setattr(client, "_invoke", fake_invoke)

    result = client.add_from_selection("\u52c9\u5f37\u3057\u305f", subtitle_text="")

    assert result["selection_lookup_text"] == "\u52c9\u5f37\u3059\u308b"
    assert result["anki_marker_tags"] == ["\u3059\u308b-Verb"]
    assert notes[0]["tags"] == ["\u3059\u308b-Verb"]
    assert notes[0]["fields"][client.add_rubies_to_front_field] == "\u52c9\u5f37\u3059\u308b"


def test_add_from_selection_includes_custom_anki_tags(monkeypatch):
    config = ConfigManager("config.json")
    config.config["ANKI_TAGS"] = "anime, mined; custom"
    client = AnkiClient(config)

    monkeypatch.setattr(client, "_card_headword_for_anki", lambda text, _subtitle="": text)
    monkeypatch.setattr(client, "_anki_marker_tags_for_selection", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(client, "_translate_word", lambda _text: "word")
    monkeypatch.setattr(client, "_translate_sentence", lambda _text: "")
    monkeypatch.setattr(client, "_to_furigana_brackets", lambda text, **_kwargs: f"ruby:{text}")
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
        client.definition_field,
    })
    monkeypatch.setattr(client, "_route_new_cards", lambda _note_id: {"reading": [], "reverse": [], "unrouted": []})
    notes = []

    def fake_invoke(action, params=None):
        if action == "multi":
            return [{"result": None, "error": None} for _ in params["actions"]]
        if action == "addNote":
            notes.append(params["note"])
            return 654
        return None

    monkeypatch.setattr(client, "_invoke", fake_invoke)

    client.add_from_selection("\u65b0\u898f", subtitle_text="")

    assert notes[0]["tags"] == ["anime", "mined", "custom"]


def test_suru_marker_tag_can_be_disabled():
    config = ConfigManager("config.json")
    config.config["ANKI_TAG_SURU_VERBS"] = False
    client = AnkiClient(config)
    client._tagger_load_attempted = True

    assert client._anki_marker_tags_for_selection("\u52c9\u5f37\u3059\u308b", "\u52c9\u5f37\u3059\u308b", "") == []


def test_adjective_marker_tags_can_be_disabled():
    config = ConfigManager("config.json")
    config.config["ANKI_TAG_I_ADJECTIVES"] = False
    config.config["ANKI_TAG_NA_ADJECTIVES"] = False
    client = AnkiClient(config)

    class Feature:
        def __init__(self, pos1, lemma):
            self.pos1 = pos1
            self.lemma = lemma
            self.orthBase = lemma
            self.formBase = lemma
            self.cType = ""
            self.pos2 = ""
            self.pos3 = ""

    class Token:
        def __init__(self, surface, feature):
            self.surface = surface
            self.feature = feature

    def fake_tagger(text):
        if text == "\u65b0\u3057\u3044":
            return [Token("\u65b0\u3057\u3044", Feature("\u5f62\u5bb9\u8a5e", "\u65b0\u3057\u3044"))]
        if text == "\u9759\u304b":
            return [Token("\u9759\u304b", Feature("\u5f62\u72b6\u8a5e", "\u9759\u304b"))]
        return []

    client._tagger = fake_tagger

    assert client._anki_marker_tags_for_selection("\u65b0\u3057\u3044", "\u65b0\u3057\u3044", "") == []
    assert client._anki_marker_tags_for_selection("\u9759\u304b", "\u9759\u304b", "") == []


def test_add_from_selection_uses_sentence_context_for_selected_suru_stem(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))

    class Feature:
        def __init__(self, pos1, lemma, orth_base="", ctype="", pos2="", pos3=""):
            self.pos1 = pos1
            self.pos2 = pos2
            self.pos3 = pos3
            self.lemma = lemma
            self.orthBase = orth_base or lemma
            self.formBase = self.orthBase
            self.cType = ctype

    class Token:
        def __init__(self, surface, feature):
            self.surface = surface
            self.feature = feature

    def fake_tagger(text):
        if text == "\u79c1\u306f\u52c9\u5f37\u3057\u305f":
            return [
                Token("\u79c1", Feature("\u4ee3\u540d\u8a5e", "\u79c1")),
                Token("\u306f", Feature("\u52a9\u8a5e", "\u306f")),
                Token("\u52c9\u5f37", Feature("\u540d\u8a5e", "\u52c9\u5f37", pos3="\u30b5\u5909\u53ef\u80fd")),
                Token(
                    "\u3057",
                    Feature("\u52d5\u8a5e", "\u70ba\u308b", "\u3059\u308b", "\u30b5\u884c\u5909\u683c", "\u975e\u81ea\u7acb\u53ef\u80fd"),
                ),
                Token("\u305f", Feature("\u52a9\u52d5\u8a5e", "\u305f")),
            ]
        if text == "\u52c9\u5f37":
            return [Token("\u52c9\u5f37", Feature("\u540d\u8a5e", "\u52c9\u5f37", pos3="\u30b5\u5909\u53ef\u80fd"))]
        return []

    client._tagger = fake_tagger
    translate_calls = []
    notes = []

    monkeypatch.setattr(client, "_translate_word", lambda text: translate_calls.append(text) or "study")
    monkeypatch.setattr(client, "_translate_sentence", lambda _text: "")
    monkeypatch.setattr(client, "_translate_google", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(client, "_to_furigana_brackets", lambda text, **_kwargs: f"ruby:{text}")
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
        client.definition_field,
    })
    monkeypatch.setattr(client, "_route_new_cards", lambda _note_id: {"reading": [], "reverse": [], "unrouted": []})

    def fake_invoke(action, params=None):
        if action == "multi":
            return [{"result": None, "error": None} for _ in params["actions"]]
        if action == "addNote":
            notes.append(params["note"])
            return 789
        return None

    monkeypatch.setattr(client, "_invoke", fake_invoke)

    result = client.add_from_selection(
        "\u52c9\u5f37",
        subtitle_text="\u79c1\u306f\u52c9\u5f37\u3057\u305f",
    )

    assert translate_calls == ["\u52c9\u5f37\u3059\u308b"]
    assert result["selection_surface_text"] == "\u52c9\u5f37"
    assert result["selection_lookup_text"] == "\u52c9\u5f37\u3059\u308b"
    assert result["anki_marker_tags"] == ["\u3059\u308b-Verb"]
    assert notes[0]["tags"] == ["\u3059\u308b-Verb"]
    assert notes[0]["fields"][client.add_rubies_to_front_field] == "\u52c9\u5f37\u3059\u308b"


def test_sentence_context_keeps_suru_capable_noun_when_used_as_noun():
    client = AnkiClient(ConfigManager("config.json"))

    class Feature:
        def __init__(self, pos1, lemma, orth_base="", ctype="", pos2="", pos3=""):
            self.pos1 = pos1
            self.pos2 = pos2
            self.pos3 = pos3
            self.lemma = lemma
            self.orthBase = orth_base or lemma
            self.formBase = self.orthBase
            self.cType = ctype

    class Token:
        def __init__(self, surface, feature):
            self.surface = surface
            self.feature = feature

    def fake_tagger(text):
        if text == "\u52c9\u5f37\u306f\u697d\u3057\u3044":
            return [
                Token("\u52c9\u5f37", Feature("\u540d\u8a5e", "\u52c9\u5f37", pos3="\u30b5\u5909\u53ef\u80fd")),
                Token("\u306f", Feature("\u52a9\u8a5e", "\u306f")),
                Token("\u697d\u3057\u3044", Feature("\u5f62\u5bb9\u8a5e", "\u697d\u3057\u3044")),
            ]
        if text == "\u52c9\u5f37":
            return [Token("\u52c9\u5f37", Feature("\u540d\u8a5e", "\u52c9\u5f37", pos3="\u30b5\u5909\u53ef\u80fd"))]
        return []

    client._tagger = fake_tagger

    assert (
        client._card_headword_for_anki("\u52c9\u5f37", "\u52c9\u5f37\u306f\u697d\u3057\u3044")
        == "\u52c9\u5f37"
    )
    assert (
        client._anki_marker_tags_for_selection(
            "\u52c9\u5f37",
            "\u52c9\u5f37",
            "\u52c9\u5f37\u306f\u697d\u3057\u3044",
        )
        == []
    )


def test_sentence_context_marks_na_adjective_only_when_context_matches():
    client = AnkiClient(ConfigManager("config.json"))

    class Feature:
        def __init__(self, pos1, lemma, pos3="", ctype=""):
            self.pos1 = pos1
            self.pos2 = ""
            self.pos3 = pos3
            self.lemma = lemma
            self.orthBase = lemma
            self.formBase = lemma
            self.cType = ctype

    class Token:
        def __init__(self, surface, feature):
            self.surface = surface
            self.feature = feature

    def fake_tagger(text):
        if text == "\u7121\u7406\u306a\u8a71":
            return [
                Token("\u7121\u7406", Feature("\u540d\u8a5e", "\u7121\u7406", pos3="\u5f62\u72b6\u8a5e\u53ef\u80fd")),
                Token("\u306a", Feature("\u52a9\u52d5\u8a5e", "\u3060", ctype="\u52a9\u52d5\u8a5e-\u30c0")),
                Token("\u8a71", Feature("\u540d\u8a5e", "\u8a71")),
            ]
        if text == "\u7121\u7406\u3092\u3057\u305f":
            return [
                Token("\u7121\u7406", Feature("\u540d\u8a5e", "\u7121\u7406", pos3="\u5f62\u72b6\u8a5e\u53ef\u80fd")),
                Token("\u3092", Feature("\u52a9\u8a5e", "\u3092")),
                Token(
                    "\u3057",
                    Feature("\u52d5\u8a5e", "\u70ba\u308b", ctype="\u30b5\u884c\u5909\u683c"),
                ),
                Token("\u305f", Feature("\u52a9\u52d5\u8a5e", "\u305f")),
            ]
        if text == "\u7121\u7406":
            return [Token("\u7121\u7406", Feature("\u540d\u8a5e", "\u7121\u7406", pos3="\u5f62\u72b6\u8a5e\u53ef\u80fd"))]
        return []

    client._tagger = fake_tagger

    assert (
        client._anki_marker_tags_for_selection(
            "\u7121\u7406",
            "\u7121\u7406",
            "\u7121\u7406\u306a\u8a71",
        )
        == ["\u306a-Adj"]
    )
    assert (
        client._anki_marker_tags_for_selection(
            "\u7121\u7406",
            "\u7121\u7406",
            "\u7121\u7406\u3092\u3057\u305f",
        )
        == []
    )


def test_anki_marker_tags_for_adjectives():
    client = AnkiClient(ConfigManager("config.json"))

    class Feature:
        def __init__(self, pos1, pos3="", ctype="", lemma=""):
            self.pos1 = pos1
            self.pos2 = ""
            self.pos3 = pos3
            self.cType = ctype
            self.lemma = lemma

    class Token:
        def __init__(self, surface, feature):
            self.surface = surface
            self.feature = feature

    def fake_tagger(text):
        if text == "\u697d\u3057\u304b\u3063\u305f":
            return [
                Token("\u697d\u3057\u304b\u3063", Feature("\u5f62\u5bb9\u8a5e", lemma="\u697d\u3057\u3044")),
                Token("\u305f", Feature("\u52a9\u52d5\u8a5e", lemma="\u305f")),
            ]
        if text == "\u9759\u304b\u306a":
            return [
                Token("\u9759\u304b", Feature("\u5f62\u72b6\u8a5e", lemma="\u9759\u304b")),
                Token("\u306a", Feature("\u52a9\u52d5\u8a5e", ctype="\u52a9\u52d5\u8a5e-\u30c0", lemma="\u3060")),
            ]
        if text == "\u7121\u7406\u306a":
            return [
                Token("\u7121\u7406", Feature("\u540d\u8a5e", pos3="\u5f62\u72b6\u8a5e\u53ef\u80fd", lemma="\u7121\u7406")),
                Token("\u306a", Feature("\u52a9\u52d5\u8a5e", ctype="\u52a9\u52d5\u8a5e-\u30c0", lemma="\u3060")),
            ]
        return []

    client._tagger = fake_tagger

    assert client._anki_marker_tags_for_selection("\u697d\u3057\u304b\u3063\u305f") == ["\u3044-Adj"]
    assert client._anki_marker_tags_for_selection("\u9759\u304b\u306a") == ["\u306a-Adj"]
    assert client._anki_marker_tags_for_selection("\u7121\u7406\u306a") == ["\u306a-Adj"]


def test_copy_existing_sentence_media_fields_from_same_subtitle(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
        client.sound_field,
        client.image_field,
    })
    monkeypatch.setattr(client, "_to_furigana_brackets", lambda text, **_kwargs: f"ruby:{text}")

    fields = client._build_note_fields(
        selected="\u65b0\u898f",
        subtitle="\u540c\u3058\u5b57\u5e55",
        word_translation="new",
        sentence_translation="",
    )
    calls = []

    def fake_invoke(action, params=None):
        calls.append((action, params))
        if action == "findNotes":
            return [101, 202]
        if action == "notesInfo":
            return [
                {
                    "noteId": 101,
                    "modelName": client.model_name,
                    "fields": {
                        client.add_rubies_to_front_field: {"value": "別の単語"},
                        client.front_field: {"value": "ruby:別の単語"},
                        client.add_rubies_to_sentence_ja_field: {"value": "\u5225\u306e\u5b57\u5e55"},
                        client.sound_field: {"value": "[sound:wrong.mp3]"},
                        client.image_field: {"value": '<img src="wrong.png">'},
                    },
                },
                {
                    "noteId": 202,
                    "modelName": client.model_name,
                    "fields": {
                        client.add_rubies_to_front_field: {"value": "別の単語"},
                        client.front_field: {"value": "ruby:別の単語"},
                        client.add_rubies_to_sentence_ja_field: {"value": "\u540c\u3058\u5b57\u5e55"},
                        client.sound_field: {"value": "[sound:asbp_clip.mp3]"},
                        client.image_field: {"value": '<img src="asbp_clip.png">'},
                    },
                },
            ]
        return None

    monkeypatch.setattr(client, "_invoke", fake_invoke)

    copied = client._copy_existing_sentence_media_fields(fields)

    assert copied == {
        client.sound_field: "[sound:asbp_clip.mp3]",
        client.image_field: '<img src="asbp_clip.png">',
    }
    assert fields[client.sound_field] == "[sound:asbp_clip.mp3]"
    assert fields[client.image_field] == '<img src="asbp_clip.png">'
    assert calls[0] == ("findNotes", {"query": '"\u540c\u3058\u5b57\u5e55"'})


def test_copy_existing_sentence_media_fields_falls_back_to_deck_when_sentence_query_misses(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
        client.sound_field,
        client.image_field,
    })
    monkeypatch.setattr(client, "_to_furigana_brackets", lambda text, **_kwargs: f"ruby:{text}")

    fields = client._build_note_fields(
        selected="新規",
        subtitle="同じ字幕",
        word_translation="new",
        sentence_translation="",
    )

    def fake_invoke(action, params=None):
        if action == "findNotes":
            if params and params.get("query") == f'deck:{client._quote_anki_search_text(client.deck_name)}':
                return [777]
            return []
        if action == "notesInfo":
            return [
                {
                    "noteId": 777,
                    "modelName": client.model_name,
                    "fields": {
                        client.add_rubies_to_front_field: {"value": "新規"},
                        client.front_field: {"value": "ruby:新規"},
                        client.add_rubies_to_sentence_ja_field: {"value": "同じ字幕"},
                        client.sound_field: {"value": "[sound:fallback.mp3]"},
                        client.image_field: {"value": '<img src="fallback.png">'},
                    },
                }
            ]
        return None

    monkeypatch.setattr(client, "_invoke", fake_invoke)

    copied = client._copy_existing_sentence_media_fields(fields)

    assert copied == {
        client.sound_field: "[sound:fallback.mp3]",
        client.image_field: '<img src="fallback.png">',
    }
    assert fields[client.sound_field] == "[sound:fallback.mp3]"
    assert fields[client.image_field] == '<img src="fallback.png">'


def test_add_from_selection_copies_media_before_creating_note(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))
    monkeypatch.setattr(client, "_card_headword_for_selection", lambda text: text)
    monkeypatch.setattr(client, "_translate_word", lambda _text: "word")
    monkeypatch.setattr(client, "_translate_sentence", lambda _text: "")
    monkeypatch.setattr(client, "_translate_google", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(client, "_to_furigana_brackets", lambda text, **_kwargs: f"ruby:{text}")
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
        client.sound_field,
        client.image_field,
    })
    monkeypatch.setattr(client, "_route_new_cards", lambda _note_id: {"reading": [], "reverse": [], "unrouted": []})
    notes = []

    def fake_invoke(action, params=None):
        if action == "findNotes":
            return [555]
        if action == "notesInfo":
            return [
                {
                    "noteId": 555,
                    "modelName": client.model_name,
                    "fields": {
                        client.add_rubies_to_sentence_ja_field: {"value": "\u540c\u3058\u5b57\u5e55"},
                        client.sound_field: {"value": "[sound:asbp_existing.mp3]"},
                        client.image_field: {"value": '<img src="asbp_existing.png">'},
                    },
                }
            ]
        if action == "multi":
            return [{"result": None, "error": None} for _ in params["actions"]]
        if action == "addNote":
            notes.append(params["note"])
            return 777
        return None

    monkeypatch.setattr(client, "_invoke", fake_invoke)

    result = client.add_from_selection("\u65b0\u898f", subtitle_text="\u540c\u3058\u5b57\u5e55")

    fields = notes[0]["fields"]
    assert fields[client.sound_field] == "[sound:asbp_existing.mp3]"
    assert fields[client.image_field] == '<img src="asbp_existing.png">'
    assert result["copied_media_fields"] == {
        client.sound_field: "[sound:asbp_existing.mp3]",
        client.image_field: '<img src="asbp_existing.png">',
    }

    notes.clear()
    capture_result = client.add_from_selection(
        "\u65b0\u898f",
        subtitle_text="\u540c\u3058\u5b57\u5e55",
        copy_existing_media=False,
    )
    capture_fields = notes[0]["fields"]
    assert capture_fields[client.sound_field] == ""
    assert capture_fields[client.image_field] == ""
    assert capture_result["copied_media_fields"] == {}


def test_copy_captured_sentence_media_to_all_previous_same_sentence_notes(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))
    update_calls = []

    def fake_invoke(action, params=None):
        if action == "notesInfo" and params == {"notes": [300]}:
            return [
                {
                    "noteId": 300,
                    "modelName": client.model_name,
                    "fields": {
                        client.add_rubies_to_sentence_ja_field: {"value": "\u540c\u3058\u5b57\u5e55"},
                        client.sentence_ja_field: {"value": "ruby:\u540c\u3058\u5b57\u5e55"},
                        client.sound_field: {"value": "[sound:new_capture.mp3]"},
                        client.image_field: {"value": '<img src="new_capture.png">'},
                    },
                }
            ]
        if action == "findNotes":
            return [101, 202, 300]
        if action == "notesInfo":
            return [
                {
                    "noteId": 202,
                    "modelName": client.model_name,
                    "fields": {
                        client.add_rubies_to_sentence_ja_field: {"value": "\u540c\u3058\u5b57\u5e55"},
                        client.sentence_ja_field: {"value": "ruby:\u540c\u3058\u5b57\u5e55"},
                        client.sound_field: {"value": ""},
                        client.image_field: {"value": ""},
                    },
                },
                {
                    "noteId": 101,
                    "modelName": client.model_name,
                    "fields": {
                        client.add_rubies_to_sentence_ja_field: {"value": "\u540c\u3058\u5b57\u5e55"},
                        client.sentence_ja_field: {"value": "ruby:\u540c\u3058\u5b57\u5e55"},
                        client.sound_field: {"value": ""},
                        client.image_field: {"value": ""},
                    },
                },
            ]
        if action == "updateNoteFields":
            update_calls.append(params)
            return None
        return None

    monkeypatch.setattr(client, "_invoke", fake_invoke)

    result = client.copy_captured_sentence_media_to_previous_note(300)

    assert result["target_note_id"] == 202
    assert result["target_note_ids"] == [202, 101]
    assert result["copied_note_ids"] == [101, 202]
    assert result["reason"] == "copied"
    assert result["copied"] == {
        client.sound_field: "[sound:new_capture.mp3]",
        client.image_field: '<img src="new_capture.png">',
    }
    assert update_calls == [
        {
            "note": {
                "id": 202,
                "fields": {
                    client.sound_field: "[sound:new_capture.mp3]",
                    client.image_field: '<img src="new_capture.png">',
                },
            }
        },
        {
            "note": {
                "id": 101,
                "fields": {
                    client.sound_field: "[sound:new_capture.mp3]",
                    client.image_field: '<img src="new_capture.png">',
                },
            }
        },
    ]


def test_prepare_note_does_not_add_until_commit(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))
    monkeypatch.setattr(client, "_card_headword_for_anki", lambda selected, _subtitle: selected)
    monkeypatch.setattr(client, "_anki_marker_tags_for_selection", lambda *_args: [])
    monkeypatch.setattr(client, "_translate_word", lambda _text: "meaning")
    monkeypatch.setattr(client, "_translate_sentence", lambda _text: "sentence translation")
    monkeypatch.setattr(client, "_collect_translation_candidates", lambda *_args, **_kwargs: {"word": {}, "sentence": {}})
    monkeypatch.setattr(client, "_detect_translation_provider", lambda **_kwargs: "test")
    monkeypatch.setattr(client, "_to_furigana_brackets", lambda text, **_kwargs: text)
    monkeypatch.setattr(client, "_copy_existing_sentence_media_fields", lambda _fields: {})
    monkeypatch.setattr(client, "_ensure_decks", lambda _decks: None)
    monkeypatch.setattr(client, "_route_new_cards", lambda _note_id: {"reading": [], "reverse": [], "unrouted": []})
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
    })
    added_notes = []

    def fake_invoke(action, params=None):
        if action == "addNote":
            added_notes.append(params["note"])
            return 123
        return None

    monkeypatch.setattr(client, "_invoke", fake_invoke)

    prepared = client.prepare_note_from_selection("\u65b0\u898f", "\u6587")
    prepared["note"]["fields"][client.back_field] = "edited meaning"

    assert added_notes == []

    result = client.commit_prepared_note(prepared)

    assert result["note_id"] == 123
    assert added_notes[0]["fields"][client.back_field] == "edited meaning"


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

    rendered_with_source_space = client._segments_to_bracket_text(
        [
            ("\u98df", "\u305f"),
            ("\u3079\u308b ", None),
            ("\u6f22", "\u304b\u3093"),
            ("\u5b57", "\u3058"),
        ],
        sentence_spacing=True,
    )
    assert rendered_with_source_space == "\u98df[\u305f]\u3079\u308b \u6f22[\u304b\u3093] \u5b57[\u3058]"


def test_anki_ruby_adds_katakana_ruby_and_common_reading_overrides():
    client = AnkiClient(ConfigManager("config.json"))

    rendered = client._to_furigana_brackets(
        "\u30aa\u30ea\u30f3\u30d4\u30c3\u30af "
        "\u30df\u30ae\u30fc "
        "\u30d1\u30e9\u30b5\u30a4\u30c8\u3067\u5341\u5206\u3060",
        collapse_inline_reading=True,
        sentence_spacing=True,
    )

    assert rendered == (
        "\u30aa\u30ea\u30f3\u30d4\u30c3\u30af[\u304a\u308a\u3093\u3074\u3063\u304f] "
        "\u30df\u30ae\u30fc[\u307f\u304e\u30fc] "
        "\u30d1\u30e9\u30b5\u30a4\u30c8[\u3071\u3089\u3055\u3044\u3068]\u3067 "
        "\u5341\u5206[\u3058\u3085\u3046\u3076\u3093]\u3060"
    )


def test_sentence_ruby_merges_adjacent_katakana_ruby_tokens():
    client = AnkiClient(ConfigManager("config.json"))

    rendered = client._to_furigana_brackets(
        "\u30df\u30ae\u30fc\u306e \u7d30\u80de\u304c",
        collapse_inline_reading=True,
        sentence_spacing=True,
    )

    assert rendered == "\u30df\u30ae\u30fc[\u307f\u304e\u30fc]\u306e \u7d30\u80de[\u3055\u3044\u307c\u3046]\u304c"


def test_sentence_ruby_merges_adjacent_kanji_ruby_tokens_when_split_is_off():
    config = ConfigManager("config.json")
    config.config["ANKI_SPLIT_KANJI_MORAS"] = False
    client = AnkiClient(config)

    assert client._merge_adjacent_kanji_ruby_segments(
        [
            ("\u773e", "\u307e"),
            ("\u767d", "\u3057\u308d"),
            ("\u3061\u3083\u3093", None),
        ]
    ) == [
        ("\u773e\u767d", "\u307e\u3057\u308d"),
        ("\u3061\u3083\u3093", None),
    ]
    assert client._to_furigana_brackets(
        "\u5df1\u306e \u7121\u4fa1\u5024\u3055\u3092",
        collapse_inline_reading=True,
        sentence_spacing=True,
    ) == "\u5df1[\u304a\u306e\u308c]\u306e \u7121\u4fa1\u5024[\u3080\u304b\u3061]\u3055\u3092"


def test_sentence_ruby_does_not_merge_kanji_tokens_across_source_space():
    config = ConfigManager("config.json")
    config.config["ANKI_SPLIT_KANJI_MORAS"] = False
    client = AnkiClient(config)

    rendered = client._to_furigana_brackets(
        "\u4ea4\u901a \u60c5\u5831\u3067\u3059",
        collapse_inline_reading=True,
        sentence_spacing=True,
    )

    assert rendered == "\u4ea4\u901a[\u3053\u3046\u3064\u3046] \u60c5\u5831[\u3058\u3087\u3046\u307b\u3046]\u3067\u3059"


def test_sentence_ruby_adds_boundary_before_katakana_ruby_word():
    client = AnkiClient(ConfigManager("config.json"))

    rendered = client._to_furigana_brackets(
        "\u3053\u3053\u306f\u30de\u30c3\u30b7\u30e5\u3084 "
        "\u30d5\u30a3\u30f3\u304c \u6240\u5c5e\u3059\u308b\u30a2\u30c9\u30e9 \u5bee",
        collapse_inline_reading=True,
        sentence_spacing=True,
    )

    assert rendered == (
        "\u3053\u3053\u306f \u30de\u30c3\u30b7\u30e5[\u307e\u3063\u3057\u3085]\u3084 "
        "\u30d5\u30a3\u30f3[\u3075\u3043\u3093]\u304c "
        "\u6240\u5c5e[\u3057\u3087\u305e\u304f]\u3059\u308b "
        "\u30a2\u30c9\u30e9[\u3042\u3069\u3089] "
        "\u5bee[\u308a\u3087\u3046]"
    )


def test_sentence_ruby_does_not_add_space_inside_middle_dot_katakana_name():
    client = AnkiClient(ConfigManager("config.json"))

    rendered = client._to_furigana_brackets(
        "\u305d\u308c\u3067\u306f\u30de\u30c3\u30b7\u30e5\u30fb\u30d0\u30fc\u30f3\u30c7\u30c3\u30c9",
        collapse_inline_reading=True,
        sentence_spacing=True,
    )

    assert rendered.startswith("\u305d\u308c\u3067\u306f ")
    assert "\u30fb " not in rendered


def test_split_all_kanji_chars_keeps_iteration_mark_with_word():
    client = AnkiClient(ConfigManager("config.json"))

    assert client._split_all_kanji_chars("\u6211\u3005", "\u308f\u308c\u308f\u308c") == [
        ("\u6211\u3005", "\u308f\u308c\u308f\u308c"),
    ]
    assert client._split_all_kanji_chars("\u6642\u3005", "\u3068\u304d\u3069\u304d") == [
        ("\u6642\u3005", "\u3068\u304d\u3069\u304d"),
    ]


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


def test_collect_translation_candidates_can_fetch_missing_google_sentence(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))
    calls = []

    def fake_google(text, source_lang, target_lang):
        calls.append((text, source_lang, target_lang))
        return "Google Satz"

    monkeypatch.setattr(client, "_translate_google", fake_google)

    candidates = client._collect_translation_candidates(
        "\u6f22\u5b57",
        "\u6f22\u5b57\u3092\u8aad\u3080",
        sentence_translation="DeepL Satz",
        fetch_missing_google_sentence=True,
    )

    assert candidates["sentence"]["google"] == "Google Satz"
    assert calls == [("\u6f22\u5b57\u3092\u8aad\u3080", "ja", client.sentence_target_lang)]


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


def test_add_note_dropped_response_uses_verified_existing_note():
    client = AnkiClient(ConfigManager("config.json"))
    fields = {
        client.add_rubies_to_front_field: "\u8a66\u9a13",
        client.front_field: "\u8a66\u9a13[\u3057\u3051\u3093]",
        client.back_field: "test",
        client.add_rubies_to_sentence_ja_field: "\u8a66\u9a13\u3060",
        client.sentence_ja_field: "\u8a66\u9a13[\u3057\u3051\u3093]\u3060",
    }
    calls = []

    def fake_invoke(action, params=None, **kwargs):
        calls.append(action)
        if action == "addNote":
            raise AnkiConnectRequestError("dropped")
        if action == "findNotes":
            return [42]
        if action == "notesInfo":
            return [
                {
                    "noteId": 42,
                    "modelName": client.model_name,
                    "fields": {name: {"value": value} for name, value in fields.items()},
                }
            ]
        return None

    client._invoke = fake_invoke

    assert client._add_note_without_duplicate_retry({"fields": fields}, fields) == 42
    assert calls == ["addNote", "findNotes", "findNotes", "findNotes", "findNotes", "notesInfo"]


def test_add_from_selection_reports_routing_failure_after_note_creation(monkeypatch):
    client = AnkiClient(ConfigManager("config.json"))

    monkeypatch.setattr(client, "_card_headword_for_anki", lambda text, _subtitle="": text)
    monkeypatch.setattr(client, "_anki_marker_tags_for_selection", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(client, "_translate_word", lambda _text: "test")
    monkeypatch.setattr(client, "_translate_sentence", lambda _text: "")
    monkeypatch.setattr(client, "_to_furigana_brackets", lambda text, **_kwargs: f"ruby:{text}")
    monkeypatch.setattr(client, "_copy_existing_sentence_media_fields", lambda _fields: {})
    monkeypatch.setattr(client, "_ensure_decks", lambda _decks: None)
    monkeypatch.setattr(client, "_add_note_without_duplicate_retry", lambda _note, _fields: 123)
    monkeypatch.setattr(client, "_route_new_cards", lambda _note_id: (_ for _ in ()).throw(AnkiConnectRequestError("closed")))
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
        client.definition_field,
    })

    result = client.add_from_selection("\u8a66\u9a13", subtitle_text="")

    assert result["note_id"] == 123
    assert result["routing_error"] == "closed"
    assert result["routed_cards"] == {"reading": [], "reverse": [], "unrouted": []}
