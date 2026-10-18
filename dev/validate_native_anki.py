"""Opt-in local Anki transport check using a suspended, tagged synthetic note.

This verifies native media attachment and retry, not live browser recording.
"""
import argparse
import base64
import io
import json
from pathlib import Path
import struct
import sys
import urllib.request
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "SubtitlePlayer"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-test-note", action="store_true", required=True)
    parser.parse_args()
    from model.anki_client import AnkiClient
    from PIL import Image, ImageDraw
    def invoke(action, params=None):
        payload = json.dumps(dict(action=action, version=6, params=params or {})).encode()
        request = urllib.request.Request("http://127.0.0.1:8765", data=payload,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
        if result.get("error"):
            raise RuntimeError(f"{action}: {result['error']}")
        return result["result"]
    config = json.loads((ROOT / "config.json").read_text("utf-8-sig"))
    model = config["ANKI_MODEL"]
    sound, image = config["ANKI_FIELD_SOUND"], config["ANKI_FIELD_IMAGE"]
    names = invoke("modelFieldNames", {"modelName": model})
    assert sound in names and image in names, "Test model lacks media fields"
    deck, tag = "SubtitlePlayer::Capture validation", "subtitleplayer::capture_validation"
    invoke("createDeck", {"deck": deck})
    notes = invoke("findNotes", {"query": f"tag:{tag}"})
    if notes:
        note_id = notes[0]
    else:
        fields = {name: "" for name in names}
        fields[config["ANKI_FIELD_FRONT"]] = "SubtitlePlayer synthetic capture validation"
        fields[config["ANKI_FIELD_BACK"]] = "Synthetic media transport test. This is not a recorded video clip."
        for key in ("ANKI_FIELD_ADD_RUBIES_FRONT", "ANKI_FIELD_SENTENCE_JA", "ANKI_FIELD_ADD_RUBIES_SENTENCE_JA"):
            if config.get(key) in fields:
                fields[config[key]] = "SubtitlePlayer synthetic capture validation"
        note_id = invoke("addNote", {"note": dict(deckName=deck, modelName=model, fields=fields,
                       tags=[tag], options={"allowDuplicate": False})})
    cards = invoke("findCards", {"query": f"nid:{note_id}"})
    if cards:
        invoke("suspend", {"cards": cards})
    original = invoke("notesInfo", {"notes": [note_id]})[0]["fields"]
    picture = Image.new("RGB", (480, 120), "#173642")
    ImageDraw.Draw(picture).text((20, 48), "SubtitlePlayer: synthetic Anki media test", fill="white")
    png = io.BytesIO(); picture.save(png, "PNG")
    wav = io.BytesIO()
    with wave.open(wav, "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
        # Quiet ramp exercises nonempty PCM without sending personal audio.
        output.writeframes(b"".join(struct.pack("<h", (i % 80 - 40) * 10) for i in range(3200)))
    audio64, image64 = (base64.b64encode(value.getvalue()).decode() for value in (wav, png))
    client = object.__new__(AnkiClient)
    client.sound_field, client.image_field, client._invoke = sound, image, invoke
    operation = uuid.uuid4().hex
    fields = client.attach_native_capture(note_id, operation, audio64, image64)
    assert client.attach_native_capture(note_id, operation, audio64, image64) == fields
    assert client.verify_capture_media(note_id)
    updated = invoke("notesInfo", {"notes": [note_id]})[0]["fields"]
    assert all(updated[name]["value"] == old["value"] for name, old in original.items() if name not in (sound, image))
    import re
    filenames = [re.search(r"\[sound:(.+)\]", fields[sound])[1], re.search(r'src="([^"]+)"', fields[image])[1]]
    for filename, expected in zip(filenames, (audio64, image64)):
        assert invoke("retrieveMediaFile", {"filename": filename}) == expected
    report = dict(mode="synthetic media / real local Anki", note_id=note_id, deck=deck,
                  audio_and_image_verified=True, retry_same_note=True, text_preserved=True,
                  cards_suspended=True, live_browser_capture_verified=False)
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs/native-anki-report.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
