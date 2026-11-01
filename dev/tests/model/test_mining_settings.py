"""Regression coverage for future ruby cards, mining policies and total offsets."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from model.anki_client import AnkiClient
from model.config_manager import ConfigManager
from model.capture_session import CaptureSession, FrozenActionTarget, PlaybackPauseLease
from model.cue_snapshot import CueLine, CueSegment, CueSnapshot
from model.native_settings import migrate
from test_native_capture import browser


def cue(lines):
    rendered = []
    for i, segments in enumerate(lines):
        parts = tuple(CueSegment(i, 0, len(base), base, ruby) for base, ruby in segments)
        rendered.append(CueLine(i, str(i), 0, "".join(base for base, _ in segments), parts, parts, ()))
    return CueSnapshot(0, 10, 12, "", tuple(rendered))


def test_snapshot_to_prepared_anki_card_katakana_ruby(monkeypatch):
    snapshot = cue([[("ムカ", "むか"), ("つく", None), ("抜け作", "ぬけさく"), ("の", None)],
                    [("しみったれた", None), ("ツラ", "つら"), ("も", None)]])
    client = AnkiClient(ConfigManager("config.json"))
    monkeypatch.setattr(client, "_invoke", lambda *_: pytest.fail("No personal Anki access"))
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {client.add_rubies_to_front_field,
        client.front_field, client.back_field, client.sentence_ja_field, client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field})
    monkeypatch.setattr(client, "_card_headword_for_anki", lambda *args: "抜け作")
    monkeypatch.setattr(client, "_anki_marker_tags_for_selection", lambda *args: [])
    monkeypatch.setattr(client, "_translate_word", lambda *args: "word")
    monkeypatch.setattr(client, "_translate_sentence", lambda *args: "sentence")
    monkeypatch.setattr(client, "_collect_translation_candidates", lambda *args, **kw: {})
    prepared = client.prepare_note_from_selection("抜け作", cue_snapshot=snapshot, copy_existing_media=False)
    client._encode_sentence_line_breaks_for_anki(prepared["fields"])
    assert prepared["fields"][client.sentence_ja_field] == "ムカ[むか]つく 抜け作[ぬけさく]の<br> しみったれた ツラ[つら]も"


@pytest.mark.parametrize("segments,expected", [
    ([("かな", None), ("ツラ", "つら")], "かな ツラ[つら]"),
    ([("かな", None), ("漢字", "かんじ")], "かな 漢字[かんじ]"),
    ([("第", None), ("2", "に")], "第 2[に]"),
    ([("「", None), ("ツラ", "つら"), ("」", None)], "「ツラ[つら]」"),
    ([("かな\u3000", None), ("ツラ", "つら")], "かな\u3000ツラ[つら]"),
    ([("ツ", "つ"), ("ラ", "ら")], "ツ[つ]ラ[ら]"),
])
def test_snapshot_and_direct_export_share_boundaries(segments, expected):
    from model.text_spacing import normalize_ruby_spacing
    assert cue([segments]).inline_ruby_text == expected
    client = object.__new__(AnkiClient)
    assert client._segments_to_bracket_text(segments, True) == normalize_ruby_spacing(expected)


@pytest.mark.parametrize("backend", ["native", "asbplayer"])
@pytest.mark.parametrize("capture_only", [False, True])
@pytest.mark.parametrize("policy", ["restore", "play", "paused"])
@pytest.mark.parametrize("playing", [False, True])
def test_both_recorders_freeze_separate_policy_and_original_state(backend, capture_only, policy, playing):
    sync = browser()
    values = {"CAPTURE_BACKEND": backend, "POST_CAPTURE_PLAYBACK": "paused",
              "POST_CAPTURE_ONLY_PLAYBACK": "paused"}
    values["POST_CAPTURE_ONLY_PLAYBACK" if capture_only else "POST_CAPTURE_PLAYBACK"] = policy
    sync.controller.config = NS(get=values.get)
    session = CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="s", sentence_key="s",
        cue_snapshot=NS(cue_index=0, start_time=10, end_time=12)), capture_only=capture_only,
        pause_lease=PlaybackPauseLease(was_playing=playing))
    sync.freeze_capture(session)
    assert session.previous_paused is not playing
    assert session.post_capture_policy == policy
    if backend == "native":
        sync.capture(session, 123)
        assert sync.sent[-1]["previousPaused"] is not playing
        assert sync.sent[-1]["policy"] == policy
    else:
        calls = []
        sync.command = lambda action, **kw: calls.append((action, kw))
        sync._finish_asb_playback(session)
        desired_pause = policy == "paused" or policy == "restore" and not playing
        assert calls == ([] if desired_pause else [("play", {})])
        session.cancel()
        calls.clear()
        sync._finish_asb_playback(session)
        assert not calls


def test_policy_migration_keeps_selected_backend_and_archives_old_resume_values():
    original = {"CAPTURE_BACKEND": "asbplayer", "ASBPLAYER_RESUME_PLAYBACK": True,
                "ASBPLAYER_CAPTURE_ONLY_RESUME": False, "PERSONAL": {"keep": 1}}
    new = migrate(original)
    assert new["POST_CAPTURE_PLAYBACK"] == "play"
    assert new["POST_CAPTURE_ONLY_PLAYBACK"] == "paused"
    assert "ASBPLAYER_RESUME_PLAYBACK" not in new
    assert new["ARCHIVED_PLAYBACK_SETTINGS"]["ASBPLAYER_RESUME_PLAYBACK"] is True
    assert new["PERSONAL"] == original["PERSONAL"]
    assert migrate(new) == new
    new["POST_CAPTURE_ONLY_PLAYBACK"] = "restore"
    assert migrate(new)["POST_CAPTURE_ONLY_PLAYBACK"] == "restore"
    native = migrate({"CAPTURE_BACKEND": "native", "POST_CAPTURE_PLAYBACK": "restore"})
    assert native["POST_CAPTURE_ONLY_PLAYBACK"] == "restore"
    assert migrate(native) == native


class Value:
    def __init__(self, value=""):
        self.value = value
    def set(self, value):
        self.value = value
    def get(self):
        return self.value


def timing_browser():
    sync = browser()
    sync.controller.settings = NS(offset_var=Value(), auto_offset_var=Value(), _offset_editing=False)
    sync._preference_context = ("episode", "srt", "captions")
    sync._offset_region = sync._auto_offset = None
    sync._source_hash = "srt"
    sync.alignment_mode = "captions"
    sync._source = lambda: None
    sync._preference_media = lambda: "episode"
    sync.saved_trims = []
    sync.preferences = NS(set_trim=lambda *args: sync.saved_trims.append(args[-1]))
    sync.snapshot = replace(sync.snapshot, current_time=11.5)
    sync._update_timing_ui()
    return sync


def test_total_offset_edit_freezes_base_and_preserves_trim_across_step():
    sync = timing_browser()
    settings = sync.controller.settings
    assert settings.offset_var.get() == "1.000 s"
    settings._offset_editing = True
    sync.begin_offset_edit()
    settings.offset_var.set("0.531 s")
    sync.snapshot = replace(sync.snapshot, current_time=23)
    sync._update_timing_ui()
    assert settings.offset_var.get() == "0.531 s"
    sync.set_effective_offset(.531)
    assert sync._trim == pytest.approx(-.469)
    settings._offset_editing = False
    sync.end_offset_edit()
    assert settings.offset_var.get() == "-7.685 s"
    assert sync.saved_trims == [pytest.approx(-.469)]
    assert not sync.sent


def test_source_switch_discards_pending_total_offset_edit():
    sync = timing_browser()
    sync.begin_offset_edit()
    sync._preference_context = ("other", "srt", "captions")
    sync.set_effective_offset(-1.5)
    assert not sync.saved_trims and not sync.sent


def test_unconfirmed_offset_waits_instead_of_looking_like_zero():
    sync = timing_browser()
    sync.mapping = None
    sync._update_timing_ui()
    assert sync.controller.settings.offset_var.get() == "..."
    sync.begin_offset_edit()
    sync.set_effective_offset(1)
    assert not sync.saved_trims and not sync.sent


def test_refresh_only_resets_trim_on_matching_successful_generation():
    sync = timing_browser()
    sync._generation = 7
    sync._refresh_generation = 6
    sync._trim = -.469
    sync._complete_manual_alignment()
    assert sync._trim == -.469
    sync._refresh_generation = 7
    sync._complete_manual_alignment()
    assert sync._trim == 0 and sync.saved_trims == [0]
    assert sync._refresh_generation is None and not sync.sent
