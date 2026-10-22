"""
routes/internal_rag.py — внутренние прокси для SVC-RAG.
SVC-RAG не ходит в llm-service напрямую: любые его LLM-вызовы (judge, иерархическая
суммаризация) идут сюда, а backend выполняет их своей выбранной моделью (ask_agent).
Авторизация — сетевая изоляция (как и весь трафик backend↔svc-rag): снаружи
/api/internal/ закрыт на frontend-nginx (404). Дополнительно (BUG-X-01) можно
задать общий секрет INTERNAL_RAG_SECRET: тогда каждый запрос обязан нести его в
заголовке X-Internal-Rag-Secret (иначе 401), и только после этого ему верим
заголовкам X-CEF-* (инициатор). Секрет не задан — прежний режим сетевой изоляции.
"""

import asyncio
import concurrent.futures
import hmac
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from backend.settings.logging import get_logger

logger = get_logger(__name__)

INTERNAL_SECRET_ENV = "INTERNAL_RAG_SECRET"
INTERNAL_SECRET_HEADER = "X-Internal-Rag-Secret"


def _configured_internal_secret() -> str:
    return (os.getenv(INTERNAL_SECRET_ENV) or "").strip()


async def _require_internal_secret(request: Request) -> None:
    """Если INTERNAL_RAG_SECRET задан — требуем его в заголовке (401 иначе).

    Выполняется раньше разбора X-CEF-*, поэтому неаутентифицированный вызывающий
    не может ни выполнить ручку, ни подставить чужого инициатора в аудит.
    """
    expected = _configured_internal_secret()
    if not expected:
        return
    got = (request.headers.get(INTERNAL_SECRET_HEADER) or "").strip()
    if not got or not hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8")):
        logger.warning(
            "internal/rag: отказ — нет или неверный %s (%s)",
            INTERNAL_SECRET_HEADER,
            request.url.path,
        )
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _audit_identity_from_svc_rag(request: Request):
    """Инициатор из заголовков SVC-RAG - для CEF и для X-User-ID к моделям.

    Пользователя backend знать неоткуда (вызов идёт от сервиса, а не от браузера),
    и вызов к модели ушёл бы на шлюз как служебный, хотя за ним стоит живой
    человек. SVC-RAG прокидывает логин теми же заголовками X-CEF-*,
    которыми backend прокидывает его в SVC-RAG - словарь общий.

    Доверие к заголовку ровно то же, что и к самой ручке: при заданном
    INTERNAL_RAG_SECRET сюда попадают только аутентифицированные вызовы
    (_require_internal_secret выполняется раньше); без секрета ручка держится на
    сетевой изоляции (/api/internal/ закрыт на nginx), о чём написано выше.

    Заголовков нет - ничего не трогаем, поведение прежнее.
    """
    from backend.settings.cef_logger.cef_audit_context import (
        cef_audit_reset,
        cef_audit_set,
    )

    suser = (request.headers.get("x-cef-suser") or "").strip()
    suid = (request.headers.get("x-cef-suid") or "").strip()
    if not suser and not suid:
        yield
        return
    user = {
        "username": suser or suid,
        "user_id": suid or None,
        "sntdom": (request.headers.get("x-cef-sntdom") or "").strip() or None,
    }
    tokens = cef_audit_set(request=request, user=user, socket_remote=None)
    try:
        yield
    finally:
        cef_audit_reset(tokens)


router = APIRouter(
    tags=["internal-rag"],
    # Порядок важен: сначала секрет, потом доверие к X-CEF-*.
    dependencies=[
        Depends(_require_internal_secret),
        Depends(_audit_identity_from_svc_rag),
    ],
)


class InternalRagLLMRequest(BaseModel):
    prompt: str
    purpose: str = "rag"
    temperature: float = 0.3
    max_tokens: int = 1024
    # Модель чата, из которого пришёл запрос. Пусто - как раньше: возьмётся
    # текущая выбранная (см. orchestrator_bridge, effective_path).
    model_path: Optional[str] = None


class InternalRagIndexFailedRequest(BaseModel):
    minio_object: str
    minio_bucket: str
    filename: str = ""
    reason: str = ""
    store: str = "kb"

