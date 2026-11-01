# AstraStudio

[![GitHub Stars](https://img.shields.io/github/stars/NeKonnnn/AstraChat_AI_System?style=social&label=Stars)](https://github.com/NeKonnnn/AstraChat_AI_System) [![GitHub Forks](https://img.shields.io/github/forks/NeKonnnn/AstraChat_AI_System?style=social&label=Forks)](https://github.com/NeKonnnn/AstraChat_AI_System) [![GitHub Watchers](https://img.shields.io/github/watchers/NeKonnnn/AstraChat_AI_System?style=social&label=Watchers)](https://github.com/NeKonnnn/AstraChat_AI_System) [![Repo Size](https://img.shields.io/github/repo-size/NeKonnnn/AstraChat_AI_System?label=repo%20size)](https://github.com/NeKonnnn/AstraChat_AI_System) [![Languages](https://img.shields.io/github/languages/count/NeKonnnn/AstraChat_AI_System?label=languages)](https://github.com/NeKonnnn/AstraChat_AI_System) [![Python](https://img.shields.io/github/languages/top/NeKonnnn/AstraChat_AI_System?label=python)](https://github.com/NeKonnnn/AstraChat_AI_System) [![Last Commit](https://img.shields.io/github/last-commit/NeKonnnn/AstraChat_AI_System?label=last%20commit)](https://github.com/NeKonnnn/AstraChat_AI_System)

![Astra Studio Logo](assets/Astra_logo.png)

## Что такое AstraStudio?

AstraStudio — корпоративная AI-платформа с микросервисной архитектурой: локальные и облачные модели, агенты, RAG по документам, голос, OCR и генерация изображений. Данные и модели можно держать on-prem; вход пользователей — через LDAP/AD или Keycloak SSO (бесшовный корпоративный логин).

---

## Основной функционал

### Микросервисная архитектура
- **llm-service** — LLM (llama.cpp / vLLM)
- **Backend** — бизнес-логика, агенты, auth, WebSocket
- **Frontend** — веб-интерфейс (React)
- **RAG** — `svc-rag` + `rag-models` (поиск, эмбеддинги, реранк)
- **Speech / OCR / ComfyUI** — STT, TTS, диаризация, OCR, генерация картинок
- **Инфраструктура** — MongoDB, PostgreSQL+pgvector, MinIO, MCP websearch, nginx (опционально)

### Агентная архитектура на LangGraph
- **Агент-оркестратор** — выбор и координация специализированных агентов
- **Специализированные агенты**: DocumentAgent, WebSearchAgent, CalculationAgent, MemoryAgent, MCPAgent, LangGraphAgent
- **Планирование задач** — многошаговое выполнение сложных сценариев

### Мультимодальность
- **Транскрипция**: WhisperX (+ опционально Vosk в отдельных сценариях)
- **Диаризация**: pyannote — разделение по спикерам
- **Синтез речи**: Silero TTS
- **OCR**: Surya
- **Документы**: PDF, DOCX, TXT, Markdown и др.
- **Изображения**: ComfyUI (Flux / SD)

### RAG (Retrieval-Augmented Generation)
- Векторный поиск на PostgreSQL + pgvector
- Гибридный поиск, реранкинг, иерархическое индексирование для больших документов
- Несколько баз знаний / entity-RAG (агенты, проекты, память)

### Хранение и real-time
- **MongoDB** — диалоги и история
- **PostgreSQL + pgvector** — RAG
- **MinIO** — файлы и документы
- **WebSocket** — стриминг ответов в чате

### Корпоративный вход (LDAP и Keycloak SSO)
- **LDAP / Active Directory** — логин доменной УЗ, группы AD → роли платформы (admin / агентные роли)
- **Keycloak (OIDC SSO)** — бесшовный вход через корпоративный IdP без отдельного пароля в AstraStudio
- Можно включить один или оба механизма; секреты и группы мапятся на роли UI/API
- Без LDAP/Keycloak доступны mock-учётки для локальной разработки: `admin`/`admin123`, `user`/`user123`, `test`/`test123`

---

## Сервисы платформы

| Сервис (compose) | Назначение | Хост-порт | Конфиг |
|------------------|------------|-----------|--------|
| `llm-service` | LLM inference (GGUF / HF+vLLM) | `8002` | `llm-svc/config/config.yml` |
| `stt-service` | Распознавание речи (WhisperX) | `8001` | `SVC-SPEECH-RECG/config/config.yml` |
| `diarization-service` | Диаризация спикеров | `8003` | `SVC-SPEECH-DIAR/config/config.yml` |
| `ocr-service` | OCR (Surya) | `8004` | `SVC-OCR/config/config.yml` |
| `tts-service` | Синтез речи (Silero) | `8005` | `SVC-SPEECH-SYNT/config/config.yml` |
| `rag-models` | Эмбеддинги + реранкер | `8010` | `SVC-RAG-MODELS/config/config.yml` |
| `svc-rag` | Логика RAG (парсинг, pgvector, BM25) | `8011` | `SVC-RAG/config/config.yml` |
| `comfyui` | Генерация изображений | `8188` | workflow + `models/comfyui/` |
| `mcp-websearch` | MCP веб-поиск для агентов | `8013` | env в compose |
| `astrachat-backend` | API, auth, агенты, оркестрация | `8000` | `.env` + `backend/` |
| `astrachat-frontend` | UI | `3000` | build-args / `frontend/.env` |
| `mongodb` | Диалоги | `27017` | `.env` |
| `postgresql` | RAG / метаданные | `5432` | `.env` |
| `minio` | Object storage | `9000` / `9001` | `.env` |
| `nginx` | Reverse proxy (профиль `production`) | `80` / `443` | `nginx.conf` |

Backend ходит к сервисам по внутренним именам сети `astrachat-network` (`SVC_LLM_URL`, `SVC_STT_URL`, `SVC_RAG_URL` и т.д.).

---

## Модельные сервисы: CPU и GPU

### Общие правила
1. Веса кладите в каталоги на хосте (см. таблицу ниже) — они монтируются в контейнеры.
2. Режим CPU/GPU задаётся в YAML-конфиге сервиса (`device`, `gpu_layers`) и/или Dockerfile / compose override.
3. Для GPU на Linux/WSL нужен [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) и драйвер NVIDIA.

### Рекомендуемые модели и откуда скачать

| Сервис | Рекомендация | Куда положить | Источник |
|--------|--------------|---------------|----------|
| **LLM (лёгкая, CPU/GPU)** | Qwen3.5 4B GGUF Q8 (или аналог Qwen3 Instruct AWQ/GGUF) | `models/llm/` | [Hugging Face — Qwen](https://huggingface.co/Qwen), GGUF-кванты (напр. Unsloth / bartowski) |
| **LLM (качество, GPU)** | Qwen3-VL / Qwen3-Omni AWQ 30B-A3B или Qwen 27B FP8 (vLLM в K8s) | `models/llm/<папка>` | [Qwen3-VL](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct-AWQ), [Qwen3-Omni](https://huggingface.co/Qwen) |
| **Эмбеддинги (дефолт)** | **FRIDA** (dim 1536) — быстрый RU/мультиязык | `models/rag/FRIDA/` | Hugging Face: `ai-forever/FRIDA` |
| **Эмбеддинги (альт.)** | Giga-Embeddings-instruct, Qwen3-Embedding-4B, MiniLM-L12 | `models/rag/<имя>/` | HF: `ai-forever/Giga-Embeddings-instruct`, `Qwen/Qwen3-Embedding-4B`, `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| **Реранкер (лёгкий)** | ms-marco-MiniLM-L-6-v2 | `models/rag/ms-marco-MiniLM-L-6-v2/` | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| **Реранкер (качество)** | bge-reranker-v2-minicpm-layerwise | `models/rag/bge-reranker-v2-minicpm-layerwise/` | `BAAI/bge-reranker-v2-minicpm-layerwise` |
| **STT** | WhisperX (large-v3 / medium) | `whisperx_models/` | подтянется при первом запуске или зеркала HF `Systran/faster-whisper-*` |
| **TTS** | Silero RU | `silero_models/` | [Silero Models](https://github.com/snakers4/silero-models) |
| **Диаризация** | pyannote speaker-diarization | `diarize_models/` | [pyannote](https://huggingface.co/pyannote) (нужен HF token для gated) |
| **OCR** | Surya text_detection + text_recognition | `surya_models/` | Datalab / Surya checkpoints (см. `SVC-OCR/config`) |
| **ComfyUI** | flux1-schnell-fp8 (быстро) или flux1-dev-fp8 | `models/comfyui/checkpoints/` | [flux1-schnell-fp8](https://huggingface.co/Comfy-Org/flux1-schnell/blob/main/flux1-schnell-fp8.safetensors), [flux1-dev-fp8](https://huggingface.co/Comfy-Org/flux1-dev) |

Путь к LLM в контейнере задаётся в `llm-svc/config/config.yml` → `model.path` / `model.name` (и `backend: llama.cpp` или `vllm`).

### CPU vs GPU по сервисам

#### llm-service
| Режим | Как |
|-------|-----|
| **CPU** | В `llm-svc/config/config.yml`: `model.gpu_layers: 0`. Сборка: `Dockerfile` (CPU) **или** CUDA-образ с `gpu_layers: 0`. |
| **GPU** | `model.gpu_layers: -1` (все слои). Сборка: `Dockerfile.cuda`. Override: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d llm-service`. Для vLLM — `backend: vllm`, HF-папка модели, `gpu_memory_utilization` и т.д. |

```bash
# GPU override
docker compose -f docker-compose.yml -f docker-compose.gpu.yml build llm-service
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d llm-service
```

#### rag-models
| Режим | Как |
|-------|-----|
| **CPU** | `SVC-RAG-MODELS/config/config.yml` → `rag_models.device: "cpu"` (дефолт), `embed_batch_size: 16` |
| **GPU** | `device: "cuda"`, увеличьте `embed_batch_size` (например 32–64). В compose при необходимости добавьте `deploy.resources...capabilities: [gpu]` |

#### stt-service (WhisperX)
| Режим | Как |
|-------|-----|
| **CPU** | `whisperx.device: "cpu"`, `compute_type: "int8"` |
| **GPU** | `device: "cuda"`, `compute_type: "float16"` (нужна совместимая CUDA/PyTorch) |

#### diarization-service
| Режим | Как |
|-------|-----|
| **CPU** | `diarization.device: "cpu"` |
| **GPU** | `device: "cuda"` или `"auto"` |

#### ocr-service (Surya)
| Режим | Как |
|-------|-----|
| **CPU** | `surya.device: "cpu"` |
| **GPU** | `device: "cuda"` или `"auto"` |

#### tts-service (Silero)
Обычно **CPU** достаточно (`gpu_layers: 0` в конфиге). GPU не обязателен.

#### comfyui
| Режим | Как |
|-------|-----|
| **CPU** | `COMFYUI_FORCE_CPU=1` в env |
| **GPU** | дефолт в `docker-compose.yml` (`gpus` / NVIDIA devices), `COMFYUI_FORCE_CPU=0` |

---

## Установка Docker

Перед началом работы с AstraStudio необходимо установить и запустить Docker на вашей системе.

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
> Убедитесь, что Docker запущен и работает перед выполнением команд установки AstraStudio. В Windows Docker Desktop должен быть запущен, в Linux — служба Docker должна быть активна.

---

## Быстрый старт с Docker

> **Примечание**
> 
> Для некоторых Docker-окружений могут потребоваться дополнительные настройки. Если вы столкнетесь с проблемами подключения, обратитесь к подробной документации в каталоге `README/`.

> **Важно**
> 
> При использовании Docker убедитесь, что корректно настроено монтирование томов для моделей и данных — иначе модели не загрузятся, а данные могут теряться при пересоздании контейнеров.

> **Совет**
> 
> Для GPU (CUDA) установите Nvidia CUDA Container Toolkit. Без toolkit контейнеры с `deploy...capabilities: [gpu]` не получат доступ к видеокарте.

### Подготовка каталогов

```bash
cd /path/to/memo_new_api

mkdir -p models/llm models/rag models/comfyui \
  diarize_models model_small silero_models whisperx_models surya_models \
  uploads memory
```

Назначение:
- `models/llm` — веса LLM
- `models/rag` — эмбеддинги и реранкеры
- `models/comfyui` — чекпоинты ComfyUI
- `diarize_models`, `silero_models`, `whisperx_models`, `surya_models` — speech/OCR
- `uploads`, `memory` — пользовательские данные backend

Скопируйте env-шаблон (если есть `env.example` / `localhost.env`) в `.env` в корне и задайте пароли БД, JWT, при необходимости LDAP/Keycloak.

---

## Запуск через Docker

### Все сервисы сразу

```bash
cd /path/to/memo_new_api
docker compose up -d
```

Поднимутся LLM, speech/OCR, ComfyUI, RAG, backend, frontend, MongoDB, PostgreSQL, MinIO, MCP websearch.

UI: **http://localhost:3000**  
API: **http://localhost:8000**

### Локальный вход (mock-пользователи)

Пока `LDAP_ENABLED=false` и Keycloak выключен, на экране логина работают встроенные учётки из `backend/auth/mock_users.py` (не для продакшена):

| Логин | Пароль | Роль |
|-------|--------|------|
| `admin` | `admin123` | Администратор (`is_admin=true`) — полный доступ, в т.ч. все агенты |
| `user` | `user123` | Обычный пользователь |
| `test` | `test123` | Тестовый пользователь |

Рекомендуемый вход для локальной разработки: **`admin` / `admin123`**.

Проверка:

```bash
docker compose ps
docker compose logs -f astrachat-backend
```

### Отдельные сервисы

```bash
# Только инфраструктура
docker compose up -d mongodb postgresql minio

# Модельные / AI
docker compose up -d llm-service
docker compose up -d rag-models svc-rag
docker compose up -d stt-service tts-service diarization-service ocr-service
docker compose up -d comfyui mcp-websearch

# Приложение
docker compose up -d astrachat-backend astrachat-frontend
```

### Поэтапные compose-файлы (legacy-сплит)

```bash
docker compose -f docker-compose-llm-svc.yml up -d
docker compose -f docker-compose-backend.yml up -d
docker compose -f docker-compose-frontend.yml up -d
```

> Основной актуальный файл — корневой `docker-compose.yml`. Сплит-файлы удобны для частичного стенда, но пути/порты могут отличаться от полного стека.

### GPU для LLM

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d llm-service
```

В `llm-svc/config/config.yml` выставьте `gpu_layers: -1`.

### Остановка

```bash
docker compose down
# с удалением volumes (осторожно — данные БД):
# docker compose down -v
```

---

## Запуск через Helm (Kubernetes)

Чарт-пакеты лежат в `astra-helm/astra-helm/`:

| Каталог | Chart name | Назначение |
|---------|------------|------------|
| `astra-back` | `astra-chat-backend` | Backend + ConfigMap (LDAP/Keycloak, RAG, агенты) |
| `astra-front` | `astra-chat-frontend` | Frontend + Ingress |
| `llm-svc` | llm-svc | LLM (в K8s часто vLLM + PVC с моделями) |
| `astra-rag` | RAG logic | SVC-RAG |
| `astra-rag-models` | RAG models | эмбеддинги/реранк |
| `astra-ocr` | OCR | Surya OCR |

Документация values → env: `*/HELM_VALUES.md` рядом с каждым чартом.

### Типовой деплой

```bash
# namespace
kubectl create namespace astra-studio

# пример: backend (путь к чарту и values уточните под свой CI/стенд)
helm upgrade --install astra-back ./astra-helm/astra-helm/astra-back/src/main/helm \
  -n astra-studio \
  -f ./astra-helm/astra-helm/astra-back/src/main/helm/values.yaml

helm upgrade --install astra-front ./astra-helm/astra-helm/astra-front/src/main/helm \
  -n astra-studio

helm upgrade --install llm-svc ./astra-helm/astra-helm/llm-svc/src/main/helm \
  -n astra-studio

helm upgrade --install astra-rag ./astra-helm/astra-helm/astra-rag/src/main/helm \
  -n astra-studio

helm upgrade --install astra-rag-models ./astra-helm/astra-helm/astra-rag-models/src/main/helm \
  -n astra-studio

helm upgrade --install astra-ocr ./astra-helm/astra-helm/astra-ocr/src/main/helm \
  -n astra-studio
```

Секреты (JWT, Keycloak client secret, пароли БД) обычно приходят из Vault через ExternalSecret — см. шаблоны в чартах. Переопределения стендов — в репозитории `common-helm-values` (если используется у вас в CI).

GPU для llm-svc / OCR / RAG-models в K8s задаётся через `resources` и nodeSelector/tolerations в values Deployment (и PVC с моделями на ноде с GPU).

---

## Где менять конфиги

### Локальная разработка (без полного Docker-стека)

1. **Backend** — корневой `.env` или `localhost.env` (скопировать в `.env`), плюс код в `backend/`.
2. **Frontend** — `frontend/.env`:
   ```bash
   REACT_APP_API_URL=http://localhost:8000
   REACT_APP_WS_URL=ws://localhost:8000
   ```
3. **LLM / speech / OCR / RAG** — YAML в соответствующих `*/config/config.yml` (пути к моделям на хосте, не `/app/...`).
4. Запуск UI+API:
   ```bash
   npm run install:all
   npm run dev
   ```
   Либо отдельно: `python backend/main.py` и `cd frontend && npm start`. Микросервисы моделей при этом должны быть уже доступны (Docker или локальный процесс).

### Docker Compose

| Что | Где править |
|-----|-------------|
| Пароли БД, JWT, feature-flags, LDAP on/off | корневой `.env` (подхватывается compose) |
| URL сервисов внутри сети | уже заданы в `docker-compose.yml` (`SVC_*_URL`) |
| Путь/имя LLM, CPU/GPU layers | `llm-svc/config/config.yml` (volume `:ro`) |
| Embedding/reranker device | `SVC-RAG-MODELS/config/config.yml` |
| WhisperX / diarization / OCR / TTS | `SVC-*/config/config.yml` |
| Frontend API URL | build-args в сервисе `astrachat-frontend` |
| ComfyUI CPU force | `COMFYUI_FORCE_CPU` в `.env` / compose |
| LDAP | `LDAP_ENABLED=true` + `LDAP_URL`, `LDAP_USER_SEARCH_BASE`, `LDAP_SEARCH_FILTER`, `LDAP_ADMIN_GROUPS`, bind DN/password |
| Keycloak SSO | `KEYCLOAK_ENABLED=true`, `KEYCLOAK_ISSUER`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CLIENT_SECRET`, `KEYCLOAK_REDIRECT_URI`, `KEYCLOAK_FRONTEND_CALLBACK_URL`, `KEYCLOAK_ADMIN_GROUPS` / `KEYCLOAK_REQUIRED_ROLES` |

После смены YAML конфигов модельных сервисов:

```bash
docker compose restart llm-service rag-models stt-service ocr-service
```

### Kubernetes (Helm)

| Что | Где |
|-----|-----|
| Env backend (LDAP, Keycloak, RAG, агенты) | `astra-back/.../values.yaml` → ConfigMap `cm-astrachatbackend.yaml` |
| Образ / ресурсы / PVC | `deployment-*.yaml` + `values.yaml` (`image.*`, `resources`, PVC size) |
| Секреты | Vault → ExternalSecret (не хранить в git) |
| Frontend / Ingress | `astra-front/.../values.yaml` |
| LLM path / GPU | `llm-svc/.../values.yaml` и args Deployment (vLLM) |
| RAG / OCR | `astra-rag*`, `astra-ocr` values + ConfigMap |

Подробные таблицы values↔env: `astra-helm/astra-helm/*/HELM_VALUES.md`.

---

## LDAP и Keycloak (SSO) — преимущества

- **Единый корпоративный вход** — пользователи не заводят отдельный пароль в AstraStudio.
- **LDAP/AD** — привычный логин доменной УЗ; группы AD управляют admin и ролями агентной платформы.
- **Keycloak OIDC** — бесшовный SSO (redirect → IdP → callback); роли/группы Keycloak мапятся на права UI/API.
- **Гибкость** — LDAP и Keycloak настраиваются независимо; для стенда разработки оставляете mock-пользователей (`LDAP_ENABLED=false`, `KEYCLOAK_ENABLED=false`).
- **Аудит** — события входа пишутся в CEF (LDAP-сервер / Keycloak issuer, роль, ТУЗ).

### Mock-логины без LDAP/SSO

Для локального стенда (Docker / `npm run dev`) при выключенном LDAP:

| Логин | Пароль | Назначение |
|-------|--------|------------|
| `admin` | `admin123` | Админ платформы |
| `user` | `user123` | Обычный пользователь |
| `test` | `test123` | Тестовый пользователь |

Исходники учёток: `backend/auth/mock_users.py`. После включения `LDAP_ENABLED=true` эти пароли для входа через AD не используются (нужна доменная УЗ).

Минимальный пример для Docker (`.env`):

```bash
LDAP_ENABLED=true
LDAP_URL=ldaps://ad.company.local:636
LDAP_USER_SEARCH_BASE=dc=company,dc=local
LDAP_SEARCH_FILTER=(sAMAccountName={username})
LDAP_ADMIN_GROUPS=CN=Astra Admins,OU=Groups,DC=company,DC=local

KEYCLOAK_ENABLED=true
KEYCLOAK_ISSUER=https://sso.company.local/auth/realms/astra
KEYCLOAK_CLIENT_ID=astra-studio
KEYCLOAK_CLIENT_SECRET=***
KEYCLOAK_REDIRECT_URI=https://astra.company.local/api/auth/keycloak/callback
KEYCLOAK_FRONTEND_CALLBACK_URL=https://astra.company.local/auth/callback
KEYCLOAK_REQUIRED_ROLES=astra-user
KEYCLOAK_ADMIN_GROUPS=astra-admin
```

В Helm те же параметры — блоки `ldap.*` и `keycloak.*` в values backend.

---

## Проверка работы

```bash
docker compose ps

curl http://localhost:8000/health
curl http://localhost:8002/v1/health   # llm-service
curl http://localhost:8010/v1/health   # rag-models
```

### Доступ к сервисам (Docker)

| Сервис | URL |
|--------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| LLM | http://localhost:8002 |
| STT | http://localhost:8001 |
| Diarization | http://localhost:8003 |
| OCR | http://localhost:8004 |
| TTS | http://localhost:8005 |
| RAG models | http://localhost:8010 |
| SVC-RAG | http://localhost:8011 |
| MCP websearch | http://localhost:8013 |
| ComfyUI | http://localhost:8188 |
| MinIO Console | http://localhost:9001 |

Дополнительно по env: `README/ENV_SETUP_GUIDE.md`.

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=NeKonnnn/AstraChat_AI_System&type=date&legend=top-left)](https://www.star-history.com/#NeKonnnn/AstraChat_AI_System&type=date&legend=top-left)

---

**Приятного использования AstraStudio!**
