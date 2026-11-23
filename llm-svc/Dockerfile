FROM ubuntu:22.04

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC

RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN apt-get update && apt-get install -y \
    software-properties-common \
    tzdata \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3.11-distutils \
    python3-pip \
    build-essential \
    cmake \
    gcc \
    g++ \
    curl \
    libopenblas-dev \
    git \
    pkg-config \
    ffmpeg \
    wget \
    unzip \
    libsndfile1 \
    libsndfile1-dev \
    libsox-dev \
    sox \
    libldap2-dev \
    libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.11 /usr/bin/python && \
    ln -sf /usr/bin/python3.11 /usr/bin/python3

# mcpu=native убран — Docker не знает архитектуру хоста при сборке
ENV CMAKE_ARGS="-DGGML_METAL=OFF -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS" \
    CFLAGS="-pthread" \
    CXXFLAGS="-pthread"

RUN python3.11 -m pip install --upgrade pip

# Шаг 1: PyTorch CPU
RUN python3.11 -m pip install --no-cache-dir --timeout 120 --retries 5 \
    torch==2.1.2 \
    torchaudio==2.1.2

# Шаг 2: llama-cpp-python — СБОРКА ИЗ ИСХОДНИКОВ (гарантированно glibc)
ENV CMAKE_ARGS="-DGGML_METAL=OFF -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS"
RUN python3.11 -m pip install --no-cache-dir --no-binary llama-cpp-python \
    llama-cpp-python

# Шаг 3: остальные зависимости
COPY requirements.txt .
RUN python3.11 -m pip install --no-cache-dir --prefer-binary --timeout 120 --retries 5 \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn \
    -r requirements.txt

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=8 \
    OPENBLAS_NUM_THREADS=8 \
    MKL_NUM_THREADS=8 \
    VECLIB_MAXIMUM_THREADS=8 \
    NUMEXPR_NUM_THREADS=8

COPY app/ ./app/
COPY config/ ./config/

RUN mkdir -p /app/models

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
RUN chmod -R 755 /app/models

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]