@router.post("/api/internal/rag/index-failed")
async def internal_rag_index_failed(body: InternalRagIndexFailedRequest):
    """SVC-RAG сообщает, что индексация не состоялась — исходник можно убрать.

    Исходник удаляет backend: хранилище его, SVC-RAG к PVC не подключён. Пока
    backend ждёт ответа, он справляется сам. Но после таймаута он ждать
    перестаёт и файл намеренно НЕ удаляет: индексация могла дойти до конца уже
    без него. Если же она не дошла — сказать об этом может только SVC-RAG, иначе
    файл останется в хранилище без документа.

    Повторный вызов безвреден: удаление уже удалённого файла ничего не делает.
    Авторизация — сетевая изоляция, как и у остальных /api/internal/rag/*.
    """
    from backend.routes.rag import _delete_rag_source_file

    logger.warning(
        "SVC-RAG: индексация «%s» (%s) не состоялась (%s) — убираю исходник %s",
        body.filename or "?",
        body.store,
        (body.reason or "")[:300],
        body.minio_object,
    )
    try:
        _delete_rag_source_file(body.minio_object, body.minio_bucket)
    except Exception as e:
        logger.exception("Не удалось убрать исходник %s", body.minio_object)
        return {"ok": False, "error": str(e)}
    return {"ok": True}


@router.post("/api/internal/rag/llm")
async def internal_rag_llm(body: InternalRagLLMRequest):
    """Выполнить один LLM-вызов от имени SVC-RAG выбранной backend-моделью."""
    from backend.agent_llm_svc import ask_agent
    from backend.llm_providers.routing import is_llm_error_text

    prompt = body.prompt or ""
    loop = asyncio.get_running_loop()

    def _call() -> str:
        result = ask_agent(
            prompt,
            history=[],
            streaming=False,
            max_tokens=int(body.max_tokens),
            temperature=float(body.temperature),
            model_path=(body.model_path or None),
            # Ответ читает svc-rag, а не человек: мышление не нужно и вредно —
            # reasoning-модель тратит на него весь бюджет токенов.
            service_call=True,
        )
        return result or ""

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            content = await loop.run_in_executor(ex, _call)
    except Exception as e:
        logger.exception(
            "internal_rag_llm: ошибка вызова модели (purpose=%s)", body.purpose
        )
        return {"content": "", "error": str(e)}

    # ask_agent при сбое возвращает текст ошибки, а не исключение. Отдать его
    # как content нельзя: svc-rag запишет его в индекс как summary документа
    # (01.08 так и произошло — чанки с «Ошибка LLM (HTTP 422)» вместо текста).
    if is_llm_error_text(content):
        logger.warning(
            "internal_rag_llm: модель вернула текст ошибки (purpose=%s): %s",
            body.purpose,
            str(content)[:200],
        )
        return {"content": "", "error": str(content)[:400]}

    logger.debug(
        "[internal_rag_llm] purpose=%s prompt_chars=%s content_chars=%s "
        "(модель не задана явно — берётся дефолтная чатовая, см. _pick_chat_model)",
        body.purpose,
        len(prompt),
        len(content or ""),
    )
    return {"content": content or ""}


class InternalRagOcrPdfRequest(BaseModel):
    filename: str = "document.pdf"
    pdf_base64: str
    # 0-based номера страниц, которые надо прогнать через OCR (RAG: скан-страницы
    # гибридного PDF). null — штатная постраничная логика, как для вложений в чате.
    pages: Optional[List[int]] = None


