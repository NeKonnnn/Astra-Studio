"""Опциональная проверка «опирается ли ответ на контекст» вторым вызовом LLM."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import re

from backend.settings.logging import get_logger

logger = get_logger(__name__)


def strip_strict_not_found_message(answer: str, replacement: str) -> str:
    """
    Если модель вывела полезный ответ и одновременно добавила стандартную
    формулировку про отсутствие опоры, убираем именно эту формулировку.

    Важно: если ответ СОВПАДАЕТ с replacement целиком, ничего не меняем.
    """
    if not answer:
        return answer
    if not replacement:
        return answer
    ans = str(answer)
    rep = str(replacement)
    ans_stripped = ans.strip()
    rep_stripped = rep.strip()
    if ans_stripped == rep_stripped:
        return ans
    if rep_stripped and rep_stripped in ans:
        # replace(rep) с краевыми пробелами в rep может не найти фразу в ответе
        cleaned = ans.replace(rep, "").strip() if rep in ans else ""
        if cleaned:
            return re.sub("[ ]{2,}", " ", cleaned)
        cleaned = ans.replace(rep_stripped, "").strip()
        if cleaned:
            return re.sub("[ ]{2,}", " ", cleaned)
    return ans


def post_verify_enabled() -> bool:
    """Faithfulness-guard: по умолчанию ВЫКЛЮЧЕН (opt-in).

    Проверяет, обоснован ли ответ найденным контекстом (второй короткий вызов
    LLM «да/нет»), и при вердикте «нет» ЗАМЕНЯЕТ уже сгенерированный ответ
    канонической формулировкой «не нашёл».

    Почему выключен по умолчанию: судья — одиночный да/нет-запрос к текущей
    UI-модели, и на слабых/средних моделях он даёт ложные срабатывания —
    стирает КОРРЕКТНЫЙ ответ, который пользователь уже видит в стриме. Это
    ощущается как «начал писать правильно и вдруг всё удалил». Для UX это хуже
    редкой галлюцинации, поэтому по умолчанию recall в приоритете.

    Включается строгий режим через RAG_POST_VERIFY=1 (для контуров, где важнее
    не допустить необоснованных утверждений, чем сохранить полный ответ).
    """
    v = os.getenv("RAG_POST_VERIFY", "").strip().lower()
    if not v:
        return False
    return v not in ("0", "false", "no", "off")


async def verify_answer_grounded(context_excerpt: str, answer: str) -> bool:
    from backend.agent_llm_svc import ask_agent

    ctx = (context_excerpt or "")[:12000]
    ans = (answer or "")[:8000]
    prompt = (
        "Ниже фрагменты CONTEXT и ответ ассистента. Можно ли считать, что все конкретные утверждения в ответе обоснованы CONTEXT (допускаются общие формулировки и перефраз)?\n"
        "Ответь строго одним словом: да или нет.\n\n"
        f"CONTEXT:\n{ctx}\n\n"
        f"ОТВЕТ:\n{ans}"
    )
    loop = asyncio.get_running_loop()
    # Модель этого чата, см. llm_judge.py: читаем до потока.
    from backend.settings.cef_logger.cef_audit_context import chat_model_peek

    _model_path = chat_model_peek()

    def _call() -> str:
        return ask_agent(
            prompt,
            history=[],
            streaming=False,
            system_prompt="Ты строгий аудитор: отвечай только «да» или «нет».",
            model_path=_model_path,
            max_tokens=8,
            # Ответ - одно слово. Мышление здесь съело бы весь бюджет целиком.
            service_call=True,
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            raw = (await loop.run_in_executor(ex, _call) or "").strip().lower()
        return raw.startswith("да") or raw.startswith("yes")
    except Exception:
        logger.exception("RAG_POST_VERIFY LLM error")
        return True


async def maybe_replace_ungrounded(
    context_excerpt: str, answer: str, replacement: str
) -> str:
    cleaned_answer = strip_strict_not_found_message(answer, replacement)
    if not post_verify_enabled():
        return cleaned_answer
    ok = await verify_answer_grounded(context_excerpt, cleaned_answer)
    if ok:
        return cleaned_answer
    logger.info("[RAG_POST_VERIFY] ответ заменён стандартной формулировкой")
    return replacement
