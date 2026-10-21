"""
Базовый OpenAI-совместимый провайдер.

Используется как основа для vLLM, Ollama (OpenAI-mode), LiteLLM, OpenRouter,
OpenAI и любых custom-серверов, поддерживающих ``/v1/chat/completions``.
Реализует:

- ``chat`` через POST /v1/chat/completions;
- ``stream_chat`` через POST /v1/chat/completions со SSE;
- ``list_models`` через GET /v1/models;
- ``health`` через GET /v1/health с fallback на /v1/models (если health нет);
- ``ensure_model_loaded`` по умолчанию = проверка, что model_id есть
  в списке моделей провайдера (без сетевых побочных эффектов).

Подклассы могут переопределить любой метод (например, ``LlmSvcProvider``
добавляет POST /v1/models/load в ``ensure_model_loaded``).
"""

from __future__ import annotations

import asyncio
import html as _html_module
import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from backend.settings.cef_logger.cef_logger import (
    log_cef_int003_llm_request,
    log_cef_int006_llm_api_failure,
)
from backend.settings.logging import get_logger
from backend.settings.logging.repeat import log_on_change

from .routing import extra_params_headers, is_thinking_requested
from .base import (
    ChatResult,
    LLMProvider,
    LLMProviderConfig,
    ModelInfo,
    ProviderCapabilities,
    ProviderHealth,
    StreamCallback,
    ToolCall,
)
from .model_health import (
    exclude_models_by_names,
    filter_models_by_chat_probe,
    model_health_filter_enabled,
    model_health_probe_max,
    unhealthy_names_from_litellm_payload,
)

logger = get_logger(__name__)

_TRACE_CALL_STATE: Dict[str, Dict[str, float]] = {}
# Кэш результата health/chat-probe для list_models (не для ensure_model_loaded).
_FILTERED_MODELS_CACHE: Dict[str, Tuple[float, Tuple[str, ...], List["ModelInfo"]]] = {}
_FILTERED_MODELS_CACHE_LOCK = asyncio.Lock()

_CONTEXT_SIZE_KEYS = (
    "context_size",
    "context_length",
    "max_model_len",
    "max_context_tokens",
    "max_context_length",
    "n_ctx",
    "context_window",
)


def _auto_continue_max() -> int:
    """Сколько раз дожимать ответ при finish_reason=length. LLM_AUTO_CONTINUE_MAX.

    0 — выключить auto-continue (старое поведение: обрыв на max_tokens).
    """
    raw = (os.getenv("LLM_AUTO_CONTINUE_MAX") or "").strip()
    if not raw:
        return 5
    try:
        return max(0, min(int(raw), 20))
    except ValueError:
        return 5


def _stream_read_timeout_sec(default: float) -> float:
    """Idle read timeout для SSE-стрима.

    Раньше был жёстко 300с: длинные паузы (thinking / auto-continue с огромным
    HTML) обрывали ответ на середине. Берём timeout провайдера из config
    (часто 3600) и даём override LLM_STREAM_READ_TIMEOUT.
    """
    raw = (os.getenv("LLM_STREAM_READ_TIMEOUT") or "").strip()
    if raw:
        try:
            return max(60.0, float(raw))
        except ValueError:
            pass
    try:
        return max(60.0, float(default))
    except (TypeError, ValueError):
        return 3600.0


def _stream_idle_timeout_sec() -> float:
    """Сколько ждать следующую SSE-строку, прежде чем считать стрим зависшим.

    LLM_STREAM_IDLE_TIMEOUT, по умолчанию 300 с. read-timeout httpx (до 3600 с)
    держал бы UI в «генерации» час, если шлюз замолчал, не закрыв соединение.
    """
    raw = (os.getenv("LLM_STREAM_IDLE_TIMEOUT") or "").strip()
    try:
        return max(30.0, float(raw)) if raw else 300.0
    except ValueError:
        return 300.0


def _stream_error_retries() -> int:
    """Сколько раз продолжать ответ после сетевого обрыва стрима. LLM_STREAM_ERROR_RETRIES."""
    try:
        return max(0, min(int(os.getenv("LLM_STREAM_ERROR_RETRIES", "2")), 5))
    except (TypeError, ValueError):
        return 2


# После finish_reason дочитываем хвост ([DONE], usage) — иначе шлюз пишет
# «client disconnected» на каждый успешный ответ.
_STREAM_DRAIN_AFTER_FINISH_S = 1.5


class _StreamIdleTimeout(Exception):
    pass


async def _next_line(line_iter: Any) -> str:
    return await line_iter.__anext__()


_SLIDE_CLASS_QUOTED_RE = re.compile(
    r"""class\s*=\s*(["'])([^"']*)\1""",
    re.IGNORECASE,
)
# unquoted class=slide, но не slide-title / slide-header (дефис = часть токена)
_SLIDE_CLASS_UNQUOTED_RE = re.compile(
    r"""class\s*=\s*slide(?![\w-])""",
    re.IGNORECASE,
)
_REQUESTED_SLIDES_RE = re.compile(r"""(?ix)
    (?:
        (?:ровно|exactly)\s+(\d+)\s*(?:слайд|slide|страниц\w*|pages?)?
        | (\d+)\s*(?:слайд(?:ов|а|ы)?|slides?|страниц(?:а|ы)?|pages?)
        | (?:слайд(?:ов|а|ы)?|slides?|страниц(?:а|ы)?|pages?)\s*[:=]?\s*(\d+)
        # «презентацию на 25 …» / «presentation with 25 …»
        | презентац\w*\s+на\s+(\d+)
        | presentation\s+(?:with|of|on)\s+(\d+)
    )
    """)


def _class_attr_has_slide_token(class_value: str) -> bool:
    """Как на фронте: только токен ``slide``, не ``slide-title`` / ``slide-header``."""
    return any(tok == "slide" for tok in (class_value or "").split())


def _count_html_slides(text: str) -> int:
    """Сколько элементов с class-токеном ``slide`` (не slide-*).

    Раньше ``\\bslide\\b`` ловил и ``slide-title`` (дефис даёт word-boundary),
    из-за чего лог писал 67/45 при реальных ~34 слайдах и auto-continue
    преждевременно останавливался.
    """
    if not text:
        return 0
    count = 0
    for m in _SLIDE_CLASS_QUOTED_RE.finditer(text):
        if _class_attr_has_slide_token(m.group(2)):
            count += 1
    count += len(_SLIDE_CLASS_UNQUOTED_RE.findall(text))
    return count


def _iter_message_texts(
    messages: List[Dict[str, Any]], *, roles: Tuple[str, ...] = ("user",)
) -> List[str]:
    """Тексты сообщений выбранных ролей в порядке диалога (без склейки)."""
    parts: List[str] = []
    allowed = {r.lower() for r in roles}
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role") or "").lower() not in allowed:
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if content.strip():
                parts.append(content)
        elif isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    chunks.append(str(item.get("text") or ""))
                elif isinstance(item, str):
                    chunks.append(item)
            blob = "\n".join(chunks).strip()
            if blob:
                parts.append(blob)
    return parts


def _parse_slide_counts_in_text(text: str) -> List[int]:
    """Все явные числа слайдов/страниц в одном тексте пользователя."""
    found: List[int] = []
    if not text:
        return found
    for m in _REQUESTED_SLIDES_RE.finditer(text):
        for g in m.groups():
            if g and g.isdigit():
                n = int(g)
                if 1 <= n <= 200:
                    found.append(n)
    return found


def _requested_slide_count(messages: List[Dict[str, Any]]) -> Optional[int]:
    """Сколько слайдов просил пользователь в ТЕКУЩЕМ (последнем) запросе.

    Только последнее role=user. Иначе при «сначала 40, потом 7» auto-continue
    брал max по всей истории и дожимал до 40 после «Спасибо за внимание».
    Если в последнем сообщении числа нет — None (дефолт скилла 8–15), а не
    старое N из прошлого запроса в том же чате.
    """
    user_texts = _iter_message_texts(messages, roles=("user",))
    if not user_texts:
        return None
    found = _parse_slide_counts_in_text(user_texts[-1])
    return max(found) if found else None


def _presentation_looks_finished(text: str) -> bool:
    """Есть финальный слайд/формулировка — без явного N новые слайды не дожимаем."""
    low = (text or "").lower()
    markers = (
        "спасибо за внимание",
        "благодарю за внимание",
        "thank you for your attention",
        "thanks for your attention",
        "конец презентации",
        "вопросы и ответы",
    )
    return any(m in low for m in markers)


def _strip_incomplete_trailing_html_comment(html: str) -> str:
    """Убирает хвост ``<!-- ...`` без ``-->`` (обрыв mid-slide / mid-comment)."""
    if not html or "<!--" not in html:
        return html
    last_open = html.rfind("<!--")
    if last_open < 0:
        return html
    if "-->" in html[last_open:]:
        return html
    return html[:last_open].rstrip()


# Минимальный GPB chrome: если модель/continue отдали пустой <head>, без этого
# слайды «голые» (системный шрифт, нет .slide 297×167mm) — как на скринах «троения».
_GPB_FALLBACK_STYLE = """
@font-face{font-family:'Cera CY';src:url('/static/fonts/Cera-Regular-App.ttf') format('truetype');font-weight:400;font-style:normal}
@font-face{font-family:'Cera CY';src:url('/static/fonts/Cera-Bold-App.ttf') format('truetype');font-weight:700;font-style:normal}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Cera CY',Calibri,sans-serif;background:#e8e8e8}
.slide{width:297mm;height:167mm;background:#fff;position:relative;overflow:hidden}
.slide-title{position:absolute;left:13.3mm;top:7.2mm;color:#2355D7;font-family:'Cera CY',Calibri,sans-serif;font-weight:700;line-height:1.2;z-index:3;max-width:220mm;word-wrap:break-word;overflow-wrap:break-word}
.slide-title--xl{font-size:43px;max-width:220mm}.slide-title--lg{font-size:37px;max-width:220mm}
.slide-title--md{font-size:32px;max-width:220mm}.slide-title--sm{font-size:27px;max-width:220mm}
.slide-title--xs{font-size:24px;max-width:220mm}.slide-title--mini{font-size:21px;max-width:220mm;line-height:1.25}
.content-zone{position:absolute;left:13.3mm;right:13.3mm;top:var(--content-top,22mm);width:calc(297mm - 26.6mm);z-index:3}
.gpb-small{position:absolute;right:13.3mm;top:8.9mm;width:43.6mm;height:auto;z-index:4}
.gpb-big{position:absolute;z-index:4}
.side-panel{position:absolute;left:0;top:0;height:167mm;z-index:1}
.page-num,.page-number{position:absolute;right:13.3mm;bottom:7.3mm;z-index:5;font-size:8px;color:#696E82}
.card{border-radius:2.5mm;padding:5mm}
.title-slide .main-title{position:absolute;left:13.6mm;top:66.8mm;font-size:48px;font-weight:700;color:#2355D7;z-index:3;max-width:200mm}
.title-slide .subtitle{position:absolute;left:13.6mm;top:95mm;font-size:20px;color:#333;z-index:3}
.title-slide .website{position:absolute;left:13.6mm;bottom:12mm;font-size:14px;color:#2355D7;z-index:3}
""".strip()


