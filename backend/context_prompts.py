"""
Модуль для управления контекстными промптами моделей
Позволяет сохранять, загружать и применять контекстные промпты для каждой модели
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.settings.logging import get_logger
from backend.utils.safe_paths import (
    context_prompts_file_fallbacks,
    context_prompts_seed_paths,
    resolve_context_prompts_file_path,
    resolve_context_prompts_file_path_primary,
    sanitize_log_value,
)

logger = get_logger(__name__)


class ContextPromptManager:
    """Менеджер контекстных промптов для моделей"""

    def __init__(self):
        self.prompts_file = str(resolve_context_prompts_file_path_primary())
        self._prompts_path: Optional[Path] = resolve_context_prompts_file_path(
            self.prompts_file
        )
        self.context_prompts = self.load_context_prompts()

    def _validated_prompts_path(self) -> Optional[Path]:
        if self._prompts_path is not None:
            return self._prompts_path
        self._prompts_path = resolve_context_prompts_file_path(self.prompts_file)
        return self._prompts_path

    @staticmethod
    def _default_prompts() -> Dict[str, Any]:
        return {"global_prompt": "", "model_prompts": {}, "custom_prompts": {}}

    def _prompts_load_candidates(self) -> list[str]:
        candidates: list[str] = []
        for path in [
            self.prompts_file,
            *[str(p) for p in context_prompts_seed_paths()],
        ]:
            abs_path = os.path.abspath(path)
            if abs_path not in candidates:
                candidates.append(abs_path)
        return candidates

    def load_context_prompts(self) -> Dict[str, Any]:
        """Загрузка контекстных промптов: сначала writable-файл, иначе seed из образа."""
        loaded = False
        data = self._default_prompts()
        for path in self._prompts_load_candidates():
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded_data = json.load(f)
                if isinstance(loaded_data, dict):
                    data = loaded_data
                    loaded = True
                if os.path.abspath(path) == os.path.abspath(self.prompts_file):
                    break
            except Exception:
                logger.exception(
                    "Ошибка при загрузке контекстных промптов из %s",
                    sanitize_log_value(path),
                )
        if loaded:
            logger.info(
                "Контекстные промпты загружены (файл: %s)",
                sanitize_log_value(self.prompts_file),
            )
            return data

        if self.save_context_prompts(data):
            logger.info(
                "Создан файл контекстных промптов: %s",
                sanitize_log_value(self.prompts_file),
            )
        else:
            logger.warning(
                "Файл контекстных промптов не найден, запись недоступна — используются значения по умолчанию в памяти"
            )
        return data

    def save_context_prompts(self, prompts: Optional[Dict[str, Any]] = None) -> bool:
        """Сохранение контекстных промптов в writable-путь с fallback (/tmp/astrachat)."""
        if prompts is not None:
            self.context_prompts = prompts
        elif not hasattr(self, "context_prompts"):
            self.context_prompts = self._default_prompts()

        last_error: Exception | None = None
        for target in context_prompts_file_fallbacks(self.prompts_file):
            target_str = str(target)
            safe = resolve_context_prompts_file_path(target_str)
            if safe is None:
                continue
            try:
                safe.parent.mkdir(parents=True, exist_ok=True)
                with open(safe, "w", encoding="utf-8") as f:
                    json.dump(self.context_prompts, f, indent=2, ensure_ascii=False)
                if target_str != self.prompts_file:
                    logger.warning(
                        "context_prompts.json: переключение на fallback путь %s (primary=%s)",
                        target_str,
                        self.prompts_file,
                    )
                    self.prompts_file = target_str
                    self._prompts_path = safe
                logger.info(
                    "Контекстные промпты сохранены в %s",
                    sanitize_log_value(self.prompts_file),
                )
                return True
            except OSError as exc:
                last_error = exc
                logger.warning(
                    "Не удалось записать context_prompts в %s: %s", target_str, exc
                )
            except Exception as exc:
                last_error = exc
                logger.exception(
                    "Ошибка при сохранении контекстных промптов в %s", target_str
                )
        if last_error is not None:
            logger.error(
                "Не удалось сохранить context_prompts ни в один путь: %s", last_error
            )
        return False

    def get_global_prompt(self) -> str:
        """Получение глобального промпта"""
        return self.context_prompts.get("global_prompt", "")

    def set_global_prompt(self, prompt: str) -> bool:
        """Установка глобального промпта"""
        self.context_prompts["global_prompt"] = prompt
        return self.save_context_prompts()

    def get_model_prompt(self, model_path: str) -> str:
        """Получение промпта для конкретной модели"""
        model_prompts = self.context_prompts.get("model_prompts", {})
        return model_prompts.get(model_path, self.get_global_prompt())

    def set_model_prompt(self, model_path: str, prompt: str) -> bool:
        """Установка промпта для конкретной модели"""
        if "model_prompts" not in self.context_prompts:
            self.context_prompts["model_prompts"] = {}
        if prompt.strip():
            self.context_prompts["model_prompts"][model_path] = prompt
        elif model_path in self.context_prompts["model_prompts"]:
            del self.context_prompts["model_prompts"][model_path]
        return self.save_context_prompts()

    def get_custom_prompt(self, prompt_id: str) -> Optional[str]:
        """Получение пользовательского промпта по ID"""
        custom_prompts = self.context_prompts.get("custom_prompts", {})
        return custom_prompts.get(prompt_id)

    def set_custom_prompt(
        self, prompt_id: str, prompt: str, description: str = ""
    ) -> bool:
        """Создание/обновление пользовательского промпта"""
        if "custom_prompts" not in self.context_prompts:
            self.context_prompts["custom_prompts"] = {}
        self.context_prompts["custom_prompts"][prompt_id] = {
            "prompt": prompt,
            "description": description,
            "created_at": self._get_current_timestamp(),
        }
        return self.save_context_prompts()

    def delete_custom_prompt(self, prompt_id: str) -> bool:
        """Удаление пользовательского промпта"""
        if (
            "custom_prompts" in self.context_prompts
            and prompt_id in self.context_prompts["custom_prompts"]
        ):
            del self.context_prompts["custom_prompts"][prompt_id]
            return self.save_context_prompts()
        return False

    def get_all_custom_prompts(self) -> Dict[str, Dict[str, Any]]:
        """Получение всех пользовательских промптов"""
        return self.context_prompts.get("custom_prompts", {})

    def get_effective_prompt(
        self, model_path: str, custom_prompt_id: Optional[str] = None
    ) -> str:
        """Получение эффективного промпта для модели с учетом приоритетов"""
        if custom_prompt_id:
            custom_prompt = self.get_custom_prompt(custom_prompt_id)
            if custom_prompt:
                return custom_prompt.get("prompt", self.get_global_prompt())
        model_prompt = self.get_model_prompt(model_path)
        if model_prompt != self.get_global_prompt():
            return model_prompt
        return self.get_global_prompt()

    def enrich_models_with_prompts(
        self, models: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Добавляет context_prompt и has_custom_prompt к списку моделей из LLM registry."""
        enriched: List[Dict[str, Any]] = []
        model_prompts = self.context_prompts.get("model_prompts", {})
        for model in models:
            path = str(model.get("path") or "").strip()
            row = dict(model)
            row["context_prompt"] = (
                self.get_model_prompt(path) if path else self.get_global_prompt()
            )
            row["has_custom_prompt"] = bool(path and path in model_prompts)
            enriched.append(row)
        return enriched

    def resolve_chat_system_prompt(
        self,
        model_path: Optional[str],
        *,
        agent_system_prompt: Optional[str] = None,
        project_instructions: Optional[str] = None,
    ) -> Optional[str]:
        """
        Собирает system prompt для чата.

        Промпт сущности важнее глобальной настройки модели: если у агента или у
        проекта задан свой текст, контекстные инструкции (global_prompt /
        model_prompts) не подставляются вовсе. Иначе два набора правил уезжали бы
        в модель одновременно, и при их противоречии выигрывал бы глобальный —
        он идёт первым (см. context_breakdown: active).
        """
        parts: List[str] = []
        agent_sp = (agent_system_prompt or "").strip()
        proj = (project_instructions or "").strip()
        if not agent_sp and not proj:
            ctx = (
                self.get_effective_prompt(model_path)
                if model_path
                else self.get_global_prompt()
            )
            ctx = (ctx or "").strip()
            if ctx:
                parts.append(ctx)
        if proj:
            parts.append(proj)
        if agent_sp:
            parts.append(agent_sp)
        return "\n\n".join(parts) if parts else None

    def _get_current_timestamp(self) -> str:
        """Получение текущей временной метки"""
        from datetime import datetime

        return datetime.now().isoformat()

    def export_prompts(self, file_path: str) -> bool:
        """Экспорт всех промптов в файл"""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self.context_prompts, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            logger.exception("Ошибка при экспорте промптов")
            return False

    def import_prompts(self, file_path: str) -> bool:
        """Импорт промптов из файла"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                imported_prompts = json.load(f)
            if not isinstance(imported_prompts, dict):
                return False
            self.context_prompts.update(imported_prompts)
            return self.save_context_prompts()
        except Exception:
            logger.exception("Ошибка при импорте промптов")
            return False


context_prompt_manager = ContextPromptManager()
