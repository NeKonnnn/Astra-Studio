import io
import re
import html
import numpy as np
import torch
import scipy.io.wavfile
from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import StreamingResponse, JSONResponse
from app.dependencies.silero_handler import get_silero_handler, reload_silero_handler
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def _resample_speech_rate(audio: torch.Tensor, speech_rate: float) -> torch.Tensor:
    """Меняет темп речи без torch.interp (его нет в части сборок PyTorch)."""
    if abs(speech_rate - 1.0) <= 1e-6:
        return audio
    if len(audio.shape) > 1:
        audio = audio.squeeze()
    original_length = int(audio.numel())
    new_length = int(original_length / speech_rate)
    if new_length <= 0 or original_length <= 0:
        return audio
    # numpy.interp доступен всегда; возвращаем tensor того же device/dtype
    x_old = np.arange(original_length, dtype=np.float64)
    x_new = np.linspace(0, original_length - 1, new_length, dtype=np.float64)
    y = audio.detach().float().cpu().numpy()
    y_new = np.interp(x_new, x_old, y).astype(np.float32)
    return torch.from_numpy(y_new).to(device=audio.device, dtype=audio.dtype)


def detect_language(text: str) -> str:
    """Простое определение языка текста"""
    # Подсчитываем кириллические символы
    cyrillic_count = sum(1 for char in text if 'а' <= char.lower() <= 'я' or char.lower() in 'ёіїєґ')
    
    # Если более 50% символов кириллические, считаем текст русским
    if cyrillic_count / max(1, len(text)) > 0.5:
        return 'ru'
    else:
        return 'en'


def split_text_into_chunks(text: str, max_chunk_size: int = 1000) -> list:
    """Делит текст на части, длина каждой не превышает max_chunk_size символов"""
    # Разбиваем текст на предложения
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        # Если добавление очередного предложения не превысит лимит,
        # то добавляем его к текущему фрагменту
        if len(current_chunk) + len(sentence) + 1 <= max_chunk_size:
            current_chunk += sentence + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence + " "
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks


def preprocess_text(text: str, language: str = "ru") -> str:
    """Минимальная предобработка текста — Silero сам обрабатывает пунктуацию"""
    # Декодируем HTML entities (&#34; → ", &amp; → &, &lt; → <, и т.д.)
    text = html.unescape(text)
    
    # Удаляем emoji и прочие нетекстовые символы Unicode
    # Оставляем только: буквы, цифры, пунктуацию, пробелы
    import unicodedata
    cleaned = []
    for ch in text:
        cat = unicodedata.category(ch)
        # So = Symbol Other (emoji), Sk = Symbol modifier, Sc = Symbol currency (оставляем)
        if cat.startswith('So') or cat.startswith('Sk') or cat == 'Cn':
            continue
        cleaned.append(ch)
    text = ''.join(cleaned)
    
    # Для английской модели убираем кириллицу, для русской — латиницу оставляем (Silero ru её терпит)
    if language == "en":
        text = re.sub(r'[а-яА-ЯёЁіІїЇєЄґҐ]+', '', text)
    
    # Убираем символы, которые могут вызвать ошибки
    text = text.replace('"', '').replace('«', '').replace('»', '')
    text = text.replace('(', ', ').replace(')', ', ')
    text = text.replace('*', '').replace('#', '').replace('`', '')
    text = text.replace('&', ' и ' if language == 'ru' else ' and ')
    text = text.replace('<', '').replace('>', '')
    text = text.replace('{', '').replace('}', '')
    text = text.replace('[', '').replace(']', '')
    text = text.replace('\\', '')
    text = text.replace('|', '')
    text = text.replace('~', '')
    text = text.replace('^', '')
    text = text.replace('_', ' ')
    # Убираем множественные пробелы
    text = ' '.join(text.split())
    return text.strip()