def _presentation_fallback_chrome() -> str:
    return f"<style>\n{_GPB_FALLBACK_STYLE}\n</style>"


def _clean_slide_fragment(frag: str) -> str:
    """Убирает из фрагмента слайда fence/DOCTYPE/html-шум auto-continue."""
    if not frag:
        return frag
    s = frag
    # ```html / ``` / язык «html» отдельной строкой (даёт утечку «html» в чат)
    s = re.sub(r"```(?:html|htm|xhtml)?\b[^\n]*\n?", "\n", s, flags=re.I)
    s = re.sub(r"```+", "\n", s)
    s = re.sub(r"(?m)^\s*html\s*$", "", s, flags=re.I)
    s = re.sub(r"<!DOCTYPE\s+html[^>]*>", "", s, flags=re.I)
    s = re.sub(r"<head\b[^>]*>[\s\S]*?</head>", "", s, flags=re.I)
    s = re.sub(r"</?(?:html|body)\b[^>]*>", "", s, flags=re.I)
    s = _strip_incomplete_trailing_html_comment(s)
    return s.strip()


def _strip_presentation_reopen_prefix(text: str) -> str:
    """Срезает повторный старт документа в начале continue-дельты."""
    s = text or ""
    # Несколько раз: модель иногда пишет ```html + DOCTYPE + head подряд.
    for _ in range(4):
        prev = s
        s = re.sub(r"^\s*```(?:html|htm|xhtml)?\b[^\n]*\n?", "", s, count=1, flags=re.I)
        s = re.sub(r"^\s*html\s*\n", "", s, count=1, flags=re.I)
        s = re.sub(r"^\s*<!DOCTYPE\s+html[^>]*>\s*", "", s, count=1, flags=re.I)
        s = re.sub(r"^\s*<html\b[^>]*>\s*", "", s, count=1, flags=re.I)
        s = re.sub(r"^\s*<head\b[^>]*>[\s\S]*?</head>\s*", "", s, count=1, flags=re.I)
        s = re.sub(r"^\s*<body\b[^>]*>\s*", "", s, count=1, flags=re.I)
        if s == prev:
            break
    return s


def _merge_presentation_continue(prev: str, full: str) -> str:
    """Склеивает continue-pass: вырезает повторный ```html/DOCTYPE из новой дельты."""
    if not full:
        return full or ""
    if not prev:
        return full
    if full.startswith(prev):
        delta = full[len(prev) :]
        stripped = _strip_presentation_reopen_prefix(delta)
        return prev + stripped if stripped != delta else full
    # open_presentation мог чуть сократить prev — режем reopen из хвоста.
    if len(full) > len(prev):
        return prev + _strip_presentation_reopen_prefix(full[len(prev) :])
    return full


def _last_slide_fragment_unbalanced(frag: str) -> bool:
    """Последний слайд оборван: открытых <div> больше, чем закрытых."""
    if not frag:
        return False
    s = re.sub(r"</body>\s*</html>\s*$", "", frag, flags=re.I).rstrip()
    s = re.sub(r"</html>\s*$", "", s, flags=re.I).rstrip()
    s = re.sub(r"</body>\s*$", "", s, flags=re.I).rstrip()
    s = _strip_incomplete_trailing_html_comment(s)
    if not s:
        return True
    # Mid-tag в хвосте слайда.
    last_lt = s.rfind("<")
    if last_lt >= 0 and ">" not in s[last_lt:]:
        return True
    opens = len(re.findall(r"<div\b", s, flags=re.I))
    closes = len(re.findall(r"</div\s*>", s, flags=re.I))
    return opens > closes


def _presentation_looks_truncated(text: str) -> bool:
    """Презентация оборвана mid-stream — нужно auto-continue даже при finish=stop.

    Типичные кейсы со скриншотов:
    - ``<!-- СЛАЙД N`` без ``-->`` / ``<!--</body></html>``
    - mid-tag: обрезанный атрибут/тег
    - последний ``.slide`` с незакрытыми ``<div>`` + насильно дописанный ``</body></html>``

    Не считаем truncation'ом просто отсутствие ``</html>`` — иначе нормальный
    stop после последнего слайда бесконечно дожимается.
    """
    if not text or not _looks_like_presentation_html(text):
        return False
    s = text.rstrip()
    if s.endswith("```"):
        s = s[:-3].rstrip()
    last_open = s.rfind("<!--")
    if last_open >= 0 and "-->" not in s[last_open:]:
        return True
    last_lt = s.rfind("<")
    if last_lt >= 0 and ">" not in s[last_lt:]:
        return True
    # ``</body>``/``</html>`` внутри незакрытого комментария.
    for m in re.finditer(r"</(?:body|html)\s*>", s, flags=re.I):
        open_before = s.rfind("<!--", 0, m.start())
        if open_before >= 0 and "-->" not in s[open_before : m.start()]:
            return True
    slides = _extract_slide_divs(s)
    if slides and _last_slide_fragment_unbalanced(slides[-1]):
        return True
    return False


