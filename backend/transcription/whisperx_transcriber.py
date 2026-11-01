import gc
import os
import shutil
import subprocess
import tempfile
import time
import warnings
from typing import Callable, Dict, Optional, Tuple

import pytubefix

from backend.llm_client import transcribe_with_diarization_llm_svc
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress

try:
    from moviepy.editor import VideoFileClip

    MOVIEPY_AVAILABLE = True
except ImportError:
    VideoFileClip = None
    MOVIEPY_AVAILABLE = False

try:
    import sounddevice as sd

    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    sd = None
    SOUNDDEVICE_AVAILABLE = False

WHISPERX_MODELS_DIR = "whisperx_models"
DIARIZE_MODELS_DIR = "diarize_models"
WHISPERX_BASE_MODEL = "medium"
DIARIZE_MODEL = "pyannote/speaker-diarization-3.1"
LOCAL_MODELS_AVAILABLE = False

logger = get_logger(__name__)


def _ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        msg = "ffmpeg не найден в PATH"
        raise RuntimeError(msg)
    return found


USE_LLM_SVC = os.getenv("USE_LLM_SVC", "false").lower() == "true"
if not USE_LLM_SVC:
    try:
        import whisperx

        WHISPERX_AVAILABLE = True
    except ImportError:
        whisperx = None
        WHISPERX_AVAILABLE = False
        logger.warning(
            "WhisperX не доступен локально, требуется запуск через микросервисы"
        )
else:
    whisperx = None
    WHISPERX_AVAILABLE = False
    logger.debug("Используется микросервис для WhisperX транскрипции")

warnings.filterwarnings("ignore", category=UserWarning, module="pytorch_lightning")
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
warnings.filterwarnings("ignore", category=UserWarning, module="torch")


def _torch():
    """Ленивый импорт: при USE_LLM_SVC=true torch в backend не нужен."""
    import torch as _torch_mod

    return _torch_mod


if not USE_LLM_SVC:
    try:
        _t = _torch()
        if _t.cuda.is_available():
            _t.backends.cuda.matmul.allow_tf32 = True
            _t.backends.cudnn.allow_tf32 = True
    except Exception:
        logger.exception("Предупреждение при настройке TF32")