@router.post("/api/internal/rag/ocr-pdf")
async def internal_rag_ocr_pdf(body: InternalRagOcrPdfRequest):
    """OCR PDF для SVC-RAG.

    Распознавание тем же 'временным' механизмом, что и вложения в
    чате: _ocr_pdf_payload_if_needed — pypdfium2-рендер страниц + vision-LLM
    по OCR_VISION_MODEL_PATH (параллельно до 4 страниц).

    Запрос (application/json): {"filename": str, "pdf_base64": str,
                                "pages": [0-based int] | null}
    Ответ: {"text": str, "ocr_used": bool, "pages": {idx: text}?, "error": str?}
    Поле "pages" (карта "0-based idx" → текст) возвращается, когда в запросе
    был задан список страниц.
    """
    import base64

    try:
        pdf_bytes = base64.b64decode(body.pdf_base64, validate=True)
    except Exception as e:
        logger.warning("internal_rag_ocr_pdf: некорректный base64 PDF: %s", e)
        return {
            "text": "",
            "ocr_used": False,
            "error": f"некорректный pdf_base64: {e}",
        }
    if not pdf_bytes:
        return {"text": "", "ocr_used": False, "error": "пустой PDF"}

    filename = (body.filename or "").strip() or "document.pdf"
    logger.info(
        "internal_rag_ocr_pdf: OCR PDF %s (%s байт) для SVC-RAG",
        filename,
        len(pdf_bytes),
    )

    try:
        from backend.routes.documents import _ocr_pdf_payload_if_needed

        page_indices = [int(p) for p in body.pages] if body.pages else None
        payload, warning = await _ocr_pdf_payload_if_needed(
            pdf_bytes, filename, {"cs1": "pdf", "type": "file"}, page_indices
        )
    except Exception as e:
        logger.exception("internal_rag_ocr_pdf: ошибка OCR PDF %s", filename)
        return {"text": "", "ocr_used": False, "error": str(e)}

    cs1 = str(payload.get("cs1") or "")
    if cs1 not in ("pdf", "pdf-ocr"):
        # OCR не настроен (OCR_VISION_MODEL_PATH / ocr-service) или текста нет.
        err = (
            warning
            or str(payload.get("content") or "")
            or "OCR не настроен или в PDF не обнаружен текст"
        )
        logger.warning(
            "internal_rag_ocr_pdf %s: текст не извлечён (cs1=%s): %s",
            filename,
            cs1 or "(нет)",
            str(err)[:300],
        )
        return {"text": "", "ocr_used": False, "error": err}

    content = str(payload.get("content") or "")
    # Убираем заголовок, который добавляет documents.py:
    # "[Текст из PDF «имя»]:\n..." — в индекс нужно чистое тело.
    marker = "]:\n"
    if content.startswith("[Текст из PDF") and marker in content:
        content = content[content.find(marker) + len(marker) :]
    text = content.strip()
    ocr_used = cs1 == "pdf-ocr"
    logger.info(
        "internal_rag_ocr_pdf %s: извлечено %s символов, ocr_used=%s",
        filename,
        len(text),
        ocr_used,
    )
    result = {"text": text, "ocr_used": ocr_used}
    pages_map = payload.get("pages_map")
    if isinstance(pages_map, dict) and pages_map:
        result["pages"] = pages_map
    if not text and not (isinstance(pages_map, dict) and pages_map):
        result["error"] = "в PDF не обнаружен текст (ни слоя, ни OCR)"
    return result


class InternalRagOcrImageItem(BaseModel):
    name: str = "image.png"
    image_base64: str


class InternalRagOcrImageRequest(BaseModel):
    images: List[InternalRagOcrImageItem] = []


@router.post("/api/internal/rag/ocr-image")
async def internal_rag_ocr_image(body: InternalRagOcrImageRequest):
    """OCR списка изображений для SVC-RAG (картинки из docx/xlsx/pptx/гибридного PDF).

    Тот же vision-LLM-механизм, что и вложения в чате: recognize_text_from_image
    по OCR_VISION_MODEL_PATH, параллельно до 4 (OCR_MAX_CONCURRENCY).

    Запрос: {"images": [{"name": str, "image_base64": str}]}
    Ответ:  {"results": [{"name": str, "text": str}], "error": str?}
    Картинки, по которым текста нет/не распознать, в results отсутствуют.
    """
    import base64

    items: List[tuple] = []
    for it in body.images or []:
        try:
            data = base64.b64decode(it.image_base64 or "", validate=True)
        except Exception as e:
            logger.warning(
                "internal_rag_ocr_image: некорректный base64 %s: %s", it.name, e
            )
            continue
        if data:
            items.append(((it.name or "image.png").strip(), data))
    if not items:
        return {"results": []}

    try:
        from backend.agent_llm_svc import get_llm_service

        svc = await get_llm_service()
    except Exception as e:
        logger.warning("internal_rag_ocr_image: LLM-сервис недоступен: %s", e)
        return {"results": [], "error": str(e)}

    if not svc.client.ocr_available:
        return {
            "results": [],
            "error": "OCR не настроен (OCR_VISION_MODEL_PATH / ocr-service)",
        }

    logger.info("internal_rag_ocr_image: OCR %s изображений для SVC-RAG", len(items))
    sem = asyncio.Semaphore(int(os.getenv("OCR_MAX_CONCURRENCY", "4")))

    async def _ocr_one(name: str, data: bytes) -> tuple:
        async with sem:
            try:
                res = await svc.client.recognize_text_from_image(data, name)
                return name, (res.get("text") or "").strip()
            except Exception as exc:
                logger.debug("internal_rag_ocr_image %s: %s", name, exc)
                return name, ""

    results = await asyncio.gather(*[_ocr_one(n, d) for n, d in items])
    out = [{"name": n, "text": t} for n, t in results if t]
    logger.info(
        "internal_rag_ocr_image: распознан текст с %s из %s изображений",
        len(out),
        len(items),
    )
    return {"results": out}
