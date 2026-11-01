import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import RagModelSelector from '@/components/RagModelSelector';

const mockNotify = jest.fn();
const mockReindex = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockNotify }),
}));
jest.mock('@/contexts/RagReindexStatusContext', () => ({
  useRagReindexStatus: () => ({ notifyReindexStarted: mockReindex }),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
  getAuthFetchHeaders: (extra: Record<string, string> = {}) => ({ Authorization: 'Bearer T', ...extra }),
}));

const ROWS = [
  { path: 'local/bge-m3', name: 'bge-m3', display_name: 'BGE M3', source: 'local', kind: 'embedding' },
  { path: 'corsur/e5', name: 'e5', display_name: 'E5 Large', source: 'corsur_rag', kind: 'embedding' },
  { path: 'phx/gte', name: 'gte', display_name: 'GTE', source: 'phoenix_embeddings', kind: 'embedding', available: false },
  { path: 'other/x', name: 'x', display_name: 'Икс', source: 'weird', kind: 'embedding' },
  { path: 'nosrc/y', name: 'y', display_name: '', source: '', kind: 'embedding' },
  { path: '', name: 'nopath', display_name: 'Нет пути', source: 'local', kind: 'embedding' },
];

let fetchMock: jest.Mock;
let listBody: any;
let selectResp: { ok: boolean; body: any; reject?: boolean };

function setupFetch() {
  fetchMock = jest.fn(async (url: string, init?: any) => {
    if (url.includes('/api/rag/models/select')) {
      if (selectResp.reject) throw new Error('net');
      return { ok: selectResp.ok, json: async () => selectResp.body } as Response;
    }
    return { ok: listBody !== null, json: async () => listBody } as Response;
  });
  (global as any).fetch = fetchMock;
}

async function renderSel(props: Partial<React.ComponentProps<typeof RagModelSelector>> = {}) {
  const r = render(<RagModelSelector kind="embedding" isDarkMode={false} {...props} />);
  await act(async () => {});
  return r;
}

const trigger = () => document.querySelector('input[readonly]') as HTMLInputElement;
async function openPanel() {
  fireEvent.click(trigger());
  await screen.findByPlaceholderText('Поиск моделей...');
}

