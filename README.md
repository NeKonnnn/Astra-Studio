# Astra Studio

[![GitHub Stars](https://img.shields.io/github/stars/NeKonnnn/AstraChat_AI_System?style=social&label=Stars)](https://github.com/NeKonnnn/AstraChat_AI_System) [![GitHub Forks](https://img.shields.io/github/forks/NeKonnnn/AstraChat_AI_System?style=social&label=Forks)](https://github.com/NeKonnnn/AstraChat_AI_System) [![GitHub Watchers](https://img.shields.io/github/watchers/NeKonnnn/AstraChat_AI_System?style=social&label=Watchers)](https://github.com/NeKonnnn/AstraChat_AI_System) [![Repo Size](https://img.shields.io/github/repo-size/NeKonnnn/AstraChat_AI_System?label=repo%20size)](https://github.com/NeKonnnn/AstraChat_AI_System) [![Languages](https://img.shields.io/github/languages/count/NeKonnnn/AstraChat_AI_System?label=languages)](https://github.com/NeKonnnn/AstraChat_AI_System) [![Python](https://img.shields.io/github/languages/top/NeKonnnn/AstraChat_AI_System?label=python)](https://github.com/NeKonnnn/AstraChat_AI_System) [![Last Commit](https://img.shields.io/github/last-commit/NeKonnnn/AstraChat_AI_System?label=last%20commit)](https://github.com/NeKonnnn/AstraChat_AI_System)

![Astra Studio Logo](assets/AstraStudio.png)

## Что такое Astra Studio?

Astra Studio — это вэб-приложение с микросервисной архитектурой, объединяющая приватность локальных моделей, масштабируемость облачных решений и интеллект агентной архитектуры для корпоративного использования. Приложение представляет собой интеллектуального AI-ассистента с расширенными возможностями для работы с документами, голосовым взаимодействием и автоматизацией задач, построенный на основе передовых технологий машинного обучения.

## Основной функционал

### Микросервисная архитектура
- **LLM-сервис** — единый сервис для инференса больших языковых моделей
- **OCR-сервис**  — извлечение текста из изображений/сканов для дальнейшей обработки
- **RAG-сервисы**:
  - **SVC-RAG**: логика RAG: индексация, поиск, иерархия чанков, связь с БД, отдает релевантные фрагменты бэкенду
  - **SVC-RAG-MODELS**: модели для эмбеддингов и реранкинга
- **TTS-сервис**: синтез речи
- **STT-сервис**: распознавание речи 
- **Diarization**: разделение речи по спикерам
- **Backend** — основная бизнес-логика с агентной архитектурой
- **Frontend** — современный веб-интерфейс на React

### Агентная архитектура на LangGraph
- **Агент-оркестратор** — автоматический выбор и координация специализированных агентов
- **Специализированные агенты**:
  - DocumentAgent — работа с загруженными документами
  - MCPAgent — интеграция с внешними сервисами
  - LangGraphAgent — планирование и выполнение сложных задач
  - PrompEnchancementAgent - создание улучшенных промпртов
  - SummarizationAgent - суммаризация документов, текстов
- **Планирование задач** — многошаговое планирование сложных задач

### Мультимодальность
- **Транскрибация речи**: WhisperX
- **Диаризация спикеров**: автоматическое разделение речи по говорящим
- **Синтез речи**: Silero TTS с несколькими голосами
- **Обработка документов**: PDF, DOCX, TXT, Markdown

### RAG система (Retrieval-Augmented Generation)
Уникальная система с возможностью гибкой настройки стратегий поиска и методов чанкования
**Статегии поиска:**
- **Векторный** 
- **Лексический** 
- **Гибридный (векторный + лексический)**
- **Графовый**

**Методы чанкования:**
- **Иерархическое** 
- **Фиксированное**
- **По разметке**
- **По разделителям**
- **Семантическое**

### Хранение данных
- **MongoDB** — хранение диалогов и истории общения
- **PostgreSQL + pgvector** — векторная база данных для RAG
- **MinIO** — хранение временных файлов и документов

### Реальное время
- **WebSocket** — потоковая передача ответов
- **Интерактивный чат** — мгновенные обновления интерфейса

---

## Установка Docker

Перед началом работы с Astra Studio необходимо установить и запустить Docker на вашей системе.

### Установка Docker для Windows

1. Скачайте **Docker Desktop для Windows** с официального сайта:
   - https://www.docker.com/products/docker-desktop/

2. Запустите установщик и следуйте инструкциям мастера установки.

3. После установки запустите Docker Desktop и дождитесь полной загрузки (иконка Docker в системном трее должна быть активна).

4. Убедитесь, что Docker запущен, выполнив в командной строке или PowerShell:
   ```bash
   docker --version
   docker-compose --version
   ```

### Установка Docker для Linux

#### Ubuntu/Debian

```bash
# Обновляем список пакетов
sudo apt-get update

# Устанавливаем необходимые пакеты
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# Добавляем официальный GPG ключ Docker
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# Настраиваем репозиторий
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Устанавливаем Docker Engine и Docker Compose
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Добавляем текущего пользователя в группу docker (чтобы не использовать sudo)
sudo usermod -aG docker $USER

# Перезапускаем сессию или выполняем:
newgrp docker
```

#### CentOS/RHEL/Fedora

```bash
# Устанавливаем необходимые пакеты
sudo yum install -y yum-utils

# Добавляем репозиторий Docker
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

# Устанавливаем Docker Engine и Docker Compose
sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Запускаем Docker
sudo systemctl start docker
sudo systemctl enable docker

# Добавляем текущего пользователя в группу docker
sudo usermod -aG docker $USER
newgrp docker
```

#### Проверка установки

После установки проверьте, что Docker работает:

```bash
docker --version
docker-compose --version
sudo systemctl status docker  # Для Linux
```

> **Важно**
> 
> Убедитесь, что Docker запущен и работает перед выполнением команд установки Astra Studio. В Windows Docker Desktop должен быть запущен, в Linux — служба Docker должна быть активна.

---

## Быстрый старт с Docker

> **Примечание**
> 
> Для некоторых Docker-окружений могут потребоваться дополнительные настройки. Если вы столкнетесь с проблемами подключения, обратитесь к подробной документации проекта.

> **Важно**
> 
> При использовании Docker для установки Astra Studio убедитесь, что вы правильно настроили монтирование томов для моделей и данных. Это критически важно для корректной работы системы и предотвращения потери данных.

> **Совет**
> 
> Для использования Astra Studio с поддержкой GPU (CUDA) убедитесь, что на вашей системе установлен Nvidia CUDA container toolkit. Это необходимо для ускорения работы больших моделей.

## Подготовка к установке

После клонирования репозитория перейдите в директорию проекта и создайте необходимые папки:

```bash
# Перейдите в директорию проекта (замените путь на ваш)
cd /path/to/Astra-Studio

# Создайте необходимые папки
mkdir venv_312 models diarize_models silero_models whisperx_models
```

Эти папки используются для хранения:
- `venv_312` — виртуальное окружение Python (опционально, если не используете Docker)
- `models` — LLM модели и модели RAG (модель эмбеддингов, реранкер)
- `diarize_models` — модели для диаризации спикеров
- `silero_models` — модели Silero для синтеза речи
- `whisperx_models` — модели WhisperX для точной транскрипции

## Установка с конфигурацией по умолчанию

> **Важно: Docker должен быть запущен!**
> 
> Перед выполнением команд установки убедитесь, что Docker запущен и работает:
> - **Windows**: Docker Desktop должен быть запущен (проверьте иконку в системном трее)
> - **Linux**: Служба Docker должна быть активна (`sudo systemctl status docker`)

### Полная установка (все сервисы)

Для запуска всех компонентов системы (LLM-сервис, звуковые сервисы, Backend, Frontend, базы данных) убедитесь, что вы находитесь в корневой директории проекта и выполните:

```bash
# Перейдите в директорию проекта (если еще не в ней)
cd /path/to/AstrraChat_AI

# Запустите все сервисы
docker-compose up -d
```

Эта команда запустит микросервисную связку (см. `docker-compose.yml`):
- **LLM-сервис** (`llm-service`) — порт хоста `8002` (внутри контейнера `8000`)
- **STT** (`stt-service`, WhisperX) — `8001`
- **Диаризация** (`diarization-service`) — `8003`
- **OCR** (`ocr-service`) — `8004`
- **TTS** (`tts-service`, Silero) — `8005`
- **Backend** (`astrachat-backend`, API + Socket.IO) — `8000`
- **Frontend** (`astrachat-frontend`) — `3000`
- **RAG: модели** (`rag-models`) — `8010`
- **RAG: логика** (`svc-rag`, парсинг, pgvector, BM25) — `8011`
- **MongoDB** — `27017`
- **PostgreSQL** (pgvector) — `5432`
- **MinIO** — `9000` (API) и `9001` (Console)

Опционально (профиль `production`): **Nginx** — `80` и `443` (см. `docker compose --profile production up -d`).

После установки вы сможете получить доступ к Astra Studio по адресу: **http://localhost:3000**

### Поэтапная установка

Если вы хотите запускать сервисы отдельно, убедитесь, что вы находитесь в корневой директории проекта:

```bash
# Перейдите в директорию проекта (если еще не в ней)
cd /path/to/Astra-Studio
```

#### 1. Сначала запустите LLM-сервис:

```bash
docker-compose -f docker-compose-llm-svc.yml up -d
```

#### 2. Затем запустите Backend и базы данных:

```bash
docker-compose -f docker-compose-backend.yml up -d
```

#### 3. И наконец, запустите Frontend:

```bash
docker-compose -f docker-compose-frontend.yml up -d
```

## Установка с поддержкой GPU

Для использования GPU-ускорения при работе с большими моделями:

```bash
docker-compose up -d
```

Убедитесь, что в вашем `docker-compose.yml` настроены параметры GPU (если требуется). Для Linux/WSL необходимо установить Nvidia CUDA container toolkit.

## Настройка переменных окружения

Создайте файл `.env` в корне проекта на основе `MAIN.env` и настройте следующие параметры:

### LLM-сервис
- `LLM_MODEL_PATH` — путь к LLM модели
- `LLM_MODEL_NAME` — имя модели
- `SILERO_MODELS_DIR` — директория с моделями Silero
- `WHISPERX_MODELS_DIR` — директория с моделями WhisperX

### Базы данных
- `MONGODB_USER` — пользователь MongoDB
- `MONGODB_PASSWORD` — пароль MongoDB
- `POSTGRES_USER` — пользователь PostgreSQL
- `POSTGRES_PASSWORD` — пароль PostgreSQL
- `MINIO_ROOT_USER` — пользователь MinIO
- `MINIO_ROOT_PASSWORD` — пароль MinIO

### Сеть
- `LLM_SVC_INTERNAL_URL` — внутренний URL LLM-сервиса (по умолчанию: `http://llm-svc:8000`)
- `USE_LLM_SVC` — использование LLM-сервиса (по умолчанию: `true`)

## Проверка работы

После установки проверьте статус контейнеров:

```bash
docker-compose ps
```

Все сервисы должны быть в состоянии `Up` и `healthy`.

## Доступ к сервисам

Основной вход пользователя:
- **Frontend**: http://localhost:3000
- **Backend API и WebSocket**: http://localhost:8000

Микросервисы (для отладки и прямых проверок; в работе приложения backend ходит к ним по именам контейнеров):
- **LLM-сервис**: http://localhost:8002
- **STT**: http://localhost:8001
- **Диаризация**: http://localhost:8003
- **OCR**: http://localhost:8004
- **TTS**: http://localhost:8005
- **RAG (модели)**: http://localhost:8010
- **RAG (SVC-RAG API)**: http://localhost:8011

Хранилища и БД:
- **MongoDB**: `localhost:27017`
- **PostgreSQL**: `localhost:5432`
- **MinIO API**: http://localhost:9000
- **MinIO Console**: http://localhost:9001

## Запуск тестов

Юнит-тесты не требуют запущенных контейнеров, баз данных и моделей: внешние сервисы в них замоканы.

Установите инструменты для тестов и зависимости того сервиса, который проверяете:

```bash
pip install -r requirements-dev.txt        # pytest, pytest-asyncio, pytest-cov, httpx
pip install -r backend/requirements.txt    # зависимости backend (для тестов backend)
```

У каждого сервиса свой пакет `app`, поэтому наборы запускаются **по отдельности**, каждый из своей директории:

| Набор | Команда |
|---|---|
| backend | `pytest` (из корня репозитория) |
| SVC-RAG | `cd SVC-RAG && pytest` |
| SVC-RAG-MODELS | `cd SVC-RAG-MODELS && pytest` |
| SVC-OCR | `cd SVC-OCR && pytest` |
| SVC-SPEECH-DIAR / -RECG / -SYNT | `cd SVC-SPEECH-<имя> && pytest` |
| llm-svc | `cd llm-svc && pytest` |
| frontend | `cd frontend && npm install && CI=true npm test -- --watchAll=false` |

Покрытие backend:

```bash
pytest --cov=backend --cov-report=term-missing
```

Что важно знать:

- Конфигурация pytest лежит в `pytest.ini` (backend) и `<сервис>/pytest.ini`. Для backend `backend/tests/conftest.py` задаёт минимальное тестовое окружение (`USE_LLM_SVC`, JWT-переменные с синтетическими значениями), поэтому реальные `.env` для тестов не нужны.
- В llm-svc нативный `llama_cpp` в тестах подменяется заглушкой (`llm-svc/conftest.py`), устанавливать его не нужно.
- Тесты, помеченные `xfail` или `skip`, описывают известные дефекты кода: причина указана в самом тесте. После исправления дефекта пометку нужно убрать.
- Тесты общих модулей OCR и SPEECH-сервисов (`core/security`, `utils/downloader`) одинаковы, потому что сами модули в этих сервисах идентичны. При изменении теста обновляйте копии во всех четырёх сервисах.

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=NeKonnnn/Astra-Studio&type=date&legend=bottom-right)](https://www.star-history.com/?repos=NeKonnnn%2FAstra-Studio&type=date&legend=bottom-right)

---

**Приятного использования Astra Studio!**
