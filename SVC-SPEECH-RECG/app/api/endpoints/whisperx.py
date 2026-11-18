import os
import tempfile
import wave
import json
import subprocess
import numpy as np
from typing import Any, Dict, Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Body
from fastapi.responses import JSONResponse
from app.dependencies.whisperx_handler import get_whisperx_handler, reload_whisperx_handler
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def convert_audio_to_wav(input_file_path: str, output_file_path: str) -> bool:
    """Конвертирует аудио/видео файл в WAV формат для WhisperX"""
    try:
        # Определяем, является ли файл видео
        video_extensions = ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v')
        is_video = input_file_path.lower().endswith(video_extensions)
        
        # Используем ffmpeg для конвертации
        command = [
            "ffmpeg", 
            "-y",  # Перезаписывать существующие файлы
            "-i", input_file_path,  # Входной файл
        ]
        
        # Если это видео, добавляем флаг для извлечения только аудио
        if is_video:
            command.append("-vn")  # Без видео (только аудио)
        
        # Добавляем параметры аудио
        command.extend([
            "-ar", "16000",  # Частота дискретизации 16 кГц
            "-ac", "1",      # Моно
            "-f", "wav",     # Формат WAV
            output_file_path  # Выходной файл
        ])
        
        logger.info(f"Выполняем команду: {' '.join(command)}")
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Проверяем, создан ли файл
        if not os.path.exists(output_file_path):
            stderr = result.stderr.decode('utf-8', errors='ignore')
            logger.error(f"Не удалось конвертировать аудио. FFmpeg ошибка: {stderr}")
            return False
        
        return True
        
    except FileNotFoundError:
        logger.error("FFmpeg не найден. Пожалуйста, установите FFmpeg и добавьте его в PATH.")
        return False
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode('utf-8', errors='ignore')
        logger.error(f"Ошибка FFmpeg: {stderr}")
        return False
    except Exception as e:
        logger.error(f"Ошибка при конвертации аудио: {str(e)}")
        return False


@router.post("/transcribe")
async def transcribe_audio_whisperx(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    compute_type: str = Form("float16"),
    batch_size: int = Form(16),
    initial_prompt: str = Form(""),
):
    """
    Транскрибация аудио файла с помощью WhisperX
    
    - **file**: Аудио файл для транскрибации
    - **language**: Язык для распознавания (ru, en, auto)
    - **compute_type**: Тип вычислений (float16, int8, int8_float16)
    - **batch_size**: Размер батча для обработки
    - **initial_prompt**: подсказка словаря/терминов для Whisper (jargon)
    """
    try:
        # Проверяем, включен ли WhisperX
        if not settings.whisperx.enabled:
            raise HTTPException(status_code=503, detail="WhisperX транскрипция отключена")
        
        # Проверяем размер файла
        if file.size and file.size > settings.whisperx.max_file_size:
            raise HTTPException(
                status_code=413,
                detail=f"Файл слишком большой. Максимальный размер: {settings.whisperx.max_file_size} байт"
            )
        
        # Получаем модели WhisperX
        models = await get_whisperx_handler()
        if not models:
            raise HTTPException(status_code=503, detail="Модели WhisperX не загружены")
        
        # Определяем язык
        if language == "auto":
            language = "ru"  # По умолчанию русский
        
        if language not in models:
            raise HTTPException(
                status_code=400, 
                detail=f"Неподдерживаемый язык: {language}. Поддерживаемые: {list(models.keys())}"
            )
        
        model_info = models[language]
        
        # Создаем временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as temp_file:
            temp_file_path = temp_file.name
            
            # Сохраняем загруженный файл
            content = await file.read()
            temp_file.write(content)
        
        try:
            # Конвертируем в WAV если нужно
            wav_file_path = temp_file_path
            if not temp_file_path.lower().endswith('.wav'):
                wav_file_path = temp_file_path + ".wav"
                if not convert_audio_to_wav(temp_file_path, wav_file_path):
                    raise HTTPException(status_code=400, detail="Не удалось конвертировать аудио файл")
            
            # Импортируем whisperx
            import whisperx
            
            # Загружаем аудио
            audio = whisperx.load_audio(wav_file_path)
            
            # Транскрибируем.
            # Вариант 1: faster-whisper (tuple: (segments, info)).
            # Вариант 2: whisperx pipeline (dict c ключом "segments" и без "text").
            prompt = (initial_prompt or "").strip()[:800]
            transcribe_kwargs = {
                "batch_size": batch_size,
                "language": language,
            }
            if prompt:
                # faster-whisper / whisperx: initial_prompt влияет на словарь и имена
                transcribe_kwargs["initial_prompt"] = prompt
            try:
                raw = model_info["model"].transcribe(audio, **transcribe_kwargs)
            except TypeError:
                # Старые обёртки без initial_prompt
                transcribe_kwargs.pop("initial_prompt", None)
                raw = model_info["model"].transcribe(audio, **transcribe_kwargs)

            text = ""
            detected_lang = language
            segments_list = []

            if isinstance(raw, tuple):
                segments_iter, info = raw
                segments_list = list(segments_iter)  # потребляем генератор
                text = " ".join((getattr(s, "text", None) or "") for s in segments_list).strip()
                detected_lang = getattr(info, "language", None) or language
            elif isinstance(raw, dict):
                # Новый формат whisperx: {"segments": [...], "word_segments": [...], "language": "..."}
                segments_list = raw.get("segments") or []
                detected_lang = raw.get("language", language)
                # Если есть готовый text - используем, иначе собираем из сегментов
                text = (raw.get("text") or "").strip()
                if not text and segments_list:
                    text = " ".join(
                        (seg.get("text") or "").strip()
                        for seg in segments_list
                        if isinstance(seg, dict)
                    ).strip()

            # Сегменты для ответа (единый формат)
            segments = []
            for seg in segments_list:
                if hasattr(seg, "text"):
                    segments.append(
                        {
                            "start": getattr(seg, "start", 0),
                            "end": getattr(seg, "end", 0),
                            "text": (seg.text or "").strip(),
                        }
                    )
                elif isinstance(seg, dict):
                    segments.append(
                        {
                            "start": seg.get("start", 0),
                            "end": seg.get("end", 0),
                            "text": (seg.get("text") or "").strip(),
                        }
                    )

            # Если совсем нет текста и сегментов — считаем это ошибкой распознавания
            if not text and not segments:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error": "Не удалось распознать текст в аудио",
                    },
                )

            return JSONResponse(content={
                "success": True,
                "text": text,
                "language": detected_lang,
                "segments": segments,
                "duration": len(audio) / 16000,
                "words_count": len(text.split()) if text else 0
            })
                
        finally:
            # Очищаем временные файлы
            try:
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                if os.path.exists(wav_file_path) and wav_file_path != temp_file_path:
                    os.unlink(wav_file_path)
            except Exception as e:
                logger.warning(f"Не удалось удалить временные файлы: {e}")
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при транскрибации WhisperX: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при транскрибации: {str(e)}")