beforeEach(() => {
  jest.clearAllMocks();
  listBody = {
    models: { embedding: ROWS, reranker: [{ path: 'local/rr', name: 'rr', display_name: 'RR', source: 'local' }] },
    current: { embedding: { path: 'local/bge-m3' } },
  };
  selectResp = { ok: true, body: {} };
  setupFetch();
  jest.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => jest.restoreAllMocks());

describe('RagModelSelector: загрузка', () => {
  it('показывает «Загрузка моделей…», пока список не получен', async () => {
    let release: (v: any) => void = () => {};
    fetchMock.mockImplementation(() => new Promise((r) => (release = r)));
    render(<RagModelSelector kind="embedding" isDarkMode />);
    expect(screen.getByText('Загрузка моделей…')).toBeInTheDocument();
    await act(async () => {
      release({ ok: true, json: async () => listBody });
    });
    expect(screen.queryByText('Загрузка моделей…')).toBeNull();
  });

  it('запрос идёт с типом, областью и project_id; заголовки авторизации передаются', async () => {
    await renderSel({ scope: 'project', entityId: 'p 1' });
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api/api/rag/models?type=embedding&scope=project&project_id=p%201',
      { headers: { Authorization: 'Bearer T' } },
    );
  });

  it('для scope=agent в запрос добавляется agent_id', async () => {
    await renderSel({ scope: 'agent', entityId: 7 });
    expect(fetchMock.mock.calls[0][0]).toBe('http://api/api/rag/models?type=embedding&scope=agent&agent_id=7');
  });

  it('без entityId запрос не содержит id (project и agent)', async () => {
    await renderSel();
    expect(fetchMock.mock.calls[0][0]).toBe('http://api/api/rag/models?type=embedding&scope=project');
    fetchMock.mockClear();
    await renderSel({ scope: 'agent', entityId: '' });
    expect(fetchMock.mock.calls[0][0]).toBe('http://api/api/rag/models?type=embedding&scope=agent');
  });

  it('onResolveEntityId (асинхронный) подставляет id, если entityId не задан', async () => {
    const resolve = jest.fn().mockResolvedValue(42);
    await renderSel({ onResolveEntityId: resolve, scope: 'agent' });
    expect(resolve).toHaveBeenCalled();
    expect(fetchMock.mock.calls[0][0]).toContain('&agent_id=42');
  });

  it('onResolveEntityId не вызывается, если entityId уже задан', async () => {
    const resolve = jest.fn();
    await renderSel({ onResolveEntityId: resolve, entityId: 1 });
    expect(resolve).not.toHaveBeenCalled();
  });

  it('выбранной подсвечивается current.path с сервера; name из display_name', async () => {
    await renderSel();
    expect(trigger().value).toBe('BGE M3');
  });

  it('если current нет — берётся cluster_default', async () => {
    listBody.current = {};
    listBody.cluster_default = { embedding: { path: 'corsur/e5' } };
    await renderSel();
    expect(trigger().value).toBe('E5 Large');
  });

  it('без current и cluster_default — заглушка «Выбрать эмбеддинг» / «Выбрать реранкер»', async () => {
    listBody.current = {};
    const { unmount } = await renderSel();
    expect(trigger().value).toBe('Выбрать эмбеддинг');
    unmount();
    await renderSel({ kind: 'reranker' });
    expect(trigger().value).toBe('Выбрать реранкер');
  });

  it('preferredPath важнее current с сервера и реагирует на смену пропса', async () => {
    const { rerender } = await renderSel({ preferredPath: 'corsur/e5' });
    expect(trigger().value).toBe('E5 Large');
    rerender(<RagModelSelector kind="embedding" isDarkMode={false} preferredPath="other/x" />);
    await act(async () => {});
    expect(trigger().value).toBe('Икс');
  });

  it('путь, которого нет в списке, показывается последним сегментом', async () => {
    await renderSel({ preferredPath: 'unknown/dir/modelfile' });
    expect(trigger().value).toBe('modelfile');
  });

  it('модель без display_name показывается последним сегментом пути', async () => {
    await renderSel({ preferredPath: 'nosrc/y' });
    expect(trigger().value).toBe('y');
  });

  it('метка поля: по умолчанию по виду модели, либо переданная; справка с tooltip', async () => {
    const { unmount } = await renderSel();
    expect(screen.getAllByText('Модель эмбеддингов').length).toBeGreaterThan(0);
    unmount();
    const r2 = await renderSel({ kind: 'reranker' });
    expect(screen.getAllByText('Модель реранкера').length).toBeGreaterThan(0);
    r2.unmount();
    await renderSel({ label: 'Своя метка', helpTooltip: 'Подсказка' });
    expect(screen.getAllByText('Своя метка').length).toBeGreaterThan(0);
    expect(screen.getByLabelText('Справка: Своя метка')).toBeInTheDocument();
  });

  it('пустой ответ: «Модели эмбеддингов недоступны» (+ пометка об offline)', async () => {
    listBody = { models: { embedding: [] }, offline: true };
    const { unmount } = await renderSel();
    expect(screen.getByText(/Модели эмбеддингов недоступны/)).toHaveTextContent('(только локальные модели)');
    unmount();
    listBody = { models: { reranker: [] } };
    await renderSel({ kind: 'reranker' });
    const el = screen.getByText(/Модели реранкера недоступны/);
    expect(el).not.toHaveTextContent('только локальные');
  });

  it('все модели local: подсказка об offline в сообщении недоступности не требуется, но список строится', async () => {
    listBody = { models: { embedding: [ROWS[0]] }, current: { embedding: { path: 'local/bge-m3' } } };
    await renderSel();
    expect(trigger().value).toBe('BGE M3');
  });

  it('не-ok ответ и сетевая ошибка: показывается «недоступны»', async () => {
    listBody = null;
    const { unmount } = await renderSel();
    expect(screen.getByText(/недоступны/)).toBeInTheDocument();
    unmount();
    fetchMock.mockRejectedValue(new Error('down'));
    await renderSel();
    expect(screen.getByText(/недоступны/)).toBeInTheDocument();
  });

  it('ответ без models: пустой список', async () => {
    listBody = {};
    await renderSel();
    expect(screen.getByText(/недоступны/)).toBeInTheDocument();
  });
});

