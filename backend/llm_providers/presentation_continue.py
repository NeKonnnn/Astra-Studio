"""Бесшовная склейка длинных HTML-презентаций и эвристики auto-continue.

Перенесено из GPB_ASTRA ``openai_compat``: подсчёт слайдов, merge continue-pass,
финальная сборка одного ```html с chrome/fallback.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

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


_SLIDE_CLASS_QUOTED_RE = re.compile(
    r"""class\s*=\s*(["'])([^"']*)\1""",
    re.IGNORECASE,
)
# unquoted class=slide, но не slide-title / slide-header (дефис = часть токена)
_SLIDE_CLASS_UNQUOTED_RE = re.compile(
    r"""class\s*=\s*slide(?![\w-])""",
    re.IGNORECASE,
)
_REQUESTED_SLIDES_RE = re.compile(
    r"""(?ix)
    (?:
        (?:ровно|exactly)\s+(\d+)\s*(?:слайд|slide|страниц\w*|pages?)?
        | (\d+)\s*(?:слайд(?:ов|а|ы)?|slides?|страниц(?:а|ы)?|pages?)
        | (?:слайд(?:ов|а|ы)?|slides?|страниц(?:а|ы)?|pages?)\s*[:=]?\s*(\d+)
        # «презентацию на 25 …» / «presentation with 25 …»
        | презентац\w*\s+на\s+(\d+)
        | presentation\s+(?:with|of|on)\s+(\d+)
    )
    """
)


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


def _iter_message_texts(messages: List[Dict[str, Any]], *, roles: Tuple[str, ...] = ("user",)) -> List[str]:
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
    links = re.findall(r"<link\b[^>]*rel\s*=\s*['\"]stylesheet['\"][^>]*>", prefix, flags=re.I)
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
        "</head>\n<body>\n"
        + "\n".join(all_slides)
        + "\n</body>\n</html>"
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



