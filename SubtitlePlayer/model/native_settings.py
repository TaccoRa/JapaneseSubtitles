"""Archive retired playback settings without deleting profile data."""
RETIRED = {
    "ANIME_OFFSETS", "EXTRA_OFFSET", "STARTUP_RESUME_PLAY",
    "VIDEO_SYNC_INTERVAL_MS", "VIDEO_SYNC_THRESHOLD", "SKIP_BUTTONS_USE_SUBTITLE_SEGMENTS",
    "VOICE_PLAYBACK_TARGET", "VOICE_PLAYBACK_HOTKEY", "VOICE_PLAYBACK_BACK_HOTKEY",
    "VOICE_PLAYBACK_FORWARD_HOTKEY", "VOICE_REPEAT_HOTKEY_INTERVAL_MS",
    "CAPTURE_ONLY_RESUME_PLAYBACK", "ANKI_AUTO_JUMP_AFTER_ADD",
}


def retired(key):
    return key in RETIRED or key.startswith(("OCR_", "POST_ADD_CAPTURE_"))


def migrate(values):
    result = dict(values)
    archive = dict(result.get("ARCHIVED_PLAYBACK_SETTINGS") or {})
    # Native-only profiles previously archived the backend. Keep that archive;
    # new and migrated profiles start Native until deliberately changed.
    if "CAPTURE_BACKEND" in archive:
        result.setdefault("CAPTURE_BACKEND", "native")
    if "CAPTURE_BACKEND" in result and result["CAPTURE_BACKEND"] not in ("native", "asbplayer"):
        result["CAPTURE_BACKEND"] = "native"
    # Re-expose the old recorder's preferences under adapter-specific keys,
    # without reviving legacy arrows/OCR/simulated playback globally.
    from model.asbplayer_legacy import LEGACY_SETTINGS
    for new, (old, _default) in LEGACY_SETTINGS.items():
        if new not in result and (old in result or old in archive):
            result[new] = result.get(old, archive.get(old))
    for key in list(result):
        if retired(key):
            archive[key] = result.pop(key)
    if archive:
        result["ARCHIVED_PLAYBACK_SETTINGS"] = archive
        result["NATIVE_PLAYBACK_VERSION"] = 2
    return result