describe('RagModelSelector: выпадающий список', () => {
  it('группирует по источникам в порядке Локальные, CORSUR, PHOENIX, затем прочие; модели без пути скрыты', async () => {
    await renderSel();
    await openPanel();
    const labels = ['Локальные', 'CORSUR', 'PHOENIX', 'WEIRD'].map((l) => screen.getByText(l));
    const order = labels.map((l) => l.compareDocumentPosition(labels[0]));
    expect(labels.every((l) => l.isConnected)).toBe(true);
    // Порядок в DOM
    const texts = Array.from(document.querySelectorAll('.MuiPopover-root p')).map((p) => p.textContent);
    expect(texts.indexOf('Локальные')).toBeLessThan(texts.indexOf('CORSUR'));
    expect(texts.indexOf('CORSUR')).toBeLessThan(texts.indexOf('PHOENIX'));
    expect(texts.indexOf('PHOENIX')).toBeLessThan(texts.indexOf('WEIRD'));
    expect(order.length).toBe(4);
    expect(screen.queryByText('Нет пути')).toBeNull();
  });

  it('открывается на источнике с выбранной моделью, отмечая её галочкой', async () => {
    await renderSel();
    await openPanel();
    expect(screen.getByText('BGE M3', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByTestId('CheckIcon')).toBeInTheDocument();
  });

  it('без выбранной модели открывается на первом источнике', async () => {
    listBody.current = {};
    await renderSel();
    await openPanel();
    expect(screen.getByText('BGE M3', { selector: 'p' })).toBeInTheDocument();
  });

  it('наведение на источник показывает его модели; недоступная модель не кликабельна', async () => {
    await renderSel();
    await openPanel();
    fireEvent.mouseEnter(screen.getByText('PHOENIX'));
    const gte = await screen.findByText('GTE', { selector: 'p' });
    fetchMock.mockClear();
    fireEvent.click(gte);
    await act(async () => {});
    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it('поиск фильтрует по имени, display_name и пути, пустой результат - «Ничего не найдено»', async () => {
    await renderSel();
    await openPanel();
    fireEvent.mouseEnter(screen.getByText('CORSUR'));
    const search = screen.getByPlaceholderText('Поиск моделей...');
    fireEvent.change(search, { target: { value: 'E5' } });
    expect(screen.getByText('E5 Large', { selector: 'p' })).toBeInTheDocument();
    fireEvent.change(search, { target: { value: 'corsur/' } });
    expect(screen.getByText('E5 Large', { selector: 'p' })).toBeInTheDocument();
    fireEvent.change(search, { target: { value: 'zzz' } });
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
    fireEvent.change(search, { target: { value: '' } });
    expect(screen.getByText('E5 Large', { selector: 'p' })).toBeInTheDocument();
  });

  it('смена источника сбрасывает поиск', async () => {
    await renderSel();
    await openPanel();
    const search = screen.getByPlaceholderText('Поиск моделей...') as HTMLInputElement;
    fireEvent.change(search, { target: { value: 'abc' } });
    fireEvent.mouseEnter(screen.getByText('CORSUR'));
    expect((screen.getByPlaceholderText('Поиск моделей...') as HTMLInputElement).value).toBe('');
  });

  it('уход мыши с панели скрывает правую часть', async () => {
    await renderSel();
    await openPanel();
    const panel = screen.getByPlaceholderText('Поиск моделей...').closest('.MuiPopover-root .MuiBox-root') as HTMLElement;
    const root = document.querySelector('.MuiPopover-paper > div') as HTMLElement;
    fireEvent.mouseLeave(root);
    await waitFor(() => expect(screen.queryByPlaceholderText('Поиск моделей...')).toBeNull());
    expect(panel).toBeTruthy();
  });

  it('закрытие по Escape', async () => {
    await renderSel();
    await openPanel();
    fireEvent.keyDown(document.querySelector('.MuiPopover-root') as HTMLElement, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByPlaceholderText('Поиск моделей...')).toBeNull());
  });

  it('disabled: клик не открывает список', async () => {
    await renderSel({ disabled: true });
    fireEvent.click(trigger());
    expect(screen.queryByPlaceholderText('Поиск моделей...')).toBeNull();
  });

  it('пустой фильтр источника: «Нет доступных моделей»', async () => {
    listBody = { models: { embedding: [ROWS[0]] }, current: {} };
    await renderSel();
    await openPanel();
    const search = screen.getByPlaceholderText('Поиск моделей...');
    fireEvent.change(search, { target: { value: '   ' } });
    expect(screen.getByText('BGE M3', { selector: 'p' })).toBeInTheDocument();
  });

  it('выбор уже выбранной модели закрывает список без запроса', async () => {
    await renderSel();
    await openPanel();
    fetchMock.mockClear();
    fireEvent.click(screen.getByText('BGE M3', { selector: 'p' }));
    await waitFor(() => expect(screen.queryByPlaceholderText('Поиск моделей...')).toBeNull());
    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });
});

describe('RagModelSelector: применение выбора', () => {
  async function pickCorsur(props: Partial<React.ComponentProps<typeof RagModelSelector>> = {}) {
    await renderSel(props);
    await openPanel();
    fireEvent.mouseEnter(screen.getByText('CORSUR'));
    fireEvent.click(await screen.findByText('E5 Large', { selector: 'p' }));
  }

  it('embedding: подтверждение, затем POST с model_type, model_path и scope; уведомление; колбэк; перезагрузка', async () => {
    const onSel = jest.fn();
    selectResp = { ok: true, body: { reindexed: false } };
    await pickCorsur({ onModelSelect: onSel, scope: 'agent', entityId: 3 });
    await waitFor(() => expect(onSel).toHaveBeenCalledWith('corsur/e5'));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('переиндексирует'));
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/models/select'))!;
    expect(call[1].method).toBe('POST');
    expect(call[1].headers).toEqual({ Authorization: 'Bearer T', 'Content-Type': 'application/json' });
    expect(JSON.parse(call[1].body)).toEqual({ model_type: 'embedding', model_path: 'corsur/e5', scope: 'agent' });
    expect(mockNotify).toHaveBeenCalledWith('success', 'Модель RAG успешно загружена');
    expect(mockReindex).not.toHaveBeenCalled();
    // после выбора список перечитан
    expect(fetchMock.mock.calls.filter((c) => String(c[0]).includes('/api/rag/models?')).length).toBeGreaterThanOrEqual(2);
  });

  it('reindexed=true: уведомляется контекст переиндексации и показывается соответствующее сообщение', async () => {
    selectResp = { ok: true, body: { reindexed: true } };
    await pickCorsur();
    await waitFor(() => expect(mockReindex).toHaveBeenCalledTimes(1));
    expect(mockNotify).toHaveBeenCalledWith(
      'success',
      'Модель загружена. Запущена переиндексация ваших документов (без Memory RAG).',
    );
  });

  it('отказ в confirm: запрос не отправляется, выбранная модель не меняется', async () => {
    (window.confirm as jest.Mock).mockReturnValue(false);
    await pickCorsur();
    await act(async () => {});
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/models/select'))).toBe(false);
    expect(trigger().value).toBe('BGE M3');
  });

  it('reranker: подтверждение не запрашивается', async () => {
    listBody = {
      models: {
        reranker: [
          { path: 'local/rr', name: 'rr', display_name: 'RR', source: 'local' },
          { path: 'local/rr2', name: 'rr2', display_name: 'RR2', source: 'local' },
        ],
      },
      current: { reranker: { path: 'local/rr' } },
    };
    await renderSel({ kind: 'reranker' });
    await openPanel();
    fireEvent.click(screen.getByText('RR2', { selector: 'p' }));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Модель RAG успешно загружена'));
    expect(window.confirm).not.toHaveBeenCalled();
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/models/select'))!;
    expect(JSON.parse(call[1].body).model_type).toBe('reranker');
  });

  it('ошибка сервера с detail: уведомление об ошибке, прежний выбор восстановлен', async () => {
    selectResp = { ok: false, body: { detail: 'Размерность не совпадает' } };
    await pickCorsur();
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Размерность не совпадает'),
    );
    expect(trigger().value).toBe('BGE M3');
  });

  it('ошибка сервера с message, либо без деталей', async () => {
    selectResp = { ok: false, body: { message: 'Занято' } };
    const { unmount } = await (async () => {
      await pickCorsur();
      return { unmount: () => {} };
    })();
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Занято'));
    unmount();
  });

  it('ошибка сервера без тела: «Не удалось загрузить модель»', async () => {
    selectResp = { ok: false, body: {} };
    await pickCorsur();
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Не удалось загрузить модель'),
    );
  });

  it('сетевая ошибка и не-Error исключение отображаются в уведомлении', async () => {
    selectResp = { ok: true, body: {}, reject: true };
    await pickCorsur();
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: net'));
  });

  it('невалидный JSON ответа при ошибке: используется текст по умолчанию', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/models/select')) {
        return { ok: false, json: async () => { throw new Error('bad'); } } as any;
      }
      return { ok: true, json: async () => listBody } as any;
    });
    await pickCorsur();
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Не удалось загрузить модель'),
    );
  });

  it('во время применения показывается индикатор, поиск в панели заблокирован', async () => {
    let release: (v: any) => void = () => {};
    fetchMock.mockImplementation((url: string) =>
      String(url).includes('/models/select')
        ? new Promise((r) => (release = r))
        : Promise.resolve({ ok: true, json: async () => listBody }),
    );
    await pickCorsur();
    await waitFor(() => expect(screen.getByRole('progressbar')).toBeInTheDocument());
    // в поле показывается имя загружаемой модели
    expect(trigger().value).toBe('E5 Large');
    // панель ещё открыта (закроется после успеха), но поиск заблокирован
    expect(screen.getByPlaceholderText('Поиск моделей...')).toBeDisabled();
    await act(async () => {
      release({ ok: true, json: async () => ({}) });
    });
    await waitFor(() => expect(screen.queryByRole('progressbar')).toBeNull());
  });

  it('deferApply: API не вызывается, обновляется UI и колбэк', async () => {
    const onSel = jest.fn();
    await renderSel({ deferApply: true, onModelSelect: onSel });
    await openPanel();
    fetchMock.mockClear();
    fireEvent.mouseEnter(screen.getByText('CORSUR'));
    fireEvent.click(await screen.findByText('E5 Large', { selector: 'p' }));
    await waitFor(() => expect(onSel).toHaveBeenCalledWith('corsur/e5'));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
    expect(trigger().value).toBe('E5 Large');
  });

  it('deferApply без колбэка не падает; тёмная тема и fieldSx принимаются', async () => {
    await renderSel({ deferApply: true, isDarkMode: true, fieldSx: { width: 300 }, triggerMaxWidth: null });
    await openPanel();
    fireEvent.mouseEnter(screen.getByText('CORSUR'));
    fireEvent.click(await screen.findByText('E5 Large', { selector: 'p' }));
    await waitFor(() => expect(trigger().value).toBe('E5 Large'));
  });
});