class WhisperXTranscriber:

    def __init__(self):
        self.logger = get_logger(f"{__name__}.WhisperXTranscriber")
        self.logger.debug("=== Инициализация WhisperXTranscriber ===")
        if USE_LLM_SVC:
            self.logger.debug(
                "[SVC] Режим микросервисов: локальные модели не загружаются"
            )
            self.model = None
            self.diarize_model = None
            self.device = "cpu"
            self.language = "ru"
            return
        if not WHISPERX_AVAILABLE:
            msg = "WhisperX не установлен локально. Установите USE_LLM_SVC=true"
            raise ImportError(msg)
        self.project_dir = os.path.abspath(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self.temp_dir = tempfile.mkdtemp()
        self.language = "ru"
        self.whisper_model_path = os.path.join(self.project_dir, WHISPERX_MODELS_DIR)
        self.diarize_model_path = os.path.join(self.project_dir, DIARIZE_MODELS_DIR)
        self.sample_rate = 16000
        self.use_ffmpeg = self._check_ffmpeg_availability()
        self.progress_callback = None
        self.model_size = WHISPERX_BASE_MODEL
        _t = _torch()
        if _t.cuda.is_available():
            self.device = "cuda"
            self.compute_type = "float16"
        else:
            self.device = "cpu"
            self.compute_type = "float32"
        self._cached_diarize_model = None
        self.logger.info(
            f"Локальный режим: device={self.device}, compute={self.compute_type}"
        )

    def _load_local_diarization_pipeline(self):
        """Загружает локальный пайплайн диаризации"""
        try:
            import yaml
            from pyannote.audio import Pipeline

            config_path = os.path.join(
                self.diarize_model_path, "pyannote_diarization_config.yaml"
            )
            if not os.path.exists(config_path):
                self.logger.error(f"Конфиг диаризации не найден: {config_path}")
                return None
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f)
            if "pipeline" in config_data and "params" in config_data["pipeline"]:
                params = config_data["pipeline"]["params"]
                if "embedding" in params and isinstance(params["embedding"], str):
                    if not params["embedding"].startswith("/") and (
                        "://" not in params["embedding"]
                    ):
                        params["embedding"] = os.path.abspath(
                            os.path.join(self.diarize_model_path, params["embedding"])
                        )
                if "segmentation" in params and isinstance(params["segmentation"], str):
                    if not params["segmentation"].startswith("/") and (
                        "://" not in params["segmentation"]
                    ):
                        params["segmentation"] = os.path.abspath(
                            os.path.join(
                                self.diarize_model_path, params["segmentation"]
                            )
                        )
            temp_config_path = os.path.join(
                tempfile.gettempdir(), "pyannote_diarization_temp.yaml"
            )
            with open(temp_config_path, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f)
            pipeline = Pipeline.from_pretrained(temp_config_path)
            with logged_suppress(logger):
                os.remove(temp_config_path)
            return pipeline
        except Exception:
            self.logger.exception("Ошибка загрузки локального пайплайна")
            return None

    def _check_ffmpeg_availability(self) -> bool:
        """Проверка FFmpeg"""
        try:
            result = subprocess.run(
                [_ffmpeg_bin(), "-version"], capture_output=True, text=True
            )  # noqa: S603
            return result.returncode == 0
        except Exception:
            logger.exception("Ошибка операции")
            return False

    def _update_progress(self, progress: int):
        """Обновление прогресса"""
        if self.progress_callback:
            with logged_suppress(logger):
                self.progress_callback(progress)

    def transcribe_audio_file(self, audio_path: str) -> Tuple[bool, str]:
        """Главный метод транскрибации"""
        if not os.path.exists(audio_path):
            return (False, f"Файл не найден: {audio_path}")
        if USE_LLM_SVC:
            self.logger.debug(
                "[SVC] Выполняю транскрибацию и диаризацию через микросервисы..."
            )
            try:
                with open(audio_path, "rb") as f:
                    audio_data = f.read()
                response = transcribe_with_diarization_llm_svc(
                    audio_data,
                    filename=os.path.basename(audio_path),
                    language=self.language,
                    engine="whisperx",
                )
                if response.get("success"):
                    segments = response.get("segments", [])
                    if segments:
                        transcript = self._format_transcript_with_speakers(
                            {"segments": segments}
                        )
                        return (True, transcript)
                    return (False, "Сервис вернул пустые сегменты")
                else:
                    return (False, f"Ошибка сервиса: {response.get('error')}")
            except Exception as e:
                self.logger.exception("[SVC] Ошибка связи с микросервисами")
                return (False, f"Ошибка микросервиса: {str(e)}")
        try:
            self._update_progress(10)
            model = whisperx.load_model(
                self.model_size,
                self.device,
                compute_type=self.compute_type,
                language=self.language,
                download_root=self.whisper_model_path,
            )
            self._update_progress(50)
            try:
                result = model.transcribe(audio_path)
            except Exception as transcribe_error:
                self.logger.exception("Ошибка транскрипции, пробуем CPU fallback")
                if "cudnn" in str(transcribe_error).lower():
                    model = whisperx.load_model(
                        self.model_size,
                        "cpu",
                        compute_type="float32",
                        language=self.language,
                        download_root=self.whisper_model_path,
                    )
                    result = model.transcribe(audio_path)
                else:
                    raise
            self._update_progress(70)
            if hasattr(self, "_cached_diarize_model") and self._cached_diarize_model:
                diarize_model = self._cached_diarize_model
            else:
                diarize_model = self._load_local_diarization_pipeline()
                self._cached_diarize_model = diarize_model
            if not diarize_model:
                return (False, "Не удалось загрузить модель диаризации")
            self._update_progress(80)
            if audio_path.lower().endswith((".mp4", ".avi", ".mov", ".mkv", ".webm")):
                temp_audio = os.path.join(
                    tempfile.gettempdir(),
                    f"diarize_tmp_{os.path.basename(audio_path)}.wav",
                )
                subprocess.run(  # noqa: S603
                    [
                        _ffmpeg_bin(),
                        "-y",
                        "-i",
                        audio_path,
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        "-f",
                        "wav",
                        temp_audio,
                    ],
                    capture_output=True,
                )
                diarize_segments = diarize_model(temp_audio)
                with logged_suppress(logger):
                    os.remove(temp_audio)
            else:
                diarize_segments = diarize_model(audio_path)
            self._update_progress(90)
            try:
                result = whisperx.assign_word_speakers(diarize_segments, result)
                if not any(("speaker" in s for s in result["segments"])):
                    result = (
                        self._manual_assign_speakers(diarize_segments, result)
                        or result
                    )
            except Exception:
                logger.exception("Ошибка операции")
                # None (неизвестный формат диаризации) не должен затирать результат
                result = (
                    self._manual_assign_speakers(diarize_segments, result) or result
                )
            transcript = self._format_transcript_with_speakers(result)
            self._update_progress(100)
            del model
            gc.collect()
            if self.device == "cuda":
                _torch().cuda.empty_cache()
            return (True, transcript)
        except Exception as e:
            self.logger.exception("Локальная ошибка")
            return (False, str(e))

    def transcribe_youtube(self, url: str) -> Tuple[bool, str]:
        """Транскрибирует аудио с YouTube"""
        try:
            self.logger.debug("Начинаю транскрипцию YouTube: %s", url)
            audio_path = self._download_youtube_audio(url)
            if not audio_path:
                return (False, "Не удалось загрузить аудио с YouTube")
            self.logger.debug("Аудио загружено: %s", audio_path)
            return self.transcribe_audio_file(audio_path)
        except Exception as e:
            self.logger.exception("Ошибка транскрипции YouTube: %s")
            # Текст исключения (адреса, пути) - только в лог (CWE-209)
            return (False, "Не удалось выполнить транскрибацию YouTube")

    def _format_transcript_with_speakers(self, result: Dict) -> str:
        """Форматирует транскрипт с информацией о спикерах.
        Формат: SPEAKER_XX: текст (без временных меток).
        Смежные сегменты одного спикера объединяются в один блок.
        """
        try:
            segments = result.get("segments", [])
            if not segments:
                return self._format_simple_transcript(result)
            lines = []
            for segment in segments:
                speaker = segment.get("speaker", None)
                text = segment.get("text", "").strip()
                if not text:
                    continue
                if speaker is None:
                    speaker = "SPEAKER_?"
                if lines and lines[-1][0] == speaker:
                    lines[-1] = (speaker, lines[-1][1] + " " + text)
                else:
                    lines.append((speaker, text))
            if not lines:
                return self._format_simple_transcript(result)
            return "\n".join(f"{spk}: {txt}" for spk, txt in lines)
        except Exception:
            self.logger.exception("Ошибка форматирования с диаризацией")
            return self._format_simple_transcript(result)

    def _format_time(self, seconds):
        """Форматирует время в секундах"""
        try:
            if seconds is None:
                return "00:00"
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes:02d}:{secs:02d}"
        except Exception:
            self.logger.exception("Ошибка форматирования времени")
            return "00:00"

    def _format_time_simple(self, seconds):
        """Форматирует время в простом виде"""
        try:
            if seconds is None:
                return "00:00"
            total_minutes = int(seconds // 60)
            hours = total_minutes // 60
            minutes = total_minutes % 60
            if hours > 0:
                return f"{hours:02d}:{minutes:02d}"
            else:
                return f"{minutes:02d}:{int(seconds % 60):02d}"
        except Exception:
            logger.exception("Ошибка операции")
            return "00:00"

    def _format_simple_transcript(self, result: Dict) -> str:
        """Форматирует простую транскрипт без диаризации"""
        try:
            segments = result.get("segments", [])
            return " ".join(
                [s.get("text", "").strip() for s in segments if s.get("text")]
            )
        except Exception:
            logger.exception("Ошибка операции")
            return str(result)

    def _download_youtube_audio(self, url: str) -> Optional[str]:
        """Загружает аудио с YouTube"""
        max_retries = 3
        retry_count = 0
        while retry_count < max_retries:
            try:
                self.logger.debug(
                    "Загрузка аудио с YouTube: %s (попытка %s/%s)",
                    url,
                    retry_count + 1,
                    max_retries,
                )
                temp_dir = tempfile.mkdtemp()
                try:
                    yt = pytubefix.YouTube(url)
                    audio_stream = yt.streams.filter(only_audio=True).first()
                except Exception as yt_error:
                    if "SSL" in str(yt_error) or "EOF" in str(yt_error):
                        self.logger.warning("SSL/сетевая ошибка: %s", yt_error)
                        if retry_count < max_retries - 1:
                            time.sleep(2)
                            retry_count += 1
                            continue
                        return None
                    raise
                if not audio_stream:
                    return None
                audio_stream.download(output_path=temp_dir, filename="youtube_audio")
                downloaded_file = os.path.join(temp_dir, "youtube_audio")
                if not os.path.exists(downloaded_file):
                    return None
                audio_path = os.path.join(temp_dir, "youtube_audio.wav")
                result = subprocess.run(  # noqa: S603
                    [
                        _ffmpeg_bin(),
                        "-y",
                        "-i",
                        downloaded_file,
                        "-acodec",
                        "pcm_s16le",
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        audio_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    audio_path = downloaded_file
                return audio_path
            except Exception:
                self.logger.exception(
                    "Ошибка загрузки YouTube аудио (попытка %s)",
                    retry_count + 1,
                )
                if retry_count < max_retries - 1:
                    time.sleep(3)
                    retry_count += 1
                else:
                    self.logger.exception(
                        "Ошибка загрузки YouTube аудио после %s попыток", max_retries
                    )
                    return None
        return None

    def set_progress_callback(self, callback: Callable[[int], None]):
        """Устанавливает callback для обновления прогресса"""
        self.progress_callback = callback

    def set_language(self, language: str):
        """Устанавливает язык транскрибации"""
        self.language = language
        self.logger.info(f"Язык транскрибации изменен на: {language}")

    def cleanup(self):
        """Очищает временные файлы"""
        try:
            if hasattr(self, "temp_dir") and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
                self.logger.debug("Временная директория очищена: %s", self.temp_dir)
        except Exception:
            self.logger.exception("Ошибка при очистке")

    def __del__(self):
        """Деструктор для очистки ресурсов"""
        self.cleanup()

    def _manual_assign_speakers(self, diarize_segments, whisper_result):
        """
        Альтернативный способ объединения диаризации с транскрипцией  .
        Адаптирован для работы как с локальными объектами, так и с JSON из сервисов.
        """
        try:
            self.logger.debug("Используем ручное объединение диаризации...")
            segments = whisper_result.get("segments", [])
            if not segments:
                return None
            diarize_timeline = []
            if hasattr(diarize_segments, "get_timeline"):
                diarize_timeline = diarize_segments.get_timeline()
            elif isinstance(diarize_segments, list):
                diarize_timeline = diarize_segments
            else:
                self.logger.warning("Неизвестный формат данных диаризации")
                return None
            result_with_speakers = whisper_result.copy()
            result_with_speakers["segments"] = []
            for _i, segment in enumerate(segments):
                segment_start = segment.get("start", 0)
                segment_end = segment.get("end", 0)
                segment_text = segment.get("text", "").strip()
                if not segment_text:
                    continue
                speaker = self._find_speaker_for_time(
                    diarize_timeline, segment_start, segment_end
                )
                new_segment = segment.copy()
                new_segment["speaker"] = speaker
                result_with_speakers["segments"].append(new_segment)
            return result_with_speakers
        except Exception:
            self.logger.exception("Ошибка ручного объединения: %s")
            return None

    def _refine_segments_for_diarization(self, segments, diarize_timeline):
        """Оставляет сегменты как есть"""
        return segments

    def _find_speaker_for_time(self, diarize_timeline, start_time, end_time):
        """Находит спикера для заданного временного интервала"""
        try:
            best_match = None
            best_overlap = 0
            for segment in diarize_timeline:
                try:
                    if hasattr(segment, "start"):
                        d_start, d_end = (segment.start, segment.end)
                    elif isinstance(segment, dict):
                        d_start, d_end = (
                            segment.get("start", 0),
                            segment.get("end", 0),
                        )
                    else:
                        continue
                    overlap_start = max(d_start, start_time)
                    overlap_end = min(d_end, end_time)
                    overlap_duration = max(0, overlap_end - overlap_start)
                    if overlap_duration > best_overlap:
                        best_overlap = overlap_duration
                        best_match = segment
                except Exception:
                    logger.exception("Ошибка операции")
                    continue
            if best_match and best_overlap > 0:
                speaker_id = None
                if isinstance(best_match, dict):
                    speaker_id = best_match.get("speaker")
                elif hasattr(best_match, "track"):
                    speaker_id = str(best_match.track)
                elif hasattr(best_match, "label"):
                    speaker_id = str(best_match.label)
                if speaker_id:
                    return speaker_id
            return "SPEAKER_?"
        except Exception:
            logger.exception("Ошибка операции")
            return "SPEAKER_?"

    def _normalize_speaker_name(self, speaker_id):
        """Нормализует имя спикера к читаемому формату"""
        try:
            if not hasattr(self, "_speaker_mapping"):
                self._speaker_mapping = {}
                self._speaker_counter = 0
            if speaker_id not in self._speaker_mapping:
                letters = ["A", "B", "C", "D", "E", "F", "G", "H"]
                if self._speaker_counter < len(letters):
                    simple_name = f"Speaker_{letters[self._speaker_counter]}"
                else:
                    simple_name = f"Speaker_{self._speaker_counter + 1}"
                self._speaker_mapping[speaker_id] = simple_name
                self._speaker_counter += 1
            return self._speaker_mapping[speaker_id]
        except Exception:
            logger.exception("Ошибка операции")
            return "Speaker_A"

    def _analyze_diarization_speakers(self, diarize_timeline):
        """Анализирует список уникальных спикеров"""
        try:
            unique_speakers = set()
            for segment in diarize_timeline:
                if isinstance(segment, dict):
                    unique_speakers.add(segment.get("speaker"))
                elif hasattr(segment, "track"):
                    unique_speakers.add(f"TRACK_{segment.track}")
            return sorted(list(unique_speakers))
        except Exception:
            logger.exception("Ошибка операции")
            return []
