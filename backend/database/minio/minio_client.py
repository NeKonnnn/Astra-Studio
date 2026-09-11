"""
Модуль для работы с MinIO - объектным хранилищем для временных файлов
Используется для хранения файлов голосового взаимодействия и транскрибации
"""

import os
import logging
import tempfile
from typing import Optional, BinaryIO
from datetime import datetime, timedelta
from io import BytesIO
from backend.settings.logging import get_logger

# Импортируем настройки
try:
    from settings import get_settings
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

try:
    from minio import Minio
    from minio.error import S3Error
    MINIO_AVAILABLE = True
except ImportError:
    MINIO_AVAILABLE = False
    logging.warning("MinIO библиотека не установлена. Установите: pip install minio")

logger = get_logger(__name__)


class MinIOClient:
    """Клиент для работы с MinIO"""
    
    def __init__(
        self,
        endpoint: str = None,
        port: int = None,
        access_key: str = None,
        secret_key: str = None,
        secure: bool = None,
        bucket_name: str = None
    ):
        """
        Инициализация MinIO клиента
        
        Args:
            endpoint: Адрес MinIO сервера (например, 'minio:9000' или 'localhost:9000')
            access_key: Access key для MinIO
            secret_key: Secret key для MinIO
            secure: Использовать ли HTTPS
            bucket_name: Имя bucket для хранения файлов
        """
        if not MINIO_AVAILABLE:
            raise ImportError("MinIO библиотека не установлена. Установите: pip install minio")
        
        if not SETTINGS_AVAILABLE:
            raise RuntimeError("Модуль settings недоступен. MinIO не может быть инициализирован.")
        
        # Получаем настройки из settings, если не переданы явно
        settings = get_settings()
        minio_config = settings.minio
        self.endpoint = endpoint or minio_config.endpoint
        self.port = port if port is not None else minio_config.port
        self.access_key = access_key or minio_config.access_key
        self.secret_key = secret_key or minio_config.secret_key
        self.secure = secure if secure is not None else minio_config.use_ssl
        self.bucket_name = bucket_name or minio_config.bucket_name
        
        # Формируем полный endpoint
        if ':' not in self.endpoint:
            self.endpoint = f"{self.endpoint}:{self.port}"
        
        logger.info(f"Инициализация MinIO клиента:")
        logger.info(f"  Endpoint: {self.endpoint}")
        logger.info(f"  Access Key: {self.access_key[:4]}***")
        logger.info(f"  Bucket: {self.bucket_name}")
        logger.info(f"  Secure (HTTPS): {self.secure}")
        
        try:
            # Создаем клиент MinIO
            logger.debug("Создание MinIO клиента...")
            self.client = Minio(
                self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.secure
            )
            logger.debug("MinIO клиент создан, проверяю bucket...")
            
            # Создаем bucket, если его нет
            self._ensure_bucket_exists()
            
            logger.info("MinIO клиент успешно инициализирован и готов к работе")
        except Exception as e:
            logger.error(f"Ошибка инициализации MinIO клиента: {e}")
            logger.error(f"Проверьте:")
            logger.error(f"1. Запущен ли MinIO сервер на {self.endpoint}")
            logger.error(f"2. Правильность учетных данных (access_key/secret_key)")
            logger.error(f"3. Доступность сети до MinIO сервера")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
    
    def _ensure_bucket_exists(self, bucket_name: str = None):
        """Проверяет существование bucket и создает его, если нужно"""
        bucket = bucket_name or self.bucket_name
        try:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)
                logger.info(f"Bucket '{bucket}' создан")
            else:
                logger.debug(f"Bucket '{bucket}' уже существует")
        except S3Error as e:
            logger.error(f"Ошибка при создании bucket: {e}")
            raise
    
    def ensure_bucket(self, bucket_name: str):
        """Проверяет и создает bucket с указанным именем"""
        self._ensure_bucket_exists(bucket_name)
    
    def upload_file(
        self,
        file_data: bytes,
        object_name: str,
        content_type: str = "application/octet-stream",
        bucket_name: str = None,
        *,
        cef_display_name: Optional[str] = None,
        cef_audit: bool = True,
    ) -> str:
        """
        Загружает файл в MinIO

        Args:
            file_data: Данные файла в виде bytes
            object_name: Имя объекта в MinIO (путь к файлу)
            content_type: MIME тип файла
            bucket_name: Имя bucket (если None, используется self.bucket_name)
            cef_display_name: человекочитаемое имя файла для CEF (по умолчанию object_name)
            cef_audit: писать CEF FS003/FS004 на запись

        Returns:
            object_name: Имя загруженного объекта
        """
        bucket = bucket_name or self.bucket_name
        try:
            # Убеждаемся, что bucket существует
            self._ensure_bucket_exists(bucket)

            file_stream = BytesIO(file_data)
            file_size = len(file_data)

            self.client.put_object(
                bucket,
                object_name,
                file_stream,
                file_size,
                content_type=content_type
            )

            logger.debug(f"Файл загружен в MinIO: {bucket}/{object_name} ({file_size} байт)")
            if cef_audit:
                from backend.settings.cef_logger.storage_audit import log_minio_write_success

                log_minio_write_success(object_name, bucket, display_name=cef_display_name)
            return object_name
        except S3Error as e:
            if cef_audit:
                from backend.settings.cef_logger.storage_audit import log_minio_write_failure

                log_minio_write_failure(object_name, bucket, str(e), display_name=cef_display_name)
            logger.error(f"Ошибка загрузки файла в MinIO: {e}")
            raise
    
    def download_file(self, object_name: str, bucket_name: str = None) -> bytes:
        """
        Скачивает файл из MinIO
        
        Args:
            object_name: Имя объекта в MinIO
            bucket_name: Имя bucket (если None, используется self.bucket_name)
            
        Returns:
            bytes: Данные файла
        """
        bucket = bucket_name or self.bucket_name
        try:
            response = self.client.get_object(bucket, object_name)
            data = response.read()
            response.close()
            response.release_conn()
            
            logger.debug(f"Файл скачан из MinIO: {bucket}/{object_name} ({len(data)} байт)")
            return data
        except S3Error as e:
            logger.error(f"Ошибка скачивания файла из MinIO: {e}")
            raise
    
    def get_file_path(self, object_name: str, bucket_name: str = None) -> str:
        """
        Получает локальный путь к файлу, скачивая его из MinIO во временную директорию
        
        Args:
            object_name: Имя объекта в MinIO
            bucket_name: Имя bucket (если None, используется self.bucket_name)
            
        Returns:
            str: Путь к временному файлу
        """
        try:
            # Скачиваем файл
            file_data = self.download_file(object_name, bucket_name)
            
            # Сохраняем во временную директорию
            temp_dir = tempfile.gettempdir()
            filename = os.path.basename(object_name) or f"temp_{datetime.now().timestamp()}"
            temp_path = os.path.join(temp_dir, filename)
            
            with open(temp_path, 'wb') as f:
                f.write(file_data)
            
            logger.debug(f"Файл сохранен во временную директорию: {temp_path}")
            return temp_path
        except Exception as e:
            logger.error(f"Ошибка получения пути к файлу: {e}")
            raise
    
    def delete_file(self, object_name: str, bucket_name: str = None, *, cef_audit: bool = True) -> bool:
        """
        Удаляет файл из MinIO
        
        Args:
            object_name: Имя объекта в MinIO
            bucket_name: Имя bucket (если None, используется self.bucket_name)
            cef_audit: писать CEF INT005/INT006 на remove
            
        Returns:
            bool: True если файл удален, False если не найден
        """
        bucket = bucket_name or self.bucket_name
        try:
            self.client.remove_object(bucket, object_name)
            logger.debug("Файл удален из MinIO: %s/%s", bucket, object_name)
            if cef_audit:
                from backend.settings.cef_logger.storage_audit import log_minio_remove_success

                log_minio_remove_success(object_name, bucket)
            return True
        except S3Error as e:
            if e.code == "NoSuchKey":
                logger.warning("Файл не найден в MinIO: %s/%s", bucket, object_name)
                return False
            if cef_audit:
                from backend.settings.cef_logger.storage_audit import log_minio_remove_failure

                log_minio_remove_failure(object_name, bucket, str(e))
            logger.exception("Ошибка удаления файла из MinIO")
            raise
    
    def file_exists(self, object_name: str, bucket_name: str = None) -> bool:
        """
        Проверяет существование файла в MinIO
        
        Args:
            object_name: Имя объекта в MinIO
            bucket_name: Имя bucket (если None, используется self.bucket_name)
            
        Returns:
            bool: True если файл существует
        """
        bucket = bucket_name or self.bucket_name
        try:
            self.client.stat_object(bucket, object_name)
            return True
        except S3Error as e:
            if e.code == 'NoSuchKey':
                return False
            logger.error(f"Ошибка проверки существования файла: {e}")
            raise
    
    def get_presigned_url(self, object_name: str, expires: timedelta = timedelta(hours=1)) -> str:
        """
        Получает presigned URL для доступа к файлу
        
        Args:
            object_name: Имя объекта в MinIO
            expires: Время жизни URL
            
        Returns:
            str: Presigned URL
        """
        try:
            url = self.client.presigned_get_object(
                self.bucket_name,
                object_name,
                expires=expires
            )
            return url
        except S3Error as e:
            logger.error(f"Ошибка получения presigned URL: {e}")
            raise
    
    def generate_object_name(self, prefix: str = "", extension: str = "") -> str:
        """
        Генерирует уникальное имя объекта
        
        Args:
            prefix: Префикс для имени (например, 'voice_', 'transcribe_')
            extension: Расширение файла (например, '.wav', '.mp3')
            
        Returns:
            str: Уникальное имя объекта
        """
        timestamp = datetime.now().timestamp()
        object_name = f"{prefix}{timestamp}{extension}"
        return object_name


# Глобальный экземпляр клиента
_minio_client: Optional[MinIOClient] = None


def get_minio_client() -> Optional[MinIOClient]:
    """
    Получает глобальный экземпляр MinIO клиента (singleton)
    
    Returns:
        MinIOClient или None, если MinIO недоступен
    """
    global _minio_client
    
    if not MINIO_AVAILABLE:
        logger.warning("MinIO библиотека не установлена. Установите: pip install minio")
        return None
    
    if _minio_client is None:
        try:
            logger.info("Инициализация MinIO клиента...")
            # MinIOClient сам загрузит настройки из settings или ENV
            _minio_client = MinIOClient()
            logger.info(f"MinIO клиент успешно инициализирован: {_minio_client.endpoint}")
        except Exception as e:
            logger.error(f"Не удалось инициализировать MinIO клиент: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            logger.warning("Будут использоваться локальные временные файлы")
            return None
    
    return _minio_client

