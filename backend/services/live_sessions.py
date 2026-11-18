"""Бизнес-логика «Живых сессий»: chunk STT, финализация через WhisperX+диаризацию, бриф."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.database import init_db
from backend.database.postgresql.live_session_models import LiveSessionCreate
from backend.services.live_session_brief import (
    build_final_prompt,
    build_part_prompt,
    split_transcript_parts,
)
from backend.services.live_session_echo import is_echo_text, mark_echo_segments
from backend.settings.logging import get_logger

logger = get_logger(__name__)


def _repo():
    repo = getattr(init_db, "live_session_repo", None)
    if repo is None:
        raise RuntimeError("live_session_repo не инициализирован")
    return repo


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clock(ms: int) -> str:
    s = max(0, int(ms) // 1000)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def normalize_speaker_label(raw: Optional[str], *, track: Optional[str] = None) -> str:
    """Схлопывает SPEAKER_SPEAKER_00 → SPEAKER_00; fallback по track."""
    s = (raw or "").strip()
    if not s:
        return "Вы" if track == "local" else ("Собеседник" if track == "remote" else "SPEAKER_00")
    upper = s.upper()
    while upper.startswith("SPEAKER_SPEAKER_"):
        s = "SPEAKER_" + s[len("SPEAKER_SPEAKER_") :]
        upper = s.upper()
    return s


def format_transcript_lines(segments: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for seg in segments:
        if seg.get("is_echo"):
            continue
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = normalize_speaker_label(
            seg.get("speaker"), track=seg.get("track")
        ) or (
            "Вы" if seg.get("track") == "local" else "Собеседник"
        )
        lines.append(f"[{_clock(int(seg.get('start_ms') or 0))}] {speaker}: {text}")
    return "\n".join(lines)


async def create_session(user_id: str, payload: LiveSessionCreate) -> Dict[str, Any]:
    return await _repo().create_session(user_id, payload)


async def start_session(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    session = await _repo().get_session(user_id, session_id)
    if not session:
        return None
    if session["status"] in ("live", "paused"):
        return session
    now = _utcnow()
    extra: Dict[str, Any] = {"started_at": now, "brief_status": "idle"}
    if session["status"] in ("ready", "processing") and session.get("ended_at"):
        # append / resume: не затираем started_at
        extra = {"brief_status": "idle"}
        if not session.get("started_at"):
            extra["started_at"] = now
    return await _repo().set_status(user_id, session_id, "live", extra=extra)


async def pause_session(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    session = await _repo().get_session(user_id, session_id)
    if not session or session["status"] != "live":
        return session
    return await _repo().set_status(
        user_id, session_id, "paused", extra={"paused_at": _utcnow()}
    )


async def resume_session(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    session = await _repo().get_session(user_id, session_id)
    if not session or session["status"] != "paused":
        return session
    return await _repo().set_status(
        user_id, session_id, "live", extra={"paused_at": None}
    )


def _jargon_prompt(jargon: Optional[List[str]]) -> str:
    terms = [str(x).strip() for x in (jargon or []) if str(x).strip()]
    if not terms:
        return ""
    return "Словарь терминов встречи: " + ", ".join(terms[:80]) + "."


async def ingest_audio_chunk(
    user_id: str,
    session_id: str,
    *,
    track: str,
    offset_ms: int,
    audio_bytes: bytes,
    filename: str = "chunk.wav",
    language: str = "auto",
    jargon: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Живой кусок → WhisperX (batch API) → сегмент в PostgreSQL."""
    session = await _repo().get_session(user_id, session_id)
    if not session:
        raise LookupError("session_not_found")
    if session["status"] not in ("live", "paused", "draft"):
        raise RuntimeError("session_not_recording")

    from backend import llm_client as llm_client_mod

    prefs = await _repo().get_prefs(user_id)
    terms = jargon if jargon is not None else prefs.get("jargon") or []
    echo_on = bool(prefs.get("echo_detect", True))

    loop = asyncio.get_running_loop()
    lang = language or session.get("language") or "auto"
    prompt = _jargon_prompt(terms)

    def _transcribe() -> str:
        return llm_client_mod.transcribe_audio_whisperx_llm_svc(
            audio_bytes,
            filename=filename,
            language=lang,
            initial_prompt=prompt,
        )

    try:
        raw = await loop.run_in_executor(None, _transcribe)
    except Exception:
        logger.exception("live chunk STT failed session=%s track=%s", session_id, track)
        raise

    text = (raw or "").strip() if isinstance(raw, str) else ""
    duration_ms = max(500, int(max(0, len(audio_bytes) - 44) / 32))

    start_ms = max(0, int(offset_ms))
    end_ms = start_ms + duration_ms
    speaker = "Вы" if track == "local" else "Собеседник"
    is_echo = False
    if text and echo_on and track == "local":
        recent_remote = [
            (s.get("text") or "")
            for s in (session.get("segments") or [])
            if s.get("track") == "remote"
            and not s.get("is_echo")
            and abs(int(s.get("start_ms") or 0) - start_ms) <= 12_000
        ]
        is_echo = is_echo_text(text, recent_remote)

    segment = None
    if text:
        segment = await _repo().add_segment(
            user_id,
            session_id,
            track=track if track in ("local", "remote", "mixed") else "mixed",
            start_ms=start_ms,
            end_ms=end_ms,
            text=text,
            speaker=speaker,
            is_echo=is_echo,
            confidence=0.85 if is_echo else None,
        )
    return {
        "text": text,
        "segment": segment,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "track": track,
        "is_echo": is_echo,
    }


