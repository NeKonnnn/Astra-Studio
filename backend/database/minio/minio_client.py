"""
Модуль для работы с MinIO - объектным хранилищем для временных файлов
Используется для хранения файлов голосового взаимодействия и транскрибации
"""

import os
import tempfile
from datetime import datetime, timedelta
from io import BytesIO
from typing import Optional

from backend.settings import get_settings

try:
    from minio import Minio
    from minio.error import S3Error

    MINIO_AVAILABLE = True
except ImportError:
    MINIO_AVAILABLE = False

from backend.settings.logging import get_logger

logger = get_logger(__name__)

SETTINGS_AVAILABLE = True

if not MINIO_AVAILABLE:
    logger.warning("MinIO библиотека не установлена. Установите: pip install minio")


def _build_minio_http_client(
    *, secure: bool, ssl_verify: bool, ca_cert_path: Optional[str]
):
    """HTTP-клиент urllib3 для MinIO с поддержкой корпоративного CA и отключения verify."""
    if not secure:
        return None

    import urllib3

    timeout = urllib3.util.Timeout(connect=10, read=30)
    retries = urllib3.Retry(
        total=5, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]
    )
    pool_kwargs = {"timeout": timeout, "maxsize": 10, "retries": retries}

    if not ssl_verify:
        logger.warning(
            "MinIO: проверка SSL-сертификата отключена (MINIO_SSL_VERIFY=false)"
        )
        return urllib3.PoolManager(cert_reqs="CERT_NONE", **pool_kwargs)

    if ca_cert_path and os.path.isfile(ca_cert_path):
        logger.debug("MinIO: CA bundle: %s", ca_cert_path)
        return urllib3.PoolManager(
            cert_reqs="CERT_REQUIRED", ca_certs=ca_cert_path, **pool_kwargs
        )

    if ca_cert_path:
        logger.warning(
            "MinIO: CA bundle не найден (%s) — используется системное хранилище сертификатов",
            ca_cert_path,
        )
    return None


