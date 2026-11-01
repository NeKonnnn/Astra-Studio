/* eslint-disable @typescript-eslint/no-explicit-any */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import AgentGalleryPage, { AgentGalleryContent } from '@/pages/AgentGalleryPage';

const mock: any = {};
function reset() {
  mock.token = 'tok';
  mock.canUseAgents = true;
  mock.notify = jest.fn();
  mock.navigate = jest.fn();
  mock.loadModel = jest.fn(async () => ({ ok: true }));
  mock.persistMcp = jest.fn();
  mock.persistArtifacts = jest.fn();
  mock.openInConstructor = jest.fn();
  mock.cloneDeep = jest.fn(async () => ({ agentId: 77, clonedAgents: 1, clonedAgentIds: [77] }));
}
reset();

jest.mock('react-router-dom', () => ({
  useNavigate: () => mock.navigate,
  Navigate: ({ to, replace }: any) => <div data-testid="navigate" data-to={to} data-replace={String(replace)} />,
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a), getApiUrl: (p: string) => `http://api${p}` }));
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ token: mock.token }) }));
jest.mock('@/contexts/AppContext', () => ({ useAppActions: () => ({ showNotification: mock.notify }) }));
jest.mock('@/hooks/useFeatureAccess', () => ({ useFeatureAccess: () => ({ canUseAgents: mock.canUseAgents }) }));
jest.mock('@/icons/AgentIcon', () => ({ __esModule: true, default: () => <span data-testid="agent-icon" /> }));
jest.mock('@/utils/applyAgentServer', () => ({ loadAgentModelOnly: (...a: any[]) => mock.loadModel(...a) }));
jest.mock('@/utils/applyAgentMcp', () => ({ persistAgentMcpConfig: (...a: any[]) => mock.persistMcp(...a) }));
jest.mock('@/utils/agentArtifactsEnabled', () => ({
  persistAgentArtifactsEnabled: (...a: any[]) => mock.persistArtifacts(...a),
}));
jest.mock('@/utils/openAgentConstructorNav', () => ({
  openAgentInConstructor: (...a: any[]) => mock.openInConstructor(...a),
}));
jest.mock('@/utils/cloneAgentDeep', () => ({ cloneAgentDeep: (...a: any[]) => mock.cloneDeep(...a) }));
jest.mock('@/components/galleryCards', () => ({
  GalleryEntityCard: (p: any) => (
    <div data-testid={`card-${p.item.id}`} data-using={String(p.using)} data-cloning={String(p.cloning)}>
      <span data-testid={`item-${p.item.id}`}>{JSON.stringify(p.item)}</span>
      <button onClick={p.onOpen}>open-{p.item.id}</button>
      <button onClick={() => p.onRate(4)}>rate-{p.item.id}</button>
      <button onClick={p.onUse}>use-{p.item.id}</button>
      <button onClick={p.onClone}>clone-{p.item.id}</button>
      <button onClick={p.onToggleBookmark}>bm-{p.item.id}</button>
    </div>
  ),
  GallerySearchBookmarksBar: (p: any) => (
    <div>
      <input
        placeholder={p.searchPlaceholder}
        value={p.searchQuery}
        onChange={(e) => p.onSearchChange(e.target.value)}
      />
      <button onClick={p.onToggleBookmarks}>toggle-bookmarks</button>
      <span data-testid="bm-state" data-show={String(p.showBookmarks)} data-enabled={String(p.bookmarksEnabled)} />
    </div>
  ),
}));

const fetchMock = jest.fn();
const ok = (data: any = {}) => ({ ok: true, status: 200, json: async () => data });
const bad = (data: any = {}) => ({ ok: false, status: 400, json: async () => data });

const agent = (over: any = {}) => ({
  id: 1,
  name: 'Помощник',
  description: 'Описание',
  system_prompt: 'Ты помощник',
  config: { model: 'llm-svc://m1' },
  author_id: 'u1',
  author_name: 'ivan',
  author_full_name: 'Иван Петров',
  is_public: true,
  usage_count: 3,
  views_count: 10,
  average_rating: 4.5,
  total_votes: 2,
  user_rating: null,
  is_bookmarked: false,
  ...over,
});

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}
async function renderContent(list: any = { agents: [agent()], pages: 1 }, props: any = {}) {
  fetchMock.mockResolvedValueOnce(ok(list));
  const utils = render(<AgentGalleryContent {...props} />);
  await flush();
  return utils;
}

