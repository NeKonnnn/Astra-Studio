import os
from typing import Callable, Optional, Tuple

from backend.llm_client import (
    transcribe_audio_whisperx_llm_svc,
    transcribe_with_diarization_llm_svc,
)
from backend.settings.logging import get_logger

logger = get_logger(__name__)

USE_LLM_SVC = os.getenv("USE_LLM_SVC", "false").lower() == "true"
if not USE_LLM_SVC:
    try:
        from backend.transcription.whisperx_transcriber import WhisperXTranscriber

        LOCAL_TRANSCRIBERS_AVAILABLE = True
    except ImportError as e:
        logger.warning("Локальный WhisperX недоступен: %s", e)
        LOCAL_TRANSCRIBERS_AVAILABLE = False
        WhisperXTranscriber = None
else:
    LOCAL_TRANSCRIBERS_AVAILABLE = False
    WhisperXTranscriber = None
    logger.debug("UniversalTranscriber: Режим микросервисов активен")


def _normalize_engine(name: str) -> str:
    n = (name or "whisperx").lower()
    if n == "vosk":
        return "whisperx"
    return n if n == "whisperx" else "whisperx"


class UniversalTranscriber:
    """Транскрайбер: единственный движок — WhisperX (локально или через STT-сервис)."""

    def __init__(self, engine: str = "whisperx", hf_token: Optional[str] = None):
        self.logger = get_logger(f"{__name__}.UniversalTranscriber")
        self.logger.debug("=== Инициализация UniversalTranscriber ===")
        self.engine = _normalize_engine(engine)
        self.whisperx_transcriber = None
        self.current_transcriber = None
        self.hf_token = hf_token
        try:
            self._initialize_engine()
            self.logger.info(
                f"UniversalTranscriber инициализирован (движок: {self.engine})"
            )
        except Exception:
            self.logger.exception("Критическая ошибка инициализации")
            raise

    def _initialize_engine(self):
        if USE_LLM_SVC:
            self.logger.debug("[SVC] Работаем через микросервисы (WhisperX)")
            self.current_transcriber = "llm-svc"
            return
        if not LOCAL_TRANSCRIBERS_AVAILABLE:
            msg = "Локальные модели отсутствуют. Проверьте папки или включите USE_LLM_SVC=true"
            raise RuntimeError(msg)
        self.whisperx_transcriber = WhisperXTranscriber()
        self.current_transcriber = self.whisperx_transcriber

    def switch_engine(self, engine: str) -> bool:
        """Сохраняем API переключения; фактически поддерживается только whisperx."""
        target = _normalize_engine(engine)
        if target != self.engine:
            self.logger.info(
                f"Переключение движка: {self.engine} -> {target} (активен WhisperX)"
            )
        self.engine = target
        if (
            not USE_LLM_SVC
            and self.whisperx_transcriber is None
            and LOCAL_TRANSCRIBERS_AVAILABLE
        ):
            try:
                self.whisperx_transcriber = WhisperXTranscriber()
                self.current_transcriber = self.whisperx_transcriber
            except Exception:
                self.logger.exception("Ошибка переключения")
                return False
        return True

    def get_current_engine(self) -> str:
        return self.engine

    def get_available_engines(self) -> list:
        return ["whisperx"]

    def set_progress_callback(self, callback: Optional[Callable[[int], None]]):
        if USE_LLM_SVC:
            return
        if self.current_transcriber and hasattr(
            self.current_transcriber, "set_progress_callback"
        ):
            self.current_transcriber.set_progress_callback(callback)

    def set_model_size(self, size: str):
        if self.whisperx_transcriber:
            self.whisperx_transcriber.set_model_size(size)

    def set_language(self, lang: str):
        if self.current_transcriber and hasattr(
            self.current_transcriber, "set_language"
        ):
            self.current_transcriber.set_language(lang)

    def set_compute_type(self, compute_type: str):
        if self.whisperx_transcriber:
            self.whisperx_transcriber.set_compute_type(compute_type)

    def transcribe_audio_file(self, audio_path: str) -> Tuple[bool, str]:
        self.logger.debug(f"Начало транскрибации: {audio_path}")
        if USE_LLM_SVC:
            try:
                self.logger.debug("[SVC] Запрос к STT-сервису (WhisperX)")
                with open(audio_path, "rb") as f:
                    audio_data = f.read()
                text = transcribe_audio_whisperx_llm_svc(
                    audio_data, filename=os.path.basename(audio_path), language="auto"
                )
                if text:
                    return (True, text)
                return (False, "Сервис вернул пустой текст")
            except Exception as e:
                self.logger.exception("[SVC] Ошибка")
                return (False, f"Ошибка микросервиса: {str(e)}")
        if self.whisperx_transcriber:
            try:
                return self.whisperx_transcriber.transcribe_audio_file(audio_path)
            except Exception as e:
                self.logger.exception("Ошибка WhisperX")
                return (False, str(e))
        return (False, "Транскрайбер не инициализирован")

    def transcribe_youtube(self, url: str) -> Tuple[bool, str]:
        self.logger.debug(f"Транскрибация YouTube: {url}")
        if USE_LLM_SVC:
            self.logger.debug("[SVC] YouTube: загружаем аудио локально...")
            try:
                from backend.transcription.whisperx_transcriber import (
                    WhisperXTranscriber as Loader,
                )

                loader = Loader()
                audio_path = loader._download_youtube_audio(url)
                if audio_path:
                    res = self.transcribe_audio_file(audio_path)
                    loader.cleanup()
                    return res
                return (False, "Не удалось скачать аудио с YouTube")
            except Exception as e:
                logger.exception("Ошибка операции")
                # Текст исключения (адреса, пути) - только в лог (CWE-209)
                return (False, "Не удалось выполнить транскрибацию YouTube")
        if self.current_transcriber and hasattr(
            self.current_transcriber, "transcribe_youtube"
        ):
            return self.current_transcriber.transcribe_youtube(url)
        return (False, "YouTube транскрибация не поддерживается текущим движком")

    def transcribe_with_diarization(self, audio_path: str) -> Tuple[bool, str]:
        self.logger.debug(f"Запрос диаризации: {audio_path}")
        if USE_LLM_SVC:
            try:
                with open(audio_path, "rb") as f:
                    audio_data = f.read()
                result = transcribe_with_diarization_llm_svc(
                    audio_data,
                    filename=os.path.basename(audio_path),
                    language="auto",
                    engine=self.engine,
                )
                if result.get("success"):
                    segments = sorted(
                        result.get("segments", []), key=lambda s: s.get("start", 0)
                    )
                    if segments:
                        lines = []
                        for seg in segments:
                            speaker = seg.get("speaker", "SPEAKER_?")
                            seg_text = (seg.get("text") or "").strip()
                            if not seg_text:
                                continue
                            if lines and lines[-1][0] == speaker:
                                lines[-1] = (speaker, lines[-1][1] + " " + seg_text)
                            else:
                                lines.append((speaker, seg_text))
                        if lines:
                            return (
                                True,
                                "\n".join((f"{spk}: {txt}" for spk, txt in lines)),
                            )
                    return (False, "Сервис вернул пустые сегменты")
                return (False, result.get("error", "Unknown error"))
            except Exception as e:
                logger.exception("Ошибка операции")
                return (False, str(e))
        if self.whisperx_transcriber:
            return self.whisperx_transcriber.transcribe_audio_file(audio_path)
        return (False, "Диаризация доступна только через WhisperX или микросервис")

    def get_engine_info(self) -> dict:
        return {
            "current_engine": self.engine,
            "available_engines": self.get_available_engines(),
            "features": {"diarization": True, "gpu_support": True},
        }

    def set_hf_token(self, token: str):
        self.hf_token = token
        if self.whisperx_transcriber:
            self.whisperx_transcriber.set_hf_token(token)

    def get_hf_token(self) -> Optional[str]:
        return self.hf_token

    def cleanup(self):
        if self.whisperx_transcriber:
            self.whisperx_transcriber.cleanup()

    def __del__(self):
        self.cleanup()
