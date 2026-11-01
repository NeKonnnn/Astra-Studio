import { LAST_SELECTED_MODEL_PATH_STORAGE_KEY } from '@/utils/modelThinking';
import { MODEL_PATH_CHANGED_EVENT } from '@/utils/contextTokens';
import {
  applyAgentModelAndSettings,
  loadAgentModelOnly,
  rememberAgentModelPath,
} from '@/utils/applyAgentServer';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (endpoint: string) => `http://api.test${endpoint}`,
}));

describe('applyAgentServer: недостающие ветки', () => {
  let fetchMock: jest.Mock;

  beforeEach(() => {
    localStorage.clear();
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
  });

  afterEach(() => jest.restoreAllMocks());

  it('rememberAgentModelPath: заглушка провайдера не запоминается и событие не шлётся', () => {
    const handler = jest.fn();
    window.addEventListener(MODEL_PATH_CHANGED_EVENT, handler);
    rememberAgentModelPath('llm-svc://');
    window.removeEventListener(MODEL_PATH_CHANGED_EVENT, handler);
    expect(localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY)).toBeNull();
    expect(handler).not.toHaveBeenCalled();
  });

  it('rememberAgentModelPath: ошибка записи в localStorage не мешает событию', () => {
    const handler = jest.fn();
    window.addEventListener(MODEL_PATH_CHANGED_EVENT, handler);
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    rememberAgentModelPath('llm-svc://model-1');
    window.removeEventListener(MODEL_PATH_CHANGED_EVENT, handler);
    expect(handler).toHaveBeenCalledTimes(1);
    expect((handler.mock.calls[0][0] as CustomEvent).detail).toEqual({ path: 'llm-svc://model-1' });
  });

  it('текущая модель с невалидным JSON в /api/models/current -> модель не определена, загрузки нет', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.reject(new Error('bad json')),
    });
    const res = await loadAgentModelOnly('tok', null);
    expect(res).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/models/current');
  });

  it('ответ /api/models/load не JSON: считается ожидающей загрузкой с сообщением по умолчанию', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.reject(new Error('not json')),
    });
    const res = await loadAgentModelOnly('tok', 'model-x');
    expect(res).toEqual({ ok: true, pending: true, message: 'Не удалось загрузить модель агента' });
    // путь без префикса нормализуется
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ model_path: 'llm-svc://model-x' });
    expect(localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY)).toBe('llm-svc://model-x');
  });

  it('applyAgentModelAndSettings (deprecated) делегирует загрузке модели без перезаписи настроек', async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ success: true }) });
    const res = await applyAgentModelAndSettings('tok', {
      system_prompt: 'p',
      model_path: 'llm-svc://m',
      model_settings: { temperature: 1 },
    });
    expect(res).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/models/load');
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer tok');
  });
});
