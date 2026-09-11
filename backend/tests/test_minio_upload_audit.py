"""Regression tests for MinIO upload CEF audit (FS003/FS004).

Callers pass ``cef_display_name`` to ``MinIOClient.upload_file`` (see
``routes/documents.py`` and ``routes/chat.py``), and the write-audit helpers
``log_minio_write_success`` / ``log_minio_write_failure`` already accept a
``display_name``. Before the fix ``upload_file`` accepted neither, so every such
call raised ``TypeError`` and no FS003/FS004 event was ever emitted.

These tests pin both halves: the signature the callers rely on, and the audit
events the helpers exist for.
"""

import unittest
from unittest import mock

import pytest

try:
    from backend.database.minio import minio_client as minio_module
except Exception as e:  # noqa: BLE001
    # Импорт пакета backend тянет рантайм-зависимости (minio, asyncpg и пр.).
    # В окружении без них тест не запускаем, а не падаем на сборе.
    pytest.skip(f"backend runtime deps unavailable: {e}", allow_module_level=True)


class _S3Error(Exception):
    """Заглушка minio.error.S3Error: код важен только delete_file."""

    def __init__(self, message="boom"):
        super().__init__(message)
        self.code = "InternalError"


def _client() -> "minio_module.MinIOClient":
    """MinIOClient без сети: __init__ обходим, ставим только нужные поля."""
    client = object.__new__(minio_module.MinIOClient)
    client.client = mock.Mock()
    client.bucket_name = "astrachat-temp"
    return client


class TestUploadFileAudit(unittest.TestCase):
    def setUp(self):
        self.client = _client()
        self.client._ensure_bucket_exists = mock.Mock()

    def test_accepts_cef_display_name_kwarg(self):
        """Сигнатура, на которую рассчитывают вызывающие в routes/*.

        До фикса: TypeError: unexpected keyword argument 'cef_display_name'.
        """
        with mock.patch.object(minio_module, "log_minio_write_success", create=True):
            name = self.client.upload_file(
                b"data",
                "doc_1.txt",
                content_type="text/plain",
                cef_display_name="original name.txt",
            )
            self.assertEqual(name, "doc_1.txt")
            self.client.client.put_object.assert_called_once()

    def test_success_emits_fs003_with_display_name(self):
        with mock.patch(
            "backend.settings.cef_logger.storage_audit.log_minio_write_success"
        ) as logged:
            self.client.upload_file(
                b"data",
                "doc_1.txt",
                bucket_name="astrachat-documents",
                cef_display_name="original name.txt",
            )
            logged.assert_called_once_with(
                "doc_1.txt", "astrachat-documents", display_name="original name.txt"
            )

    def test_failure_emits_fs004_and_reraises(self):
        self.client.client.put_object.side_effect = _S3Error("access denied")
        # create=True: в окружении без пакета minio имя S3Error в модуле отсутствует.
        with mock.patch.object(minio_module, "S3Error", _S3Error, create=True):
            with mock.patch(
                "backend.settings.cef_logger.storage_audit.log_minio_write_failure"
            ) as logged:
                with self.assertRaises(_S3Error):
                    self.client.upload_file(
                        b"data",
                        "doc_1.txt",
                        bucket_name="astrachat-documents",
                        cef_display_name="original name.txt",
                    )
                self.assertEqual(logged.call_count, 1)
                args, kwargs = logged.call_args
                self.assertEqual(args[0], "doc_1.txt")
                self.assertEqual(args[1], "astrachat-documents")
                self.assertEqual(kwargs.get("display_name"), "original name.txt")

    def test_cef_audit_false_emits_nothing(self):
        """Симметрично delete_file: аудит можно выключить точечно."""
        with mock.patch(
            "backend.settings.cef_logger.storage_audit.log_minio_write_success"
        ) as logged:
            self.client.upload_file(b"data", "doc_1.txt", cef_audit=False)
            logged.assert_not_called()

    def test_positional_callers_still_work(self):
        """Новые параметры keyword-only: позиционные вызовы в routes/* не ломаются."""
        with mock.patch(
            "backend.settings.cef_logger.storage_audit.log_minio_write_success"
        ):
            name = self.client.upload_file(b"data", "voice.wav", "audio/wav", "astrachat-temp")
            self.assertEqual(name, "voice.wav")


if __name__ == "__main__":
    unittest.main()
