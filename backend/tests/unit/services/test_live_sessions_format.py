from backend.services.live_sessions import format_transcript_lines, _suggest_title, _clock


def test_clock_formats():
    assert _clock(0) == "00:00"
    assert _clock(65_000) == "01:05"
    assert _clock(3_661_000) == "1:01:01"


def test_format_transcript_skips_echo_and_empty():
    text = format_transcript_lines(
        [
            {"start_ms": 1000, "text": "раз", "track": "local", "speaker": "Вы"},
            {"start_ms": 2000, "text": "эхо", "track": "local", "is_echo": True},
            {"start_ms": 3000, "text": "  ", "track": "remote"},
            {"start_ms": 4000, "text": "два", "track": "remote"},
        ]
    )
    assert "раз" in text
    assert "два" in text
    assert "эхо" not in text
    assert "Собеседник" in text


def test_suggest_title_from_brief():
    md = "## Кратко\nОбсудили релиз AstraSessions\n## Решения\n- ship it"
    assert _suggest_title(md, "Новая сессия") == "Обсудили релиз AstraSessions"
    assert _suggest_title(md, "Уже задано") is None
