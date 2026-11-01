"""openai_compat.stream_chat: auto-continue презентаций, обрывы, heartbeat/idle, контекстное окно."""

import asyncio
import json

import httpx
import pytest
import respx

from backend.llm_providers import openai_compat as oc
from backend.llm_providers.base import LLMProviderConfig

pytestmark = pytest.mark.unit

BASE = "http://llm.test"
CHAT = f"{BASE}/v1/chat/completions"
MSGS = [{"role": "user", "content": "hi"}]
ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)


def slide(n):
    return f'<div class="slide"><div>slide {n}</div></div>'


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in (
        "LLM_RATE_LIMIT_RETRIES",
        "LLM_AUTO_CONTINUE_MAX",
        "LLM_STREAM_ERROR_RETRIES",
        "LLM_STREAM_IDLE_TIMEOUT",
        "TLS_CERT_PATH",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
    ):
        monkeypatch.delenv(name, raising=False)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(oc.asyncio, "sleep", no_sleep)
    with ROUTER:
        yield
    ROUTER.reset()


def provider(**kw):
    cfg = {"id": "gw", "kind": "openai-compat", "base_url": BASE + "/", **kw}
    return oc.OpenAICompatProvider(LLMProviderConfig(**cfg))


def sse(*events, done=True):
    lines = []
    for e in events:
        lines.append("data: " + (e if isinstance(e, str) else json.dumps(e)))
        lines.append("")
    if done:
        lines += ["data: [DONE]", ""]
    return httpx.Response(200, content=chr(10).join(lines).encode(), headers={"content-type": "text/event-stream"})


def delta(content=None, finish=None, **extra):
    d = {k: v for k, v in {"content": content, **extra}.items() if v is not None}
    return {"choices": [{"delta": d, "finish_reason": finish}]}


async def run(p, callback=None, messages=MSGS, **kw):
    calls = []

    def cb(chunk, accumulated, role="content"):
        calls.append((role, chunk, accumulated))
        return callback(chunk, accumulated, role) if callback else True

    text = await p.stream_chat(messages, "m", cb, **kw)
    return text, calls


def request_bodies(route):
    return [json.loads(c.request.content) for c in route.calls]


class TestPresentationAutoContinue:
    async def test_many_requested_slides_continue_until_count_reached(self):
        pass1 = "```html\n<html><head></head><body>" + slide(1) + slide(2) + "</body></html>\n```"
        pass2 = "".join(slide(i) for i in range(3, 13)) + "</body></html>"
        route = ROUTER.post(CHAT).mock(
            side_effect=[sse(delta(pass1, finish="stop")), sse(delta(pass2, finish="stop"))]
        )
        messages = [{"role": "user", "content": "Сделай презентацию на 12 слайдов"}]

        text, calls = await run(provider(), messages=messages)

        assert route.call_count == 2
        second = request_bodies(route)[1]["messages"]
        assert second[-1]["role"] == "user" and "нужно ровно 12" in second[-1]["content"]
        # документ открыт для продолжения: без закрывающих тегов и закрытого fence
        assistant = second[-2]
        assert assistant["role"] == "assistant"
        assert "</html>" not in assistant["content"] and not assistant["content"].endswith("```")
        assert text.startswith("```html") and text.endswith("```")
        assert text.count('class="slide"') == 12
        # callback получил «пустой» чанк при открытии документа
        assert any(role == "content" and chunk == "" for role, chunk, _ in calls)

    async def test_stop_in_callback_while_opening_document_ends_the_loop(self):
        pass1 = "```html\n<html><head></head><body>" + slide(1) + "</body></html>\n```"
        route = ROUTER.post(CHAT).mock(side_effect=[sse(delta(pass1, finish="stop")), sse(delta("лишнее", finish="stop"))])
        messages = [{"role": "user", "content": "Сделай 10 слайдов"}]

        text, _ = await run(provider(), callback=lambda c, a, r: not (r == "content" and c == ""), messages=messages)

        assert route.call_count == 1
        assert "лишнее" not in text

    async def test_truncated_deck_gets_a_repair_hint(self, monkeypatch):
        monkeypatch.setenv("LLM_AUTO_CONTINUE_MAX", "1")
        # закрывающие теги внутри комментария: документ «оборван», но срезать нечего
        broken = "```html\n<html><body>" + slide(1) + "<!-- x </body></html> -->"
        route = ROUTER.post(CHAT).mock(
            side_effect=[sse(delta(broken, finish="stop")), sse(delta(slide(2), finish="stop"))]
        )

        await run(provider())

        assert route.call_count == 2
        hint = request_bodies(route)[1]["messages"][-1]["content"]
        assert "ответ оборван" in hint and "незакрытый комментарий" in hint

    async def test_plain_length_continue_uses_generic_hint(self, monkeypatch):
        monkeypatch.setenv("LLM_AUTO_CONTINUE_MAX", "1")
        route = ROUTER.post(CHAT).mock(
            side_effect=[sse(delta("начало ", finish="length")), sse(delta("конец", finish="stop"))]
        )

        text, _ = await run(provider())

        assert text == "начало конец"
        assert "Продолжи ровно с того места" in request_bodies(route)[1]["messages"][-1]["content"]

    async def test_reopen_in_continue_pass_is_merged_and_callback_can_stop(self, monkeypatch):
        monkeypatch.setenv("LLM_AUTO_CONTINUE_MAX", "2")
        route = ROUTER.post(CHAT).mock(
            side_effect=[
                sse(delta("начало ", finish="length")),
                sse(delta("```html\nпродолжение", finish="length")),
                sse(delta("не должно быть запрошено", finish="stop")),
            ]
        )

        text, calls = await run(provider(), callback=lambda c, a, r: not (r == "content" and c == ""))

        assert route.call_count == 2
        assert text == "начало продолжение"
        assert ("content", "", "начало продолжение") in calls

    async def test_finished_deck_with_length_is_not_extended(self):
        route = ROUTER.post(CHAT).mock(return_value=sse(delta("Спасибо за внимание", finish="length")))

        text, _ = await run(provider())

        assert route.call_count == 1 and text == "Спасибо за внимание"

    async def test_final_message_is_normalised_to_a_single_html_block(self):
        raw = "<html><head></head><body>" + slide(1) + slide(2) + "</body></html>"
        ROUTER.post(CHAT).mock(return_value=sse(delta(raw, finish="stop")))

        text, calls = await run(provider())

        assert text.startswith("```html\n<!DOCTYPE html>") and text.endswith("```")
        assert ".slide{width:297mm" in text  # подставлен запасной chrome
        role, chunk, accumulated = calls[-1]
        assert (role, chunk) == ("content", "") and accumulated == text.rstrip()

    async def test_continuation_limit_stops_the_loop(self, monkeypatch):
        monkeypatch.setenv("LLM_AUTO_CONTINUE_MAX", "1")
        route = ROUTER.post(CHAT).mock(
            side_effect=[sse(delta("a ", finish="length")), sse(delta("b ", finish="length")), sse(delta("c", finish="stop"))]
        )

        text, _ = await run(provider())

        assert route.call_count == 2 and text == "a b"


