from backend.services.live_session_echo import is_echo_text, mark_echo_segments
from backend.services.live_session_brief import split_transcript_parts, build_final_prompt


def test_echo_detects_repeated_remote():
    assert is_echo_text(
        "давайте обсудим релиз AstraStudio завтра",
        ["коллеги давайте обсудим релиз AstraStudio завтра утром"],
    )
    assert not is_echo_text("полностью другая мысль про погоду", ["релиз AstraStudio"])


def test_mark_echo_segments():
    segs = [
        {"track": "remote", "start_ms": 1000, "text": "нужно обновить векторный индекс pgvector"},
        {"track": "local", "start_ms": 1500, "text": "нужно обновить векторный индекс pgvector"},
        {"track": "local", "start_ms": 5000, "text": "я согласен с планом"},
    ]
    out = mark_echo_segments(segs)
    assert out[1]["is_echo"] is True
    assert not out[2].get("is_echo")


def test_split_parts_and_final_prompt():
    long = "\n".join(f"[{i:02d}:00] Вы: строка {i} " + ("слово " * 40) for i in range(40))
    parts = split_transcript_parts(long, max_chars=500)
    assert len(parts) > 1
    sys_p, user_p = build_final_prompt(
        participants="A, B",
        source_app="browser",
        instruction=None,
        part_summaries=["часть 1", "часть 2"],
        full_transcript=None,
    )
    assert "Суть" in sys_p or "финальный бриф" in sys_p
    assert "Сводка части" in user_p
