from SubtitlePlayer.model.renderer import SubtitleRenderer


class _Config:
    def get(self, key):
        return {
            "SHIFT_HOVER_KANJI_DICTIONARY": False,
        }.get(key)


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


def _renderer():
    renderer = SubtitleRenderer(_Canvas(), _Config())
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
