"""Parse MCP tool results into structured form (B-37)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from backend.mcp.types import ParsedMcpResult

# Лимит текста tool-result в follow-up LLM (иначе n_ctx переполняется на fetchWebContent)
DEFAULT_TOOL_RESULT_MAX_CHARS = 6000
_SEARCH_RESULT_LIMIT = 5
_FETCH_CONTENT_MAX_CHARS = 3500

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_FILE_PATH_RE = re.compile(r"[^\s<>\"']+\.(?:pptx|ppt|pdf|docx|xlsx)\b", re.IGNORECASE)
_LINK_KEYS = (
    "download_url",
    "downloadUrl",
    "file_url",
    "fileUrl",
    "url",
    "href",
    "link",
    "download_link",
    "downloadLink",
    "s3_url",
    "s3Url",
    "presigned_url",
    "presignedUrl",
)
_NAME_KEYS = ("filename", "file_name", "fileName", "name", "title")
_STORAGE_KEY_KEYS = (
    "s3_key",
    "s3Key",
    "object_key",
    "objectKey",
    "storage_key",
    "storageKey",
    "minio_object",
    "minioObject",
    "minio_key",
    "minioKey",
)
_S3_URI_RE = re.compile(r"s3://[^/\s]+/([^\s\"']+)", re.IGNORECASE)
_SAVED_FILE_RE = re.compile(
    r"(?:файл\s+сохран[её]н|saved(?:\s+as)?|file\s+saved|download(?:ed)?(?:\s+as)?)"
    r"[\s:]+['\"]?([^\s'\"<>]+\.(?:pptx|ppt|pdf|docx|xlsx))['\"]?",
    re.IGNORECASE,
)


def parse_mcp_result(content: Any) -> str:
    return format_parsed_for_llm(parse_mcp_result_to_struct(content))


def parse_mcp_result_to_struct(content: Any) -> ParsedMcpResult:
    if content is None:
        return ParsedMcpResult(text="")
    if isinstance(content, str):
        return ParsedMcpResult(text=content, raw=content)
    if isinstance(content, dict):
        if "contents" in content and isinstance(content["contents"], list):
            return parse_mcp_result_to_struct(content["contents"])
        if "content" in content:
            return parse_mcp_result_to_struct(content["content"])
        if "text" in content and isinstance(content["text"], str):
            return ParsedMcpResult(text=content["text"], raw=content)
        if "type" in content:
            return _parse_content_item(content)
        return ParsedMcpResult(text=str(content), raw=content)
    if isinstance(content, list):
        parts: List[str] = []
        images: List[dict] = []
        audio: List[dict] = []
        resources: List[dict] = []
        for item in content:
            parsed = _parse_content_item(item if isinstance(item, dict) else {"type": "text", "text": str(item)})
            if parsed.text:
                parts.append(parsed.text)
            images.extend(parsed.images)
            audio.extend(parsed.audio)
            resources.extend(parsed.resources)
        return ParsedMcpResult(
            text="\n".join(p for p in parts if p),
            images=images,
            audio=audio,
            resources=resources,
            raw=content,
        )
    return ParsedMcpResult(text=str(content), raw=content)


def _parse_content_item(item: dict) -> ParsedMcpResult:
    ctype = str(item.get("type") or "").lower()
    if ctype == "text":
        text = item.get("text") or item.get("data") or ""
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        return ParsedMcpResult(text=str(text), raw=item)
    if ctype == "image":
        mime = item.get("mimeType") or item.get("mime_type") or "image/png"
        data = item.get("data") or item.get("blob")
        meta = {"type": "image", "mimeType": mime, "data": data, "uri": item.get("uri")}
        return ParsedMcpResult(text="[image]", images=[meta], raw=item)
    if ctype == "audio":
        mime = item.get("mimeType") or item.get("mime_type") or "audio/wav"
        data = item.get("data") or item.get("blob")
        meta = {"type": "audio", "mimeType": mime, "data": data, "uri": item.get("uri")}
        return ParsedMcpResult(text="[audio]", audio=[meta], raw=item)
    if ctype == "resource":
        uri = item.get("uri") or item.get("resource", {}).get("uri") if isinstance(item.get("resource"), dict) else None
        text = item.get("text")
        if not text and isinstance(item.get("resource"), dict):
            text = item["resource"].get("text")
        meta = {
            "type": "resource",
            "uri": uri,
            "mimeType": item.get("mimeType") or item.get("mime_type"),
            "text": text,
        }
        label = text or uri or "[resource]"
        return ParsedMcpResult(text=str(label), resources=[meta], raw=item)
    if "text" in item and isinstance(item["text"], str):
        return ParsedMcpResult(text=item["text"], raw=item)
    return ParsedMcpResult(text=str(item), raw=item)


def format_parsed_for_llm(parsed: ParsedMcpResult, *, max_resource_chars: int = 4000) -> str:
    """Текст для LLM follow-up с кратким описанием non-text content."""
    lines: List[str] = []
    if parsed.text:
        lines.append(compact_tool_result_for_llm(parsed.text))
    for img in parsed.images:
        mime = img.get("mimeType") or "image"
        lines.append(f"[MCP image attachment: {mime}]")
    for aud in parsed.audio:
        mime = aud.get("mimeType") or "audio"
        lines.append(f"[MCP audio attachment: {mime}]")
    for res in parsed.resources:
        uri = res.get("uri") or "resource"
        body = res.get("text")
        if body:
            snippet = str(body)[:max_resource_chars]
            lines.append(f"[MCP resource {uri}]\n{snippet}")
        else:
            lines.append(f"[MCP resource: {uri}]")
    return "\n".join(lines).strip()


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _truncate(text: str, limit: int) -> str:
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "…"


def _compact_web_search_payload(data: dict) -> dict:
    results = data.get("results")
    if not isinstance(results, list):
        return data
    compact_results = []
    for row in results[:_SEARCH_RESULT_LIMIT]:
        if not isinstance(row, dict):
            continue
        compact_results.append(
            {
                "title": row.get("title"),
                "url": row.get("url"),
                "description": _truncate(_strip_html(str(row.get("description") or "")), 500),
                "source": row.get("source"),
            }
        )
    out = {
        "query": data.get("query"),
        "engines": data.get("engines"),
        "totalResults": data.get("totalResults"),
        "results": compact_results,
    }
    if data.get("partialFailures"):
        out["partialFailures"] = data.get("partialFailures")
    return out


def _compact_fetch_web_payload(data: dict) -> dict:
    content = str(data.get("content") or data.get("excerpt") or "")
    content = _truncate(_strip_html(content), _FETCH_CONTENT_MAX_CHARS)
    return {
        "url": data.get("url") or data.get("finalUrl"),
        "title": data.get("title"),
        "content": content,
        "excerpt": _truncate(_strip_html(str(data.get("excerpt") or "")), 800),
    }


def compact_tool_result_for_llm(text: str, *, max_chars: int = DEFAULT_TOOL_RESULT_MAX_CHARS) -> str:
    """
    Ужимает JSON/HTML из MCP (websearch) перед передачей в LLM.
    Убирает readableHtml, links и прочие тяжёлые поля.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _truncate(raw, max_chars)

    if isinstance(data, dict):
        if "results" in data and isinstance(data.get("results"), list):
            data = _compact_web_search_payload(data)
        elif data.get("url") and ("content" in data or "readableHtml" in data):
            data = _compact_fetch_web_payload(data)
        else:
            data = {k: v for k, v in data.items() if k not in ("readableHtml", "links", "html")}
        compact = json.dumps(data, ensure_ascii=False)
        return _truncate(compact, max_chars)

    if isinstance(data, list):
        return _truncate(json.dumps(data, ensure_ascii=False), max_chars)

    return _truncate(str(data), max_chars)


