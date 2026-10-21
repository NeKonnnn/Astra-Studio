# Dockerfile для AstraChat Backend
FROM ubuntu:22.04

# Установка Python и системных зависимостей
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    build-essential \
    cmake \
    git \
    wget \
    curl \
    libsndfile1 \
    libsndfile1-dev \
    portaudio19-dev \
    ffmpeg \
    libasound2-dev \
    libportaudio2 \
    libportaudiocpp0 \
    espeak \
    libespeak-dev \
    libldap2-dev \
    libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

# Создание символической ссыл ки для python
RUN ln -s /usr/bin/python3 /usr/bin/python

# Обновление pip, setuptools и wheel для избежания конфликтов зависимостей
RUN python3 -m pip install --upgrade pip setuptools wheel

# Установка рабочей директории
WORKDIR /app

# Копирование requirements.txt
COPY requirements.txt .

# Установка Python зависимостей (python3 -m pip гарантирует правильный интерпретатор)
RUN python3 -m pip install --no-cache-dir --upgrade-strategy only-if-needed --timeout 120 --retries 5 -r requirements.txt

# Копирование исходного кода с явным указанием прав доступа
COPY --chown=root:root . /app/

# Создание только необходимых директорий для backend
RUN mkdir -p /app/memory /app/logs /app/uploads /tmp/astrachat

# Установка переменных окружения
ENV PYTHONPATH=/app
ENV MODEL_PATH=/app/models/model.gguf

# Создаем символическую ссылку для совместимости с импортами "from backend.xxx"
RUN ln -sf /app /app/backend

# Открытие порта
EXPOSE 8000

# Команда запуска - новый backend с микросервисной архитектурой
CMD ["python", "main.py"]
