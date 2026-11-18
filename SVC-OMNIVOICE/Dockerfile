# OmniVoice: transformers>=5.3 + RTX 50xx (sm_120) → PyTorch 2.7+ / CUDA 12.8
FROM pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/tmp/hf
ENV OMNIVOICE_MODEL=/app/models/omnivoice
ENV OMNIVOICE_DEVICE=cuda:0
ENV OMNIVOICE_EAGER_LOAD=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# На всякий случай фиксируем torch cu128 (как в comfyui) — sm_120 для RTX 5080
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir torch torchaudio \
        --index-url https://download.pytorch.org/whl/cu128 \
    && pip install --no-cache-dir "transformers==5.3.0" \
    && pip install --no-cache-dir --no-deps omnivoice \
    && pip install --no-cache-dir accelerate pydub librosa soundfile webdataset tensorboardX

COPY app ./app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=5 --start-period=180s \
  CMD curl -fsS http://127.0.0.1:8000/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
