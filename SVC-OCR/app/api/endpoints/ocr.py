import os
import tempfile
import json
from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
from PIL import Image
from io import BytesIO
from app.dependencies.surya_handler import get_surya_handler, reload_surya_handler
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/ocr")
async def recognize_text_from_image(
    file: UploadFile = File(...),
    languages: str = Form("ru,en")  # Список языков через запятую
):
    """
    Распознавание текста с изображения с помощью Surya OCR
    
    - **file**: Изображение для распознавания
    - **languages**: Список языков через запятую (например, "ru,en")
    """
    try:
        # Проверяем, включен ли Surya OCR
        if not settings.surya.enabled:
            raise HTTPException(status_code=503, detail="Surya OCR отключен")
        
        # Проверяем размер файла
        if file.size and file.size > settings.surya.max_file_size:
            raise HTTPException(
                status_code=413,
                detail=f"Файл слишком большой. Максимальный размер: {settings.surya.max_file_size} байт"
            )
        
        # Получаем handler Surya OCR
        surya = await get_surya_handler()
        if surya is None:
            raise HTTPException(
                status_code=503,
                detail="Surya OCR не загружен. Проверьте логи ocr-service при старте (models_dir, surya-ocr, загрузка моделей). Вызовите GET /v1/ocr/health для диагностики."
            )
        
        # Читаем файл
        file_data = await file.read()
        
        # Проверяем, что это изображение
        try:
            image = Image.open(BytesIO(file_data))
            if image.mode != "RGB":
                image = image.convert("RGB")
            # Крупный текст (одно слово на пол-экрана) детектор часто пропускает — масштабируем вниз
            w, h = image.size
            max_side = 2048
            if max(w, h) > max_side:
                scale = max_side / max(w, h)
                new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
                image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
                logger.info(f"Изображение уменьшено для детекции: {w}x{h} -> {new_w}x{new_h}")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Не удалось открыть изображение: {str(e)}")
        
        # Парсим языки
        lang_list = [lang.strip() for lang in languages.split(",") if lang.strip()]
        if not lang_list:
            lang_list = ["ru", "en"]  # По умолчанию
        
        # Проверяем поддерживаемые языки
        valid_languages = [lang for lang in lang_list if lang in settings.surya.supported_languages]
        if not valid_languages:
            valid_languages = ["ru", "en"]  # Fallback
        
        logger.info(f"Распознавание текста с изображения. Языки: {valid_languages}")
        
        # Выполняем OCR (surya-ocr v0.17+: recognition_predictor + detection_predictor)
        try:
            run_ocr = surya["run_ocr"]
            recognition_predictor = surya["recognition_predictor"]
            detection_predictor = surya["detection_predictor"]

            predictions = run_ocr([image], recognition_predictor, detection_predictor)

            if not predictions or len(predictions) == 0:
                return JSONResponse(content={
                    "success": True,
                    "text": "",
                    "languages": valid_languages,
                    "words": [],
                    "words_count": 0,
                    "confidence": 0.0
                })

            # Формат v0.17: список результатов по изображениям; каждый элемент — список страниц или одна запись с text_lines
            pred0 = predictions[0]
            # Может быть список страниц (для PDF) или один объект с text_lines
            if isinstance(pred0, list):
                pages = pred0
            else:
                pages = [pred0]

            text_lines = []
            words_with_confidence = []
            total_confidence = 0.0
            word_count = 0

            for page in pages:
                lines = page.get("text_lines", []) if isinstance(page, dict) else getattr(page, "text_lines", [])
                for text_line in lines:
                    if isinstance(text_line, dict):
                        line_text = text_line.get("text", "")
                        line_conf = text_line.get("confidence", 0.0)
                    else:
                        line_text = getattr(text_line, "text", "")
                        line_conf = getattr(text_line, "confidence", 0.0)
                    if line_text and str(line_text).strip():
                        # Нормализуем HTML-переносы в обычные, чтобы не склеивать слова
                        line_text = str(line_text).replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
                        text_lines.append(line_text)
                        for word in line_text.split():
                            conf = float(line_conf) if line_conf else 0.85
                            words_with_confidence.append({"word": word, "confidence": conf})
                            total_confidence += conf
                            word_count += 1

            full_text = "\n".join(text_lines)
            avg_confidence = (total_confidence / word_count * 100) if word_count > 0 else 0.0

            logger.info(f"OCR успешно выполнен. Извлечено {word_count} слов, средняя уверенность: {avg_confidence:.2f}%")

            return JSONResponse(content={
                "success": True,
                "text": full_text,
                "languages": valid_languages,
                "words": words_with_confidence,
                "words_count": word_count,
                "confidence": round(avg_confidence, 2)
            })
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Ошибка при выполнении OCR: {error_msg}")
            import traceback
            full_traceback = traceback.format_exc()
            logger.error(f"Полный traceback:\n{full_traceback}")
            
            # Возвращаем более информативную ошибку
            error_detail = f"Ошибка при распознавании текста: {error_msg}"
            if "AttributeError" in error_msg or "text_lines" in error_msg:
                error_detail += ". Возможно, формат ответа от Surya OCR изменился."
            elif "CUDA" in error_msg or "device" in error_msg.lower():
                error_detail += ". Проблема с устройством (CPU/GPU)."
            elif "model" in error_msg.lower() or "load" in error_msg.lower():
                error_detail += ". Проблема с загрузкой моделей."
            
            raise HTTPException(status_code=500, detail=error_detail)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при обработке OCR запроса: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ошибка при обработке запроса: {str(e)}")