describe('AgentGalleryPage', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
    reset();
    localStorage.clear();
    fetchMock.mockReset();
    (global as any).fetch = fetchMock;
    jest.spyOn(window, 'confirm').mockImplementation(() => true);
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('страница-обёртка перенаправляет на вкладку агентов галереи', () => {
    render(<AgentGalleryPage />);
    const nav = screen.getByTestId('navigate');
    expect(nav.getAttribute('data-to')).toBe('/gallery?tab=agents');
    expect(nav.getAttribute('data-replace')).toBe('true');
  });

  describe('загрузка списка', () => {
    it('запрашивает первую страницу с токеном и сортировкой по рейтингу', async () => {
      await renderContent();
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe('http://api/api/agents/?page=1&limit=20&sort_by=rating&sort_order=desc');
      expect(init.headers).toEqual({ Authorization: 'Bearer tok' });
      expect(screen.getByTestId('card-1')).toBeTruthy();
    });

    it('без токена заголовок авторизации не добавляется', async () => {
      mock.token = null;
      await renderContent();
      expect(fetchMock.mock.calls[0][1].headers).toEqual({});
      expect(screen.getByTestId('bm-state').getAttribute('data-enabled')).toBe('false');
    });

    it('карточка: описание, обрезка длинного промпта, автор', async () => {
      const long = 'x'.repeat(200);
      await renderContent({
        agents: [
          agent({ id: 1, description: '  Кратко  ' }),
          agent({ id: 2, description: '', system_prompt: long, author_full_name: null }),
          agent({ id: 3, description: undefined, system_prompt: 'короткий' }),
        ],
      });
      const item = (id: number) => JSON.parse(screen.getByTestId(`item-${id}`).textContent!);
      expect(item(1)).toMatchObject({
        title: 'Помощник', preview: 'Кратко', authorName: 'Иван Петров · ivan',
        viewsCount: 10, usageCount: 3, averageRating: 4.5, totalVotes: 2,
      });
      expect(item(2).preview).toBe('x'.repeat(160) + '…');
      expect(item(2).authorName).toBe('ivan');
      expect(item(3).preview).toBe('короткий');
    });

    it('без права на агентов: сообщение и никаких запросов', async () => {
      mock.canUseAgents = false;
      render(<AgentGalleryContent />);
      await flush();
      expect(screen.getByText('Агенты недоступны для вашей роли.')).toBeTruthy();
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('пустой список — подсказка про публикацию', async () => {
      await renderContent({ agents: [] });
      expect(screen.getByText(/Публичных агентов пока нет/)).toBeTruthy();
    });

    it('ответ без agents/pages даёт пустой список', async () => {
      await renderContent({});
      expect(screen.getByText(/Публичных агентов пока нет/)).toBeTruthy();
      expect(screen.queryByRole('navigation')).toBeNull();
    });

    it('HTTP-ошибка и сетевой сбой дают пустой список без исключений', async () => {
      fetchMock.mockResolvedValueOnce(bad());
      const a = render(<AgentGalleryContent />);
      await flush();
      expect(screen.getByText(/Публичных агентов пока нет/)).toBeTruthy();
      a.unmount();
      fetchMock.mockRejectedValueOnce(new Error('net'));
      render(<AgentGalleryContent />);
      await flush();
      expect(screen.getByText(/Публичных агентов пока нет/)).toBeTruthy();
    });

    it('пока идёт загрузка, виден спиннер', async () => {
      fetchMock.mockReturnValueOnce(new Promise(() => {}));
      render(<AgentGalleryContent />);
      await flush();
      expect(screen.getByRole('progressbar')).toBeTruthy();
    });

    it('пагинация: переход на вторую страницу запрашивает page=2', async () => {
      await renderContent({ agents: [agent()], pages: 3 });
      fetchMock.mockResolvedValueOnce(ok({ agents: [agent({ id: 2 })], pages: 3 }));
      fireEvent.click(screen.getByLabelText('Go to page 2'));
      await flush();
      expect(fetchMock.mock.calls[1][0]).toContain('page=2');
      expect(screen.getByTestId('card-2')).toBeTruthy();
    });
  });

  describe('поиск и закладки', () => {
    it('поиск с задержкой 400 мс добавляет search к запросу', async () => {
      await renderContent();
      fetchMock.mockResolvedValue(ok({ agents: [agent({ id: 5 })], pages: 1 }));
      fireEvent.change(screen.getByPlaceholderText('Поиск агентов…'), { target: { value: '  юрист ' } });
      act(() => { jest.advanceTimersByTime(399); });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      act(() => { jest.advanceTimersByTime(2); });
      await flush();
      const url = fetchMock.mock.calls[1][0] as string;
      expect(url).toContain('search=%D1%8E%D1%80%D0%B8%D1%81%D1%82');
      expect(url).toContain('page=1');
      expect(screen.getByTestId('card-5')).toBeTruthy();
    });

    it('поиск на 2-й странице уходит с page=1 (BUG-FE-66)', async () => {
      await renderContent({ agents: [agent()], pages: 3 });
      fetchMock.mockResolvedValueOnce(ok({ agents: [agent({ id: 2 })], pages: 3 }));
      fireEvent.click(screen.getByLabelText('Go to page 2'));
      await flush();
      fetchMock.mockResolvedValue(ok({ agents: [agent({ id: 5 })], pages: 3 }));
      fireEvent.change(screen.getByPlaceholderText('Поиск агентов…'), { target: { value: 'юрист' } });
      act(() => { jest.advanceTimersByTime(500); });
      await flush();
      const urls = fetchMock.mock.calls.map((c) => c[0] as string);
      expect(urls[urls.length - 1]).toContain('search=');
      expect(urls[urls.length - 1]).toContain('page=1');
      // ни один запрос с новым поиском не уходит со старой страницей
      urls.filter((u) => u.includes('search=')).forEach((u) => expect(u).not.toContain('page=2'));
      expect(screen.getByTestId('card-5')).toBeTruthy();
    });

    it('запрос, начатый до смены поиска, перезапускается: устаревшие результаты не показываются (BUG-FE-66)', async () => {
      let resolveFirst!: (v: any) => void;
      fetchMock.mockReturnValueOnce(new Promise((r) => { resolveFirst = r; }));
      fetchMock.mockResolvedValue(ok({ agents: [agent({ id: 5 })], pages: 1 }));
      render(<AgentGalleryContent />);
      await flush();
      fireEvent.change(screen.getByPlaceholderText('Поиск агентов…'), { target: { value: 'юрист' } });
      act(() => { jest.advanceTimersByTime(500); });
      await flush();
      // первый (нефильтрованный) ответ приходит позже и не должен затирать результаты поиска
      await act(async () => { resolveFirst(ok({ agents: [agent({ id: 9 })], pages: 1 })); });
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(fetchMock.mock.calls[1][0]).toContain('search=');
      expect(screen.getByTestId('card-5')).toBeTruthy();
      expect(screen.queryByTestId('card-9')).toBeNull();
    });

    it('режим закладок использует отдельный эндпоинт и подсказку о пустых закладках', async () => {
      await renderContent();
      fetchMock.mockResolvedValueOnce(ok({ agents: [], pages: 1 }));
      fireEvent.click(screen.getByText('toggle-bookmarks'));
      await flush();
      expect(fetchMock.mock.calls[1][0]).toBe('http://api/api/agents/my/bookmarks?page=1&limit=20');
      expect(screen.getByText(/В закладках пока нет агентов/)).toBeTruthy();
    });

    it('закладки без токена: запрос не выполняется, список пуст', async () => {
      mock.token = null;
      await renderContent();
      fetchMock.mockClear();
      fireEvent.click(screen.getByText('toggle-bookmarks'));
      await flush();
      expect(fetchMock).not.toHaveBeenCalled();
      expect(screen.getByText(/В закладках пока нет агентов/)).toBeTruthy();
    });
  });

  describe('оформление', () => {
    it('встроенный режим без заголовка и кнопки «К чату»', async () => {
      await renderContent(undefined, { embedded: true });
      expect(screen.queryByText('Галерея агентов')).toBeNull();
      expect(screen.getAllByText(/Нажмите на карточку/).length).toBe(1);
    });

    it('обычный режим: заголовок и «К чату» ведёт на главную', async () => {
      await renderContent();
      expect(screen.getByText('Галерея агентов')).toBeTruthy();
      fireEvent.click(screen.getByText('К чату'));
      expect(mock.navigate).toHaveBeenCalledWith('/');
    });

    it('клик по карточке открывает агента в конструкторе', async () => {
      await renderContent();
      fireEvent.click(screen.getByText('open-1'));
      expect(mock.openInConstructor).toHaveBeenCalledWith(1, mock.navigate);
    });
  });

  describe('закладка', () => {
    it('без токена — ошибка, без запроса', async () => {
      mock.token = null;
      await renderContent();
      fetchMock.mockClear();
      fireEvent.click(screen.getByText('bm-1'));
      expect(mock.notify).toHaveBeenCalledWith('error', 'Для закладок нужно войти в систему');
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('добавление: POST и локальный флаг is_bookmarked', async () => {
      await renderContent();
      fetchMock.mockResolvedValueOnce(ok());
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/agents/1/bookmark', {
        method: 'POST',
        headers: { Authorization: 'Bearer tok' },
      });
      expect(mock.notify).toHaveBeenCalledWith('success', 'Добавлено в закладки');
      expect(JSON.parse(screen.getByTestId('item-1').textContent!).isBookmarked).toBe(true);
    });

    it('удаление в обычном режиме: DELETE и снятие флага', async () => {
      await renderContent({ agents: [agent({ is_bookmarked: true })] });
      fetchMock.mockResolvedValueOnce(ok());
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(fetchMock.mock.calls[1][1].method).toBe('DELETE');
      expect(mock.notify).toHaveBeenCalledWith('success', 'Удалено из закладок');
      expect(JSON.parse(screen.getByTestId('item-1').textContent!).isBookmarked).toBe(false);
    });

    it('удаление в режиме закладок убирает карточку из списка', async () => {
      await renderContent();
      fetchMock.mockResolvedValueOnce(ok({ agents: [agent({ is_bookmarked: true })], pages: 1 }));
      fireEvent.click(screen.getByText('toggle-bookmarks'));
      await flush();
      fetchMock.mockResolvedValueOnce(ok());
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(screen.queryByTestId('card-1')).toBeNull();
    });

    it('ошибки: detail сервера, запасной текст, не-Error', async () => {
      await renderContent();
      fetchMock.mockResolvedValueOnce(bad({ detail: 'Лимит закладок' }));
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Лимит закладок');
      fetchMock.mockResolvedValueOnce({ ok: false, json: async () => { throw new Error('x'); } });
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Не удалось изменить закладку');
      fetchMock.mockRejectedValueOnce('str');
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Ошибка закладки');
    });
  });

  describe('оценка', () => {
    it('без токена — ошибка', async () => {
      mock.token = null;
      await renderContent();
      fireEvent.click(screen.getByText('rate-1'));
      expect(mock.notify).toHaveBeenCalledWith('error', 'Для оценки нужно войти в систему');
    });

    it('успех: POST с рейтингом, уведомление и перезагрузка списка', async () => {
      await renderContent();
      fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok({ agents: [agent({ id: 3 })], pages: 1 }));
      await act(async () => { fireEvent.click(screen.getByText('rate-1')); });
      await flush();
      const [url, init] = fetchMock.mock.calls[1];
      expect(url).toBe('http://api/api/agents/1/rate');
      expect(init.method).toBe('POST');
      expect(JSON.parse(init.body)).toEqual({ rating: 4 });
      expect(init.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer tok' });
      expect(mock.notify).toHaveBeenCalledWith('success', 'Оценка сохранена');
      expect(screen.getByTestId('card-3')).toBeTruthy();
    });

    it('ошибки: detail, запасной текст, не-Error', async () => {
      await renderContent();
      fetchMock.mockResolvedValueOnce(bad({ detail: 'Нельзя оценивать своё' }));
      await act(async () => { fireEvent.click(screen.getByText('rate-1')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Нельзя оценивать своё');
      fetchMock.mockResolvedValueOnce({ ok: false, json: async () => { throw new Error('x'); } });
      await act(async () => { fireEvent.click(screen.getByText('rate-1')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Не удалось сохранить оценку');
      fetchMock.mockRejectedValueOnce(42);
      await act(async () => { fireEvent.click(screen.getByText('rate-1')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Ошибка оценки');
    });
  });

  describe('клонирование', () => {
    it('без токена — ошибка и без подтверждения', async () => {
      mock.token = null;
      await renderContent();
      fireEvent.click(screen.getByText('clone-1'));
      expect(mock.notify).toHaveBeenCalledWith('error', 'Для клонирования агента нужно войти в систему');
      expect(window.confirm).not.toHaveBeenCalled();
    });

    it('отказ в подтверждении ничего не делает', async () => {
      (window.confirm as jest.Mock).mockReturnValue(false);
      await renderContent();
      await act(async () => { fireEvent.click(screen.getByText('clone-1')); });
      expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('«Помощник»'));
      expect(mock.cloneDeep).not.toHaveBeenCalled();
    });

    it('одиночный клон: сообщение и открытие в конструкторе со связанными id', async () => {
      await renderContent();
      await act(async () => { fireEvent.click(screen.getByText('clone-1')); });
      expect(mock.cloneDeep).toHaveBeenCalledWith(1, 'tok');
      expect(mock.notify).toHaveBeenCalledWith('success', 'Агент полностью склонирован');
      expect(mock.openInConstructor).toHaveBeenCalledWith(77, mock.navigate, { relatedAgentIds: [77] });
      expect(screen.getByTestId('card-1').getAttribute('data-cloning')).toBe('false');
    });

    it('клон с цепочкой: сообщение со счётчиком; во время клонирования карточка помечена', async () => {
      let resolve!: (v: any) => void;
      mock.cloneDeep.mockReturnValueOnce(new Promise((r) => { resolve = r; }));
      await renderContent();
      await act(async () => { fireEvent.click(screen.getByText('clone-1')); });
      expect(screen.getByTestId('card-1').getAttribute('data-cloning')).toBe('true');
      await act(async () => { resolve({ agentId: 5, clonedAgents: 3, clonedAgentIds: [5, 6, 7] }); });
      expect(mock.notify).toHaveBeenCalledWith('success', 'Склонировано агентов: 3 (родитель + цепочка/субагенты)');
    });

    it('ошибка клонирования: message или запасной текст', async () => {
      await renderContent();
      mock.cloneDeep.mockRejectedValueOnce(new Error('нет места'));
      await act(async () => { fireEvent.click(screen.getByText('clone-1')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'нет места');
      mock.cloneDeep.mockRejectedValueOnce('x');
      await act(async () => { fireEvent.click(screen.getByText('clone-1')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Ошибка клонирования агента');
      expect(mock.openInConstructor).not.toHaveBeenCalled();
    });
  });

  describe('использование агента', () => {
    const clickUse = async () => {
      await act(async () => { fireEvent.click(screen.getByText('use-1')); });
    };

    it('без токена — ошибка', async () => {
      mock.token = null;
      await renderContent();
      fireEvent.click(screen.getByText('use-1'));
      expect(mock.notify).toHaveBeenCalledWith('error', 'Для использования агента нужно войти в систему');
    });

    it('полный сценарий: закладка, use, детали, применение модели, localStorage, событие и переход через 800 мс', async () => {
      await renderContent({ agents: [agent({ config: { model: ' 1lm-svc://my model ' } })] });
      const selected: any[] = [];
      const on = (e: Event) => selected.push((e as CustomEvent).detail);
      window.addEventListener('agentSelected', on);
      fetchMock
        .mockResolvedValueOnce(ok()) // bookmark
        .mockResolvedValueOnce(ok()) // use
        .mockResolvedValueOnce(ok({ system_prompt: 'Новый промпт', config: { model: 'llm-svc://full', mcp: 1 } }));
      await clickUse();
      window.removeEventListener('agentSelected', on);
      const calls = fetchMock.mock.calls.slice(1).map((c) => [c[0], c[1].method]);
      expect(calls).toEqual([
        ['http://api/api/agents/1/bookmark', 'POST'],
        ['http://api/api/agents/1/use', 'POST'],
        ['http://api/api/agents/1', undefined],
      ]);
      expect(mock.loadModel).toHaveBeenCalledWith('tok', 'llm-svc://full');
      expect(localStorage.getItem('active_agent_id')).toBe('1');
      expect(localStorage.getItem('active_agent_name')).toBe('Помощник');
      expect(localStorage.getItem('active_agent_prompt')).toBe('Новый промпт');
      expect(mock.persistMcp).toHaveBeenCalledWith({ model: 'llm-svc://full', mcp: 1 });
      expect(mock.persistArtifacts).toHaveBeenCalledWith({ model: 'llm-svc://full', mcp: 1 });
      expect(selected[0]).toMatchObject({ id: 1, system_prompt: 'Новый промпт' });
      expect(mock.notify).toHaveBeenCalledWith(
        'success',
        'Агент «Помощник» добавлен. Промпт и настройки применятся в чате. Переходим…',
      );
      expect(JSON.parse(screen.getByTestId('item-1').textContent!).isBookmarked).toBe(true);
      expect(mock.navigate).not.toHaveBeenCalled();
      act(() => { jest.advanceTimersByTime(800); });
      expect(mock.navigate).toHaveBeenCalledWith('/');
    });

    it('уже в закладках: повторный POST закладки не шлётся; ошибка загрузки деталей не фатальна', async () => {
      await renderContent({ agents: [agent({ is_bookmarked: true, config: { model_path: ' 1LM-SVC://a b' } })] });
      fetchMock
        .mockResolvedValueOnce(ok()) // use
        .mockRejectedValueOnce(new Error('detail down'));
      await clickUse();
      const methods = fetchMock.mock.calls.slice(1).map((c) => c[0]);
      expect(methods).toEqual(['http://api/api/agents/1/use', 'http://api/api/agents/1']);
      // имя модели нормализуется: опечатка 1lm-svc:// → llm-svc://, пробелы удаляются
      expect(mock.loadModel).toHaveBeenCalledWith('tok', 'llm-svc://ab');
      expect(localStorage.getItem('active_agent_prompt')).toBe('Ты помощник');
    });

    it('детали не ok: используются данные карточки; пустая модель передаётся как null', async () => {
      await renderContent({ agents: [agent({ is_bookmarked: true, config: undefined, system_prompt: '' })] });
      fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(bad());
      await clickUse();
      expect(mock.loadModel).toHaveBeenCalledWith('tok', null);
      expect(localStorage.getItem('active_agent_prompt')).toBe('');
      expect(mock.persistMcp).toHaveBeenCalledWith({});
    });

    it('модель ожидает первого сообщения (pending) — информационное уведомление', async () => {
      mock.loadModel.mockResolvedValueOnce({ ok: true, pending: true });
      await renderContent({ agents: [agent({ is_bookmarked: true })] });
      fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok({}));
      await clickUse();
      expect(mock.notify).toHaveBeenCalledWith(
        'info',
        'Агент «Помощник» добавлен. Модель подтянется при первом сообщении. Переходим…',
      );
    });

    it('настройки не применились — предупреждение с причиной', async () => {
      mock.loadModel.mockResolvedValueOnce({ ok: false, message: 'модель недоступна' });
      await renderContent({ agents: [agent({ is_bookmarked: true })] });
      fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok({}));
      await clickUse();
      expect(mock.notify).toHaveBeenCalledWith(
        'warning',
        'Агент «Помощник» добавлен в «Агенты из галереи», но настройки не применились: модель недоступна',
      );
    });

    it('исключение в процессе: уведомление и снятие индикатора использования', async () => {
      mock.loadModel.mockRejectedValueOnce(new Error('сбой модели'));
      await renderContent({ agents: [agent({ is_bookmarked: true })] });
      fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok({}));
      await clickUse();
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'сбой модели');
      expect(screen.getByTestId('card-1').getAttribute('data-using')).toBe('false');
      mock.loadModel.mockRejectedValueOnce('x');
      fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok({}));
      await clickUse();
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Ошибка использования агента');
    });

    it('во время использования карточка помечена как using', async () => {
      let resolve!: (v: any) => void;
      await renderContent({ agents: [agent({ is_bookmarked: true })] });
      fetchMock.mockReturnValueOnce(new Promise((r) => { resolve = r; }));
      await clickUse();
      expect(screen.getByTestId('card-1').getAttribute('data-using')).toBe('true');
      fetchMock.mockResolvedValueOnce(ok({}));
      await act(async () => { resolve(ok()); });
      expect(screen.getByTestId('card-1').getAttribute('data-using')).toBe('false');
    });
  });
});