class TestStreamErrors:
    async def test_error_before_first_token_is_retried(self, monkeypatch):
        monkeypatch.setenv("LLM_STREAM_ERROR_RETRIES", "2")
        route = ROUTER.post(CHAT).mock(
            side_effect=[httpx.ConnectError("refused"), sse(delta("ok", finish="stop"))]
        )

        text, _ = await run(provider())

        assert route.call_count == 2 and text == "ok"

    async def test_retries_exhausted_returns_the_error_text(self, monkeypatch):
        monkeypatch.setenv("LLM_STREAM_ERROR_RETRIES", "1")
        route = ROUTER.post(CHAT).mock(side_effect=httpx.ConnectError("refused"))

        text, _ = await run(provider())

        assert route.call_count == 2 and text == oc.LLM_USER_FACING_OUTAGE_TEXT
        assert "refused" not in text

    async def test_error_in_continuation_keeps_what_was_collected(self, monkeypatch):
        monkeypatch.setenv("LLM_AUTO_CONTINUE_MAX", "2")
        route = ROUTER.post(CHAT).mock(
            side_effect=[sse(delta("часть 1 ", finish="length")), httpx.Response(500, text="boom")]
        )

        text, _ = await run(provider())

        assert route.call_count == 2 and text == "часть 1"

    async def test_http_error_in_first_pass_is_returned_as_text(self):
        ROUTER.post(CHAT).mock(return_value=httpx.Response(500, text="boom"))

        text, _ = await run(provider())

        assert text == oc.LLM_USER_FACING_OUTAGE_TEXT

    async def test_unreadable_error_body_does_not_break_error_handling(self):
        class Broken(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise httpx.ReadError("stream died")
                yield b""

        ROUTER.post(CHAT).mock(return_value=httpx.Response(500, stream=Broken()))

        text, _ = await run(provider())

        assert text == oc.LLM_USER_FACING_OUTAGE_TEXT

    async def test_context_overflow_in_stream_retries_with_smaller_max_tokens(self):
        overflow = httpx.Response(
            400, text="maximum context length of 1000 tokens. you sent 100 tokens from the input"
        )
        route = ROUTER.post(CHAT).mock(side_effect=[overflow, sse(delta("ok", finish="stop"))])

        text, _ = await run(provider(), max_tokens=1024)

        assert text == "ok"
        assert [b["max_tokens"] for b in request_bodies(route)] == [1024, 1000 - 100 - 512]


class TestStreamDetails:
    async def test_late_chunks_after_finish_are_ignored_until_done(self):
        ROUTER.post(CHAT).mock(return_value=sse(delta("x", finish="stop"), delta("late")))

        text, _ = await run(provider())

        assert text == "x"

    async def test_done_marker_without_finish_reason_ends_the_stream(self):
        route = ROUTER.post(CHAT).mock(return_value=sse(delta("a"), delta("b")))

        text, _ = await run(provider())

        # [DONE] без finish_reason - нормальное завершение, продолжение не запрашивается
        assert text == "ab" and route.call_count == 1

    async def test_drain_deadline_ends_the_stream(self, monkeypatch):
        monkeypatch.setattr(oc, "_STREAM_DRAIN_AFTER_FINISH_S", -1.0)
        ROUTER.post(CHAT).mock(return_value=sse(delta("x", finish="stop"), delta("late"), done=False))

        text, _ = await run(provider())

        assert text == "x"

    async def test_think_block_is_hidden_from_callback_and_result_in_fast_mode(self):
        ROUTER.post(CHAT).mock(return_value=sse(delta("<think>рассуждаю</think>ответ", finish="stop")))

        text, calls = await run(provider())

        assert text == "ответ"
        content_calls = [c for c in calls if c[0] == "content" and c[1]]
        assert content_calls[0][2] == "ответ"

    async def test_reasoning_callback_can_stop_the_stream(self):
        ROUTER.post(CHAT).mock(
            return_value=sse(delta(None, reasoning_content="думаю"), delta("не дойдёт", finish="stop"))
        )

        text, calls = await run(
            provider(),
            callback=lambda c, a, r: r != "reasoning",
            request_extra={"enable_thinking": True},
        )

        assert text == ""
        assert calls[0][0] == "reasoning"

    async def test_chat_template_tag_cuts_the_stream(self):
        ROUTER.post(CHAT).mock(return_value=sse(delta("ответ<|im_start|>user"), delta("хвост", finish="stop")))

        text, _ = await run(provider())

        assert text.startswith("ответ")

    async def test_stream_closed_by_a_race_in_wait_cancels_cleanly(self, monkeypatch):
        """Исключение в asyncio.wait уже после завершения задачи чтения: задача разбирается в finally."""
        real_wait = asyncio.wait

        async def racing_wait(fs, timeout=None, **kw):
            await real_wait(fs, timeout=5)
            raise RuntimeError("race")

        monkeypatch.setattr(oc.asyncio, "wait", racing_wait)
        monkeypatch.setenv("LLM_STREAM_ERROR_RETRIES", "0")
        ROUTER.post(CHAT).mock(return_value=sse(delta("x", finish="stop")))

        text, _ = await run(provider())

        # Частичный ответ («x») или дружелюбный outage — без утечки RuntimeError.
        assert "race" not in text
        assert text in ("x", oc.LLM_USER_FACING_OUTAGE_TEXT) or oc.is_llm_outage_text(
            text
        )


class TestHeartbeat:
    @pytest.fixture
    def silent_gateway(self, monkeypatch):
        """Шлюз открыл поток и молчит: чтение строки не завершается, тик ожидания укорочен."""
        real_wait = asyncio.wait

        async def fast_wait(fs, timeout=None, **kw):
            return await real_wait(fs, timeout=0.01, **kw)

        async def hang(_it):
            await asyncio.Event().wait()

        monkeypatch.setattr(oc.asyncio, "wait", fast_wait)
        monkeypatch.setattr(oc, "_next_line", hang)
        monkeypatch.setenv("LLM_STREAM_ERROR_RETRIES", "0")
        ROUTER.post(CHAT).mock(return_value=sse(delta("x", finish="stop")))

    async def test_stop_pressed_during_silence_ends_the_stream(self, silent_gateway):
        text, calls = await run(provider(), callback=lambda c, a, r: r != "heartbeat")

        assert text == ""
        assert calls and calls[0][0] == "heartbeat"

    async def test_idle_timeout_gives_an_error_message(self, silent_gateway, monkeypatch):
        monkeypatch.setattr(oc, "_stream_idle_timeout_sec", lambda: -1.0)

        text, calls = await run(provider())

        assert text == oc.LLM_USER_FACING_OUTAGE_TEXT
        assert "LLM не присылает данные" not in text
        assert calls[0][0] == "heartbeat"

    async def test_silence_below_idle_limit_keeps_waiting(self, silent_gateway, monkeypatch):
        ticks = []

        def heartbeat_then_stop(c, a, r):
            ticks.append(r)
            return len(ticks) < 3

        text, _ = await run(provider(), callback=heartbeat_then_stop)

        assert text == "" and ticks == ["heartbeat"] * 3