def preview_parsed_for_ui(parsed: ParsedMcpResult, *, max_len: int = 320) -> str:
    """Короткий snippet для socket event / UI preview (F-13)."""
    text = (parsed.text or "").strip()
    if text and text not in ("[image]", "[audio]"):
        return text if len(text) <= max_len else text[: max_len - 1] + "…"
    if parsed.images:
        mime = parsed.images[0].get("mimeType") or "image"
        return f"[Изображение: {mime}]"
    if parsed.audio:
        mime = parsed.audio[0].get("mimeType") or "audio"
        return f"[Аудио: {mime}]"
    if parsed.resources:
        uri = parsed.resources[0].get("uri") or "resource"
        body = parsed.resources[0].get("text")
        if body:
            snippet = str(body).strip()
            return snippet if len(snippet) <= max_len else snippet[: max_len - 1] + "…"
        return f"[Resource: {uri}]"
    return text[:max_len] if text else ""


def parsed_to_api_dict(parsed: ParsedMcpResult, *, include_binary: bool = False) -> dict:
    """JSON-safe dict для REST API (без base64 blob по умолчанию)."""
    def _strip_blob(items: List[dict]) -> List[dict]:
        out = []
        for it in items:
            row = dict(it)
            if not include_binary and "data" in row:
                row["data"] = "[binary omitted]" if row["data"] else None
            out.append(row)
        return out

    return {
        "text": parsed.text,
        "images": _strip_blob(parsed.images),
        "audio": _strip_blob(parsed.audio),
        "resources": parsed.resources,
        "has_attachments": bool(parsed.images or parsed.audio or parsed.resources),
    }


def extract_download_links(parsed: ParsedMcpResult) -> List[Dict[str, str]]:
    """Ссылки на файлы из текста/JSON MCP tool result (pptx и др.)."""
    links: List[Dict[str, str]] = []
    seen: set[str] = set()

    def _add(url: str, label: str = "Скачать файл", mime: str = "") -> None:
        cleaned = (url or "").strip().rstrip(".,);]")
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        links.append({"url": cleaned, "label": label, "mime": mime})

    def _scan_text(text: str) -> None:
        if not text:
            return
        stripped = text.strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                label = next(
                    (str(data[k]) for k in _NAME_KEYS if data.get(k)), "Скачать файл"
                )
                for key in _LINK_KEYS:
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        _add(value, label)
                for key in (
                    "path",
                    "file_path",
                    "filePath",
                    "output_path",
                    "outputPath",
                ):
                    value = data.get(key)
                    if isinstance(value, str) and _FILE_PATH_RE.search(value):
                        _add(value, label or value.rsplit("/", 1)[-1])
        for match in _URL_RE.findall(text):
            name = match.rsplit("/", 1)[-1]
            _add(match, name or "Скачать файл")
        for match in _FILE_PATH_RE.findall(text):
            if match.startswith("http"):
                _add(match, match.rsplit("/", 1)[-1])

    _scan_text(parsed.text or "")
    for res in parsed.resources:
        uri = res.get("uri")
        if isinstance(uri, str):
            _scan_text(uri)
        body = res.get("text")
        if isinstance(body, str):
            _scan_text(body)

    return links


