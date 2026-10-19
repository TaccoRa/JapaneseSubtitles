"""The original asbplayer capture hotkey, with Native remaining independent."""
from dataclasses import replace
from types import SimpleNamespace as NS
import queue
import pytest
from model.asbplayer_legacy import LegacyAsbplayerCapture,legacy_value
from model.capture_session import CaptureSession,FrozenActionTarget
from model.native_settings import migrate
from model.anki_client import AnkiClient
from test_native_capture import browser


def config(values=None,archive=None):
    local=values or {}
    return NS(local_config=local,get=lambda key: archive if key=="ARCHIVED_PLAYBACK_SETTINGS" else local.get(key))


def test_archived_original_hotkey_title_delay_and_resume_are_restored():
    archive=dict(POST_ADD_CAPTURE_EXTERNAL_HOTKEY="ctrl+alt+y",POST_ADD_CAPTURE_TARGET_TITLE="My Chrome",
                 POST_ADD_CAPTURE_DELAY_MS=500,POST_ADD_CAPTURE_RESUME_PLAYBACK=True)
    cfg=config(archive=archive)
    assert legacy_value(cfg,"ASBPLAYER_CAPTURE_HOTKEY")=="ctrl+alt+y"
    assert legacy_value(cfg,"ASBPLAYER_TARGET_TITLE")=="My Chrome"
    assert LegacyAsbplayerCapture(cfg).delay_ms()==500
    migrated=migrate({"ARCHIVED_PLAYBACK_SETTINGS":archive})
    assert migrated["ASBPLAYER_CAPTURE_HOTKEY"]=="ctrl+alt+y"
    assert migrated["ASBPLAYER_RESUME_PLAYBACK"] is True
    assert migrated["ARCHIVED_PLAYBACK_SETTINGS"]==archive
    assert not any("PROXY" in key or "BRIDGE_PORT" in key for key in migrated)


def test_original_capture_focuses_original_window_and_sends_one_original_shortcut(monkeypatch):
    import model.asbplayer_legacy as module
    calls=[]
    monkeypatch.setattr(module,"focus_window_by_title",lambda title:calls.append(("focus",title)) or True)
    monkeypatch.setattr(module,"send_global_hotkey",lambda hotkey:calls.append(("key",hotkey)) or True)
    hotkeys=NS()
    assert LegacyAsbplayerCapture(config()).dispatch(hotkeys)=="ctrl+shift+y"
    assert calls==[("focus","ABSPlayer, Google Chrome, Chrome"),("key","ctrl+shift+y")]
    assert hotkeys._legacy_capture_chord=="ctrl+shift+y" and hotkeys._legacy_capture_until>module.time.monotonic()


def test_missing_window_does_not_send_to_whichever_app_is_active(monkeypatch):
    import model.asbplayer_legacy as module
    monkeypatch.setattr(module,"focus_window_by_title",lambda title:False)
    monkeypatch.setattr(module,"send_global_hotkey",lambda key:pytest.fail("Wrong foreground target"))
    with pytest.raises(ValueError,match="window not found"):
        LegacyAsbplayerCapture(config()).dispatch()


def legacy_browser():
    sync=browser()
    sync.controller.config=config({"CAPTURE_BACKEND":"asbplayer"})
    sync._asb=LegacyAsbplayerCapture(sync.controller.config)
    sync._results=queue.Queue()
    sync._preference_media=lambda:"movie"
    return sync


def session():
    target=FrozenActionTarget.create(selected_text="word",subtitle_text="sentence",sentence_key="s",
                                     cue_snapshot=NS(start_time=10,end_time=12,cue_index=0))
    return CaptureSession(target)


def test_legacy_needs_neither_native_permission_alignment_nor_asb_api_connection():
    sync=legacy_browser()
    sync._native_enabled=False;sync.mapping=None;sync.snapshot=None;sync.identity=None;sync.following=False
    operation=session();sync.freeze_capture(operation)
    assert operation.backend=="asbplayer" and operation.mapped_interval==(10,12)
    sync.capture(operation)
    assert operation.phase=="waiting_note" and not sync.sent