def _looks_like_presentation_html(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    if _count_html_slides(text) > 0:
        return True
    if 'class="slide"' in low or "class='slide'" in low:
        return True
    return "gpb" in low and ".slide" in low


def _html_document_unfinished(text: str) -> bool:
    """HTML-документ начат (```html / DOCTYPE / <html>), но не закрыт и слайдов нет.

    Обрыв внутри <head>/<style> до первого .slide: ``_looks_like_presentation_html``
    такое не узнаёт, и без этой проверки auto-continue не срабатывал.
    """
    if not text or _count_html_slides(text) > 0:
        return False
    low = text.lower()
    started = "<!doctype html" in low or "<html" in low or "```html" in low
    if not started or "</html>" in low:
        return False
    return "slide" in low or ("<head" in low and "</head>" not in low)


_HTML_FENCE_RE = re.compile(
    r"```(?:html|htm|xhtml)\b[^\n]*\n(.*?)(?:```|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _extract_slide_divs(html: str) -> List[str]:
    """Грубый extract кусков от каждого class=slide до следующего (как на фронте)."""
    if not html:
        return []
    starts: List[int] = []
    for m in re.finditer(
        r"""<[a-zA-Z][\w-]*\b[^>]*\bclass\s*=\s*(["'])([^"']*)\1[^>]*>""",
        html,
        flags=re.IGNORECASE,
    ):
        classes = (m.group(2) or "").split()
        if "slide" in classes:
            starts.append(m.start())
    for m in re.finditer(
        r"""<[a-zA-Z][\w-]*\b[^>]*\bclass\s*=\s*slide(?![\w-])[^>]*>""",
        html,
        flags=re.IGNORECASE,
    ):
        starts.append(m.start())
    starts = sorted(set(starts))
    if not starts:
        return []
    unique: List[int] = []
    for idx in starts:
        if not unique or idx - unique[-1] > 2:
            unique.append(idx)
    out: List[str] = []
    for i, start in enumerate(unique):
        end = unique[i + 1] if i + 1 < len(unique) else len(html)
        frag = html[start:end].rstrip()
        # Обрезаем закрывающие теги документа у хвоста последнего фрагмента
        if i + 1 >= len(unique):
            frag = re.sub(r"</body>\s*</html>\s*$", "", frag, flags=re.I).rstrip()
            frag = re.sub(r"</html>\s*$", "", frag, flags=re.I).rstrip()
            frag = re.sub(r"</body>\s*$", "", frag, flags=re.I).rstrip()
            frag = _strip_incomplete_trailing_html_comment(frag)
        frag = _clean_slide_fragment(frag)
        if frag:
            out.append(frag)
    return out


def _extract_head_chrome(html: str) -> str:
    head_m = re.search(r"<head\b[^>]*>([\s\S]*?)</head>", html or "", flags=re.I)
    if head_m:
        return (head_m.group(1) or "").strip()
    # Fallback: link/style до первого slide
    slides = _extract_slide_divs(html or "")
    if slides:
        idx = (html or "").find(slides[0][:64] if len(slides[0]) > 64 else slides[0])
        prefix = (html or "")[: idx if idx >= 0 else 8000]
    else:
        prefix = (html or "")[:8000]
    links = re.findall(
        r"<link\b[^>]*rel\s*=\s*['\"]stylesheet['\"][^>]*>", prefix, flags=re.I
    )
    styles = re.findall(r"<style\b[^>]*>[\s\S]*?</style>", prefix, flags=re.I)
    return "\n".join([*links, *styles]).strip()


def _ensure_html_document_closed(html: str) -> str:
    body = (html or "").rstrip()
    if not body:
        return body
    if re.search(r"</html\s*>\s*$", body, flags=re.I):
        return body
    if re.search(r"</body\s*>\s*$", body, flags=re.I):
        return body + "\n</html>"
    return body + "\n</body>\n</html>"


def _finalize_presentation_message(text: str) -> str:
    """Все слайды из всего сообщения (fenced ```html + unfenced <!DOCTYPE>) → один ```html.

    Инвариант как на фронте: собираем ВСЕ фрагменты ``.slide`` из всего текста
    независимо от границ fence/doctype — это чинит смешанный кейс
    (первый блок в ```html, второй — сырой <!DOCTYPE html>), из-за которого UI
    рисовал два viewer'а. Заодно убираем хвост ``</body></html>`` / пустой ```text.
    """
    if not text or not _looks_like_presentation_html(text):
        return text

    # Начало презентационной части: первый fence / doctype / class="slide".
    starts: List[int] = []
    fence_m = _HTML_FENCE_RE.search(text)
    if fence_m:
        starts.append(fence_m.start())
    doc_m = re.search(r"<!DOCTYPE\s+html\b|<html\b", text, flags=re.I)
    if doc_m:
        starts.append(doc_m.start())
    slide_m = re.search(
        r"""<[a-zA-Z][\w-]*\b[^>]*\bclass\s*=\s*(["'])[^"'>]*\bslide\b""",
        text,
        flags=re.I,
    )
    if slide_m:
        starts.append(slide_m.start())
    if not starts:
        return text
    pres_start = min(starts)
    prefix = text[:pres_start].rstrip()
    pres_part = text[pres_start:]

    all_slides: List[str] = []
    seen: Set[str] = set()
    for frag in _extract_slide_divs(pres_part):
        cleaned = _clean_slide_fragment(frag)
        cleaned = re.sub(r"```[\w.+-]*\s*$", "", cleaned)
        cleaned = re.sub(r"</body>\s*</html>\s*$", "", cleaned, flags=re.I).rstrip()
        cleaned = _strip_incomplete_trailing_html_comment(cleaned)
        if not cleaned:
            continue
        if not re.search(
            r"""class\s*=\s*(?:(['"])[^'"]*\bslide\b|(?:[\w-]*-)?slide\b)""",
            cleaned,
            flags=re.I,
        ):
            continue
        # Полный фрагмент — иначе два title-slide (титул / «Спасибо») или похожие
        # контентные слайды схлопывались по первым 240 символам → 24 вместо 25.
        key = cleaned
        if key in seen:
            continue
        seen.add(key)
        all_slides.append(cleaned)
    if not all_slides:
        return text

    # Битый хвост (незакрытые div) — не показываем как «готовый» последний слайд.
    # Финальный title-slide / «Спасибо» не выкидываем: у него мало вложенных div,
    # и эвристика unbalanced иногда ошибается на img + 2–3 блока.
    if len(all_slides) > 1 and _last_slide_fragment_unbalanced(all_slides[-1]):
        last = all_slides[-1].lower()
        keep_final = (
            "title-slide" in last
            or "спасибо за внимание" in last
            or "thank you for your attention" in last
        )
        if not keep_final:
            all_slides = all_slides[:-1]

    chrome = (_extract_head_chrome(pres_part) or "").strip()
    if not chrome or not re.search(r"<style\b", chrome, flags=re.I):
        # Пустой <head></head> после continue → «голые» слайды без Cera/297mm.
        chrome = (
            _presentation_fallback_chrome()
            if not chrome
            else f"{chrome}\n{_presentation_fallback_chrome()}"
        )

    merged = (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f"{chrome}\n"
        "</head>\n<body>\n" + "\n".join(all_slides) + "\n</body>\n</html>"
    )
    body = f"```html\n{merged}\n```"
    return f"{prefix}\n\n{body}".strip() if prefix else body


def _open_presentation_for_continue(accumulated: str) -> str:
    """Готовим assistant-префикс: открытый fence, без </body></html>, чтобы модель дописывала .slide."""
    s = (accumulated or "").rstrip()
    if s.endswith("```"):
        s = s[:-3].rstrip()
    s = re.sub(r"</body>\s*</html>\s*$", "", s, flags=re.I).rstrip()
    s = re.sub(r"</html>\s*$", "", s, flags=re.I).rstrip()
    s = re.sub(r"</body>\s*$", "", s, flags=re.I).rstrip()
    s = _strip_incomplete_trailing_html_comment(s)
    # Оборванный последний слайд лучше срезать — модель перепишет его целиком.
    slides = _extract_slide_divs(s)
    if len(slides) >= 1 and _last_slide_fragment_unbalanced(slides[-1]):
        idx = s.rfind(slides[-1][: min(80, len(slides[-1]))])
        if idx >= 0:
            s = s[:idx].rstrip()
    return s


def _rate_limit_retries() -> int:
    """Повторов запроса к LLM на 429: LLM_RATE_LIMIT_RETRIES, по умолчанию 3."""
    try:
        return max(0, min(int(os.getenv("LLM_RATE_LIMIT_RETRIES", "3")), 10))
    except (TypeError, ValueError):
        return 3


def _rate_limit_delay(response: Any, attempt: int) -> float:
    """Пауза перед повтором: Retry-After шлюза (до 60 с) или 3 / 8 / 20 с."""
    try:
        raw = (response.headers or {}).get("retry-after")
        if raw is not None:
            return max(1.0, min(float(raw), 60.0))
    except (TypeError, ValueError, AttributeError):
        pass
    return (3.0, 8.0, 20.0)[min(attempt, 2)]


# Отказ «не помещается в контекст»: предел модели и размер входа из текста ошибки.
# vLLM: "maximum context length of 262144 tokens. ... 71140 tokens from the input"
# OpenAI: "maximum context length is 8192 tokens. ... (7000 in the messages, ..."
_CONTEXT_OVERFLOW_RES = (
    re.compile(
        r"maximum context length of (\d+) tokens.*?(\d+) tokens from the input", re.S
    ),
    re.compile(
        r"maximum context length is (\d+) tokens.*?\((\d+) in the messages", re.S
    ),
)
# Запас на расхождение подсчёта токенов и минимум, ради которого стоит повторять.
_CONTEXT_FIT_MARGIN = 512
_CONTEXT_FIT_MIN = 256

def _context_fit_max_tokens(
    provider_id: str, model: str, status_code: int, body: str, max_tokens: Any
) -> Optional[int]:
    """max_tokens, при котором отклонённый запрос поместится в контекст модели, или None.

    Например: у агента в карточке max_tokens=200000, вход главного к концу хода -
    70 тыс. токенов, вместе больше контекста 262 144 (лимит на стороне мддь):
    vLLM ответил 400, ход оборвался. Предел и размер входа модель пишет в ошибке - 
    повторяем с max_tokens по оставшемуся месту (с запасом). Места не осталось или
    max_tokens и так меньше - None, ошибка уходит как была.
    """
    if status_code != 400 or not body:
        return None
    for rx in _CONTEXT_OVERFLOW_RES:
        m = rx.search(body)
        if not m:
            continue
        limit, used = int(m.group(1)), int(m.group(2))
        fit = limit - used - _CONTEXT_FIT_MARGIN
        try:
            current = int(max_tokens)
        except (TypeError, ValueError):
            return None
        if not _CONTEXT_FIT_MIN <= fit < current:
            return None
        logger.warning(
            "[%s] запрос не помещается в контекст модели model=%r: контекст %s, "
            "вход %s + max_tokens %s - повтор с max_tokens=%s",
            provider_id,
            model,
            limit,
            used,
            current,
            fit,
        )
        return fit
    return None


def _invoke_stream_callback(
    callback: Any, chunk: str, acc: str, stream_role: str = "content"
) -> bool:
    """Вызывает callback стрима; поддерживает 2- и 3-аргументные колбэки.

    ``False`` — прервать поток. Любое другое значение (включая ``None``) — продолжать.
    """
    try:
        result = callback(chunk, acc, stream_role)
    except TypeError:
        if stream_role == "heartbeat":
            return True
        result = callback(chunk, acc)
    return result is not False


def _parse_context_size_from_mapping(data: Optional[Dict[str, Any]]) -> Optional[int]:
    """Известные поля ответа API провайдера; без эвристик по имени модели."""
    if not data:
        return None
    for key in _CONTEXT_SIZE_KEYS:
        raw = data.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, float) and raw > 0:
            return int(raw)
        if isinstance(raw, str):
            s = raw.strip().replace(",", "").replace(" ", "")
            if s.isdigit():
                n = int(s)
                if n > 0:
                    return n
            m = re.match(r"^(\d+(?:\.\d+)?)\s*([kKmM])$", s)
            if m:
                num = float(m.group(1))
                mult = 1_000 if m.group(2).lower() == "k" else 1_000_000
                return int(num * mult)
    return None


def _trace_outbound_probe(provider_id: str, operation: str, url: str) -> None:
    key = f"{provider_id}:{operation}"
    now = time.monotonic()
    state = _TRACE_CALL_STATE.get(key, {"count": 0.0, "last_ts": 0.0})
    last_ts = float(state.get("last_ts", 0.0) or 0.0)
    delta_s = (now - last_ts) if last_ts > 0 else None
    state["count"] = float(state.get("count", 0.0) or 0.0) + 1.0
    state["last_ts"] = now
    _TRACE_CALL_STATE[key] = state
    logger.debug(
        "[LLM_TRAFFIC] outbound provider=%s op=%s count=%s delta_s=%s url=%s",
        provider_id,
        operation,
        int(state["count"]),
        f"{delta_s:.2f}" if delta_s is not None else "first",
        url,
    )


# Один паттерн на оба маркера: разэкранирование идёт ДО обрезки, поэтому ловить
# отдельно <, < и &lt; больше не нужно — к моменту обрезки они
# уже приведены к обычному виду. Раньше здесь были два списка «лестницей» по
# уровням экранирования, причём с дублями (одинаковый паттерн дважды — второй
# проход не находил ничего, так как первый уже сносил всё до конца строки)
# и с разной глубиной у im_start и im_end: у im_end не хватало двух уровней,
# и дважды заэкранированный хвост доезжал до пользователя.
_CHAT_TEMPLATE_RE = re.compile(r"<\|im_(?:start|end)\|>.*", re.DOTALL)


def clean_llm_response(text: str) -> str:
    """Убирает хвост ``<|im_start|>`` / ``<|im_end|>`` и HTML entities."""
    if not text:
        return text
    # Шлюз отдаёт маркеры экранированными на разную глубину - сначала
    # разворачиваем до неподвижной точки, иначе паттерн их не видит
    for _ in range(3):
        new_text = _html_module.unescape(text)
        if new_text == text:
            break
        text = new_text
    return _CHAT_TEMPLATE_RE.sub("", text).rstrip()


# Маркеры блокировки контента в теле ответа шлюза. Phoenix/LiteLLM отбивают
# запрос до модели и возвращают причину в свободной форме — единого поля нет,
# поэтому ищем по подстрокам.
_GUARDRAIL_MARKERS = (
    "guardrail",
    "guard rail",
    "content filter",
    "content_filter",
    "content policy",
    "moderation",
    "flagged",
    "violat",
    "заблокирован",
    "модерац",
    "запрещ",
)


def _model_in_allowlist(model: Any, configured: Any) -> bool:
    """Совпадает ли модель шлюза с одним из имён в ```models.name```.

    Сверяем три написания, потому что у шлюза id и человекочитаемое имя разные:
    bifrost отдаёт ```{"id": "embed/FRIDA", "name": "FRIDA"}```. Требовать в
    конфиге полный id с префиксом группы — лишний способ ошибиться, поэтому
    подходит и ```embed/FRIDA```, и ```FRIDA```.

    Обратная сторона: короткое имя совпадёт со всеми группами, где такая модель
    есть (```vllm/Qwen…``` и ```vllm_qwen36-27b/Qwen…``` — обе). Нужна ровно
    одна — пишите полный id.
    """
    mid = str(getattr(model, "model_id", "") or "").strip()
    name = str(getattr(model, "display_name", "") or "").strip()
    variants = {v.lower() for v in (mid, name, mid.rsplit("/", 1)[-1]) if v}
    return any(str(c or "").strip().lower() in variants for c in configured)


def _llm_error_body_limit() -> int:
    """Сколько символов тела ошибки писать в лог. LLM_ERROR_BODY_LOG_CHARS."""
    try:
        return max(0, min(20000, int(os.getenv("LLM_ERROR_BODY_LOG_CHARS", "2000"))))
    except ValueError:
        return 2000


def _cut_for_log(text: Optional[str], limit: Optional[int] = None) -> str:
    cap = _llm_error_body_limit() if limit is None else limit
    s = " ".join(str(text or "").split())
    return s if len(s) <= cap else f"{s[:cap]}…(+{len(s) - cap} симв.)"


def _safe_response_text(response: Any) -> str:
    """Тело ответа, даже если это недочитанный поток (иначе ResponseNotRead)."""
    try:
        return response.text or ""
    except Exception:
        return "<тело ответа недоступно: поток не прочитан>"


def _looks_like_guardrail(body: Optional[str]) -> bool:
    s = str(body or "").lower()
    return any(marker in s for marker in _GUARDRAIL_MARKERS)


def _log_llm_http_failure(
    *,
    provider_id: str,
    status_code: int,
    body: str,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    has_tools: bool,
    payload_keys: List[str],
) -> None:
    """Разбираемый след отказа шлюза.

    До этого тело ошибки уходило только в CEF (отдельный поток) и обрезалось до
    512 символов, а в обычном логе оставался лишь текст из
    ```format_llm_http_error``` — по нему нельзя отличить «сработал guardrail» от
    «модель не отвечает». Здесь пишем и тело, и форму запроса: размер промпта
    часто и есть причина.
    """
    total_chars = sum(len(str((m or {}).get("content") or "")) for m in messages or [])
    roles = ",".join(str((m or {}).get("role") or "?") for m in (messages or [])[:8])
    logger.warning(
        "[%s] отказ шлюза HTTP %s model=%r: %s",
        provider_id,
        status_code,
        model,
        _cut_for_log(body),
    )
    logger.warning(
        "[%s] форма запроса: сообщений=%s (роли: %s), символов в промпте=%s, "
        "max_tokens=%s, tools=%s, поля тела=%s",
        provider_id,
        len(messages or []),
        roles,
        total_chars,
        max_tokens,
        has_tools,
        payload_keys,
    )
    if _looks_like_guardrail(body):
        logger.warning(
            "[%s] похоже на блокировку контента на стороне шлюза (guardrail/модерация), "
            "а не на ошибку нашего запроса: HTTP %s",
            provider_id,
            status_code,
        )


def _strip_think_tags(text: str) -> str:
    """Удаляет блоки <think>...</think> и незакрытые <think>... из текста.

    Также вырезает «сиротский» закрывающий тег без открывающего
    (Qwen3.5 / GLM в режиме enable_thinking=False часто пишут reasoning…</think>ответ).

    Используется в режиме быстрого ответа (thinking_requested=False), чтобы
    рассуждения модели не попадали в финальный ответ пользователю.
    """
    if not text:
        return text
    lower = text.lower()
    open_l = "<think>".lower()
    close_l = "</think>".lower()
    if open_l not in lower and close_l not in lower:
        return text
    if open_l in lower:
        text = re.sub(
            re.escape("<think>") + r"[\s\S]*?" + re.escape("</think>"),
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            re.escape("<think>") + r"[\s\S]*$",
            "",
            text,
            flags=re.IGNORECASE,
        )
    # Orphan closer: всё до первого </think> включительно — рассуждение.
    if close_l in text.lower():
        text = re.sub(
            r"^[\s\S]*?" + re.escape("</think>"),
            "",
            text,
            count=1,
            flags=re.IGNORECASE,
        )
    return text.strip()


def _has_think_markers(text: str) -> bool:
    """True, если в тексте есть открывающий или закрывающий think-тег."""
    if not text:
        return False
    lower = text.lower()
    return "<think>".lower() in lower or "</think>".lower() in lower



def _normalize_reasoning_payload(value: Any) -> str:
    """Нормализует reasoning payload к плоскому тексту."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                txt = item.get("text") or item.get("content") or item.get("reasoning")
                if isinstance(txt, str):
                    parts.append(txt)
        return "".join(parts)
    if isinstance(value, dict):
        txt = (
            value.get("text")
            or value.get("content")
            or value.get("reasoning")
            or value.get("reasoning_text")
            or value.get("thinking")
            or value.get("thought")
        )
        if isinstance(txt, str):
            return txt
        nested_content = value.get("content")
        if isinstance(nested_content, list):
            return _normalize_reasoning_payload(nested_content)
        parts: List[str] = []
        for k, v in value.items():
            if any(
                (token in str(k).lower() for token in ("reason", "think", "thought"))
            ):
                parts.append(_normalize_reasoning_payload(v))
        return "".join(parts)
    return str(value)


def _normalize_content_payload(value: Any) -> str:
    """Нормализует content payload к строке."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in ("text", "output_text"):
                    txt = item.get("text") or item.get("content")
                    if isinstance(txt, str):
                        parts.append(txt)
                else:
                    txt = item.get("content") or item.get("text")
                    if isinstance(txt, str):
                        parts.append(txt)
        return "".join(parts)
    if isinstance(value, dict):
        txt = value.get("text") or value.get("content")
        return txt if isinstance(txt, str) else ""
    return str(value)


class OpenAICompatProvider(LLMProvider):
    """Провайдер для любого OpenAI-совместимого REST."""

    HEALTH_PATH: str = "/v1/health"
    HEALTH_FALLBACK_TO_MODELS: bool = True
    _capabilities = ProviderCapabilities(
        hot_swap=False,
        multi_loaded=True,
        native_chat_api=True,
        streaming=True,
        vision=True,
        function_calling=True,
        prompt_json_fc=True,
        langgraph_agent=True,
    )

    def __init__(self, config: LLMProviderConfig) -> None:
        super().__init__(config)
        self._timeout_read = float(config.timeout)
        extra = config.extra or {}
        if "function_calling" in extra:
            fc = bool(extra.get("function_calling"))
            self._capabilities = ProviderCapabilities(
                hot_swap=self._capabilities.hot_swap,
                multi_loaded=self._capabilities.multi_loaded,
                native_chat_api=self._capabilities.native_chat_api,
                streaming=self._capabilities.streaming,
                vision=self._capabilities.vision,
                function_calling=fc,
                prompt_json_fc=bool(extra.get("prompt_json_fc", True)),
                langgraph_agent=bool(extra.get("langgraph_agent", True)),
            )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def _headers(
        self,
        *,
        accept_sse: bool = False,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """Заголовки запроса. ```payload``` — чтобы добавить проброс нестандартных
        полей тела, если они в нём есть (см. ```extra_params_headers```)."""
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if accept_sse:
            headers["Accept"] = "text/event-stream"
        api_key = self.get_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-API-Key"] = api_key
        # X-User-ID. Здесь, в общем сборщике,
        # чтобы заголовок был на всех вызовах к шлюзу разом
        from backend.settings.cef_logger.cef_audit_context import (
            model_identity_headers,
        )

        headers.update(model_identity_headers())
        headers.update(extra_params_headers(payload))
        return headers

    def _auth_diag(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Безопасная диагностика авторизации (без вывода секретов)."""
        hdrs = headers or self._headers()
        return {
            "api_key_env": self._config.api_key_env,
            "api_key_set": bool(self.get_api_key()),
            "auth_header": (
                "Authorization: Bearer *" if "Authorization" in hdrs else None
            ),
            "x_api_key_header": "X-API-Key: *" if "X-API-Key" in hdrs else None,
        }

    def _safe_auth_diag(
        self, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Защитный вызов auth-диагностики.
        Нужен на случай старых рантаймов/оберток, где _auth_diag не принимает headers.
        """
        try:
            return self._auth_diag(headers)
        except TypeError:
            try:
                return self._auth_diag()
            except Exception:
                logger.exception("Ошибка операции")
                return {
                    "api_key_env": self._config.api_key_env,
                    "api_key_set": bool(self.get_api_key()),
                }
        except Exception:
            logger.exception("Ошибка операции")
            return {
                "api_key_env": self._config.api_key_env,
                "api_key_set": bool(self.get_api_key()),
            }

    def _http_error_details(self, e: Exception) -> Dict[str, Any]:
        """
        Нормализованный payload ошибки HTTP для логов.
        Даёт максимально полезную причину: status code + кусок response body.
        """
        if isinstance(e, httpx.HTTPStatusError):
            response = e.response
            body = ""
            try:
                body = (response.text or "")[:500]
            except Exception:
                logger.exception("Ошибка операции")
                body = ""
            return {
                "error_type": type(e).__name__,
                "status_code": response.status_code,
                "reason": body or str(e),
            }
        return {"error_type": type(e).__name__, "status_code": None, "reason": str(e)}

    def _short_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(10.0, connect=5.0, read=10.0, write=5.0)

    def _request_timeout(self, seconds: Optional[float] = None) -> httpx.Timeout:
        t = float(seconds) if seconds is not None else self._timeout_read
        return httpx.Timeout(t, connect=10.0, read=t, write=10.0)

    def _http_verify(self) -> Any:
        """
        TLS verify для httpx.
        Приоритет: TLS_CERT_PATH -> SSL_CERT_FILE -> REQUESTS_CA_BUNDLE -> True.
        """
        for env_name in ("TLS_CERT_PATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            cert_path = str(os.getenv(env_name, "") or "").strip()
            if cert_path:
                return cert_path
        return True

    def _decode_health_json(
        self, response: httpx.Response, *, url: str
    ) -> Optional[Dict[str, Any]]:
        """Разбор тела GET health: пустое тело и plain-text «ok» не считаем ошибкой."""
        body = (response.text or "").strip()
        if not body:
            return {}
        try:
            payload = response.json()
        except json.JSONDecodeError:
            if body.lower() in ("ok", "healthy", "up", "ready"):
                return {"status": body.lower()}
            logger.warning(
                "[%s] health %s: non-JSON body: %r", self.id, url, body[:200]
            )
            return None
        if isinstance(payload, dict):
            return payload
        return {"value": payload}

    def _interpret_health_payload(self, payload: Dict[str, Any]) -> ProviderHealth:
        """Из health-JSON провайдера — список доступных model id."""
        loaded: List[str] = []
        lm = payload.get("loaded_models")
        if isinstance(lm, list):
            loaded = [str(x) for x in lm if x]
        elif payload.get("model_loaded") and payload.get("model_name"):
            loaded = [str(payload["model_name"])]

        endpoints = payload.get("healthy_endpoints")
        if isinstance(endpoints, list):
            for ep in endpoints:
                if not isinstance(ep, dict):
                    continue
                model = ep.get("model") or ep.get("model_name") or ep.get("model_id")
                if model:
                    name = str(model).strip()
                    if name and name not in loaded:
                        loaded.append(name)

        status = str(payload.get("status", "")).lower()
        healthy = not status or status in ("ok", "healthy", "up", "ready", "connected")
        if "healthy_endpoints" in payload or "unhealthy_endpoints" in payload:
            healthy = True
        return ProviderHealth(healthy=healthy, loaded_models=loaded, raw=payload)

    async def _health_via_models(self, client: httpx.AsyncClient) -> ProviderHealth:
        models_url = f"{self.base_url}/v1/models"
        _trace_outbound_probe(self.id, "health_fallback_models", models_url)
        try:
            response = await client.get(models_url, headers=self._headers())
            if response.status_code == 200:
                data = response.json()
                ids = [
                    str(row.get("id") or "").strip()
                    for row in (data.get("data") or [])
                    if isinstance(row, dict) and row.get("id")
                ]
                ids = [i for i in ids if i]
                return ProviderHealth(
                    healthy=True,
                    loaded_models=ids,
                    raw={"fallback": "models", "models_count": len(ids)},
                )
            return ProviderHealth(
                healthy=False, error=f"/v1/models HTTP {response.status_code}"
            )
        except (httpx.HTTPError, OSError) as e:
            logger.warning("[%s] /v1/models health fallback failed: %s", self.id, e)
            return ProviderHealth(healthy=False, error=f"/v1/models: {e}")

    def _resolve_health_paths(self) -> List[str]:
        """Пути health для текущего подключения (openai-compat / LiteLLM / vLLM gateway)."""
        extra_path = str((self._config.extra or {}).get("health_path") or "").strip()
        candidates = [extra_path, self.HEALTH_PATH, "/health", "/v1/health"]
        paths: List[str] = []
        for p in candidates:
            if p and p not in paths:
                paths.append(p)
        return paths

    def _litellm_health_payload(self, raw: Optional[Dict[str, Any]]) -> bool:
        if not raw:
            return False
        return "healthy_endpoints" in raw or "unhealthy_endpoints" in raw

    async def _connection_health(self, *, timeout: httpx.Timeout) -> ProviderHealth:
        """Health-check через текущее подключение провайдера (base_url, headers, api key)."""
        headers = self._headers()
        paths = self._resolve_health_paths()
        last_error: Optional[str] = None

        try:
            async with httpx.AsyncClient(
                timeout=timeout, verify=self._http_verify()
            ) as client:
                for path in paths:
                    url = f"{self.base_url}{path}"
                    _trace_outbound_probe(self.id, "health", url)
                    try:
                        response = await client.get(url, headers=headers)
                    except (httpx.HTTPError, OSError) as exc:
                        last_error = str(exc)
                        continue

                    if response.status_code in (404, 405):
                        continue
                    if response.status_code != 200:
                        last_error = f"HTTP {response.status_code}: {(response.text or '')[:200]}"
                        continue

                    payload = self._decode_health_json(response, url=url)
                    if payload is None:
                        if self.HEALTH_FALLBACK_TO_MODELS:
                            enriched = await self._health_via_models(client)
                            if enriched.loaded_models or enriched.healthy:
                                return enriched
                        last_error = (
                            f"health returned non-JSON: {(response.text or '')[:200]}"
                        )
                        continue

                    result = self._interpret_health_payload(payload)
                    if (
                        result.healthy
                        and not result.loaded_models
                        and self.HEALTH_FALLBACK_TO_MODELS
                    ):
                        if not self._litellm_health_payload(result.raw):
                            enriched = await self._health_via_models(client)
                            if enriched.loaded_models:
                                return ProviderHealth(
                                    healthy=True,
                                    loaded_models=enriched.loaded_models,
                                    raw={
                                        **(result.raw or {}),
                                        "enriched_from": "v1/models",
                                    },
                                )
                    return result

                if self.HEALTH_FALLBACK_TO_MODELS:
                    return await self._health_via_models(client)
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("[%s] connection health failed: %s", self.id, exc)
            return ProviderHealth(healthy=False, error=str(exc))

        return ProviderHealth(
            healthy=False, error=last_error or "health endpoints unavailable"
        )

    @staticmethod
    def _health_error_is_forbidden(error: Optional[str]) -> bool:
        err = (error or "").lower()
        if "403" in err and "health" in err:
            return True
        if "not allowed to call route" in err and "health" in err:
            return True
        return "not allowed" in err and "health" in err

    def _listing_health_paths(self) -> List[str]:
        extra_path = str((self._config.extra or {}).get("health_path") or "").strip()
        paths: List[str] = []
        for path in (extra_path, "/health", "/v1/health", self.HEALTH_PATH):
            if path and path not in paths:
                paths.append(path)
        return paths

    async def _try_litellm_batch_health(
        self,
        *,
        timeout: httpx.Timeout,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Batch GET /health для фильтра UI. Успех: (payload, None), иначе (None, error)."""
        headers = self._headers()
        last_error: Optional[str] = None
        try:
            async with httpx.AsyncClient(
                timeout=timeout, verify=self._http_verify()
            ) as client:
                for path in self._listing_health_paths():
                    url = f"{self.base_url}{path}"
                    _trace_outbound_probe(self.id, "health_listing", url)
                    try:
                        response = await client.get(url, headers=headers)
                    except (httpx.HTTPError, OSError) as exc:
                        last_error = str(exc)
                        continue
                    if response.status_code in (404, 405):
                        continue
                    if response.status_code != 200:
                        last_error = f"HTTP {response.status_code}: {(response.text or '')[:200]}"
                        continue
                    payload = self._decode_health_json(response, url=url)
                    if payload is None:
                        last_error = (
                            f"health returned non-JSON: {(response.text or '')[:200]}"
                        )
                        continue
                    return payload, None
        except (httpx.HTTPError, OSError) as exc:
            return None, str(exc)
        return None, last_error or "health endpoints unavailable"

    def _chat_probe_timeout(self) -> httpx.Timeout:
        read = min(45.0, max(15.0, float(self._config.timeout) / 8))
        return httpx.Timeout(read, connect=5.0, read=read, write=10.0)

    async def _probe_single_model_via_chat(
        self,
        client: httpx.AsyncClient,
        model_id: str,
    ) -> Tuple[str, bool]:
        """Минимальный POST /v1/chat/completions — работает с virtual key llm_api_routes."""
        mid = (model_id or "").strip()
        if not mid:
            return mid, False

        url = f"{self.base_url}/v1/chat/completions"
        payload: Dict[str, Any] = {
            "model": mid,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
        _trace_outbound_probe(self.id, "health_chat_probe", url)
        try:
            response = await client.post(url, headers=self._headers(), json=payload)
            if response.status_code != 200:
                return mid, False
            try:
                data = response.json()
                choices = data.get("choices")
                if isinstance(choices, list) and choices:
                    return mid, True
            except json.JSONDecodeError:
                return mid, True
            return mid, False
        except (httpx.HTTPError, OSError):
            return mid, False

    async def _probe_models_via_chat_map(
        self,
        client: httpx.AsyncClient,
        model_ids: List[str],
    ) -> Dict[str, bool]:
        ids = [str(m).strip() for m in model_ids if str(m or "").strip()]
        if not ids:
            return {}

        sem = asyncio.Semaphore(3)

        async def _run(mid: str) -> Tuple[str, bool]:
            async with sem:
                return await self._probe_single_model_via_chat(client, mid)

        results = await asyncio.gather(*(_run(mid) for mid in ids))
        probed = {mid: ok for mid, ok in results}
        passed = sum(1 for ok in probed.values() if ok)
        # Результат пробы меняется редко; на INFO — только когда изменился,
        # иначе одна и та же строка шла раз в минуту.
        log_on_change(
            logger,
            f"health.chat_probe.{self.id}",
            (passed, len(ids)),
            "[%s] chat-probe: %d / %d моделей ответили на /v1/chat/completions",
            self.id,
            passed,
            len(ids),
        )
        return probed

    async def _filter_models_by_health(
        self, models: List[ModelInfo]
    ) -> List[ModelInfo]:
        if not models or not model_health_filter_enabled(self._config):
            return models
        try:
            model_ids = [m.model_id for m in models if (m.model_id or "").strip()]
            if not model_ids:
                return models

            max_probe = model_health_probe_max(self._config)
            ids_to_probe = model_ids[:max_probe]
            if len(model_ids) > max_probe:
                # Строка про лимит повторяется на каждую пересборку каталога
                # (раз в MODELS_AVAILABLE_CACHE_SECONDS), а сам лимит статичен.
                log_on_change(
                    logger,
                    f"health.probe_limit.{self.id}",
                    (max_probe, len(model_ids)),
                    "[%s] model health: проверяем %d из %d (лимит MODEL_HEALTH_PROBE_MAX)",
                    self.id,
                    max_probe,
                    len(model_ids),
                )

            payload, health_err = await self._try_litellm_batch_health(
                timeout=self._health_probe_timeout()
            )
            if payload is not None:
                unhealthy = unhealthy_names_from_litellm_payload(payload)
                if unhealthy:
                    return exclude_models_by_names(
                        models, unhealthy, provider_id=self.id
                    )
                return models

            if self._health_error_is_forbidden(health_err):
                logger.warning(
                    "[%s] GET /health запрещён virtual key (%s) — chat-probe",
                    self.id,
                    (health_err or "")[:240],
                )
            else:
                # Недоступность batch /health — состояние шлюза, а не событие:
                # пока оно не меняется, повторять его каждую минуту незачем.
                log_on_change(
                    logger,
                    f"health.batch_unavailable.{self.id}",
                    str(health_err or "no data")[:240],
                    "[%s] batch /health недоступен (%s) — chat-probe",
                    self.id,
                    (health_err or "no data")[:240],
                )

            async with httpx.AsyncClient(
                timeout=self._chat_probe_timeout(), verify=self._http_verify()
            ) as client:
                probed = await self._probe_models_via_chat_map(client, ids_to_probe)
            return filter_models_by_chat_probe(models, probed, provider_id=self.id)
        except Exception:
            logger.exception(
                "[%s] model health filter failed; returning unfiltered list", self.id
            )
            return models

    async def health(self) -> ProviderHealth:
        return await self._connection_health(timeout=self._short_timeout())

    def _health_probe_timeout(self) -> httpx.Timeout:
        """Таймаут для batch/per-model health (LiteLLM может вызывать каждую модель)."""
        read = min(120.0, max(30.0, float(self._config.timeout) / 2))
        return httpx.Timeout(read, connect=5.0, read=read, write=5.0)

    async def list_models(self) -> List[ModelInfo]:
        items = await self._fetch_model_list_items()
        return await self._filter_models_by_health_cached(items)

    async def _filter_models_by_health_cached(
        self, models: List[ModelInfo]
    ) -> List[ModelInfo]:
        if not models or not model_health_filter_enabled(self._config):
            return models

        from backend.settings import get_settings

        ttl = max(1, int(get_settings().model_health.models_available_cache_seconds))
        cache_key = self.id
        item_ids = tuple(
            sorted(
                (m.model_id or "").strip() for m in models if (m.model_id or "").strip()
            )
        )
        now = time.monotonic()

        async with _FILTERED_MODELS_CACHE_LOCK:
            cached = _FILTERED_MODELS_CACHE.get(cache_key)
            if cached and (now - cached[0]) < ttl and cached[1] == item_ids:
                logger.debug(
                    "[%s] health-filter cache hit (%d моделей, ttl=%ds)",
                    self.id,
                    len(cached[2]),
                    ttl,
                )
                return list(cached[2])

        filtered = await self._filter_models_by_health(models)
        if models and not filtered:
            logger.warning(
                "[%s] health-filter cache: пустой результат — кэшируем полный список (%d)",
                self.id,
                len(models),
            )
            filtered = models

        async with _FILTERED_MODELS_CACHE_LOCK:
            _FILTERED_MODELS_CACHE[cache_key] = (
                time.monotonic(),
                item_ids,
                list(filtered),
            )

        return filtered

    async def _fetch_model_list_items(self) -> List[ModelInfo]:
        static = (self._config.static_model or "").strip()
        configured = [
            str(m).strip() for m in self._config.models or [] if str(m or "").strip()
        ]
        url = f"{self.base_url}/v1/models"
        headers = self._headers()
        _trace_outbound_probe(self.id, "list_models", url)
        try:
            async with httpx.AsyncClient(
                timeout=self._short_timeout(), verify=self._http_verify()
            ) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
            items: List[ModelInfo] = []
            for row in data.get("data", []) or []:
                if not isinstance(row, dict):
                    continue
                mid = str(row.get("id") or "").strip()
                if not mid:
                    continue
                extra = {k: v for k, v in row.items() if k not in {"id"}}
                items.append(
                    ModelInfo(
                        provider_id=self.id,
                        model_id=mid,
                        display_name=str(
                            row.get("display_name") or row.get("name") or mid
                        ),
                        context_size=_parse_context_size_from_mapping(extra),
                        extra=extra,
                    )
                )
            if configured:
                dropped = [
                    m.model_id for m in items if not _model_in_allowlist(m, configured)
                ]
                items = [m for m in items if _model_in_allowlist(m, configured)]
                if dropped:
                    logger.info(
                        "[%s] models.name задан — из ответа шлюза оставлены только "
                        "перечисленные; отброшено: %s",
                        self.id,
                        dropped,
                    )
            if configured:
                missing = [
                    c
                    for c in configured
                    if not any(_model_in_allowlist(m, [c]) for m in items)
                ]
                if missing:
                    logger.warning(
                        "[%s] в models.name перечислены модели, которых шлюз не отдал "
                        "— в список не попадут: %s",
                        self.id,
                        missing,
                )
            if not items and static:
                items.append(
                    ModelInfo(
                        provider_id=self.id,
                        model_id=static,
                        display_name=static,
                        extra={
                            "synthetic": True,
                            "reason": "empty_list_with_static_model",
                        },
                    )
                )
            return items
        except Exception as e:
            err = self._http_error_details(e)
            logger.exception(
                "[LLM-PROVIDER][list_models] id=%s url=%s status=%s type=%s reason=%r auth=%s",
                self.id,
                url,
                err.get("status_code"),
                err.get("error_type"),
                err.get("reason"),
                self._safe_auth_diag(headers),
            )
            if static:
                logger.warning(
                    "Provider %s /v1/models failed (%s); fallback на static_model=%r",
                    self.id,
                    e,
                    static,
                )
                fallback_items = [
                        ModelInfo(
                            provider_id=self.id,
                        model_id=static,
                        display_name=static,
                        extra={
                            "synthetic": True,
                            "reason": f"fallback:{type(e).__name__}",
                        },
                    )
                ]
                return fallback_items
            logger.warning(
                "[%s] /v1/models недоступен (%s) — список моделей пуст; "
                "из конфига ничего не подставляем",
                self.id,
                type(e).__name__,
            )
            return []

    async def ensure_model_loaded(self, model_id: str) -> bool:
        """
        Базовая реализация: проверяем, что model_id есть в list_models() или
        совпадает со static_model. Никаких сетевых побочных эффектов.
        Подклассы (LlmSvcProvider) переопределяют это.
        """
        mid = (model_id or "").strip()
        if not mid:
            return False
        static = (self._config.static_model or "").strip()
        if static and mid == static:
            return True
        configured = {
            str(m).strip() for m in (self._config.models or []) if str(m or "").strip()
        }
        if mid in configured:
            return True
        try:
            models = await self._fetch_model_list_items()
        except Exception:
            logger.exception("ensure_model_loaded(): list_models error")
            return bool(static and mid == static)
        for m in models:
            if m.model_id == mid:
                return True
        logger.warning(
            "Provider %s: модель %r отсутствует в /v1/models. Она должна быть запущена на стороне сервера (для vLLM/OpenAI свап невозможен).",
            self.id,
            mid,
        )
        return False

    def _log_finish_reason(self, data: dict, *, model: str) -> None:
        """Аномальный finish_reason — единственный след «мягкой» блокировки.

        Часть шлюзов (Phoenix/LiteLLM с guardrail) отвечает HTTP 200 и пустым
        content, а причину кладёт в ```finish_reason``` (```content_filter```,
        ```blocked```, ```guardrail```) или в отдельное поле. Без этого лога такой
        ответ неотличим от «модель ничего не сказала».
        """
        try:
            choice = (data.get("choices") or [{}])[0]
        except (IndexError, AttributeError, TypeError):
            return
        reason = str(choice.get("finish_reason") or choice.get("stop_reason") or "")
        if not reason or reason.lower() in ("stop", "tool_calls", "function_call"):
            return
        extra = {
            k: data.get(k)
            for k in ("guardrail", "guardrails", "moderation", "warning", "error")
            if data.get(k)
        }
        logger.warning(
            "[%s] нештатный finish_reason=%r model=%r (длина content=%s)%s",
            self.id,
            reason,
            model,
            len(str((choice.get("message") or {}).get("content") or "")),
            (
                f" доп. поля ответа: {_cut_for_log(json.dumps(extra, ensure_ascii=False))}"
                if extra
                else ""
            ),
        )

    def _parse_chat_response(self, data: dict, *, cef_rid: str) -> ChatResult:
        choices = data.get("choices") or []
        if not choices:
            logger.error("[%s] chat: нет choices в ответе: %s", self.id, data)
            log_cef_int006_llm_api_failure(
                request_uuid=cef_rid,
                code_status="FORMAT",
                text_status=str(data)[:512],
                service_name=f"openai-compat-{self.id}",
                status_code=200,
            )
            return ChatResult(content="Ошибка генерации ответа")
        msg = choices[0].get("message") or {}
        tool_calls_raw = msg.get("tool_calls") or []
        tool_calls: List[ToolCall] = []
        for tc in tool_calls_raw:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = str(fn.get("name") or "")
            if not name:
                continue
            args_raw = fn.get("arguments") or "{}"
            try:
                args = (
                    json.loads(args_raw)
                    if isinstance(args_raw, str)
                    else dict(args_raw or {})
                )
            except json.JSONDecodeError:
                args = {"raw": args_raw}
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id") or uuid.uuid4().hex),
                    name=name,
                    arguments=args if isinstance(args, dict) else {},
                )
            )
        content = _normalize_content_payload(msg.get("content"))
        if not tool_calls and content:
            from backend.mcp.text_tool_calls import extract_tool_calls_from_content

            content, parsed_calls = extract_tool_calls_from_content(content)
            tool_calls.extend(parsed_calls)
        reasoning = _normalize_reasoning_payload(
            msg.get("reasoning_content") or msg.get("reasoning")
        ).strip()
        cleaned = clean_llm_response(content)
        if reasoning and "<think>" not in cleaned:
            cleaned = (
                f"<think>{reasoning}</think>\n\n{cleaned}" if reasoning else cleaned
            )
        return ChatResult(content=cleaned, tool_calls=tool_calls, raw_message=msg)

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        request_extra: Optional[Dict[str, Any]] = None,
    ) -> ChatResult:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
        if request_extra:
            for k, v in request_extra.items():
                if v is not None:
                    payload[k] = v
        cef_rid = uuid.uuid4().hex
        log_cef_int003_llm_request(
            base_url=self.base_url,
            provider_id=self.id,
            model=model,
            request_uuid=cef_rid,
        )
        async with httpx.AsyncClient(
            timeout=self._request_timeout(), verify=self._http_verify()
        ) as client:
            try:
                # 429 от шлюза (лимит запросов) - пауза и повтор. 24.09 после
                # 17 субагентов подряд итог главного упал на 429 - вся
                # работа пропала.
                for attempt in range(_rate_limit_retries() + 1):
                    response = await client.post(
                        f"{self.base_url}/v1/chat/completions",
                        headers=self._headers(payload=payload),
                        json=payload,
                    )
                    if response.status_code != 429 or attempt >= _rate_limit_retries():
                        break
                    delay = _rate_limit_delay(response, attempt)
                    logger.warning(
                        "[%s] 429 Too Many Requests model=%r - повтор %s/%s через %.0f с",
                        self.id,
                        model,
                        attempt + 1,
                        _rate_limit_retries(),
                        delay,
                    )
                    await asyncio.sleep(delay)
                # Вход + max_tokens больше контекста модели - один повтор с
                # max_tokens по оставшемуся месту.
                fit = _context_fit_max_tokens(
                    self.id,
                    model,
                    response.status_code,
                    response.text,
                    payload.get("max_tokens"),
                )
                if fit:
                    payload["max_tokens"] = fit
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers=self._headers(payload=payload),
                    json=payload,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as he:
                _log_llm_http_failure(
                    provider_id=self.id,
                    status_code=he.response.status_code,
                    body=he.response.text or "",
                    model=model,
                    messages=messages,
                    max_tokens=payload.get("max_tokens", max_tokens),
                    has_tools=bool(tools),
                    payload_keys=sorted(payload.keys()),
                )
                log_cef_int006_llm_api_failure(
                    request_uuid=cef_rid,
                    code_status=str(he.response.status_code),
                    text_status=(he.response.text or "")[:512],
                    service_name=f"openai-compat-{self.id}",
                    status_code=he.response.status_code,
                )
                raise
            except Exception as e:
                logger.exception("Ошибка операции")
                log_cef_int006_llm_api_failure(
                    request_uuid=cef_rid,
                    code_status="EXCEPTION",
                    text_status=str(e)[:512],
                    service_name=f"openai-compat-{self.id}",
                    status_code=None,
                )
                raise
            data = response.json()
        self._log_finish_reason(data, model=model)
        return self._parse_chat_response(data, cef_rid=cef_rid)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        *,
        request_extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if request_extra:
            for k, v in request_extra.items():
                if v is not None:
                    payload[k] = v
        thinking_flag = is_thinking_requested(request_extra)
        logger.debug(
            "[%s] chat flags: enable_thinking=%r payload_keys=%s",
            self.id,
            thinking_flag,
            sorted(list(payload.keys())),
        )
        logger.debug("[%s] POST /v1/chat/completions model=%r", self.id, model)
        result = await self.chat_completion(
            messages,
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            request_extra=request_extra,
        )
        thinking_requested = thinking_flag
        cleaned = result.content
        if not thinking_requested and _has_think_markers(cleaned):
            stripped = _strip_think_tags(cleaned)
            if not stripped.strip():
                # Модель потратила весь бюджет на рассуждение и до ответа не дошла.
                # HTTP 200, тело непустое — но после вырезания <think> не остаётся
                # ничего, и вызывающий код получает пустую строку. Раньше это было
                # молча: ask_agent возвращал «Не удалось получить ответ от модели»,
                # а служебные вызовы (HyDE, опечатки, суммаризация) принимали этот
                # текст за ответ модели. 01.08 так подменился поисковый запрос.
                logger.warning(
                    "[%s] модель вернула только рассуждение (%s симв.) и пустой ответ: "
                    "model=%r max_tokens=%s. Увеличьте max_tokens или отключите "
                    "мышление для служебных вызовов",
                    self.id,
                    len(cleaned),
                    model,
                    max_tokens,
                )
            return stripped
        return cleaned

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        callback: StreamCallback,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        *,
        request_extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        # Auto-continue:
        # 1) finish_reason=length — упёрлись в max_tokens одного ответа
        # 2) презентация: модель часто ставит stop, не дописав N слайдов —
        #    дожимаем, пока class="slide" не достигнет запрошенного числа
        # Лимит продолжений защищает от бесконечного цикла.
        max_continuations = _auto_continue_max()
        requested_slides = _requested_slide_count(messages)
        if requested_slides and requested_slides > 8:
            # На длинных презах 5 продолжений часто мало (тяжёлый HTML).
            max_continuations = max(max_continuations, min(requested_slides, 20))
        base_messages = list(messages)
        thinking_requested = is_thinking_requested(request_extra)

        accumulated = ""
        reasoning_accumulated = ""
        _final_finish_reason: Optional[str] = None
        stream_errors = 0
        error_box: List[str] = []

        for _cont_idx in range(max_continuations + 1):
            if _cont_idx == 0 or not accumulated.strip():
                pass_messages = base_messages
            else:
                have = _count_html_slides(accumulated)
                is_pres = _looks_like_presentation_html(accumulated)
                # Важно: открываем документ В accumulated до стрима, иначе модель
                # допишет </body></html> / новый fence УЖЕ ПОСЛЕ закрытого ``` → хвост в UI.
                if is_pres:
                    opened = _open_presentation_for_continue(accumulated)
                    if opened != accumulated:
                        accumulated = opened
                        if (
                            _invoke_stream_callback(
                                callback, "", accumulated, "content"
                            )
                            is False
                        ):
                            break
                if requested_slides and have < requested_slides and is_pres:
                    continue_hint = (
                        f'В HTML сейчас {have} слайдов с class="slide", нужно ровно {requested_slides}. '
                        f'Допиши ТОЛЬКО следующие элементы <… class="slide"> с номерами {have + 1}–{requested_slides} '
                        "сразу после последнего слайда.\n"
                        "СТРОГО ЗАПРЕЩЕНО: новый блок ``` / ```html, слово html отдельной строкой, "
                        "<!DOCTYPE, <html>, <head>, <body>, пояснения, повтор слайдов.\n"
                        f"Когда будет ровно {requested_slides} слайдов — закрой </body></html> и fence ```."
                    )
                elif is_pres and _presentation_looks_truncated(accumulated):
                    continue_hint = (
                        f'В HTML сейчас {have} слайдов с class="slide", но ответ оборван '
                        "(незакрытый комментарий, mid-tag или незакрытые <div> в последнем слайде).\n"
                        'Допиши ТОЛЬКО недостающие <div class="slide"> сразу после последнего готового слайда, '
                        "затем финальный слайд и закрой </body></html> и fence ```.\n"
                        "СТРОГО ЗАПРЕЩЕНО: новый блок ``` / ```html, слово html отдельной строкой, "
                        "<!DOCTYPE, <html>, <head>, <body>, пояснения, повтор слайдов."
                    )
                else:
                    continue_hint = (
                        "Продолжи ровно с того места, где ответ оборвался. "
                        "Не повторяй уже написанное, не добавляй пояснений — только продолжение. "
                        "Не открывай новый блок ``` / ```html — допиши текущий HTML."
                    )
                pass_messages = base_messages + [
                    {"role": "assistant", "content": accumulated},
                    {"role": "user", "content": continue_hint},
                ]

            _acc_snapshot = accumulated
            _reason_snapshot = reasoning_accumulated
            finish_reason, pass_accumulated, pass_reasoning = (
                await self._stream_chat_once(
                pass_messages,
                model,
                callback,
                temperature=temperature,
                max_tokens=max_tokens,
                request_extra=request_extra,
                _shared=lambda: (_acc_snapshot, _reason_snapshot),
                    _error_box=error_box,
                )
            )
            fr = str(finish_reason or "").lower()

            # Обрыв стрима (сеть / зависание шлюза): дописываем продолжением то,
            # что уже пришло, а если не пришло ничего — повторяем запрос.
            stream_cut = False
            if fr == "stream_error":
                stream_errors += 1
                can_retry = (
                    stream_errors <= _stream_error_retries()
                    and _cont_idx < max_continuations
                )
                if not (pass_accumulated or "").strip():
                    if can_retry:
                        logger.warning(
                            "[%s] стрим оборван до первого токена, повтор %s/%s",
                            self.id,
                            stream_errors,
                            _stream_error_retries(),
                        )
                        continue
                    accumulated = (
                        error_box[-1]
                        if error_box
                        else "Ошибка потока: соединение с LLM прервано"
                    )
                    reasoning_accumulated = pass_reasoning
                    _final_finish_reason = "error"
                    break
                stream_cut = can_retry
                logger.warning(
                    "[%s] стрим оборван на %s симв. (pass #%s): %s",
                    self.id,
                    len(pass_accumulated),
                    _cont_idx,
                    "продолжаем с места обрыва"
                    if stream_cut
                    else "лимит повторов исчерпан, отдаём что есть",
                )
            # Ошибка потока в ПРОДОЛЖЕНИИ: не теряем уже готовые слайды.
            elif fr == "error" or (
                isinstance(pass_accumulated, str)
                and pass_accumulated.startswith("Ошибка потока")
            ):
                if _cont_idx > 0 and _acc_snapshot.strip():
                    logger.debug(
                        "[%s] auto-continue: ошибка в pass #%s, оставляем накопленное (%s симв.)",
                        self.id,
                        _cont_idx,
                        len(_acc_snapshot),
                    )
                    accumulated = _acc_snapshot
                    reasoning_accumulated = _reason_snapshot
                    _final_finish_reason = "stop"
                    break
                accumulated = pass_accumulated
                reasoning_accumulated = pass_reasoning
                _final_finish_reason = finish_reason
                break

            accumulated = pass_accumulated
            reasoning_accumulated = pass_reasoning

            # Continue-pass: модель часто начинает новый ```html / <!DOCTYPE> —
            # вырезаем reopen из дельты, иначе UI «троится» и в чат утекает «html».
            if _cont_idx > 0:
                merged = _merge_presentation_continue(_acc_snapshot, accumulated)
                if merged != accumulated:
                    accumulated = merged
                    if (
                        _invoke_stream_callback(callback, "", accumulated, "content")
                        is False
                    ):
                        break

            # Детект обрыва на СЫРОМ тексте. Finalize (закрытый fence) только
            # в конце всех pass'ов — иначе ```html попадает внутрь слайдов.
            trunc_before_finalize = _presentation_looks_truncated(accumulated)
            _final_finish_reason = finish_reason

            # Пользовательский стоп — не крутим continue.
            if fr == "stop_user":
                break

            have_slides = _count_html_slides(accumulated)
            need_more_slides = bool(
                requested_slides
                and have_slides < requested_slides
                and (_looks_like_presentation_html(accumulated) or have_slides > 0)
            )
            need_fix_truncation = bool(
                trunc_before_finalize
                and have_slides > 0
                and not _presentation_looks_finished(accumulated)
            ) or _html_document_unfinished(accumulated)

            # length после финального слайда без явного N — не дожимать слайды.
            if (
                fr == "length"
                and not need_more_slides
                and not need_fix_truncation
                and not requested_slides
                and _presentation_looks_finished(accumulated)
            ):
                break

            if fr == "length" or stream_cut or need_more_slides or need_fix_truncation:
                if _cont_idx >= max_continuations:
                    logger.debug(
                        "[%s] auto-continue: лимит продолжений (%s), стоп "
                        "(finish=%r slides=%s/%s trunc=%s накоплено=%s)",
                        self.id,
                        max_continuations,
                        fr,
                        have_slides,
                        requested_slides,
                        need_fix_truncation,
                        len(accumulated),
                    )
                    break
                logger.debug(
                    "[%s] auto-continue #%s: finish=%r slides=%s/%s trunc=%s, дожимаем (накоплено %s симв.)",
                    self.id,
                    _cont_idx + 1,
                    fr,
                    have_slides,
                    requested_slides,
                    need_fix_truncation,
                    len(accumulated),
                )
                continue

            break

        # Единая финальная склейка: один ```html, chrome (или fallback), без fence-шума.
        if _looks_like_presentation_html(accumulated):
            finalized = _finalize_presentation_message(accumulated)
            if finalized != accumulated:
                accumulated = finalized
                _invoke_stream_callback(callback, "", accumulated, "content")

        logger.debug(
            "[%s] ИТОГ СТРИМА (all passes): finish_reason=%r slides=%s/%s накоплено=%s симв.",
            self.id,
            _final_finish_reason,
            _count_html_slides(accumulated),
            requested_slides,
            len(accumulated),
        )
        cleaned = clean_llm_response(accumulated)
        if thinking_requested:
            return cleaned
        if _has_think_markers(cleaned):
            return _strip_think_tags(cleaned)
        if reasoning_accumulated.strip() and "<think>" not in cleaned:
            return f"<think>{reasoning_accumulated.strip()}</think>\n\n{cleaned}"
        return cleaned

    async def _stream_chat_once(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        callback: StreamCallback,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        *,
        request_extra: Optional[Dict[str, Any]] = None,
        _shared=None,
        _rl_attempt: int = 0,
        _error_box: Optional[List[str]] = None,
    ):
        """Один проход стрима. Возвращает (finish_reason, accumulated, reasoning).

        accumulated/reasoning инициализируются значениями из _shared() (для
        бесшовного продолжения при auto-continue): callback получает суммарный
        текст всех проходов, а не только текущего.

        finish_reason ``stream_error`` — стрим оборвался (сеть, зависание, закрытие
        без finish_reason); accumulated содержит всё, что успело прийти, текст
        ошибки кладётся в ``_error_box``.
        """
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if request_extra:
            for k, v in request_extra.items():
                if v is not None:
                    payload[k] = v
        thinking_requested = is_thinking_requested(request_extra)
        logger.debug(
            "[%s] stream enable_thinking=%r model=%r url=%s/v1/chat/completions",
            self.id,
            thinking_requested,
            model,
            self.base_url,
        )
        headers = self._headers(accept_sse=True, payload=payload)
        read_s = _stream_read_timeout_sec(self._timeout_read)
        stream_timeout = httpx.Timeout(read_s, connect=10.0, read=read_s, write=10.0)
        # Продолжаем накопление предыдущих проходов (auto-continue), чтобы callback
        # получал суммарный текст, а UI не «моргал» на границе продолжений.
        _seed_acc, _seed_reason = _shared() if _shared else ("", "")
        accumulated = _seed_acc or ""
        reasoning_accumulated = _seed_reason or ""
        logged_delta_shape = False
        # Диагностика обрыва: у цикла ниже пять выходов, и четыре из них
        # сегодня не оставляют в логах ничего. Пишем, какой сработал.
        _t0 = time.monotonic()
        _chunks = 0
        _end_reason = "iterator_end"
        _finish_reason_seen = None
        logger.debug(
            "[%s] POST /v1/chat/completions stream=True model=%r read_timeout=%ss",
            self.id,
            model,
            read_s,
        )
        cef_rid = uuid.uuid4().hex
        log_cef_int003_llm_request(
            base_url=self.base_url,
            provider_id=self.id,
            model=model,
            request_uuid=cef_rid,
        )
        # Задача чтения живёт между тиками: wait_for отменял бы __anext__, а
        # отмена закрывает сам генератор aiter_lines — любая пауза модели > 1 с
        # молча обрывала стрим (в шлюзе: «client disconnected»).
        next_line_task: Optional[asyncio.Task] = None
        try:
            async with httpx.AsyncClient(
                timeout=stream_timeout, verify=self._http_verify()
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        # Тело потокового ответа не прочитано, и .text в обработчике
                        # ошибки бросил бы ResponseNotRead — причина отказа шлюза
                        # (в т.ч. guardrail) терялась вместе с ней. Читаем здесь,
                        # пока поток открыт.
                        try:
                            await response.aread()
                        except Exception:
                            logger.debug(
                                "[%s] тело ошибки потока прочитать не удалось", self.id
                            )
                    response.raise_for_status()
                    line_iter = response.aiter_lines().__aiter__()
                    idle_s = _stream_idle_timeout_sec()
                    last_line_at = time.monotonic()
                    drain_until: Optional[float] = None
                    while True:
                        if drain_until is not None and time.monotonic() >= drain_until:
                            break
                        if next_line_task is None:
                            next_line_task = asyncio.ensure_future(
                                _next_line(line_iter)
                            )
                        # Тик 1 с: стоп пользователя не ждёт следующего токена.
                        done, _ = await asyncio.wait({next_line_task}, timeout=1.0)
                        if not done:
                            if (
                                _invoke_stream_callback(
                                    callback, "", accumulated, "heartbeat"
                                )
                                is False
                            ):
                                logger.debug(
                                    "[%s] поток прерван callback'ом (heartbeat/stop)",
                                    self.id,
                                )
                                _end_reason = "callback_stop"
                                return ("stop_user", accumulated, reasoning_accumulated)
                            if (
                                drain_until is None
                                and time.monotonic() - last_line_at > idle_s
                            ):
                                _end_reason = "idle_timeout"
                                raise _StreamIdleTimeout(
                                    f"LLM не присылает данные {idle_s:.0f} с"
                                )
                            continue
                        finished_task, next_line_task = next_line_task, None
                        try:
                            line = finished_task.result()
                        except StopAsyncIteration:
                            break
                        last_line_at = time.monotonic()
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            if drain_until is None:
                                _end_reason = "done"
                            break
                        if drain_until is not None:
                            continue
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        choices = data.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        if not logged_delta_shape:
                            logged_delta_shape = True
                            logger.debug(
                                "[%s] first delta keys=%s has_reasoning_fields=%s has_content_fields=%s",
                                self.id,
                                sorted(list(delta.keys())),
                                any(
                                    (
                                        k in delta
                                        for k in (
                                            "reasoning_content",
                                            "reasoning",
                                            "reasoning_text",
                                            "thinking",
                                            "thought",
                                        )
                                    )
                                ),
                                any(
                                    (
                                        k in delta
                                        for k in (
                                            "content",
                                            "text",
                                            "output_text",
                                            "message",
                                        )
                                    )
                                ),
                            )
                        reasoning_source = delta.get("reasoning_content")
                        if reasoning_source is None:
                            reasoning_source = delta.get("reasoning")
                        if reasoning_source is None:
                            reasoning_source = delta.get("reasoning_text")
                        if reasoning_source is None:
                            reasoning_source = delta.get("thinking")
                        if reasoning_source is None:
                            reasoning_source = delta.get("thought")
                        reasoning_chunk = _normalize_reasoning_payload(reasoning_source)
                        if thinking_requested and reasoning_chunk:
                            reasoning_accumulated += reasoning_chunk
                            if (
                                _invoke_stream_callback(
                                    callback,
                                    reasoning_chunk,
                                    reasoning_accumulated,
                                    "reasoning",
                                )
                                is False
                            ):
                                logger.debug(
                                    "[%s] поток прерван callback'ом (reasoning)",
                                    self.id,
                                )
                                return ("stop_user", accumulated, reasoning_accumulated)
                        chunk = _normalize_content_payload(
                            delta.get("content")
                            or delta.get("text")
                            or delta.get("output_text")
                            or delta.get("message")
                        )
                        if chunk:
                            accumulated += chunk
                            _chunks += 1
                            if (
                                "<|im_start|>" in accumulated
                                or "<|im_end|>" in accumulated
                            ):
                                logger.debug(
                                    "[%s] СТРИМ ОБОРВАН callback'ом: чанков=%s накоплено=%s симв. за %.1f c",
                                    self.id,
                                    _chunks,
                                    len(accumulated),
                                    time.monotonic() - _t0,
                                ) 
                                _end_reason = "chat_template_tag"
                                return ("stop", accumulated, reasoning_accumulated)
                            # Быстрый режим: не светим reasoning в UI (в т.ч. orphan </think>).
                            cb_accumulated = accumulated
                            if not thinking_requested and _has_think_markers(accumulated):
                                cb_accumulated = _strip_think_tags(accumulated)
                            # Отдаём контентный чанк наружу — это и есть стрим на фронт.
                            # callback получает суммарный accumulated (в т.ч. хвост
                            # предыдущих проходов auto-continue), поэтому UI видит
                            # непрерывный текст. Отказ callback'а прерывает поток.
                            if (
                                _invoke_stream_callback(
                                    callback, chunk, cb_accumulated, "content"
                                )
                                is False
                            ):
                                logger.debug(
                                    "[%s] поток прерван callback'ом (content)", self.id
                                )
                                _end_reason = "callback_stop"
                                return ("stop_user", accumulated, reasoning_accumulated)
                        # finish_reason (в т.ч. length = лимит токенов) — конец стрима.
                        # Часть провайдеров не присылает [DONE] после этого: ждём хвост
                        # не дольше _STREAM_DRAIN_AFTER_FINISH_S, а не read-timeout.
                        finish_reason = choices[0].get("finish_reason") or choices[
                            0
                        ].get("stop_reason")
                        if finish_reason:
                            _finish_reason_seen = str(finish_reason)
                            _end_reason = "finish_reason"
                            logger.debug(
                                "[%s] stream finish_reason=%r model=%r (накоплено %s симв.)",
                                self.id,
                                finish_reason,
                                model,
                                len(accumulated),
                            )
                            drain_until = time.monotonic() + _STREAM_DRAIN_AFTER_FINISH_S
        except httpx.HTTPStatusError as e:
            stream_body = _safe_response_text(e.response)
            _log_llm_http_failure(
                provider_id=self.id,
                status_code=e.response.status_code,
                body=stream_body,
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                has_tools=False,
                payload_keys=sorted(payload.keys()),
            )
            log_cef_int006_llm_api_failure(
                request_uuid=cef_rid,
                code_status=str(e.response.status_code),
                text_status=stream_body[:512],
                service_name=f"openai-compat-{self.id}",
                status_code=e.response.status_code,
            )
            # 429 приходит до первого токена - повтор ничего не задвоит.
            if e.response.status_code == 429 and _rl_attempt < _rate_limit_retries():
                delay = _rate_limit_delay(e.response, _rl_attempt)
                logger.warning(
                    "[%s] 429 Too Many Requests (поток) model=%r - повтор %s/%s через %.0f с",
                    self.id,
                    model,
                    _rl_attempt + 1,
                    _rate_limit_retries(),
                    delay,
                )
                await asyncio.sleep(delay)
                return await self._stream_chat_once(
                    messages,
                    model,
                    callback,
                    temperature,
                    max_tokens,
                    request_extra=request_extra,
                    _shared=_shared,
                    _rl_attempt=_rl_attempt + 1,
                    _error_box=_error_box,
                )
            # Вход + max_tokens больше контекста модели: отказ тоже до первого
            # токена - повтор с max_tokens по оставшемуся месту.
            fit = _context_fit_max_tokens(
                self.id, model, e.response.status_code, stream_body, max_tokens
            )
            if fit:
                return await self._stream_chat_once(
                    messages,
                    model,
                    callback,
                    temperature,
                    fit,
                    request_extra=request_extra,
                    _shared=_shared,
                    _rl_attempt=_rl_attempt,
                    _error_box=_error_box,
            )
            if e.response.status_code == 503:
                detail = ""
                try:
                    detail = str((e.response.json() or {}).get("detail", ""))
                except Exception:
                    logger.debug("[%s] 503: тело не разобрано как JSON", self.id)
                    detail = stream_body[:500]
                low = detail.lower()
                if "not loaded" in low or "не загруж" in low:
                    return (
                        "stop",
                        "Модель не загружена в LLM-бэкенде (503). Проверьте, что модель активна на стороне провайдера.",
                        "",
                    )
                return (
                    "stop",
                    "Сервис LLM недоступен (503). Повторите запрос через несколько секунд.",
                    "",
                )
            return ("stop", f"Ошибка потока: {e}", "")
        except Exception as e:
            logger.exception(
                "[%s] stream error: конец=%s чанков=%s накоплено=%s симв. за %.1f c",
                self.id,
                _end_reason,
                _chunks,
                len(accumulated),
                time.monotonic() - _t0,
            )
            log_cef_int006_llm_api_failure(
                request_uuid=cef_rid,
                code_status="EXCEPTION",
                text_status=str(e)[:512],
                service_name=f"openai-compat-{self.id}",
                status_code=None,
            )
            if _error_box is not None:
                _error_box.append(f"Ошибка потока: {e}")
            # Уже полученный текст не выбрасываем: stream_chat решит, дописывать
            # его продолжением или отдать как есть.
            return ("stream_error", accumulated, reasoning_accumulated)
        finally:
            if next_line_task is not None:
                if next_line_task.done():
                    if not next_line_task.cancelled():
                        next_line_task.exception()
                else:
                    next_line_task.cancel()
        if _finish_reason_seen is None and _end_reason == "iterator_end":
            # Соединение закрылось без finish_reason и [DONE] — ответ оборван.
            logger.warning(
                "[%s] стрим закрыт без finish_reason/[DONE]: чанков=%s накоплено=%s симв. за %.1f c",
                self.id,
                _chunks,
                len(accumulated),
                time.monotonic() - _t0,
            )
            return ("stream_error", accumulated, reasoning_accumulated)
        cleaned = clean_llm_response(accumulated)
        # Итог одной строкой. Ключевое - сравнить накоплено и после_очистки:
        # если второе заметно меньше, текст режет наша постобработка, а не
        # модель (незакрытый <think> вырезает всё до конца текста).
        logger.debug(
            "[%s] ИТОГ ПРОХОДА СТРИМА: конец=%s finish_reason=%r чанков=%s "
            "накоплено=%s симв. после_очистки=%s симв. think=%s "
            "reasoning=%s симв. max_tokens=%s за %.1f c",
            self.id,
            _end_reason,
            _finish_reason_seen,
            _chunks,
            len(accumulated),
            len(cleaned),
            "<think>" in cleaned.lower(),
            len(reasoning_accumulated),
            max_tokens,
            time.monotonic() - _t0,
        )
        # Постобработка (<think>, очистка) и решение о продолжении — в stream_chat.
        return (_finish_reason_seen, accumulated, reasoning_accumulated)
