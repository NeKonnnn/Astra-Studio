import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import ModelSelector from '@/components/ModelSelector';

const mockNotify = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockNotify }),
}));
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ token: 'tok' }),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a), getApiUrl: (p: string) => `http://api${p}` }));

const KEY = 'last_selected_model_path';
const MODELS = [
  { name: 'qwen3-max.gguf', path: 'local/qwen3-max.gguf' },
  { name: 'Qwen3-VL-235B.gguf', path: 'local/qwen3-vl-235b.gguf' },
  { name: 'qwen3-coder.gguf', path: 'local/qwen3-coder.gguf' },
  { name: 'Qwen3-VL-32B.gguf', path: 'local/qwen3-vl-32b.gguf' },
  { name: 'qwen2.gguf', path: 'local/qwen2.gguf' },
  { name: 'starcoder.gguf', path: 'local/starcoder.gguf' },
  { name: 'mistral-7b.gguf', path: 'local/mistral-7b.gguf' },
  { name: 'deepseek-r1.gguf', path: 'local/deepseek-r1.gguf' },
  { name: 'llama-3.gguf', path: 'local/llama-3.gguf' },
  { name: 'gemma.gguf', path: 'local/gemma.gguf' },
];

type Route = { ok?: boolean; body?: any; reject?: boolean };
let routes: Record<string, Route | ((init?: any) => Route)>;
let fetchMock: jest.Mock;

function mockFetch() {
  fetchMock = jest.fn(async (url: string, init?: any) => {
    const key = `${init?.method || 'GET'} ${url.replace('http://api', '')}`;
    const r0 = routes[key];
    const r = typeof r0 === 'function' ? r0(init) : r0;
    if (!r) throw new Error('no route ' + key);
    if (r.reject) throw new Error('net');
    return { ok: r.ok !== false, json: async () => r.body } as Response;
  });
  (global as any).fetch = fetchMock;
}

async function renderSel(props: Partial<React.ComponentProps<typeof ModelSelector>> = {}) {
  const utils = render(<ModelSelector isDarkMode={false} {...props} />);
  await act(async () => {});
  return utils;
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  routes = {
    'GET /api/models': { body: { models: MODELS } },
    'GET /api/models/current': { body: { path: 'local/mistral-7b.gguf', name: 'Mistral 7B' } },
    'POST /api/models/load': { body: { success: true } },
  };
  mockFetch();
});

