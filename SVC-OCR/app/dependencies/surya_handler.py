import os
import logging
from typing import List, Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# Глобальная переменная для инициализированного Surya OCR
surya_ocr = None


def _run_ocr_surya(images: List[Any], recognition_predictor, detection_predictor) -> List[Any]:
    """Базовый вызов OCR через Surya: recognition + detection."""
    return recognition_predictor(images, det_predictor=detection_predictor)


async def get_surya_handler():
    """
    Инициализация Surya OCR в «чистом» варианте (как в примерах библиотеки)
    """
    global surya_ocr

    if not settings.surya.enabled:
        logger.info("Surya OCR отключен в конфигурации")
        return None

    if surya_ocr is not None:
        return surya_ocr

    try:
        models_dir = os.path.abspath(settings.surya.models_dir)
        os.makedirs(models_dir, exist_ok=True)

        # Все кэши Surya/datalab в один каталог (том surya_models)
        os.environ["MODEL_CACHE_DIR"] = models_dir
        os.environ["DATALAB_CACHE_DIR"] = models_dir
        os.environ["HF_HOME"] = models_dir
        os.environ["HF_HUB_CACHE"] = models_dir
        os.environ["TRANSFORMERS_CACHE"] = models_dir

        # Локальные чекпоинты для офлайн-режима: относительные пути от models_dir (например text_detection/2025_02_28)
        det_ckpt = settings.surya.detection_checkpoint
        rec_ckpt = settings.surya.recognition_checkpoint
        if det_ckpt:
            # Surya ищет модель в MODEL_CACHE_DIR / checkpoint
            os.environ["DETECTOR_MODEL_CHECKPOINT"] = os.path.join(models_dir, det_ckpt) if not os.path.isabs(det_ckpt) else det_ckpt
        if rec_ckpt:
            rec_path = os.path.join(models_dir, rec_ckpt) if not os.path.isabs(rec_ckpt) else rec_ckpt
            os.environ["RECOGNITION_MODEL_CHECKPOINT"] = rec_path
            os.environ["FOUNDATION_MODEL_CHECKPOINT"] = rec_path
        if settings.surya.offline:
            os.environ["HF_HUB_OFFLINE"] = "1"
            logger.info("Surya OCR: офлайн-режим (HF_HUB_OFFLINE=1)")

        # Импортируем Surya после установки env (настройки читаются при импорте)
        from surya.recognition import RecognitionPredictor
        from surya.detection import DetectionPredictor

        device = settings.surya.device
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"
        logger.info(f"Surya OCR: используем устройство {device}")

        try:
            from surya.foundation import FoundationPredictor

            foundation_predictor = FoundationPredictor()
            recognition_predictor = RecognitionPredictor(foundation_predictor)
        except ImportError:
            # surya-ocr 0.20.x: FoundationPredictor отсутствует
            recognition_predictor = RecognitionPredictor()
        detection_predictor = DetectionPredictor()

        surya_ocr = {
            "run_ocr": _run_ocr_surya,
            "recognition_predictor": recognition_predictor,
            "detection_predictor": detection_predictor,
            "device": device,
        }

        logger.info("Surya OCR успешно инициализирован (чистый v0.17+ API, без патчей)")
        return surya_ocr
    except Exception as e:
        logger.error(f"Ошибка инициализации Surya OCR: {e}")
        import traceback
        traceback.print_exc()
        return None


async def cleanup_surya_handler():
    """Очистка ресурсов Surya OCR."""
    global surya_ocr
    if surya_ocr is not None:
        logger.info("Освобождение ресурсов Surya OCR")
        surya_ocr = None


async def reload_surya_handler():
    """Сброс и повторная инициализация Surya OCR."""
    await cleanup_surya_handler()
    return await get_surya_handler()
