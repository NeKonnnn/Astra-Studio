"""Загрузка моделей (HF / HTTP) в локальные каталоги платформы."""

from backend.services.model_downloads.job_manager import get_job_manager

__all__ = ["get_job_manager"]
