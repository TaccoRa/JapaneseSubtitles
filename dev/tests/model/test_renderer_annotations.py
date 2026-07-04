from SubtitlePlayer.model.renderer import SubtitleRenderer


class _Config:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key):
        defaults = {
            "SHIFT_HOVER_KANJI_DICTIONARY": False,
            "SUBTITLE_FONT_SIZE": 20,
        }
        defaults.update(self.values)
        return defaults.get(key)


class _Event:
    def __init__(self, x=100, y=25, state=0):
        self.x = x
        self.y = y
        self.state = state


class _Font:
    def measure(self, text):
        return len(str(text or "")) * 10

    def metrics(self, key):
        return 20

    def actual(self, key):
        if key == "family":
            return "Arial"
        if key == "size":
            return 20
        return ""


class _Canvas:
    def __init__(self):
        self.texts = []
        self.rectangles = []
        self.lines = []

    def create_text(self, *args, **kwargs):
        self.texts.append((args, kwargs))

    def create_rectangle(self, *args, **kwargs):
        self.rectangles.append((args, kwargs))

    def create_line(self, *args, **kwargs):
        self.lines.append((args, kwargs))

    def tag_lower(self, *args, **kwargs):
        pass


def _renderer(config=None):
    renderer = SubtitleRenderer(_Canvas(), config or _Config())
    renderer.font = _Font()
    renderer.ruby_font = _Font()
    renderer.color = "white"
    renderer.glow_color = "black"
    renderer.glow_radius = 0
    renderer.ruby_glow_radius = 0
    renderer.line_height = 20
    renderer.ruby_height = 12
    return renderer


def test_renderer_applies_annotation_text_color_and_background():
    renderer = _renderer()
    meta = {
        "annotation": True,
        "normal_style_visible": True,
        "style": {
            "enabled": True,
            "text_color": "#66e07f",
            "background_color": "#102010",
            "underline": True,
            "underline_color": "#66e07f",
            "underline_thickness": 2,
        },
    }

    renderer._render_line([("人間", "にんげん", meta)], ruby_y=5, base_y=25, max_width=200)

    assert renderer.canvas.rectangles
    assert renderer.canvas.lines
    assert any(call[1].get("text") == "人間" and call[1].get("fill") == "#66e07f" for call in renderer.canvas.texts)


def test_renderer_does_not_apply_disabled_annotation_style():
    renderer = _renderer()
    meta = {
        "annotation": True,
        "normal_style_visible": True,
        "style": {"enabled": False, "text_color": "#66e07f", "background_color": "#102010"},
    }

    renderer._render_line([("人間", None, meta)], ruby_y=5, base_y=25, max_width=200)

    assert not renderer.canvas.rectangles
    assert any(call[1].get("text") == "人間" and call[1].get("fill") == "white" for call in renderer.canvas.texts)


def test_renderer_hides_ruby_but_keeps_hover_region():
    renderer = _renderer()
    meta = {
        "annotation": True,
        "normal_style_visible": True,
        "hide_ruby": True,
        "hidden_ruby": "にんげん",
        "show_ruby_on_hover": True,
        "hover_highlight": True,
        "style": {"enabled": True},
    }

    renderer._render_line([("人間", "にんげん", meta)], ruby_y=5, base_y=25, max_width=200)

    assert not any(call[1].get("text") == "にんげん" for call in renderer.canvas.texts)
    assert renderer._hover_regions
    assert renderer._hover_regions[0]["ruby"] == "にんげん"


def test_renderer_annotation_visible_ruby_hover_ignores_hidden_ruby_toggle():
    renderer = _renderer()
    renderer.hover_ruby_enabled = True
    meta = {
        "annotation": True,
        "normal_style_visible": True,
        "hide_ruby": False,
        "show_ruby_on_hover": False,
        "style": {"enabled": True},
    }

    renderer._render_line([("\u4eba\u9593", "\u306b\u3093\u3052\u3093", meta)], ruby_y=5, base_y=25, max_width=200)

    assert renderer._hover_regions
    assert renderer._hover_regions[0]["ruby"] == "\u306b\u3093\u3052\u3093"
    assert renderer._hover_regions[0]["show_ruby_on_hover"] is True


def test_renderer_draws_shared_ruby_group_for_partial_annotation():
    renderer = _renderer()
    group_meta = {
        "ruby_group_id": "0:4:\u3058\u3085\u3046\u3088\u3046\u304d\u304b\u3093",
        "ruby_group_base": "\u91cd\u8981\u5668\u5b98",
        "ruby_group_ruby": "\u3058\u3085\u3046\u3088\u3046\u304d\u304b\u3093",
    }
    annotation_meta = {
        **group_meta,
        "annotation": True,
        "normal_style_visible": True,
        "style": {"enabled": True, "underline": True, "underline_color": "#66e07f"},
    }

    renderer._render_line(
        [
            ("\u91cd\u8981", None, annotation_meta),
            ("\u5668\u5b98", None, group_meta),
        ],
        ruby_y=5,
        base_y=25,
        max_width=300,
    )

    drawn_texts = [call[1].get("text") for call in renderer.canvas.texts]
    assert drawn_texts.count("\u3058\u3085\u3046\u3088\u3046\u304d\u304b\u3093") == 1
    assert "\u91cd\u8981" in drawn_texts
    assert "\u5668\u5b98" in drawn_texts
    assert renderer._hover_regions[0]["ruby"] == "\u3058\u3085\u3046\u3088\u3046\u304d\u304b\u3093"
    assert renderer._hover_regions[0]["base_w"] == 40
    assert renderer._hover_regions[0]["ruby_w"] == 80