@router.post("/synthesize")
async def synthesize_speech(
    text: str = Form(...),
    language: str = Form("auto"),
    speaker: str = Form("baya"),
    sample_rate: int = Form(48000),
    speech_rate: float = Form(1.0)
):
    """
    Синтез речи из текста
    
    - **text**: Текст для синтеза речи
    - **language**: Язык (ru, en, auto)
    - **speaker**: Голос (baya, kseniya, xenia, eugene, aidar для ru; v3_en для en)
    - **sample_rate**: Частота дискретизации (48000, 24000, 16000)
    - **speech_rate**: Скорость речи (0.5-2.0)
    """
    try:
        # Проверяем, включен ли Silero
        if not settings.silero.enabled:
            raise HTTPException(status_code=503, detail="Silero TTS отключен")
        
        # Валидация входных данных
        if not text or len(text.strip()) == 0:
            raise HTTPException(status_code=400, detail="Текст не может быть пустым")
        
        if len(text) > settings.silero.max_text_length:
            raise HTTPException(
                status_code=400, 
                detail=f"Текст слишком длинный. Максимальная длина: {settings.silero.max_text_length} символов"
            )
        
        if speech_rate < 0.5 or speech_rate > 2.0:
            raise HTTPException(status_code=400, detail="Скорость речи должна быть от 0.5 до 2.0")
        
        if sample_rate not in [16000, 24000, 48000]:
            raise HTTPException(status_code=400, detail="Частота дискретизации должна быть 16000, 24000 или 48000")
        
        # Определяем язык
        if language == "auto":
            language = detect_language(text)
        
        if language not in settings.silero.supported_languages:
            raise HTTPException(
                status_code=400, 
                detail=f"Неподдерживаемый язык: {language}. Поддерживаемые: {settings.silero.supported_languages}"
            )
        
        # Проверяем голос
        if speaker not in settings.silero.supported_speakers.get(language, []):
            default_speaker = settings.silero.supported_speakers[language][0]
            logger.warning(f"Неподдерживаемый голос {speaker} для языка {language}, используем {default_speaker}")
            speaker = default_speaker
        
        # Получаем модели
        models = await get_silero_handler()
        
        if models is None or language not in models:
            raise HTTPException(status_code=503, detail=f"Модель для языка {language} не загружена")
        
        model = models[language]
        
        # Предобработка текста
        processed_text = preprocess_text(text, language=language)
        
        if not processed_text or len(processed_text.strip()) == 0:
            raise HTTPException(status_code=400, detail="Текст пуст после предобработки (удалены emoji/спецсимволы)")
        
        # Разбиваем текст на части если он длинный
        chunks = split_text_into_chunks(processed_text)
        all_audio = []
        
        logger.info(f"Синтез речи: язык={language}, голос={speaker}, частей={len(chunks)}")
        
        for i, chunk in enumerate(chunks):
            if i > 0:
                # Добавляем небольшую паузу между частями
                silence = torch.zeros(int(0.3 * sample_rate), dtype=torch.float32)
                all_audio.append(silence)
            
            try:
                # Синтезируем аудио для части текста
                audio = model.apply_tts(
                    text=chunk,
                    speaker=speaker,
                    sample_rate=sample_rate,
                    put_accent=False,  # Убираем акценты для стабильности
                    put_yo=False       # Убираем ё для стабильности
                )
                
                # Изменяем скорость речи если нужно
                if abs(speech_rate - 1.0) > 1e-6:
                    audio = _resample_speech_rate(audio, speech_rate)

                all_audio.append(audio)

            except Exception as chunk_error:
                logger.error(f"Ошибка синтеза части {i+1}: {chunk_error}")
                # Пытаемся с упрощенным текстом
                try:
                    simplified_chunk = chunk.replace(',', '').replace('.', '').replace('!', '').replace('?', '')
                    if simplified_chunk.strip():
                        audio = model.apply_tts(
                            text=simplified_chunk,
                            speaker=speaker,
                            sample_rate=sample_rate,
                            put_accent=False,
                            put_yo=False
                        )

                        if abs(speech_rate - 1.0) > 1e-6:
                            audio = _resample_speech_rate(audio, speech_rate)

                        all_audio.append(audio)
                except Exception as fallback_error:
                    logger.error(f"Fallback синтез тоже не удался для части {i+1}: {fallback_error}")
                    continue
        
        if not all_audio:
            raise HTTPException(status_code=500, detail="Не удалось синтезировать ни одной части текста")
        
        # Объединяем все части аудио
        combined_audio = torch.cat(all_audio, dim=0)
        audio_numpy = combined_audio.cpu().numpy()
        
        # Нормализуем аудио
        if audio_numpy.max() <= 1.0:
            audio_numpy = (audio_numpy * 32767).astype('int16')
        
        # Создаем WAV файл в памяти
        wav_buffer = io.BytesIO()
        scipy.io.wavfile.write(wav_buffer, sample_rate, audio_numpy)
        wav_buffer.seek(0)
        
        # Возвращаем аудио как поток
        return StreamingResponse(
            io.BytesIO(wav_buffer.getvalue()),
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=synthesized_speech.wav",
                "X-Audio-Duration": str(len(audio_numpy) / sample_rate),
                "X-Audio-Sample-Rate": str(sample_rate)
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при синтезе речи: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ошибка при синтезе речи: {str(e)}")


@router.get("/tts/health")
async def tts_health_check():
    """Проверка состояния сервиса TTS"""
    try:
        if not settings.silero.enabled:
            return JSONResponse(content={
                "status": "disabled",
                "service": "silero-tts",
                "enabled": False
            })
        
        models = await get_silero_handler()
        return JSONResponse(content={
            "status": "healthy",
            "service": "silero-tts",
            "enabled": True,
            "models_loaded": list(models.keys()),
            "total_models": len(models)
        })
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "service": "silero-tts",
                "error": str(e)
            }
        )


@router.get("/tts/info")
async def get_tts_info():
    """Получение информации о сервисе TTS"""
    return JSONResponse(content={
        "service": "silero-tts",
        "enabled": settings.silero.enabled,
        "supported_languages": settings.silero.supported_languages,
        "supported_speakers": settings.silero.supported_speakers,
        "sample_rates": [16000, 24000, 48000],
        "max_text_length": settings.silero.max_text_length,
        "speech_rate_range": [0.5, 2.0]
    })


@router.get("/tts/voices")
async def get_available_voices():
    """Получение списка доступных голосов"""
    return JSONResponse(content={
        "voices": settings.silero.supported_speakers,
        "default_voices": {
            lang: voices[0] for lang, voices in settings.silero.supported_speakers.items()
        }
    })


@router.post("/tts/reload")
async def reload_tts_models():
    """Перезагрузка весов Silero TTS с диска."""
    try:
        if not settings.silero.enabled:
            raise HTTPException(status_code=503, detail="Silero TTS отключен")
        models = await reload_silero_handler()
        if not models:
            raise HTTPException(status_code=503, detail="Не удалось загрузить Silero TTS")
        return JSONResponse(content={
            "success": True,
            "message": "Silero TTS перезагружен",
            "models_loaded": list(models.keys()),
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error("TTS reload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))