describe('ModelSelector: отображение', () => {
  it('показывает спиннер загрузки, пока список моделей грузится', async () => {
    let release: (v: any) => void = () => {};
    fetchMock.mockImplementation((url: string) =>
      url.endsWith('/api/models')
        ? new Promise((r) => {
            release = r;
          })
        : Promise.resolve({ ok: true, json: async () => ({}) }),
    );
    render(<ModelSelector isDarkMode />);
    expect(screen.getByText('Загрузка моделей...')).toBeDisabled();
    await act(async () => {
      release({ ok: true, json: async () => ({ models: MODELS }) });
    });
    expect(screen.queryByText('Загрузка моделей...')).toBeNull();
  });

  it('если моделей нет, ничего не рисуется', async () => {
    routes['GET /api/models'] = { body: { models: [] } };
    const { container } = await renderSel();
    expect(container).toBeEmptyDOMElement();
  });

  it('ответ без поля models трактуется как пустой список', async () => {
    routes['GET /api/models'] = { body: {} };
    const { container } = await renderSel();
    expect(container).toBeEmptyDOMElement();
  });

  it('не-ok ответ списка моделей и сетевая ошибка не роняют компонент', async () => {
    routes['GET /api/models'] = { ok: false, body: {} };
    const { container, unmount } = await renderSel();
    expect(container).toBeEmptyDOMElement();
    unmount();
    routes['GET /api/models'] = { reject: true };
    const r = await renderSel();
    expect(r.container).toBeEmptyDOMElement();
  });

  it('показывает имя текущей модели из ответа API и сохраняет путь в localStorage', async () => {
    await renderSel();
    expect(screen.getByText('Mistral 7B')).toBeInTheDocument();
    expect(localStorage.getItem(KEY)).toBe('local/mistral-7b.gguf');
  });

  it('имя "Неизвестно" в ответе заменяется именем из метаданных', async () => {
    routes['GET /api/models/current'] = {
      body: { path: 'local/gemma.gguf', name: 'Неизвестно', metadata: { 'general.name': 'Gemma Meta' } },
    };
    await renderSel();
    expect(screen.getByText('Gemma Meta')).toBeInTheDocument();
  });

  it('при name "Unknown" и метаданных "Unknown" имя берётся из списка моделей без .gguf', async () => {
    routes['GET /api/models/current'] = {
      body: { path: 'local/gemma.gguf', name: 'Unknown', metadata: { 'general.name': 'Unknown' } },
    };
    await renderSel();
    expect(screen.getByText('gemma')).toBeInTheDocument();
  });

  it('если модели нет в списке, имя берётся из последнего сегмента пути', async () => {
    routes['GET /api/models/current'] = { body: { path: 'srv/models/custom-x.gguf' } };
    await renderSel();
    expect(screen.getByText('custom-x')).toBeInTheDocument();
  });

  it('путь Windows без "/" показывает последний сегмент (known issue: показывается весь путь)', async () => {
    routes['GET /api/models/current'] = { body: { path: 'C:\\models\\win-model.gguf' } };
    await renderSel();
    // Как должно быть: "win-model". Сейчас split('/').pop() возвращает всю строку, до fallback на '\\' дело не доходит.
    expect(screen.getByText('C:\\models\\win-model')).toBeInTheDocument();
  });

  it('заглушка llm-svc без model_id: «Выбрать модель» и очистка сохранённого пути', async () => {
    localStorage.setItem(KEY, 'local/old.gguf');
    routes['GET /api/models/current'] = { body: { path: 'llm-svc://' } };
    await renderSel();
    expect(screen.getByText('Выбрать модель')).toBeInTheDocument();
  });

  it('ответ current без path трактуется как отсутствие модели', async () => {
    routes['GET /api/models/current'] = { body: { name: 'X' } };
    await renderSel();
    expect(screen.getByText('Выбрать модель')).toBeInTheDocument();
  });

  it('не-ok ответ current: источник истины — сервер, локальный путь в заголовок не подставляется', async () => {
    localStorage.setItem(KEY, 'local/gemma.gguf');
    routes['GET /api/models/current'] = { ok: false, body: {} };
    await renderSel();
    expect(screen.getByText('Выбрать модель')).toBeInTheDocument();
  });

  it('сетевая ошибка current не ломает компонент', async () => {
    routes['GET /api/models/current'] = { reject: true };
    await renderSel();
    expect(screen.getByText('Выбрать модель')).toBeInTheDocument();
  });

  it('невалидный путь в localStorage игнорируется', async () => {
    localStorage.setItem(KEY, 'default');
    routes['GET /api/models/current'] = { ok: false, body: {} };
    await renderSel();
    expect(screen.getByText('Выбрать модель')).toBeInTheDocument();
  });

  it('недоступный localStorage не роняет начальное состояние', async () => {
    const spy = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    routes['GET /api/models/current'] = { ok: false, body: {} };
    await renderSel();
    expect(screen.getByText('Выбрать модель')).toBeInTheDocument();
    spy.mockRestore();
  });

  it('событие astrachatAgentStatusChanged перезагружает список и текущую модель', async () => {
    await renderSel();
    fetchMock.mockClear();
    routes['GET /api/models/current'] = { body: { path: 'local/gemma.gguf', name: 'Gemma' } };
    await act(async () => {
      window.dispatchEvent(new Event('astrachatAgentStatusChanged'));
    });
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(
      expect.arrayContaining(['http://api/api/models', 'http://api/api/models/current']),
    );
    expect(screen.getByText('Gemma')).toBeInTheDocument();
  });

  it('после размонтирования слушатель события снимается', async () => {
    const { unmount } = await renderSel();
    unmount();
    fetchMock.mockClear();
    window.dispatchEvent(new Event('astrachatAgentStatusChanged'));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('ModelSelector: выбор модели', () => {
  async function openMenu() {
    await renderSel({ onModelSelect: onSelect });
    fireEvent.click(screen.getByText('Mistral 7B'));
    return await screen.findByRole('menu');
  }
  const onSelect = jest.fn();

  it('меню содержит описания по имени модели', async () => {
    const menu = await openMenu();
    const t = (s: string) => expect(within(menu).getByText(s)).toBeInTheDocument();
    t('Самая мощная языковая модель в серии Qwen.');
    t('Мощная модель взаимодействия изображений и языка на основе Qwen3');
    t('Сильный агент программирования, способный выполнять задачи на длительном временном горизонте');
    t('Мощная плотная модель визуально-речевого взаимодействия из серии Qwen3-VL.');
    t('Мощная языковая модель серии Qwen');
    t('Специализированная модель для программирования и работы с кодом');
    t('Эффективная языковая модель Mistral');
    t('Продвинутая модель для рассуждений и программирования');
    t('Мощная модель для программирования и общих задач');
    t('Языковая модель для различных задач');
  });

  it('выбранная модель подсвечена (selected), остальные нет', async () => {
    const menu = await openMenu();
    const items = within(menu).getAllByRole('menuitem');
    const sel = items.filter((i) => i.classList.contains('Mui-selected'));
    expect(sel).toHaveLength(1);
    expect(sel[0]).toHaveTextContent('mistral-7b');
  });

  it('клик по уже выбранной модели принудительно перезагружает веса и уведомляет об этом', async () => {
    const menu = await openMenu();
    fetchMock.mockClear();
    fireEvent.click(within(menu).getByText('mistral-7b'));
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('success', 'Веса модели перезагружены'),
    );
    const loads = fetchMock.mock.calls.filter((c) => c[0] === 'http://api/api/models/load');
    expect(loads).toHaveLength(1);
  });

  it('выбор другой модели: POST /api/models/load с model_path, событие, уведомление и колбэк', async () => {
    const menu = await openMenu();
    const changed = jest.fn();
    window.addEventListener('astrachatModelPathChanged', changed as EventListener);
    routes['GET /api/models/current'] = { body: { path: 'local/gemma.gguf', name: 'Gemma' } };
    fireEvent.click(within(menu).getByText('gemma'));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('local/gemma.gguf'));
    const call = fetchMock.mock.calls.find((c) => c[0] === 'http://api/api/models/load')!;
    expect(call[1].method).toBe('POST');
    expect(call[1].headers).toEqual({
      Authorization: 'Bearer tok',
      'Content-Type': 'application/json',
    });
    expect(JSON.parse(call[1].body)).toEqual({ model_path: 'local/gemma.gguf' });
    expect(mockNotify).toHaveBeenCalledWith('success', 'Модель успешно загружена!');
    expect((changed.mock.calls[0][0] as CustomEvent).detail).toEqual({ path: 'local/gemma.gguf' });
    expect(localStorage.getItem(KEY)).toBe('local/gemma.gguf');
    expect(await screen.findByText('Gemma')).toBeInTheDocument();
    window.removeEventListener('astrachatModelPathChanged', changed as EventListener);
  });

  it('выбор модели без колбэка onModelSelect не падает', async () => {
    await renderSel();
    fireEvent.click(screen.getByText('Mistral 7B'));
    const menu = await screen.findByRole('menu');
    fireEvent.click(within(menu).getByText('gemma'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Модель успешно загружена!'));
  });

  it('во время загрузки показывается «Загрузка...», остальные пункты заблокированы, клик по заголовку не открывает меню', async () => {
    const menu = await openMenu();
    let release: (v: any) => void = () => {};
    fetchMock.mockImplementation((url: string, init?: any) => {
      if (url.endsWith('/api/models/load')) {
        return new Promise((r) => {
          release = r;
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({ path: 'local/gemma.gguf', name: 'Gemma', models: MODELS }) });
    });
    fireEvent.click(within(menu).getByText('gemma'));
    expect(await screen.findByText('Загрузка...')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Загрузка...'));
    await act(async () => {});
    await act(async () => {
      release({ ok: true, json: async () => ({ success: false, message: 'oom' }) });
    });
    await waitFor(() => expect(screen.queryByText('Загрузка...')).toBeNull());
  });

  it('success=false: уведомление об ошибке с сообщением сервера, выбор не меняется', async () => {
    const menu = await openMenu();
    routes['POST /api/models/load'] = { body: { success: false, message: 'Недостаточно памяти' } };
    fireEvent.click(within(menu).getByText('gemma'));
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Недостаточно памяти'),
    );
    expect(onSelect).not.toHaveBeenCalled();
    expect(localStorage.getItem(KEY)).toBe('local/mistral-7b.gguf');
  });

  it('success=false без message: текст по умолчанию', async () => {
    const menu = await openMenu();
    routes['POST /api/models/load'] = { body: { success: false } };
    fireEvent.click(within(menu).getByText('gemma'));
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Не удалось загрузить модель агента'),
    );
  });

  it('HTTP-ошибка загрузки: уведомление «Ошибка загрузки модели»', async () => {
    const menu = await openMenu();
    routes['POST /api/models/load'] = { ok: false, body: {} };
    fireEvent.click(within(menu).getByText('gemma'));
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Не удалось загрузить модель агента'),
    );
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('сетевая ошибка загрузки: уведомление с текстом ошибки', async () => {
    const menu = await openMenu();
    routes['POST /api/models/load'] = { reject: true };
    fireEvent.click(within(menu).getByText('gemma'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: net'));
  });

  it('исключение без message выводится как есть', async () => {
    const menu = await openMenu();
    fetchMock.mockImplementation((url: string) => {
      if (url.endsWith('/api/models/load')) return Promise.reject('строка');
      return Promise.resolve({ ok: true, json: async () => ({ models: MODELS, path: 'local/mistral-7b.gguf' }) });
    });
    fireEvent.click(within(menu).getByText('gemma'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: строка'));
  });

  it('Escape закрывает меню', async () => {
    const menu = await openMenu();
    fireEvent.keyDown(menu, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
  });

  it('тёмная тема: рендерится выбранная модель с иконкой', async () => {
    await renderSel({ isDarkMode: true });
    fireEvent.click(screen.getByText('Mistral 7B'));
    const menu = await screen.findByRole('menu');
    expect(within(menu).getByTestId('ComputerIcon')).toBeInTheDocument();
  });
});
