import pytest

from SubtitlePlayer.model.wanikani_client import WaniKaniClient, map_srs_stage


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, params))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


def test_map_srs_stages():
    assert map_srs_stage(1) == "wanikani_apprentice"
    assert map_srs_stage(5) == "wanikani_guru"
    assert map_srs_stage(7) == "wanikani_master"
    assert map_srs_stage(8) == "wanikani_enlightened"
    assert map_srs_stage(9) == "wanikani_burned"
    assert map_srs_stage(0) == "wanikani_unlocked"


def test_token_success():
    session = _Session([_Response(200, {"data": {"username": "tobi"}})])
    ok, message = WaniKaniClient("token", session=session).test_token()

    assert ok is True
    assert "tobi" in message


def test_invalid_token():
    session = _Session([_Response(401, {})])
    ok, message = WaniKaniClient("bad", session=session).test_token()

    assert ok is False
    assert "invalid" in message.lower()


def test_sync_pagination_and_subject_mapping():
    session = _Session(
        [
            _Response(
                200,
                {
                    "data": [
                        {"data": {"subject_id": 100, "subject_type": "vocabulary", "srs_stage": 9}},
                    ],
                    "pages": {"next_url": "next-assignments"},
                },
            ),
            _Response(
                200,
                {
                    "data": [
                        {"data": {"subject_id": 101, "subject_type": "kanji", "srs_stage": 5}},
                    ],
                    "pages": {"next_url": None},
                },
            ),
            _Response(
                200,
                {
                    "data": [
                        {
                            "id": 100,
                            "object": "vocabulary",
                            "data": {
                                "characters": "人間",
                                "readings": [{"reading": "にんげん", "primary": True}],
                                "meanings": [{"meaning": "human", "primary": True}],
                            },
                        },
                        {
                            "id": 101,
                            "object": "kanji",
                            "data": {
                                "characters": "人",
                                "readings": [{"reading": "じん", "primary": True}],
                                "meanings": [{"meaning": "person", "primary": True}],
                            },
                        },
                    ],
                    "pages": {"next_url": None},
                },
            ),
        ]
    )

    result = WaniKaniClient("token", session=session).sync()

    assert result.error == ""
    assert result.vocabulary_count == 1
    assert result.kanji_count == 1
    assert {entry.status for entry in result.entries} == {"wanikani_burned", "wanikani_guru"}


def test_network_failure_does_not_crash():
    class FailingSession:
        def get(self, *args, **kwargs):
            raise RuntimeError("network down")

    result = WaniKaniClient("token", session=FailingSession()).sync()

    assert "network down" in result.error