def _extract_saved_filenames(parsed: ParsedMcpResult) -> List[str]:
    names: List[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        cleaned = (name or "").strip().strip("'\"")
        if not cleaned or cleaned in seen:
            return
        if not _FILE_PATH_RE.search(cleaned):
            return
        seen.add(cleaned)
        names.append(cleaned)

    texts = [parsed.text or ""]
    for res in parsed.resources:
        if isinstance(res.get("text"), str):
            texts.append(str(res["text"]))
    for text in texts:
        for match in _SAVED_FILE_RE.findall(text):
            _add(match)
        for match in _FILE_PATH_RE.findall(text):
            if not match.startswith("http"):
                _add(match.rsplit("\\", 1)[-1].rsplit("/", 1)[-1])
    return names


def enrich_download_links(
    parsed: ParsedMcpResult, server_id: str
) -> List[Dict[str, str]]:
    """URL для скачивания: из ответа tool или proxy через backend по имени файла."""
    from urllib.parse import quote

    links = extract_download_links(parsed)
    if links:
        return links
    mime_pptx = (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    storage_key = _extract_storage_key(parsed)
    for filename in _extract_saved_filenames(parsed):
        query = f"filename={quote(filename)}"
        if storage_key:
            query += f"&storage_key={quote(storage_key)}"
        links.append(
            {
                "url": f"/api/mcp/servers/{quote(str(server_id), safe='')}/download?{query}",
                "label": filename,
                "mime": (
                    mime_pptx
                    if filename.lower().endswith(".pptx")
                    else "application/octet-stream"
                ),
                **({"storage_key": storage_key} if storage_key else {}),
            }
        )
    return links


def _extract_storage_key(parsed: ParsedMcpResult) -> str:
    """S3-ключ из JSON-ответа MCP tool (office-powerpoint-mcp-server → MinIO)."""
    texts = [parsed.text or ""]
    for res in parsed.resources:
        if isinstance(res.get("text"), str):
            texts.append(str(res["text"]))
    raw = parsed.raw
    candidates: List[Any] = []
    if isinstance(raw, dict):
        candidates.append(raw)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                candidates.append(item)
    for text in texts:
        stripped = (text or "").strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                candidates.append(data)
    for data in candidates:
        if not isinstance(data, dict):
            continue
        for key in _STORAGE_KEY_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                cleaned = value.strip().lstrip("/")
                if cleaned.lower().startswith("s3://"):
                    match = _S3_URI_RE.search(cleaned)
                    if match:
                        return match.group(1).lstrip("/")
                return cleaned
        for path_key in (
            "path",
            "file_path",
            "filePath",
            "output_path",
            "outputPath",
            "s3_path",
            "s3Path",
        ):
            value = data.get(path_key)
            if (
                isinstance(value, str)
                and value.strip()
                and not value.strip().startswith("http")
            ):
                cleaned = value.strip().replace("\\", "/").lstrip("/")
                if cleaned.lower().startswith("s3://"):
                    match = _S3_URI_RE.search(cleaned)
                    if match:
                        return match.group(1).lstrip("/")
                if "/" in cleaned or cleaned.lower().endswith(
                    (".pptx", ".ppt", ".pdf", ".docx", ".xlsx")
                ):
                    return cleaned
    for text in texts:
        for match in _S3_URI_RE.findall(text or ""):
            return match.lstrip("/")
    return ""


def format_tool_result_for_ui(parsed: ParsedMcpResult, raw: Any = None) -> str:
    """Полный результат tool для UI (pretty JSON или текст)."""
    source = raw if raw is not None else parsed.raw
    if source is not None:
        try:
            return json.dumps(source, ensure_ascii=False, indent=2, default=str)
        except TypeError:
            pass
    text = format_parsed_for_llm(parsed)
    stripped = (text or "").strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.dumps(json.loads(stripped), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
    return text or ""


def append_download_links_to_content(content: str, links: List[Dict[str, str]]) -> str:
    if not links:
        return content
    body = (content or "").strip()
    block_lines = ["", "**Файлы:**"]
    for item in links:
        label = item.get("label") or "Скачать"
        url = item.get("url") or ""
        if url:
            block_lines.append(f"- [{label}]({url})")
    block = "\n".join(block_lines)
    if body and any(url in body for url in (x.get("url") or "" for x in links)):
        return body
    return f"{body}{block}".strip()