def test_legacy_waits_for_created_note_before_any_capture_hotkey(tmp_path):
    sync=legacy_browser();operation=session();sync.freeze_capture(operation);sync.capture(operation)
    calls=[]
    sync.controller.anki=NS(capture_note_snapshot=lambda note:calls.append(("snapshot",note)) or {"fields":{},"tags":[]},
                           _invoke=lambda action,params:calls.append((action,params)) or [99])
    sync.bind_capture_note(operation,99)
    kind,operation_id,result=sync._results.get(timeout=3)
    assert kind=="asb-ready" and operation_id==operation.id
    assert calls[0]==("snapshot",99) and not sync.sent
    assert operation.target_note_id==99 and operation.phase=="preparing"
    sync.bind_capture_note(operation,99)
    assert sync._results.empty()


def test_capture_only_refuses_an_unrelated_last_added_card():
    sync=legacy_browser();operation=session();sync.freeze_capture(operation)
    sync.controller.anki=NS(capture_note_snapshot=lambda note:{"fields":{},"tags":[]},_invoke=lambda *args:[99,100])
    sync.capture(operation,99)
    kind,_,error=sync._results.get(timeout=3)
    assert kind=="asb-result" and "Another card was added" in error
    assert not sync.sent


def test_late_cancelled_capture_does_not_send_shortcut(tmp_path):
    sync=legacy_browser();operation=session();sync.freeze_capture(operation);sync.capture(operation)
    scheduled=[]
    sync.controller.settings=NS(root=NS(after=lambda delay,fn:scheduled.append(fn)))
    sync._asb=NS(delay_ms=lambda:0,dispatch=lambda *_:pytest.fail("Cancelled hotkey"))
    sync._dispatch_asb_capture(operation,{"baseline":{}})
    operation.cancel();scheduled[0]()
    assert not sync.sent


def test_completion_keeps_continued_source_playback_instead_of_rewinding():
    sync=legacy_browser();operation=session();sync.freeze_capture(operation)
    sync._control_generation=0
    sync.snapshot=replace(sync.snapshot,paused=False,current_time=30)
    sync.command=lambda *args,**kw:pytest.fail("Already beyond clip end")
    sync._finish_asb_playback(operation)


def test_completion_has_one_end_jump_and_one_optional_play():
    from controller.browser_sync_controller import BrowserSyncController
    sync=legacy_browser();operation=session();sync.freeze_capture(operation)
    sync._capture=None;sync._control_generation=0
    sync.snapshot=replace(sync.snapshot,current_time=11.5)
    operation.post_capture_policy="play"
    sync.command=BrowserSyncController.command.__get__(sync)
    calls=[]
    sync.bridge.command=lambda identity,action,**kw:calls.append((action,kw)) or str(len(calls))
    sync._finish_asb_playback(operation)
    assert calls==[("seek",{"time":13.0})]
    sync._event(dict(type="CommandAck",operationId="1",ok=True))
    assert calls==[("seek",{"time":13.0}),("play",{})]


def test_manual_action_overrides_pending_legacy_resume():
    from controller.browser_sync_controller import BrowserSyncController
    sync=legacy_browser();operation=session();sync.freeze_capture(operation)
    sync._capture=None;sync._control_generation=0
    sync.snapshot=replace(sync.snapshot,current_time=11.5)
    operation.post_capture_policy="play"
    sync.command=BrowserSyncController.command.__get__(sync)
    calls=[]
    sync.bridge.command=lambda identity,action,**kw:calls.append(action) or str(len(calls))
    sync._finish_asb_playback(operation);sync.command("pause")
    sync._event(dict(type="CommandAck",operationId="1",ok=True))
    assert calls==["seek","pause"]


def test_other_cards_media_cannot_finish_an_explicit_fresh_capture():
    anki=object.__new__(AnkiClient);anki.sound_field="Sound";anki.image_field="Image"
    note={"noteId":99,"fields":{name:{"value":value} for name,value in dict(Front="word",Sound="",Image="").items()},"tags":[]}
    calls=[]
    anki._invoke=lambda action,params:calls.append((action,params)) or [note]
    assert not anki.verify_asbplayer_capture(99,{"fields":dict(Front="word",Sound="",Image=""),"tags":[]})
    assert calls==[("notesInfo",{"notes":[99]})]
