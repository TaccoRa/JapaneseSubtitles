import pytest
from SubtitlePlayer.utils import parse_time_value

def test_parse_time_value():
    assert parse_time_value("01:02", 0.0) == 62
    assert parse_time_value("1234", 0.0) == 12 * 60 + 34
    assert parse_time_value("not a time", 0.0) == 0.0