async def _store_audio(
    user_id: str, session_id: str, track: str, data: bytes, content_type: str
) -> Optional[str]:
    from backend.app_state import minio_client

    if not minio_client or not data:
        return None
    ext = "webm"
    if "wav" in content_type:
        ext = "wav"
    elif "ogg" in content_type:
        ext = "ogg"
    elif "mp4" in content_type or "m4a" in content_type:
        ext = "m4a"
    key = f"live_session_{user_id}_{session_id}_{track}_{int(_utcnow().timestamp())}.{ext}"
    await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: minio_client.upload_file(
            data, key, content_type or "application/octet-stream"
        ),
    )
    return key


async def finalize_session(
    user_id: str,
    session_id: str,
    *,
    local_audio: Optional[Tuple[bytes, str]] = None,
    remote_audio: Optional[Tuple[bytes, str]] = None,
    mixed_audio: Optional[Tuple[bytes, str]] = None,
    duration_ms: Optional[int] = None,
    generate_brief: bool = True,
) -> Dict[str, Any]:
    """Стоп записи: сохранить аудио, уточнить стенограмму диаризацией, построить бриф."""
    session = await _repo().get_session(user_id, session_id)
    if not session:
        raise LookupError("session_not_found")

    await _repo().set_status(
        user_id,
        session_id,
        "processing",
        extra={
            "ended_at": _utcnow(),
            "duration_ms": int(duration_ms or session.get("duration_ms") or 0),
            "brief_status": "waiting",
        },
    )

    audio_keys: Dict[str, Optional[str]] = {}
    for track, payload in (
        ("local", local_audio),
        ("remote", remote_audio),
        ("mixed", mixed_audio),
    ):
        if not payload:
            continue
        data, ctype = payload
        try:
            audio_keys[f"{track}_audio_key"] = await _store_audio(
                user_id, session_id, track, data, ctype
            )
        except Exception:
            logger.exception("failed to store %s audio for %s", track, session_id)

    if audio_keys:
        await _repo().set_status(
            user_id, session_id, "processing", extra=audio_keys
        )

    # Полный архив надёжнее live-чанков: если стенограмма короче записи —
    # обязательно перегоняем файл через WhisperX(+diarization).
    current = await _repo().get_session(user_id, session_id, include_trashed=True)
    segs = [
        s
        for s in (current or {}).get("segments") or []
        if (s.get("text") or "").strip() and not s.get("is_echo")
    ]
    duration = int(duration_ms or session.get("duration_ms") or 0)
    text_len = sum(len((s.get("text") or "").strip()) for s in segs)
    # эвристика: ~4+ символа на секунду речи; иначе считаем стенограмму дырявой
    sparse = duration >= 8000 and (len(segs) < max(2, duration // 6000) or text_len < duration // 80)
    tracks = {s.get("track") for s in segs}
    rich_dual = "local" in tracks and "remote" in tracks and len(segs) >= 3 and not sparse

    if not rich_dual:
        refine_bytes: Optional[bytes] = None
        refine_name = "session.wav"
        # Предпочитаем local (mic), затем mixed/remote — для mic-only сессий
        if local_audio and local_audio[0]:
            refine_bytes, ctype = local_audio
            refine_name = "local.webm" if "webm" in (ctype or "") else "local.wav"
        elif mixed_audio and mixed_audio[0]:
            refine_bytes, ctype = mixed_audio
            refine_name = "session.webm" if "webm" in (ctype or "") else "session.wav"
        elif remote_audio and remote_audio[0]:
            refine_bytes, ctype = remote_audio
            refine_name = "remote.webm" if "webm" in (ctype or "") else "remote.wav"

        if refine_bytes:
            await _refine_with_diarization(
                user_id,
                session_id,
                refine_bytes,
                refine_name,
                session.get("language") or "auto",
            )

    # Финальный echo-sweep по всей стенограмме
    prefs = await _repo().get_prefs(user_id)
    if prefs.get("echo_detect", True):
        cur = await _repo().get_session(user_id, session_id, include_trashed=True)
        segs_all = (cur or {}).get("segments") or []
        marked = mark_echo_segments(segs_all)
        if any(bool(a.get("is_echo")) != bool(b.get("is_echo")) for a, b in zip(segs_all, marked)):
            await _repo().replace_segments(user_id, session_id, marked)

    brief_md = ""
    brief_status = "empty"
    title = session.get("title") or "Сессия"
    if generate_brief:
        brief_md, brief_status, auto_title = await _build_brief(user_id, session_id)
        if auto_title and (not session.get("title") or session.get("title") == "Новая сессия"):
            title = auto_title

    result = await _repo().set_status(
        user_id,
        session_id,
        "ready",
        extra={
            "brief_status": brief_status,
            "brief_markdown": brief_md,
            "title": title,
            **audio_keys,
        },
    )
    await _repo().refresh_search_document(user_id, session_id)
    return result or {}


async def _refine_with_diarization(
    user_id: str,
    session_id: str,
    audio_bytes: bytes,
    filename: str,
    language: str,
) -> None:
    from backend import llm_client as llm_client_mod

    loop = asyncio.get_running_loop()

    def _run() -> Dict[str, Any]:
        return llm_client_mod.transcribe_with_diarization_llm_svc(
            audio_bytes,
            filename=filename,
            language=language or "auto",
        )

    try:
        result = await loop.run_in_executor(None, _run)
    except Exception:
        logger.exception("diarization refine failed for %s", session_id)
        return

    if not isinstance(result, dict) or not result.get("success"):
        return
    segs_in = result.get("segments") or []
    if not segs_in:
        return
    segments: List[Dict[str, Any]] = []
    for s in segs_in:
        if not isinstance(s, dict):
            continue
        text = (s.get("text") or "").strip()
        if not text:
            continue
        start = float(s.get("start") or 0)
        end = float(s.get("end") or start)
        speaker = normalize_speaker_label(str(s.get("speaker") or "SPEAKER_00"))
        segments.append(
            {
                "track": "mixed",
                "start_ms": int(start * 1000),
                "end_ms": int(end * 1000),
                "text": text,
                "speaker": speaker,
                "is_echo": False,
            }
        )
    if segments:
        await _repo().replace_segments(user_id, session_id, segments)


async def rename_speakers(
    user_id: str, session_id: str, mapping: Dict[str, str]
) -> Optional[Dict[str, Any]]:
    return await _repo().rename_speakers(user_id, session_id, mapping)


async def _llm_text(system: str, user: str, *, max_tokens: int = 2048) -> str:
    """Вызов LLM для брифа.

    ``app_state.ask_agent`` — синхронная ``agent_llm_svc.ask_agent``.
    Раньше код искал async ``.generate_response`` и молча возвращал "" —
    бриф после записи никогда не появлялся.
    """
    from backend.app_state import ask_agent

    if ask_agent is None:
        logger.warning("brief: ask_agent is None — LLM недоступен")
        return ""

    # Async mock / LLMService в тестах
    gen = getattr(ask_agent, "generate_response", None)
    if gen is not None and asyncio.iscoroutinefunction(gen):
        try:
            out = await gen(
                user,
                system_prompt=system,
                temperature=0.3,
                max_tokens=max_tokens,
            )
            return (out or "").strip() if isinstance(out, str) else str(out or "").strip()
        except Exception:
            logger.exception("brief LLM (async) failed")
            return ""

    if not callable(ask_agent):
        logger.warning("brief: ask_agent не callable: %s", type(ask_agent))
        return ""

    def _call_sync() -> str:
        raw = ask_agent(
            user,
            history=None,
            system_prompt=system,
            temperature=0.3,
            max_tokens=max_tokens,
            streaming=False,
            service_call=True,
        )
        return (raw or "").strip() if isinstance(raw, str) else str(raw or "").strip()

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _call_sync)
    except Exception:
        logger.exception("brief LLM call failed")
        return ""


async def _build_brief(
    user_id: str, session_id: str, instruction: Optional[str] = None
) -> Tuple[str, str, Optional[str]]:
    session = await _repo().get_session(user_id, session_id, include_trashed=True)
    if not session:
        return "", "failed", None
    transcript = format_transcript_lines(session.get("segments") or [])
    if not transcript.strip():
        return "", "empty", None

    await _repo().set_status(
        user_id, session_id, session["status"], extra={"brief_status": "writing"}
    )

    participants = ", ".join(session.get("participants") or []) or "не указаны"
    source = session.get("source_app") or "неизвестно"
    parts = split_transcript_parts(transcript, max_chars=9000)
    part_summaries: List[str] = []

    try:
        if len(parts) > 1:
            for i, part in enumerate(parts, start=1):
                sys_p, user_p = build_part_prompt(
                    part,
                    index=i,
                    total=len(parts),
                    participants=participants,
                    instruction=instruction,
                )
                summary = await _llm_text(sys_p, user_p, max_tokens=900)
                if summary:
                    part_summaries.append(summary)

        sys_f, user_f = build_final_prompt(
            participants=participants,
            source_app=source,
            instruction=instruction,
            part_summaries=part_summaries if len(part_summaries) > 1 else None,
            full_transcript=transcript if len(parts) <= 1 else None,
        )
        text = await _llm_text(sys_f, user_f, max_tokens=2500)
    except Exception:
        logger.exception("brief generation failed for %s", session_id)
        return "", "failed", None

    if not text:
        return "", "empty", None

    auto_title = _suggest_title(text, session.get("title"))
    return text, "ready", auto_title


def _suggest_title(brief_md: str, current: Optional[str]) -> Optional[str]:
    if current and current not in ("Новая сессия", "Без названия", ""):
        return None
    skip = {
        "суть",
        "кратко",
        "решения",
        "задачи",
        "открытые вопросы",
        "риски / блокеры",
        "риски",
        "блокеры",
        "фрагмент",
    }
    for line in brief_md.splitlines():
        clean = re.sub(r"^#+\s*", "", line).strip()
        if clean and clean.lower() not in skip:
            return clean[:80]
    return None


async def regenerate_brief(
    user_id: str, session_id: str, instruction: Optional[str] = None
) -> Dict[str, Any]:
    session = await _repo().get_session(user_id, session_id)
    if not session:
        raise LookupError("session_not_found")
    brief_md, brief_status, auto_title = await _build_brief(
        user_id, session_id, instruction=instruction
    )
    extra: Dict[str, Any] = {
        "brief_status": brief_status,
        "brief_markdown": brief_md,
    }
    if auto_title:
        extra["title"] = auto_title
    result = await _repo().set_status(user_id, session_id, "ready", extra=extra)
    await _repo().refresh_search_document(user_id, session_id)
    return result or {}