@router.post("/ocr/reload")
async def reload_ocr_models():
    """Перезагрузка Surya OCR с диска."""
    try:
        if not settings.surya.enabled:
            raise HTTPException(status_code=503, detail="Surya OCR отключен")
        surya = await reload_surya_handler()
        if surya is None:
            raise HTTPException(status_code=503, detail="Не удалось загрузить Surya OCR")
        return JSONResponse(content={
            "success": True,
            "message": "Surya OCR перезагружен",
            "model_loaded": True,
            "device": surya.get("device"),
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error("OCR reload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ocr/health")
async def ocr_health_check():
    """Проверка состояния сервиса OCR"""
    try:
        if not settings.surya.enabled:
            return JSONResponse(content={
                "status": "disabled",
                "service": "surya-ocr",
                "enabled": False
            })
        
        surya = await get_surya_handler()
        if surya is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "service": "surya-ocr",
                    "enabled": True,
                    "model_loaded": False,
                    "models_dir": settings.surya.models_dir,
                    "reason": "Surya model not loaded — check ocr-service startup logs (models_dir, surya-ocr install, model load error)"
                }
            )
        return JSONResponse(content={
            "status": "healthy",
            "service": "surya-ocr",
            "enabled": True,
            "model_loaded": True,
            "models_dir": settings.surya.models_dir,
            "device": surya["device"]
        })
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "service": "surya-ocr",
                "error": str(e)
            }
        )


@router.get("/ocr/info")
async def get_ocr_info():
    """Получение информации о сервисе OCR"""
    return JSONResponse(content={
        "service": "surya-ocr",
        "enabled": settings.surya.enabled,
        "supported_formats": ["jpg", "jpeg", "png", "webp", "bmp", "tiff"],
        "max_file_size": settings.surya.max_file_size,
        "supported_languages": settings.surya.supported_languages,
        "device": settings.surya.device,
        "models_dir": settings.surya.models_dir
    })


@router.get("/ocr/quality-check")
async def ocr_quality_check(run_sample: bool = False):
    """
    Проверка качества работы OCR.
    - Без параметров: статус сервиса и подсказка по ?run_sample=1.
    - С run_sample=1: тест на сгенерированном изображении (метрики text, confidence, words_count).
    """
    try:
        if not settings.surya.enabled:
            return JSONResponse(content={
                "service": "surya-ocr",
                "enabled": False,
                "message": "OCR отключен в конфигурации",
            })

        surya = await get_surya_handler()
        if surya is None:
            return JSONResponse(content={
                "service": "surya-ocr",
                "model_loaded": False,
                "message": "Модель не загружена. Проверьте логи при старте.",
            })

        if not run_sample:
            return JSONResponse(content={
                "service": "surya-ocr",
                "enabled": True,
                "model_loaded": True,
                "quality_check_with_sample": "Добавьте ?run_sample=1 к URL для автоматического теста на сгенерированном изображении."
            })

        # Генерируем тестовое изображение с текстом и прогоняем OCR
        try:
            from PIL import ImageDraw, ImageFont
            img = Image.new("RGB", (400, 120), color=(255, 255, 255))
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
            except Exception:
                font = ImageFont.load_default()
            draw.text((20, 40), "OCR Test 123", fill=(0, 0, 0), font=font)
            draw.text((20, 75), "Проверка качества", fill=(0, 0, 0), font=font)

            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            test_image = Image.open(buf).convert("RGB")

            run_ocr = surya["run_ocr"]
            recognition_predictor = surya["recognition_predictor"]
            detection_predictor = surya["detection_predictor"]
            predictions = run_ocr([test_image], recognition_predictor, detection_predictor)

            if not predictions or len(predictions) == 0:
                return JSONResponse(content={
                    "service": "surya-ocr",
                    "sample_test": True,
                    "result": "no_predictions",
                    "text": "",
                    "confidence": 0.0,
                    "words_count": 0,
                    "quality_ok": False,
                    "message": "OCR не вернул результат для тестового изображения."
                })

            pred0 = predictions[0]
            pages = pred0 if isinstance(pred0, list) else [pred0]
            text_lines = []
            total_conf = 0.0
            word_count = 0
            for page in pages:
                lines = page.get("text_lines", []) if isinstance(page, dict) else getattr(page, "text_lines", [])
                for line in lines:
                    lt = line.get("text", "") if isinstance(line, dict) else getattr(line, "text", "")
                    lc = line.get("confidence", 0.0) if isinstance(line, dict) else getattr(line, "confidence", 0.0)
                    if lt and str(lt).strip():
                        text_lines.append(lt)
                        for _ in str(lt).split():
                            total_conf += float(lc) if lc else 0.85
                            word_count += 1
            full_text = "\n".join(text_lines)
            avg_conf = (total_conf / word_count * 100) if word_count > 0 else 0.0

            return JSONResponse(content={
                "service": "surya-ocr",
                "sample_test": True,
                "text": full_text,
                "confidence": round(avg_conf, 2),
                "words_count": word_count,
                "quality_ok": word_count > 0 and avg_conf >= 50.0,
            })
        except Exception as sample_err:
            logger.exception("OCR quality-check sample failed")
            return JSONResponse(content={
                "service": "surya-ocr",
                "sample_test": True,
                "error": str(sample_err),
                "quality_ok": False,
            })
    except Exception as e:
        logger.exception("OCR quality-check failed")
        return JSONResponse(status_code=500, content={"error": str(e)})