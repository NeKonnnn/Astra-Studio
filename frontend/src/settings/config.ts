/**
 * Основной модуль конфигурации для astrachat Frontend
 * Загружает настройки из YAML и переменных окружения
 */

import yaml from 'js-yaml';
import { APIConnectionConfigImpl, WebSocketConnectionConfigImpl } from './connections';

export interface UrlsConfig {
  frontend_port: string;
  backend_port: string;
  ingress_port: string;
  llm_service_port: string;
}

export interface AppConfig {
  name: string;
  version: string;
  description: string;
  debug: boolean;
  llmStatusPollSeconds: number;
  llmStatusPollTrace: boolean;
  ragReindexStatusPollSeconds: number;
}

export interface SettingsConfig {
  app: AppConfig;
  urls: UrlsConfig;
  api: APIConnectionConfigImpl;
  websocket: WebSocketConnectionConfigImpl;
}

let _settings: SettingsConfig | null = null;
let _loadPromise: Promise<SettingsConfig> | null = null;

const loadConfig = async (): Promise<SettingsConfig> => {
  if (_settings) return _settings;
  if (_loadPromise) return _loadPromise;

  _loadPromise = (async () => {
    try {
      const response = await fetch('/config/config.yml');
      if (!response.ok) {
        throw new Error(
          `Не удалось загрузить config.yml: ${response.statusText}. ` +
          `Проверьте наличие файла public/config/config.yml`
        );
      }

      const yamlText = await response.text();
      const configData: any = yaml.load(yamlText) || {};

      if (!configData.urls) {
        throw new Error('В config.yml отсутствует секция urls. Проверьте формат файла.');
      }

      const llmStatusPollSeconds = Number.parseInt(String(configData.app?.llm_status_poll_seconds ?? ''), 10);
      if (!Number.isFinite(llmStatusPollSeconds) || llmStatusPollSeconds <= 0) {
        throw new Error('В config.yml app.llm_status_poll_seconds должен быть положительным целым числом (секунды).');
      }
      const llmStatusPollTrace =
        String(configData.app?.llm_status_poll_trace ?? '')
          .trim()
          .toLowerCase() === 'true';

      const ragReindexStatusPollSeconds = Number.parseInt(
        String(configData.app?.rag_reindex_status_poll_seconds ?? '10'),
        10,
      );
      if (!Number.isFinite(ragReindexStatusPollSeconds) || ragReindexStatusPollSeconds <= 0) {
        throw new Error(
          'В config.yml app.rag_reindex_status_poll_seconds должен быть положительным целым числом (секунды).',
        );
      }

      const appConfig: AppConfig = {
        name: configData.app?.name || 'astrachat Frontend',
        version: configData.app?.version || '1.0.0',
        description: configData.app?.description || 'Frontend for astrachat',
        debug: configData.app?.debug ?? false,
        llmStatusPollSeconds,
        llmStatusPollTrace,
        ragReindexStatusPollSeconds,
      };

      const getUrlValue = (yamlKey: string, envKey: string): string => {
        if (configData.urls && configData.urls[yamlKey]) {
          return configData.urls[yamlKey];
        }
        const envValue = process.env[envKey];
        if (envValue) {
          return envValue;
        }
        throw new Error(
          `${yamlKey} не задан в YAML (urls.${yamlKey}) или ENV (${envKey}). ` +
          `Проверьте файл public/config/config.yml или переменные окружения.`
        );
      };

      const urlsConfig: UrlsConfig = {
        frontend_port: getUrlValue('frontend_port', 'REACT_APP_FRONTEND_PORT'),
        backend_port: getUrlValue('backend_port', 'REACT_APP_BACKEND_PORT'),
        ingress_port: getUrlValue('ingress_port', 'REACT_APP_INGRESS_PORT'),
        llm_service_port: getUrlValue('llm_service_port', 'REACT_APP_LLM_SERVICE_PORT'),
      };

      const apiBaseUrl = process.env.REACT_APP_API_URL || urlsConfig.ingress_port;

      const wsBaseUrlRaw = process.env.REACT_APP_WS_URL || urlsConfig.ingress_port;
      const wsBaseUrl =
        wsBaseUrlRaw.startsWith('ws://') || wsBaseUrlRaw.startsWith('wss://')
          ? wsBaseUrlRaw
          : wsBaseUrlRaw.replace('http://', 'ws://').replace('https://', 'wss://');

      const getConfigValue = <T>(yamlPath: string[], envKey: string, defaultValue: T | null = null): T => {
        let value: any = configData;
        for (const key of yamlPath) {
          value = value?.[key];
          if (value === undefined) break;
        }
        if (value !== undefined) {
          return value;
        }
        const envValue = process.env[envKey];
        if (envValue !== undefined) {
          if (typeof defaultValue === 'number') {
            return parseInt(envValue, 10) as T;
          }
          if (typeof defaultValue === 'boolean') {
            return (envValue.toLowerCase() === 'true') as T;
          }
          return envValue as T;
        }
        if (defaultValue !== null) {
          return defaultValue;
        }
        throw new Error(
          `Значение не задано в YAML (${yamlPath.join('.')}) или ENV (${envKey}). ` +
          `Проверьте файл public/config/config.yml или переменные окружения.`
        );
      };

      const apiConfig = new APIConnectionConfigImpl({
        baseUrl: apiBaseUrl,
        timeout: getConfigValue(['api', 'timeout'], 'REACT_APP_API_TIMEOUT', 30000),
        retryEnabled: getConfigValue(['api', 'retryEnabled'], 'REACT_APP_API_RETRY_ENABLED', true),
        retryAttempts: getConfigValue(['api', 'retryAttempts'], 'REACT_APP_API_RETRY_ATTEMPTS', 3),
        retryDelay: getConfigValue(['api', 'retryDelay'], 'REACT_APP_API_RETRY_DELAY', 1000),
      });

      const websocketConfig = new WebSocketConnectionConfigImpl({
        baseUrl: wsBaseUrl,
        timeout: getConfigValue(['websocket', 'timeout'], 'REACT_APP_WS_TIMEOUT', 10000),
        pingInterval: getConfigValue(['websocket', 'pingInterval'], 'REACT_APP_WS_PING_INTERVAL', 30000),
        pingTimeout: getConfigValue(['websocket', 'pingTimeout'], 'REACT_APP_WS_PING_TIMEOUT', 10000),
        reconnectionAttempts: getConfigValue(
          ['websocket', 'reconnectionAttempts'],
          'REACT_APP_WS_RECONNECTION_ATTEMPTS',
          10,
        ),
        reconnectionDelay: getConfigValue(
          ['websocket', 'reconnectionDelay'],
          'REACT_APP_WS_RECONNECTION_DELAY',
          1000,
        ),
        reconnectionDelayMax: getConfigValue(
          ['websocket', 'reconnectionDelayMax'],
          'REACT_APP_WS_RECONNECTION_DELAY_MAX',
          5000,
        ),
      });

      _settings = {
        app: appConfig,
        urls: urlsConfig,
        api: apiConfig,
        websocket: websocketConfig,
      };

      return _settings;
    } catch (error) {
      console.error('КРИТИЧЕСКАЯ ОШИБКА загрузки конфигурации:', error);
      throw error;
    } finally {
      _loadPromise = null;
    }
  })();

  return _loadPromise;
};

export const getSettings = (): SettingsConfig => {
  if (!_settings) {
    throw new Error(
      'Конфигурация не загружена! Убедитесь, что initSettings() вызван перед использованием getSettings().'
    );
  }
  return _settings;
};

export const initSettings = async (): Promise<SettingsConfig> => loadConfig();

export const initConfig = initSettings;

export const resetSettings = async (): Promise<SettingsConfig> => {
  _settings = null;
  _loadPromise = null;
  return loadConfig();
};

export const getUrl = (key: keyof UrlsConfig): string => {
  const settings = getSettings();
  const url = settings.urls[key];
  if (!url) {
    throw new Error(
      `Ключ '${key}' не найден в config.yml! Проверьте наличие этого ключа в секции urls файла public/config/config.yml`
    );
  }
  return url;
};
