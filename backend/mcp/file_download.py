"""Скачивание файлов MCP (PowerPoint .pptx и др.) из S3 или HTTP proxy."""

from __future__ import annotations

import os
from typing import List, Optional, Tuple
from urllib.parse import quote

import httpx

from backend.settings.config import McpServerConfig
from backend.settings.logging import get_logger

logger = get_logger(__name__)

_ZIP_MAGIC = b"PK"
_JSON_HTML_PREFIXES = (b"{", b"[", b"<", b"<!DOCTYPE", b"<!doctype")


def is_valid_office_blob(data: bytes, filename: str) -> bool:
    """PPTX/DOCX/XLSX — ZIP (PK); PPT — OLE (D0 CF 11 E0)."""
    if not data or len(data) < 4:
        return False
    lower = filename.lower()
    if lower.endswith((".pptx", ".docx", ".xlsx")):
        return data[:2] == _ZIP_MAGIC
    if lower.endswith(".ppt"):
        return data[:4] == b"\xd0\xcf\x11\xe0"
    if lower.endswith(".pdf"):
        return data[:4] == b"%PDF"
    head = data[:32].lstrip()
    return not any((head.startswith(prefix) for prefix in _JSON_HTML_PREFIXES))


def guess_content_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".pptx"):
        return (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
    if lower.endswith(".ppt"):
        return "application/vnd.ms-powerpoint"
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if lower.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"


def _storage_env_prefix(server_id: str) -> str:
    return f"MCP_SERVER_{server_id.upper().replace('-', '_')}_FILES_S3_"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _resolve_ca_cert_path(server_id: str) -> Optional[str]:
    prefix = _storage_env_prefix(server_id)
    for env_name in (
        f"{prefix}CA_PATH",
        "TLS_CERT_PATH",
        "LDAP_CA_CERT_PATH",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
    ):
        path = (os.getenv(env_name) or "").strip()
        if path and os.path.isfile(path):
            return path
    return None


def _resolve_verify_tls(server_id: str, default: bool = True) -> bool:
    prefix = _storage_env_prefix(server_id)
    raw = os.getenv(f"{prefix}VERIFY_TLS")
    if raw is not None:
        return _env_bool(f"{prefix}VERIFY_TLS", default)
    return default


def _minio_fallback(field: str, current: str) -> str:
    if current:
        return current
    env_map = {
        "bucket": ("MINIO_BUCKET_NAME", "MINIO_DOCUMENTS_BUCKET_NAME"),
        "endpoint": ("MINIO_ENDPOINT",),
        "access_key": ("MINIO_ACCESS_KEY", "MINIO_ROOT_USER"),
        "secret_key": ("MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"),
    }
    for env_name in env_map.get(field, ()):
        value = (os.getenv(env_name) or "").strip()
        if value:
            return value
    return ""


def resolve_files_storage(server: McpServerConfig) -> Optional[dict]:
    """Настройки S3 для файлов MCP-сервера (YAML + env + fallback MINIO_*)."""
    fs = getattr(server, "files_storage", None)
    if fs is not None and (not getattr(fs, "enabled", True)):
        return None
    prefix = _storage_env_prefix(server.id)
    bucket = (os.getenv(f"{prefix}BUCKET") or (fs.bucket if fs else "") or "").strip()
    bucket = _minio_fallback("bucket", bucket)
    endpoint = (
        os.getenv(f"{prefix}ENDPOINT") or (fs.endpoint if fs else "") or ""
    ).strip()
    endpoint = _minio_fallback("endpoint", endpoint)
    if not bucket or not endpoint:
        logger.warning(
            "MCP files_storage not configured for server=%s (bucket=%s endpoint=%s)",
            server.id,
            bucket or "<empty>",
            endpoint or "<empty>",
        )
        return None
    key_prefix = (
        os.getenv(f"{prefix}PREFIX") or (fs.prefix if fs else "") or "presentations"
    ).strip()
    key_prefix = key_prefix.strip("/")
    access_key = (
        os.getenv(f"{prefix}ACCESS_KEY") or (fs.access_key if fs else "") or ""
    ).strip()
    secret_key = (
        os.getenv(f"{prefix}SECRET_KEY") or (fs.secret_key if fs else "") or ""
    ).strip()
    access_key = _minio_fallback("access_key", access_key)
    secret_key = _minio_fallback("secret_key", secret_key)
    if not access_key or not secret_key:
        logger.warning(
            "MCP files_storage credentials missing for server=%s (set %sACCESS_KEY/_SECRET_KEY or MINIO_ACCESS_KEY/MINIO_SECRET_KEY)",
            server.id,
            prefix,
        )
        return None
    use_ssl_raw = os.getenv(f"{prefix}USE_SSL")
    if use_ssl_raw is not None:
        use_ssl = _env_bool(f"{prefix}USE_SSL", True)
    elif fs is not None:
        use_ssl = bool(fs.use_ssl)
    else:
        use_ssl = _env_bool("MINIO_USE_SSL", True)
    port_raw = os.getenv(f"{prefix}PORT") or os.getenv("MINIO_PORT")
    if port_raw:
        try:
            port = int(port_raw)
        except ValueError:
            port = fs.port if fs else 443 if use_ssl else 80
    elif fs is not None:
        port = fs.port
    else:
        port = 443 if use_ssl else 80
    return {
        "bucket": bucket,
        "endpoint": endpoint,
        "port": port,
        "use_ssl": use_ssl,
        "prefix": key_prefix,
        "access_key": access_key,
        "secret_key": secret_key,
        "ca_path": _resolve_ca_cert_path(server.id),
        "verify_tls": _resolve_verify_tls(server.id, use_ssl),
        "server_id": server.id,
    }


def _s3_object_key_candidates(
    filename: str, storage: dict, storage_key: Optional[str] = None
) -> List[str]:
    keys: List[str] = []
    seen: set[str] = set()

    def _add(key: str) -> None:
        cleaned = (key or "").strip().lstrip("/")
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        keys.append(cleaned)

    if storage_key:
        _add(storage_key)
    prefix = storage.get("prefix") or ""
    _add(f"{prefix}/{filename}" if prefix else filename)
    if prefix:
        _add(filename)
    _add(f"outputs/{filename}")
    _add(f"files/{filename}")
    return keys


def _build_s3_client(storage: dict):
    try:
        import urllib3
        from minio import Minio
    except ImportError as exc:
        msg = "MinIO library not installed"
        raise RuntimeError(msg) from exc
    endpoint = storage["endpoint"]
    port = storage.get("port")
    if port and ":" not in endpoint:
        endpoint = f"{endpoint}:{port}"
    verify_tls = bool(storage.get("verify_tls", storage.get("use_ssl", True)))
    ca_path = storage.get("ca_path")
    if verify_tls and ca_path:
        http_client = urllib3.PoolManager(cert_reqs="CERT_REQUIRED", ca_certs=ca_path)
        logger.debug("MCP S3 client using CA bundle: %s", ca_path)
    elif verify_tls:
        http_client = urllib3.PoolManager()
    else:
        http_client = urllib3.PoolManager(cert_reqs="CERT_NONE")
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return Minio(
        endpoint,
        access_key=storage["access_key"],
        secret_key=storage["secret_key"],
        secure=bool(storage.get("use_ssl")),
        http_client=http_client,
        region=os.getenv("MINIO_REGION") or os.getenv("S3_REGION") or "us-east-1",
    )


def fetch_from_s3(
    filename: str, storage: dict, *, storage_key: Optional[str] = None
) -> Tuple[Optional[bytes], str]:
    client = _build_s3_client(storage)
    bucket = storage["bucket"]
    candidates = _s3_object_key_candidates(filename, storage, storage_key)
    last_error = ""
    for key in candidates:
        try:
            response = client.get_object(bucket, key)
            data = response.read()
            response.close()
            response.release_conn()
        except Exception as exc:
            last_error = str(exc)
            logger.exception("MCP S3 download miss server= bucket= key=")
            continue
        if is_valid_office_blob(data, filename):
            logger.info(
                "MCP file fetched from S3 bucket=%s key=%s size=%s",
                bucket,
                key,
                len(data),
            )
            return (data, "")
        last_error = (
            f"object at key={key} is not a valid office file (size={len(data)})"
        )
        logger.warning(
            "MCP S3 object bucket=%s key=%s is not a valid office file (size=%s)",
            bucket,
            key,
            len(data),
        )
    if not last_error:
        last_error = f"no object found for keys: {', '.join(candidates)}"
    return (None, last_error)


def _http_verify_setting(server_id: str, server_verify_ssl: bool) -> bool | str:
    ca_path = _resolve_ca_cert_path(server_id)
    if ca_path:
        return ca_path
    if not _resolve_verify_tls(server_id, server_verify_ssl):
        return False
    return server_verify_ssl


def _http_candidate_urls(base: str, filename: str) -> List[str]:
    base = base.rstrip("/")
    q = quote(filename, safe="")
    return [
        f"{base}/files/{q}",
        f"{base}/download/{q}",
        f"{base}/api/files/{q}",
        f"{base}/api/download/{q}",
        f"{base}/outputs/{q}",
        f"{base}/presentations/{q}",
        f"{base}/{q}",
    ]


async def fetch_from_http_proxy(
    filename: str,
    base_url: str,
    headers: dict,
    *,
    verify_ssl: bool | str = True,
    timeout: float = 120.0,
) -> Optional[bytes]:
    async with httpx.AsyncClient(timeout=timeout, verify=verify_ssl) as client:
        for url in _http_candidate_urls(base_url, filename):
            try:
                response = await client.get(url, headers=headers, follow_redirects=True)
            except Exception:
                logger.exception("MCP HTTP download try failed url=")
                continue
            if response.status_code != 200:
                continue
            data = response.content
            if is_valid_office_blob(data, filename):
                logger.info("MCP file fetched via HTTP url=%s size=%s", url, len(data))
                return data
            logger.debug(
                "MCP HTTP url=%s returned non-office payload (status=%s, size=%s)",
                url,
                response.status_code,
                len(data),
            )
    return None


async def fetch_mcp_server_file(
    *,
    server: McpServerConfig,
    filename: str,
    base_url: str,
    http_headers: dict,
    storage_key: Optional[str] = None,
) -> Tuple[bytes, str]:
    """
    Загрузить файл с MCP-сервера.
    Returns: (bytes, content_type)
    Raises: LookupError if not found, ValueError if invalid payload
    """
    safe_name = (filename or "").strip()
    if not safe_name:
        msg = "Invalid filename"
        raise ValueError(msg)
    storage = resolve_files_storage(server)
    s3_error = ""
    if storage:
        data, s3_error = fetch_from_s3(safe_name, storage, storage_key=storage_key)
        if data:
            return (data, guess_content_type(safe_name))
    else:
        s3_error = "S3 storage is not configured (MCP_SERVER_*_FILES_S3_* or MINIO_ACCESS_KEY/MINIO_SECRET_KEY)"
    data = await fetch_from_http_proxy(
        safe_name,
        base_url,
        http_headers,
        verify_ssl=_http_verify_setting(server.id, bool(server.verify_ssl)),
    )
    if data:
        return (data, guess_content_type(safe_name))
    detail = f"File '{safe_name}' not found or not a valid office document"
    if storage_key:
        detail += f" (storage_key={storage_key})"
    if s3_error:
        detail += f"; S3: {s3_error}"
    raise LookupError(detail)
