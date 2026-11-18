"""OmniVoice TTS microservice: zero-shot voice cloning."""

from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger("omnivoice-svc")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

_model = None
_load_error: Optional[str] = None
_loading = False


def _device() -> str:
    return os.getenv("OMNIVOICE_DEVICE", "cuda:0")


def get_model():
    global _model, _load_error, _loading
    if _model is not None:
        return _model
    if _load_error:
        raise RuntimeError(_load_error)
    _loading = True
    try:
        import torch
        from omnivoice import OmniVoice

        model_id = os.getenv("OMNIVOICE_MODEL", "/app/models/omnivoice")
        device = _device()
        dtype = torch.float16 if device.startswith("cuda") else torch.float32
        logger.info("Loading OmniVoice %s on %s…", model_id, device)
        # load_asr=True — Whisper для авто-транскрипта ref.wav (кэш в HF_HOME)
        asr = os.getenv("OMNIVOICE_LOAD_ASR", "1") not in {"0", "false", "False"}
        _model = OmniVoice.from_pretrained(
            model_id,
            device_map=device,
            dtype=dtype,
            load_asr=asr,
        )
        logger.info("OmniVoice loaded")
        return _model
    except Exception as e:
        _load_error = str(e)
        logger.exception("OmniVoice load failed")
        raise
    finally:
        _loading = False


def _warmup_background() -> None:
    try:
        get_model()
    except Exception:
        # Ошибка уже в _load_error / логах
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if os.getenv("OMNIVOICE_EAGER_LOAD", "1") not in {"0", "false", "False"}:
        threading.Thread(target=_warmup_background, name="omnivoice-warmup", daemon=True).start()
    yield


app = FastAPI(title="OmniVoice TTS", version="0.1.0", lifespan=lifespan)


@app.get("/v1/health")
async def health():
    if _model is not None:
        status = "healthy"
    elif _load_error:
        status = "unhealthy"
    elif _loading:
        status = "loading"
    else:
        status = "starting"
    return {
        "status": status,
        "service": "omnivoice",
        "model_loaded": _model is not None,
        "error": _load_error,
        "license_note": "Model weights CC-BY-NC — non-commercial use only",
    }


@app.post("/v1/warmup")
async def warmup():
    try:
        get_model()
        return {"ok": True, "model_loaded": True}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post("/v1/reload")
async def reload_model():
    """Выгрузить OmniVoice и загрузить веса заново с диска."""
    global _model, _load_error, _loading
    try:
        if _model is not None:
            del _model
        _model = None
        _load_error = None
        _loading = False
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        get_model()
        return {"success": True, "ok": True, "model_loaded": True}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post("/v1/synthesize")
async def synthesize(
    text: str = Form(...),
    ref_audio: UploadFile = File(...),
    ref_text: Optional[str] = Form(None),
):
    """Клонирование: text + reference wav → audio/wav 24kHz."""
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="text too long")

    raw = await ref_audio.read()
    if len(raw) < 1000:
        raise HTTPException(status_code=400, detail="ref_audio too short")

    try:
        model = get_model()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"OmniVoice unavailable: {e}") from e

    suffix = ".wav"
    name = (ref_audio.filename or "").lower()
    if name.endswith(".webm"):
        suffix = ".webm"
    elif name.endswith(".ogg"):
        suffix = ".ogg"
    elif name.endswith(".mp3"):
        suffix = ".mp3"

    ref_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(raw)
            ref_path = f.name

        kwargs = {"text": text, "ref_audio": ref_path}
        if ref_text and ref_text.strip():
            kwargs["ref_text"] = ref_text.strip()

        audio = model.generate(**kwargs)
        arr = audio[0] if isinstance(audio, (list, tuple)) else audio
        arr = np.asarray(arr, dtype=np.float32)

        import soundfile as sf

        buf = io.BytesIO()
        sf.write(buf, arr, 24000, format="WAV")
        return Response(
            content=buf.getvalue(),
            media_type="audio/wav",
            headers={"X-TTS-Engine": "omnivoice", "X-Sample-Rate": "24000"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("synthesize failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        if ref_path and os.path.exists(ref_path):
            try:
                os.remove(ref_path)
            except OSError:
                pass


@app.get("/")
async def root():
    return JSONResponse(
        {
            "service": "omnivoice",
            "docs": "/docs",
            "note": "CC-BY-NC model weights — record only your own voice with consent",
        }
    )
