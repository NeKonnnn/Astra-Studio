"""Разбиение текста на чанки для RAG.

Стратегии:
- ``universal`` — структурный чанкер (страницы/заголовки/таблицы + RecursiveCharacter).
  Используется для Библиотеки (KB + memory-rag): единый стабильный режим.
- ``fixed`` — только RecursiveCharacterTextSplitter по размеру.
- ``markdown`` — приоритет markdown-заголовков, затем размерный сплит.
- ``separators`` — сплит по явным разделителям (\\n\\n, \\n, . ).
- ``semantic`` — укрупнённые абзацы с мягким лимитом (без эмбеддинг-сегментации).
- ``hierarchical`` — как universal (иерархия уровней — отдельно в hierarchical.py для global).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_VALID_STRATEGIES = frozenset({"universal", "fixed", "markdown", "separators", "semantic", "hierarchical"})

_HEADING_PATTERNS: List[re.Pattern] = [
    re.compile(r"^\s*#{1,6}\s+.+$", re.MULTILINE),
    re.compile(r"^\s*(?:Глава|Раздел|Часть)\s+\S.+$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*\d{1,3}(?:\.\d{1,3}){0,4}\s+\S.+$", re.MULTILINE),
    re.compile(r"^\s*[IVXLC]{1,6}\.\s+\S.+$", re.MULTILINE),
]

_PAGE_BREAK_PATTERN = re.compile(r"\n?-{2,}\s*Страница.*?-{2,}\s*\n?", re.IGNORECASE)
_TABLE_LINE_PATTERN = re.compile(r"^\s*\|.+\|\s*$")
_MD_HEADING = re.compile(r"^\s*#{1,6}\s+.+$", re.MULTILINE)


def normalize_chunking_strategy(raw: Optional[str]) -> str:
    s = (raw or "universal").strip().lower()
    if s in {"structure", "structural", "default", "library"}:
        return "universal"
    if s in _VALID_STRATEGIES:
        return s
    return "universal"


def describe_embed_client(client) -> str:
    """Человекочитаемое имя активного эмбеддера для логов индексации.

    SplitRagClient разворачиваем до embed-части; у OpenAI-compat клиентов
    берём provider_id + embedding_model; иначе это native svc-rag-models.
    """
    inner = getattr(client, "embed_client", None) or client
    provider = getattr(inner, "provider_id", None)
    if provider:
        return f"{provider}({getattr(inner, 'embedding_model', None) or '?'})"
    return "native(svc-rag-models)"    


def _split_by_page_breaks(text: str) -> List[str]:
    parts = _PAGE_BREAK_PATTERN.split(text)
    return [p for p in parts if p and p.strip()]


def _find_heading_positions(text: str, patterns: Optional[List[re.Pattern]] = None) -> List[int]:
    positions: set = {0}
    for pat in patterns or _HEADING_PATTERNS:
        for m in pat.finditer(text):
            positions.add(m.start())
    return sorted(positions)


def _split_by_headings(text: str, patterns: Optional[List[re.Pattern]] = None) -> List[str]:
    if not text:
        return []
    positions = _find_heading_positions(text, patterns)
    if len(positions) <= 1:
        return [text]
    sections: List[str] = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        sec = text[pos:end].strip()
        if sec:
            sections.append(sec)
    return sections


def _protect_tables(section: str) -> List[str]:
    lines = section.split("\n")
    blocks: List[str] = []
    buf: List[str] = []
    table_buf: List[str] = []
    in_table = False

    def flush_buf() -> None:
        if buf:
            blocks.append("\n".join(buf).strip())
            buf.clear()

    def flush_table() -> None:
        if table_buf:
            blocks.append("\n".join(table_buf).strip())
            table_buf.clear()

    for ln in lines:
        if _TABLE_LINE_PATTERN.match(ln):
            if not in_table:
                flush_buf()
                in_table = True
            table_buf.append(ln)
        else:
            if in_table:
                if ln.strip() == "" or re.match(r"^\s*[-:|]+\s*$", ln):
                    table_buf.append(ln)
                    continue
                flush_table()
                in_table = False
            buf.append(ln)
    flush_table()
    flush_buf()
    return [b for b in blocks if b]


def _make_splitter(
    chunk_size: int,
    chunk_overlap: int,
    separators: Optional[List[str]] = None,
) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=separators
        or [
            "\n\n\n",
            "\n\n",
            "\n",
            ". ",
            "! ",
            "? ",
            "; ",
            ": ",
            ", ",
            " ",
            "",
        ],
    )


def resolve_chunk_params(
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
) -> Tuple[int, int]:
    cfg = get_settings().rag
    cs = max(200, int(chunk_size)) if chunk_size is not None else max(200, int(getattr(cfg, "chunk_size", 1000)))
    co = max(0, int(chunk_overlap)) if chunk_overlap is not None else max(0, int(getattr(cfg, "chunk_overlap", 200)))
    if co >= cs:
        co = max(0, cs // 4)
    return cs, co


def _pack_plain(
    parts: List[str],
    *,
    chunk_size: int,
    chunk_overlap: int,
    strategy: str,
    splitter: RecursiveCharacterTextSplitter,
) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    for s_idx, part in enumerate(parts):
        head_line = part.split("\n", 1)[0].strip()[:200]
        if len(part) <= chunk_size:
            out.append(
                (
                    part,
                    {
                        "section_index": s_idx,
                        "section_heading": head_line,
                        "chunk_of_section": 0,
                        "is_table": False,
                        "chunking_strategy": strategy,
                    },
                )
            )
            continue
        for j, sub in enumerate(splitter.split_text(part)):
            sub_clean = sub.strip()
            if not sub_clean:
                continue
            out.append(
                (
                    sub_clean,
                    {
                        "section_index": s_idx,
                        "section_heading": head_line,
                        "chunk_of_section": j,
                        "is_table": False,
                        "chunking_strategy": strategy,
                    },
                )
            )
    return out


def _split_universal(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> List[Tuple[str, Dict[str, Any]]]:
    splitter = _make_splitter(chunk_size, chunk_overlap)
    pages = _split_by_page_breaks(text.strip())
    sections: List[str] = []
    for page in pages:
        sections.extend(_split_by_headings(page))
    if not sections:
        sections = [text.strip()]

    out: List[Tuple[str, Dict[str, Any]]] = []
    for s_idx, section in enumerate(sections):
        head_line = section.split("\n", 1)[0].strip()[:200]
        blocks = _protect_tables(section)
        chunk_of_sec = 0
        for block in blocks:
            is_table = block.startswith("|") and "\n|" in block
            effective_limit = chunk_size * 2 if is_table else chunk_size
            if len(block) <= effective_limit:
                out.append(
                    (
                        block,
                        {
                            "section_index": s_idx,
                            "section_heading": head_line,
                            "chunk_of_section": chunk_of_sec,
                            "is_table": is_table,
                            "chunking_strategy": "universal",
                        },
                    )
                )
                chunk_of_sec += 1
                continue
            for sub in splitter.split_text(block):
                sub_clean = sub.strip()
                if not sub_clean:
                    continue
                out.append(
                    (
                        sub_clean,
                        {
                            "section_index": s_idx,
                            "section_heading": head_line,
                            "chunk_of_section": chunk_of_sec,
                            "is_table": is_table,
                            "chunking_strategy": "universal",
                        },
                    )
                )
                chunk_of_sec += 1
    return out


def _split_fixed(text: str, *, chunk_size: int, chunk_overlap: int) -> List[Tuple[str, Dict[str, Any]]]:
    splitter = _make_splitter(chunk_size, chunk_overlap)
    return _pack_plain(
        [text.strip()], chunk_size=chunk_size, chunk_overlap=chunk_overlap, strategy="fixed", splitter=splitter
    )


def _split_markdown(text: str, *, chunk_size: int, chunk_overlap: int) -> List[Tuple[str, Dict[str, Any]]]:
    sections = _split_by_headings(text.strip(), patterns=[_MD_HEADING])
    if len(sections) <= 1:
        sections = _split_by_headings(text.strip())
    splitter = _make_splitter(chunk_size, chunk_overlap)
    return _pack_plain(
        sections, chunk_size=chunk_size, chunk_overlap=chunk_overlap, strategy="markdown", splitter=splitter
    )


def _split_separators(text: str, *, chunk_size: int, chunk_overlap: int) -> List[Tuple[str, Dict[str, Any]]]:
    splitter = _make_splitter(
        chunk_size,
        chunk_overlap,
        separators=["\n\n\n", "\n\n", "\n", ". ", " ", ""],
    )
    return _pack_plain(
        [text.strip()], chunk_size=chunk_size, chunk_overlap=chunk_overlap, strategy="separators", splitter=splitter
    )


def _split_semantic(text: str, *, chunk_size: int, chunk_overlap: int) -> List[Tuple[str, Dict[str, Any]]]:
    """Абзацный semantic-like сплит без эмбеддингов: держим абзацы целыми, пока влезают."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if not paras:
        return []
    soft = max(chunk_size, int(chunk_size * 1.25))
    chunks: List[str] = []
    buf = ""
    for p in paras:
        if not buf:
            buf = p
            continue
        if len(buf) + 2 + len(p) <= soft:
            buf = f"{buf}\n\n{p}"
        else:
            chunks.append(buf)
            # overlap: хвост предыдущего абзаца
            if chunk_overlap > 0 and len(buf) > chunk_overlap:
                tail = buf[-chunk_overlap:]
                buf = f"{tail}\n\n{p}" if len(p) < soft else p
            else:
                buf = p
    if buf:
        chunks.append(buf)
    # Доразбиваем слишком длинные
    splitter = _make_splitter(chunk_size, chunk_overlap)
    return _pack_plain(
        chunks, chunk_size=chunk_size, chunk_overlap=chunk_overlap, strategy="semantic", splitter=splitter
    )


