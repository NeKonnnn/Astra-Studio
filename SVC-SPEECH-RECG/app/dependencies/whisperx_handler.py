import os
import logging
import torch
from typing import Optional, Dict, Any, Union
from app.core.config import settings

logger = logging.getLogger(__name__)

# Глобальные переменные для хранения моделей WhisperX
whisperx_models: Dict[str, Any] = {}


async def get_whisperx_handler() -> Dict[str, Any]:
    """Получение экземпляров моделей WhisperX"""
    global whisperx_models
    
    if not settings.whisperx.enabled:
        logger.info("WhisperX отключен в конфигурации")
        return {}
    
    if not whisperx_models:
        try:
            print(f"[WhisperX] Загрузка моделей из {settings.whisperx.models_dir}", flush=True)
            
            # Проверяем доступность WhisperX
            try:
                import whisperx
                print(f"[WhisperX] Библиотека whisperx импортирована OK", flush=True)
            except ImportError as ie:
                print(f"[WhisperX] ОШИБКА: whisperx не установлен: {ie}", flush=True)
                return {}
            
            # Создаем директорию для моделей если не существует
            os.makedirs(settings.whisperx.models_dir, exist_ok=True)
            
            # Определяем устройство
            device = settings.whisperx.device
            if device == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            
            print(f"[WhisperX] Устройство: {device}, CUDA доступна: {torch.cuda.is_available()}", flush=True)
            
            # Определяем compute_type в зависимости от устройства
            compute_type = settings.whisperx.compute_type
            if compute_type == "float16" and device == "cpu":
                print("[WhisperX] float16 не поддерживается на CPU, переключаемся на int8", flush=True)
                compute_type = "int8"
            elif compute_type == "auto":
                compute_type = "float16" if device == "cuda" else "int8"
            
            print(f"[WhisperX] compute_type: {compute_type}", flush=True)
            
            # Проверяем содержимое директории
            if os.path.exists(settings.whisperx.models_dir):
                dir_contents = os.listdir(settings.whisperx.models_dir)
                print(f"[WhisperX] Директория моделей ({len(dir_contents)} элементов): {dir_contents[:10]}", flush=True)
            else:
                print(f"[WhisperX] Директория НЕ СУЩЕСТВУЕТ: {settings.whisperx.models_dir}", flush=True)
            
            # Проверяем права записи
            test_file = os.path.join(settings.whisperx.models_dir, ".write_test")
            try:
                with open(test_file, "w") as f:
                    f.write("test")
                os.remove(test_file)
                print("[WhisperX] Директория доступна для записи ✓", flush=True)
            except Exception as we:
                print(f"[WhisperX] ОШИБКА: директория НЕ доступна для записи: {we}", flush=True)
            
            langs = [l for l in settings.whisperx.supported_languages if l != "auto"]
            print(f"[WhisperX] Языки для загрузки: {langs}", flush=True)
            
            # Загружаем только русскую модель для экономии памяти
            for lang in langs:
                try:
                    print(f"[WhisperX] >>> Загрузка модели для '{lang}'...", flush=True)
                    print(f"[WhisperX] Параметры: device={device}, compute_type={compute_type}, download_root={settings.whisperx.models_dir}", flush=True)
                    
                    model_size = getattr(settings.whisperx, "model_size", None) or "medium"
                    model = whisperx.load_model(
                        model_size,
                        device=device,
                        compute_type=compute_type,
                        language=lang,
                        download_root=settings.whisperx.models_dir
                    )
                    
                    whisperx_models[lang] = {
                        "model": model,
                        "device": device,
                        "compute_type": compute_type
                    }
                    
                    print(f"[WhisperX] ✅ Модель '{lang}' загружена успешно!", flush=True)
                    
                except Exception as e:
                    print(f"[WhisperX] ❌ Ошибка загрузки '{lang}': {e}", flush=True)
                    import traceback
                    traceback.print_exc()
                    continue
            
            print(f"[WhisperX] Итого загружено моделей: {len(whisperx_models)}", flush=True)
            
        except Exception as e:
            print(f"[WhisperX] ❌ КРИТИЧЕСКАЯ ОШИБКА: {e}", flush=True)
            import traceback
            traceback.print_exc()
    
    return whisperx_models


async def reload_whisperx_handler(model_size: Optional[str] = None) -> Dict[str, Any]:
    """Принудительная перезагрузка моделей WhisperX (опционально сменить size)."""
    global whisperx_models

    if model_size:
        size = str(model_size).strip()
        if size:
            settings.whisperx.model_size = size
            logger.info("WhisperX model_size → %s", size)

    logger.info("Принудительная перезагрузка моделей WhisperX")

    # Очищаем существующие модели
    if whisperx_models:
        logger.info("Освобождение ресурсов существующих моделей WhisperX")
        for lang, model_info in whisperx_models.items():
            if "model" in model_info:
                try:
                    del model_info["model"]
                except Exception as e:
                    logger.warning(f"Ошибка при удалении модели {lang}: {e}")
        whisperx_models.clear()

        # Очищаем кэш CUDA если используется
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    # Загружаем модели заново
    return await get_whisperx_handler()


async def cleanup_whisperx_handler():
    """Очистка ресурсов моделей WhisperX"""
    global whisperx_models
    
    if whisperx_models:
        logger.info("Освобождение ресурсов моделей WhisperX")
        for lang, model_info in whisperx_models.items():
            if "model" in model_info:
                del model_info["model"]
        whisperx_models.clear()
        
        # Очищаем кэш CUDA если используется
        if torch.cuda.is_available():
            torch.cuda.empty_cache()