@router.get("/whisperx/health")
async def whisperx_health_check():
    """Проверка состояния сервиса WhisperX"""
    try:
        if not settings.whisperx.enabled:
            return JSONResponse(content={
                "status": "disabled",
                "service": "whisperx-transcription",
                "enabled": False
            })
        
        models = await get_whisperx_handler()
        return JSONResponse(content={
            "status": "healthy" if models else "unhealthy",
            "service": "whisperx-transcription",
            "enabled": True,
            "models_loaded": list(models.keys()),
            "total_models": len(models)
        })
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "service": "whisperx-transcription",
                "error": str(e)
            }
        )


@router.get("/whisperx/info")
async def get_whisperx_info():
    """Получение информации о сервисе WhisperX"""
    return JSONResponse(content={
        "service": "whisperx-transcription",
        "enabled": settings.whisperx.enabled,
        "supported_languages": settings.whisperx.supported_languages,
        "device": settings.whisperx.device,
        "compute_types": ["float16", "int8", "int8_float16"],
        "max_file_size": settings.whisperx.max_file_size,
        "batch_size": settings.whisperx.batch_size
    })


@router.post("/whisperx/reload")
async def reload_whisperx_models(payload: Optional[Dict[str, Any]] = Body(default=None)):
    """Принудительная перезагрузка моделей WhisperX (body: {model_size?})."""
    try:
        if not settings.whisperx.enabled:
            raise HTTPException(status_code=503, detail="WhisperX отключен в конфигурации")

        model_size = None
        if isinstance(payload, dict):
            model_size = payload.get("model_size")

        logger.info("Запрос на перезагрузку моделей WhisperX (size=%s)", model_size)
        models = await reload_whisperx_handler(model_size=model_size)

        if not models:
            raise HTTPException(
                status_code=503,
                detail="Не удалось загрузить модели WhisperX. Проверьте логи для деталей."
            )

        return JSONResponse(content={
            "success": True,
            "message": "Модели WhisperX успешно перезагружены",
            "model_size": getattr(settings.whisperx, "model_size", None),
            "loaded_models": list(models.keys()),
            "total_models": len(models)
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при перезагрузке моделей WhisperX: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при перезагрузке моделей WhisperX: {str(e)}"
        )