def split_into_chunks_with_meta(
    text: str,
    *,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    chunking_strategy: Optional[str] = None,
) -> List[Tuple[str, Dict[str, Any]]]:
    """Возвращает [(chunk_text, metadata)]."""
    if not text or not text.strip():
        return []

    cfg = get_settings().rag
    chunk_size, chunk_overlap = resolve_chunk_params(chunk_size, chunk_overlap)
    strategy = normalize_chunking_strategy(chunking_strategy)
    logger.info(
        "[chunker] strategy=%s chunk_size=%s chunk_overlap=%s, длина текста: %s",
        strategy,
        chunk_size,
        chunk_overlap,
        len(text),
    )

    if strategy in {"universal", "hierarchical"}:
        out = _split_universal(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if strategy == "hierarchical":
            for _, meta in out:
                meta["chunking_strategy"] = "hierarchical"
    elif strategy == "fixed":
        out = _split_fixed(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    elif strategy == "markdown":
        out = _split_markdown(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    elif strategy == "separators":
        out = _split_separators(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    elif strategy == "semantic":
        out = _split_semantic(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    else:
        out = _split_universal(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    min_useful = max(8, int(getattr(cfg, "min_chunk_length", 40)) // 2)
    kept = [(t, m) for t, m in out if len(t) >= min_useful]
    # Не выбрасываем единственное содержимое крошечного документа.
    if kept or not out:
        out = kept
    logger.debug(
        "[chunker] strategy=%s → чанков=%s (min_useful=%s)",
        strategy,
        len(out),
        min_useful,
    )
    return out


def split_into_chunks(
    text: str,
    *,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    chunking_strategy: Optional[str] = None,
) -> List[str]:
    return [
        t
        for t, _m in split_into_chunks_with_meta(
            text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunking_strategy=chunking_strategy,
        )
    ]
