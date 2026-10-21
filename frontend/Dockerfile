# Одностадийный билд React-приложения на Node (без nginx),
# чтобы не тянуть отдельный образ nginx из Docker Hub.
FROM node:20-alpine

# Установка рабочей директории
WORKDIR /app

# Установка build arguments
ARG REACT_APP_API_URL=http://localhost:8000
ARG REACT_APP_WS_URL=ws://localhost:8000

# Установка переменных окружения
ENV REACT_APP_API_URL=$REACT_APP_API_URL
ENV REACT_APP_WS_URL=$REACT_APP_WS_URL

# Увеличиваем heap Node.js для сборки webpack + monaco-editor
ENV NODE_OPTIONS=--max-old-space-size=4096
# Docker BuildKit часто прокидывает CI=true → CRA валит сборку на eslint-warnings.
ENV CI=false
ENV DISABLE_ESLINT_PLUGIN=true

# Копирование package.json
COPY package.json ./

# Установка зависимостей и простого статического сервера
RUN npm install && npm install -g serve

# Копирование исходного кода
COPY . .

# Monaco static assets для подсветки кода в чате (офлайн, без CDN)
RUN node scripts/copy-monaco-assets.js

# Сборка приложения
RUN npm run build

# Открытие порта
EXPOSE 3000

# Запуск статики из папки build
CMD ["serve", "-s", "build", "-l", "3000"]
