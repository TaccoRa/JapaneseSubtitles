"""Archive retired playback settings without deleting profile data."""
RETIRED = {
    "ANIME_OFFSETS", "EXTRA_OFFSET", "STARTUP_RESUME_PLAY",
    "VIDEO_SYNC_INTERVAL_MS", "VIDEO_SYNC_THRESHOLD", "SKIP_BUTTONS_USE_SUBTITLE_SEGMENTS",
    "VOICE_PLAYBACK_TARGET", "VOICE_PLAYBACK_HOTKEY", "VOICE_PLAYBACK_BACK_HOTKEY",
    "VOICE_PLAYBACK_FORWARD_HOTKEY", "VOICE_REPEAT_HOTKEY_INTERVAL_MS",
    "CAPTURE_ONLY_RESUME_PLAYBACK", "ANKI_AUTO_JUMP_AFTER_ADD",
    "ASBPLAYER_RESUME_PLAYBACK", "ASBPLAYER_CAPTURE_ONLY_RESUME",
}


def retired(key):
    return key in RETIRED or key.startswith(("OCR_", "POST_ADD_CAPTURE_"))


def migrate(values):
    result = dict(values)
    archive = dict(result.get("ARCHIVED_PLAYBACK_SETTINGS") or {})
    if not result.get("CAPTURE_ONLY_Z_SHORTCUT_VERSION"):
        current = str(result.get("SHORTCUT_CAPTURE_ONLY", "shift+v")).strip()
        if "z" not in [part.strip().lower() for part in current.replace(";", ",").split(",")]:
            archive["SHORTCUT_CAPTURE_ONLY_BEFORE_Z"] = current
            result["SHORTCUT_CAPTURE_ONLY"] = current + ", z" if current else "z"
        result["CAPTURE_ONLY_Z_SHORTCUT_VERSION"] = 1
    if "CAPTURE_SCREENSHOT_POSITION" not in result and "CAPTURE_SCREENSHOT_DELAY_MS" in result:
        from model.screenshot_position import ScreenshotPosition
        try:
            result["CAPTURE_SCREENSHOT_POSITION"] = ScreenshotPosition.parse(result["CAPTURE_SCREENSHOT_DELAY_MS"]).canonical()
        except ValueError:
            result["CAPTURE_SCREENSHOT_POSITION"] = "500 ms"
        archive["CAPTURE_SCREENSHOT_DELAY_MS"] = result.pop("CAPTURE_SCREENSHOT_DELAY_MS")
    # Native-only profiles previously archived the backend. Keep that archive;
    # new and migrated profiles start Native until deliberately changed.
    if "CAPTURE_BACKEND" in archive:
        result.setdefault("CAPTURE_BACKEND", "native")
    if "CAPTURE_BACKEND" in result and result["CAPTURE_BACKEND"] not in ("native", "asbplayer"):
        result["CAPTURE_BACKEND"] = "native"
    # Re-expose the old recorder's preferences under adapter-specific keys,
    # without reviving legacy arrows/OCR/simulated playback globally.
    from model.asbplayer_legacy import LEGACY_SETTINGS
    if not result.get("MINING_PLAYBACK_POLICY_VERSION"):
        def resume(key):
            old, default = LEGACY_SETTINGS[key]
            return result.get(key, archive.get(key, result.get(old, archive.get(old, default))))
        if result.get("CAPTURE_BACKEND") == "asbplayer":
            result["POST_CAPTURE_PLAYBACK"] = "play" if resume("ASBPLAYER_RESUME_PLAYBACK") else "paused"
            result["POST_CAPTURE_ONLY_PLAYBACK"] = "play" if resume("ASBPLAYER_CAPTURE_ONLY_RESUME") else "paused"
        else:
            result.setdefault("POST_CAPTURE_PLAYBACK", "play")
            result.setdefault("POST_CAPTURE_ONLY_PLAYBACK", result["POST_CAPTURE_PLAYBACK"])
        result["MINING_PLAYBACK_POLICY_VERSION"] = 1
    for new, (old, _default) in LEGACY_SETTINGS.items():
        if retired(new):
            continue
        if new not in result and (old in result or old in archive):
            result[new] = result.get(old, archive.get(old))
            if new == "ASBPLAYER_CAPTURE_DELAY_MS":
                try:
                    result[new] = max(0, int(float(str(result[new]).replace(",", "."))))
                except (TypeError, ValueError):
                    result[new] = 0
    for key in list(result):
        if retired(key):
            archive[key] = result.pop(key)
    if archive:
        result["ARCHIVED_PLAYBACK_SETTINGS"] = archive
        result["NATIVE_PLAYBACK_VERSION"] = 2
    for key in ("POST_CAPTURE_PLAYBACK", "POST_CAPTURE_ONLY_PLAYBACK"):
        if result.get(key) not in ("play", "paused", "restore"):
            result[key] = "play"
    return result