class MinIOClient:
    """Клиент для работы с MinIO"""

    def __init__(
        self,
        endpoint: str = None,
        port: int = None,
        access_key: str = None,
        secret_key: str = None,
        secure: bool = None,
        bucket_name: str = None,
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
            msg = "MinIO библиотека не установлена. Установите: pip install minio"
            raise ImportError(msg)
        if not SETTINGS_AVAILABLE:
            msg = "Модуль settings недоступен. MinIO не может быть инициализирован."
            raise RuntimeError(msg)
        settings = get_settings()
        minio_config = settings.minio
        self.endpoint = endpoint or minio_config.endpoint
        self.port = port if port is not None else minio_config.port
        self.access_key = access_key or minio_config.access_key
        self.secret_key = secret_key or minio_config.secret_key
        self.secure = secure if secure is not None else minio_config.use_ssl
        self.bucket_name = bucket_name or minio_config.bucket_name
        self.ssl_verify = getattr(minio_config, "ssl_verify", True)
        self.ca_cert_path = getattr(minio_config, "ca_cert_path", None)
        if ":" not in self.endpoint:
            self.endpoint = f"{self.endpoint}:{self.port}"
        logger.debug("Инициализация MinIO клиента:")
        logger.debug("Endpoint: %s", self.endpoint)
        logger.debug("Access Key: %s***", self.access_key[:4])
        logger.debug("Bucket: %s", self.bucket_name)
        docs_bucket = getattr(minio_config, "documents_bucket_name", None)
        if docs_bucket and docs_bucket != self.bucket_name:
            logger.debug("Documents bucket: %s", docs_bucket)
        logger.debug("Secure (HTTPS): %s", self.secure)
        if self.secure:
            logger.debug("SSL verify: %s", self.ssl_verify)
            if self.ca_cert_path:
                logger.debug("CA cert path: %s", self.ca_cert_path)
        try:
            logger.debug("Создание MinIO клиента...")
            http_client = _build_minio_http_client(
                secure=self.secure,
                ssl_verify=self.ssl_verify,
                ca_cert_path=self.ca_cert_path,
            )
            minio_kwargs = {
                "access_key": self.access_key,
                "secret_key": self.secret_key,
                "secure": self.secure,
            }
            if http_client is not None:
                minio_kwargs["http_client"] = http_client
            if self.secure:
                minio_kwargs["cert_check"] = self.ssl_verify
            self.client = Minio(self.endpoint, **minio_kwargs)
            logger.debug("MinIO клиент создан, проверяю bucket...")
            self._ensure_bucket_exists()
            logger.info("MinIO клиент успешно инициализирован и готов к работе")
        except Exception as exc:
            logger.error(
                "Ошибка инициализации MinIO клиента (%s). Проверьте: "
                "1) доступность сервера %s; "
                "2) access_key/secret_key; "
                "3) сеть; "
                "4) SSL — смонтируйте CA bundle (TLS_CERT_PATH / MINIO_CA_CERT) "
                "или для dev задайте MINIO_SSL_VERIFY=false",
                exc,
                self.endpoint,
                exc_info=True,
            )
            raise

    def _ensure_bucket_exists(self, bucket_name: str = None) -> None:
        """Проверяет bucket; создание — только если MINIO_AUTO_CREATE_BUCKETS=true."""
        bucket = bucket_name or self.bucket_name
        auto_create = os.getenv(
            "MINIO_AUTO_CREATE_BUCKETS", "false"
        ).strip().lower() in ("1", "true", "yes")
        try:
            exists = self.client.bucket_exists(bucket)
        except S3Error as e:
            if e.code in ("AccessDenied", "AccessDeniedException"):
                logger.warning(
                    "MinIO: нет прав на HEAD bucket '%s' (%s) — пропускаем проверку, пробуем upload",
                    bucket,
                    e.code,
                )
                return
            logger.exception("Ошибка при проверке bucket '%s'", bucket)
            raise

        if exists:
            logger.debug("Bucket '%s' уже существует", bucket)
            return
        if auto_create:
            try:
                self.client.make_bucket(bucket)
                logger.info("Bucket '%s' создан", bucket)
            except S3Error as e:
                if e.code in (
                    "AccessDenied",
                    "AccessDeniedException",
                    "BucketAlreadyOwnedByYou",
                ):
                    logger.warning(
                        "MinIO: не удалось создать bucket '%s' (%s) — пробуем upload в существующий",
                        bucket,
                        e.code,
                    )
                    return
                logger.exception("Ошибка при создании bucket '%s'", bucket)
                raise
        else:
            logger.debug(
                "Bucket '%s' не найден через HEAD, auto-create отключён — пробуем upload",
                bucket,
            )

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
        cef_audit: bool = True,
        cef_display_name: str | None = None,
    ) -> str:
        """
        Загружает файл в MinIO

        Args:
            file_data: Данные файла в виде bytes
            object_name: Имя объекта в MinIO (путь к файлу)
            content_type: MIME тип файла
            bucket_name: Имя bucket (если None, используется self.bucket_name)

        Returns:
            object_name: Имя загруженного объекта
        """
        bucket = bucket_name or self.bucket_name
        try:
            self._ensure_bucket_exists(bucket)
            file_stream = BytesIO(file_data)
            file_size = len(file_data)
            self.client.put_object(
                bucket, object_name, file_stream, file_size, content_type=content_type
            )
            logger.debug(
                "Файл загружен в MinIO: %s/%s (%s байт)", bucket, object_name, file_size
            )
            if cef_audit:
                from backend.settings.cef_logger.storage_audit import (
                    log_minio_write_success,
                )

                log_minio_write_success(
                    object_name, bucket, display_name=cef_display_name
                )
            return object_name
        except S3Error as e:
            if cef_audit:
                from backend.settings.cef_logger.storage_audit import (
                    log_minio_write_failure,
                )

                log_minio_write_failure(
                    object_name, bucket, str(e), display_name=cef_display_name
                )
            logger.exception("Ошибка загрузки файла в MinIO")
            raise

    def download_file(
        self, object_name: str, bucket_name: str = None, *, cef_audit: bool = True
    ) -> bytes:
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
            logger.debug(
                "Файл скачан из MinIO: %s/%s (%s байт)", bucket, object_name, len(data)
            )
            if cef_audit:
                from backend.settings.cef_logger.storage_audit import (
                    log_minio_read_success,
                )

                log_minio_read_success(object_name, bucket)
            return data
        except S3Error as e:
            if cef_audit:
                from backend.settings.cef_logger.storage_audit import (
                    log_minio_read_failure,
                )

                log_minio_read_failure(object_name, bucket, str(e))
            logger.exception("Ошибка скачивания файла из MinIO")
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
            file_data = self.download_file(object_name, bucket_name)
            temp_dir = tempfile.gettempdir()
            filename = (
                os.path.basename(object_name) or f"temp_{datetime.now().timestamp()}"
            )
            temp_path = os.path.join(temp_dir, filename)
            with open(temp_path, "wb") as f:
                f.write(file_data)
            logger.debug("Файл сохранен во временную директорию: %s", temp_path)
            return temp_path
        except Exception:
            logger.exception("Ошибка получения пути к файлу")
            raise

    def delete_file(
        self, object_name: str, bucket_name: str = None, *, cef_audit: bool = True
    ) -> bool:
        """
        Удаляет файл из MinIO

        Args:
            object_name: Имя объекта в MinIO
            bucket_name: Имя bucket (если None, используется self.bucket_name)

        Returns:
            bool: True если файл удален, False если не найден
        """
        bucket = bucket_name or self.bucket_name
        try:
            self.client.remove_object(bucket, object_name)
            logger.debug("Файл удален из MinIO: %s/%s", bucket, object_name)
            if cef_audit:
                from backend.settings.cef_logger.storage_audit import (
                    log_minio_remove_success,
                )

                log_minio_remove_success(object_name, bucket)
            return True
        except S3Error as e:
            if e.code == "NoSuchKey":
                logger.warning("Файл не найден в MinIO: %s/%s", bucket, object_name)
                return False
            if cef_audit:
                from backend.settings.cef_logger.storage_audit import (
                    log_minio_remove_failure,
                )

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
            if e.code == "NoSuchKey":
                return False
            logger.exception("Ошибка проверки существования файла")
            raise

    def get_presigned_url(
        self, object_name: str, expires: timedelta = timedelta(hours=1)
    ) -> str:
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
                self.bucket_name, object_name, expires=expires
            )
            return url
        except S3Error:
            logger.exception("Ошибка получения presigned URL")
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
            logger.debug("Инициализация MinIO клиента...")
            _minio_client = MinIOClient()
            logger.info(
                "MinIO клиент успешно инициализирован: %s", _minio_client.endpoint
            )
        except Exception as exc:
            logger.error(
                "Не удалось инициализировать MinIO клиент (%s). Будут использоваться локальные временные файлы",
                exc,
                exc_info=True,
            )
            return None
    return _minio_client