def test_renderer_hover_event_state_zero_forces_ruby_even_with_stale_shift_callback():
    renderer = _renderer(_Config({"SHIFT_HOVER_KANJI_DICTIONARY": True}))
    renderer.bind_shift_state(lambda: True)

    assert renderer._hover_mode(_Event(state=0)) == "ruby"


def test_renderer_hover_shift_dictionary_requires_enabled_setting_and_current_shift():
    disabled = _renderer(_Config({"SHIFT_HOVER_KANJI_DICTIONARY": False}))
    disabled.bind_shift_state(lambda: True)
    assert disabled._hover_mode(_Event(state=0x0001)) == "ruby"

    stale = _renderer(_Config({"SHIFT_HOVER_KANJI_DICTIONARY": True}))
    stale.bind_shift_state(lambda: False)
    assert stale._hover_mode(_Event(state=0x0001)) == "ruby"

    enabled = _renderer(_Config({"SHIFT_HOVER_KANJI_DICTIONARY": True}))
    enabled.bind_shift_state(lambda: True)
    assert enabled._hover_mode(_Event(state=0x0001)) == "dictionary"


def test_renderer_hover_modifier_priority_alt_ctrl_shift():
    renderer = _renderer(_Config({"SHIFT_HOVER_KANJI_DICTIONARY": True}))
    renderer.bind_shift_state(lambda: True)

    assert renderer._hover_mode(_Event(state=0x0004)) == "status"
    assert renderer._hover_mode(_Event(state=0x0008)) == "translation"
    assert renderer._hover_mode(_Event(state=0x0008 | 0x0004 | 0x0001)) == "translation"


def test_renderer_normal_hover_draws_annotation_ruby_even_when_annotation_hover_ruby_disabled():
    renderer = _renderer()
    renderer.hover_ruby_enabled = True
    meta = {
        "annotation": True,
        "normal_style_visible": True,
        "hide_ruby": True,
        "hidden_ruby": "\u306b\u3093\u3052\u3093",
        "show_ruby_on_hover": False,
        "style": {"enabled": True},
    }
    dictionary_calls = []
    renderer.bind_dictionary_lookup(lambda text: dictionary_calls.append(text) or "dictionary")
    renderer._render_line([("\u4eba\u9593", "\u306b\u3093\u3052\u3093", meta)], ruby_y=5, base_y=25, max_width=200)

    before = len(renderer.canvas.texts)
    renderer._on_hover_motion(_Event(state=0))
    drawn_texts = [call[1].get("text") for call in renderer.canvas.texts[before:]]

    assert "\u306b\u3093\u3052\u3093" in drawn_texts
    assert dictionary_calls == []


def test_renderer_shift_hover_calls_dictionary_only_when_enabled_and_shift_current():
    renderer = _renderer(_Config({"SHIFT_HOVER_KANJI_DICTIONARY": True}))
    renderer.hover_ruby_enabled = True
    renderer.bind_shift_state(lambda: True)
    dictionary_calls = []
    shown_texts = []
    renderer.bind_dictionary_lookup(lambda text: dictionary_calls.append(text) or "dictionary")
    renderer._show_hover_text = lambda _region, text: shown_texts.append(text)
    renderer._render_line([("\u5207\u308b", "\u304d\u308b")], ruby_y=5, base_y=25, max_width=200)

    renderer._on_hover_motion(_Event(state=0x0001))

    assert dictionary_calls == ["\u5207\u308b"]
    assert shown_texts == ["dictionary"]


def test_renderer_shift_dictionary_uses_hovered_word_not_full_sentence():
    renderer = _renderer(_Config({"SHIFT_HOVER_KANJI_DICTIONARY": True}))
    renderer.hover_ruby_enabled = True
    renderer.bind_shift_state(lambda: True)
    renderer.bind_word_tokenizer(
        lambda _text: [
            {"surface": "\uff11", "lookup": "\uff11", "reading": "\u3044\u3061", "start": 0, "end": 1},
            {"surface": "\u79d2\u3067\u3082", "lookup": "\u79d2", "reading": "\u3073\u3087\u3046", "start": 1, "end": 4},
            {"surface": "\u65e9\u304f", "lookup": "\u65e9\u3044", "reading": "\u306f\u3084\u3044", "start": 4, "end": 6},
        ]
    )
    dictionary_calls = []
    renderer.bind_dictionary_lookup(lambda text: dictionary_calls.append(text) or "dictionary")
    renderer._show_hover_text = lambda _region, _text: None
    renderer._render_line(
        [
            ("\uff11", "\u3044\u3061"),
            ("\u79d2\u3067\u3082", "\u3073\u3087\u3046\u3067\u3082"),
            ("\u65e9\u304f", "\u306f\u3084\u304f"),
        ],
        ruby_y=5,
        base_y=25,
        max_width=300,
    )
    target = next(region for region in renderer._word_regions if region["lookup"] == "\u65e9\u3044")
    x1, y1, x2, y2 = target["bbox"]

    renderer._on_hover_motion(_Event(x=(x1 + x2) / 2, y=(y1 + y2) / 2, state=0x0001))

    assert dictionary_calls == ["\u65e9\u3044"]
