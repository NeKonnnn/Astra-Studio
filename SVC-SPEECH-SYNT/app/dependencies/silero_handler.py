import os
import logging
from typing import Optional, Dict
import torch
from app.core.config import settings

logger = logging.getLogger(__name__)

# Глобальная переменная для хранения моделей Silero TTS
silero_models: Optional[Dict[str, any]] = None


async def get_silero_handler() -> Optional[Dict[str, any]]:
    """Получение экземпляров моделей Silero TTS"""
    global silero_models
    
    logger.info(f"[DEBUG] get_silero_handler вызван. silero_models={silero_models}, enabled={settings.silero.enabled}")
    
    if not settings.silero.enabled:
        logger.warning("Silero TTS отключен в конфигурации")
        return None
    
    if silero_models is None:
        logger.info("[DEBUG] silero_models is None, начинаем загрузку...")
        try:
            models_dir = settings.silero.models_dir
            logger.info(f"Загрузка моделей Silero TTS из {models_dir}")
            
            # Проверяем существование директории с моделями
            if not os.path.exists(models_dir):
                logger.warning(f"Директория с моделями Silero TTS не найдена в {models_dir}")
                return None
            
            # Инициализируем словарь для моделей
            silero_models = {}
            
            # Загружаем модели для русского и английского языков
            try:
                # Загрузка модели для русского языка
                ru_model_path = os.path.join(models_dir, "ru", "model.pt")
                logger.info(f"Проверка наличия модели Silero TTS (ru) по пути: {ru_model_path}")
                logger.info(f"Файл существует: {os.path.exists(ru_model_path)}")
                
                if os.path.exists(ru_model_path):
                    logger.info(f"Загрузка модели Silero TTS (ru) из {ru_model_path}...")
                    silero_models['ru'] = torch.package.PackageImporter(ru_model_path).load_pickle("tts_models", "model")
                    logger.info("Модель Silero TTS (ru) успешно загружена")
                else:
                    logger.warning(f"Модель Silero TTS (ru) не найдена в {ru_model_path}")
                
                # Загрузка модели для английского языка
                en_model_path = os.path.join(models_dir, "en", "model.pt")
                logger.info(f"Проверка наличия модели Silero TTS (en) по пути: {en_model_path}")
                logger.info(f"Файл существует: {os.path.exists(en_model_path)}")
                
                if os.path.exists(en_model_path):
                    logger.info(f"Загрузка модели Silero TTS (en) из {en_model_path}...")
                    silero_models['en'] = torch.package.PackageImporter(en_model_path).load_pickle("tts_models", "model")
                    logger.info("Модель Silero TTS (en) успешно загружена")
                else:
                    logger.warning(f"Модель Silero TTS (en) не найдена в {en_model_path}")
                    
            except Exception as e:
                logger.error(f"Ошибка загрузки моделей Silero TTS: {str(e)}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                return None
            
            # Проверяем что хотя бы одна модель загружена
            if not silero_models or len(silero_models) == 0:
                logger.error("Ни одна модель Silero TTS не была загружена!")
                silero_models = None  # Сбрасываем, чтобы следующий вызов попытался загрузить снова
                return None
            
            logger.info(f"Модели Silero TTS успешно загружены: {list(silero_models.keys())}")
            
        except Exception as e:
            logger.error(f"Ошибка инициализации моделей Silero TTS: {str(e)}")
            return None
    
    return silero_models


async def cleanup_silero_handler():
    """Очистка ресурсов моделей Silero TTS"""
    global silero_models
    
    if silero_models is not None:
        logger.info("Освобождение ресурсов моделей Silero TTS")
        silero_models = None


async def reload_silero_handler():
    """Сброс и повторная загрузка Silero TTS."""
    global silero_models
    await cleanup_silero_handler()
    return await get_silero_handler()