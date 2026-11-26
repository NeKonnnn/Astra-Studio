"""Модуль для работы с хранилищем файлов (MinIO)"""

from .minio_client import MinIOClient, get_minio_client, reset_minio_client

__all__ = ["MinIOClient", "get_minio_client", "reset_minio_client"]





