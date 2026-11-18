import os
import tempfile
import wave
import json
import subprocess
import numpy as np
from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
from app.dependencies.diarization_handler import get_diarization_handler, reload_diarization_handler
# УДАЛЕНО: импорт whisperx_handler
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def convert_audio_to_wav(input_file_path: str, output_file_path: str) -> bool:
    """Конвертирует аудио/видео файл в WAV формат для диаризации (ОРИГИНАЛ)"""
    try:
        video_extensions = ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v')
        is_video = input_file_path.lower().endswith(video_extensions)
        
        command = [
            "ffmpeg", 
            "-y", 
            "-i", input_file_path,
        ]
        
        if is_video:
            command.append("-vn")
        
        command.extend([
            "-ar", "16000",
            "-ac", "1",
            "-f", "wav",
            output_file_path
        ])
        
        logger.info(f"Выполняем команду: {' '.join(command)}")
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if not os.path.exists(output_file_path):
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка при конвертации аудио: {str(e)}")
        return False


@router.post("/diarize")
async def diarize_audio(
    file: UploadFile = File(...),
    min_speakers: int = Form(1),
    max_speakers: int = Form(10),
    min_duration: float = Form(1.0)
):
    """
    Диаризация аудио файла (разделение по спикерам) - ОРИГИНАЛЬНАЯ ЛОГИКА
    """
    try:
        # Отладка: гарантированный вывод в консоль (логи роутера могут не показываться)
        import sys
        print("[diarize] enabled=%s" % getattr(settings.diarization, "enabled", "?"), flush=True)
        sys.stdout.flush()
        if not settings.diarization.enabled:
            print("[diarize] 503: Диаризация отключена в настройках", flush=True)
            logger.warning("503: Диаризация отключена в настройках (diarization.enabled=%s)", settings.diarization.enabled)
            raise HTTPException(status_code=503, detail="Диаризация отключена")
        
        if file.size and file.size > settings.diarization.max_file_size:
            raise HTTPException(
                status_code=413,
                detail=f"Файл слишком большой. Максимальный размер: {settings.diarization.max_file_size} байт"
            )
        
        pipeline = await get_diarization_handler()
        print("[diarize] pipeline is None: %s" % (pipeline is None), flush=True)
        if pipeline is None:
            print("[diarize] 503: Пайплайн не загружен в этом процессе", flush=True)
            logger.warning("503: get_diarization_handler() вернул None — пайплайн не загружен в этом процессе")
            raise HTTPException(status_code=503, detail="Пайплайн диаризации не загружен")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as temp_file:
            temp_file_path = temp_file.name
            content = await file.read()
            temp_file.write(content)
        
        try:
            wav_file_path = temp_file_path
            if not temp_file_path.lower().endswith('.wav'):
                wav_file_path = temp_file_path + ".wav"
                if not convert_audio_to_wav(temp_file_path, wav_file_path):
                    raise HTTPException(status_code=400, detail="Не удалось конвертировать аудио файл")
            
            logger.info(f"Начинаем диаризацию файла: {file.filename}")
            diarization = pipeline(
                wav_file_path,
                min_speakers=min_speakers,
                max_speakers=max_speakers
            )
            
            speakers = []
            speaker_segments = {}
            
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                start_time = turn.start
                end_time = turn.end
                duration = end_time - start_time
                
                if duration < min_duration:
                    continue
                
                # pyannote уже отдаёт SPEAKER_00 — не префиксуем повторно
                # (иначе получается SPEAKER_SPEAKER_00)
                label = str(speaker or "").strip()
                if label.upper().startswith("SPEAKER_"):
                    speaker_id = label
                else:
                    speaker_id = f"SPEAKER_{label}" if label else "SPEAKER_00"
                
                segment = {
                    "start": round(start_time, 2),
                    "end": round(end_time, 2),
                    "duration": round(duration, 2),
                    "speaker": speaker_id
                }
                
                speakers.append(segment)
                
                if speaker_id not in speaker_segments:
                    speaker_segments[speaker_id] = []
                speaker_segments[speaker_id].append(segment)
            
            unique_speakers = len(speaker_segments)
            total_duration = sum(seg["duration"] for seg in speakers)
            speakers.sort(key=lambda x: x["start"])
            
            return JSONResponse(content={
                "success": True,
                "speakers_count": unique_speakers,
                "segments_count": len(speakers),
                "total_duration": round(total_duration, 2),
                "segments": speakers,
                "speaker_segments": speaker_segments,
                "parameters": {
                    "min_speakers": min_speakers,
                    "max_speakers": max_speakers,
                    "min_duration": min_duration
                }
            })
                
        finally:
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
        logger.error(f"Ошибка при диаризации: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ошибка при диаризации: {str(e)}")


@router.get("/diarization/health")
async def diarization_health_check():
    """Проверка состояния сервиса диаризации (ОРИГИНАЛ)"""
    try:
        if not settings.diarization.enabled:
            return JSONResponse(content={
                "status": "disabled",
                "service": "diarization",
                "enabled": False
            })
        
        pipeline = await get_diarization_handler()
        return JSONResponse(content={
            "status": "healthy" if pipeline else "unhealthy",
            "service": "diarization",
            "enabled": True,
            "pipeline_loaded": pipeline is not None
        })
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "service": "diarization",
                "error": str(e)
            }
        )

@router.post("/diarization/reload")
async def reload_diarization_models():
    """Перезагрузка пайплайна диаризации с диска."""
    try:
        if not settings.diarization.enabled:
            raise HTTPException(status_code=503, detail="Диаризация отключена")
        pipeline = await reload_diarization_handler()
        if pipeline is None:
            raise HTTPException(status_code=503, detail="Не удалось загрузить пайплайн диаризации")
        return JSONResponse(content={
            "success": True,
            "message": "Диаризация перезагружена",
            "pipeline_loaded": True,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Diarization reload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# УДАЛЕНО: transcribe_with_diarization (так как требует whisperx)