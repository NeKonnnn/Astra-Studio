/* eslint-disable @typescript-eslint/no-explicit-any */
import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import SkillsPage, { SkillsGalleryContent } from '@/pages/SkillsPage';

const mock: any = {};
function reset() {
  mock.token = 'tok';
  mock.user = { is_admin: false };
  mock.canPublish = true;
  mock.notify = jest.fn();
  mock.navigate = jest.fn();
  mock.params = new URLSearchParams();
  mock.setParams = jest.fn();
  mock.notifyChanged = jest.fn();
  mock.openSidebar = jest.fn();
  mock.toggleActive = jest.fn();
}
reset();

jest.mock('react-router-dom', () => ({
  useNavigate: () => mock.navigate,
  useSearchParams: () => [mock.params, mock.setParams],
  Navigate: ({ to, replace }: any) => <div data-testid="navigate" data-to={to} data-replace={String(replace)} />,
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
  API_ENDPOINTS: { SKILLS: '/api/skills' },
}));
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ token: mock.token, user: mock.user }) }));
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mock.notify }),
  useAppContext: () => ({ state: { currentChatId: 'chat-1' } }),
}));
jest.mock('@/hooks/useFeatureAccess', () => ({ useFeatureAccess: () => ({ canPublishToLibrary: mock.canPublish }) }));
jest.mock('@/icons/SkillsIcon', () => ({ __esModule: true, default: () => <span data-testid="skill-icon" /> }));
jest.mock('@/components/skills/SkillFilesEditor', () => ({ __esModule: true, default: () => <div data-testid="files-editor" /> }));
jest.mock('@/utils/skillsImportExport', () => ({
  slugifySkillName: (s: string) => s.toLowerCase().replace(/\s+/g, '-'),
  formatSkillsApiDetail: (d: unknown, fb: string) => (typeof d === 'string' && d ? d : fb),
  notifySkillsChanged: () => mock.notifyChanged(),
}));
jest.mock('@/utils/openSkillSidebarNav', () => ({ openSkillInSidebar: (...a: any[]) => mock.openSidebar(...a) }));
jest.mock('@/utils/skillSelectionStorage', () => ({ toggleActiveSkill: (...a: any[]) => mock.toggleActive(...a) }));
jest.mock('@/components/galleryCards', () => ({
  GalleryEntityCard: (p: any) => (
    <div data-testid={`card-${p.item.id}`} data-using={String(p.using)}>
      <span data-testid={`item-${p.item.id}`}>{JSON.stringify(p.item)}</span>
      <button onClick={p.onOpen}>open-{p.item.id}</button>
      <button onClick={() => p.onRate(5)}>rate-{p.item.id}</button>
      <button onClick={p.onUse}>use-{p.item.id}</button>
      <button onClick={p.onToggleBookmark}>bm-{p.item.id}</button>
    </div>
  ),
  GallerySearchBookmarksBar: (p: any) => (
    <div>
      <input placeholder={p.searchPlaceholder} value={p.searchQuery} onChange={(e) => p.onSearchChange(e.target.value)} />
      <button onClick={p.onToggleBookmarks}>toggle-bookmarks</button>
    </div>
  ),
}));

const fetchMock = jest.fn();
const ok = (data: any = {}) => ({ ok: true, status: 200, json: async () => data });
const bad = (data: any = {}) => ({ ok: false, status: 400, json: async () => data });

const skill = (over: any = {}) => ({
  id: 1,
  slug: 'my-skill',
  name: 'Мой скилл',
  display_title: 'Заголовок',
  description: 'Описание',
  content: 'Контент',
  is_active: true,
  is_public: true,
  author_id: 'u1',
  author_name: 'ivan',
  author_full_name: 'Иван Петров',
  created_at: '',
  updated_at: '',
  category: 'dev',
  file_count: 2,
  views_count: 5,
  usage_count: 1,
  average_rating: 4,
  total_votes: 3,
  user_rating: 2,
  is_bookmarked: false,
  ...over,
});

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}
async function renderContent(list: any = { items: [skill()], pages: 1 }, props: any = {}) {
  fetchMock.mockResolvedValueOnce(ok(list));
  const utils = render(<SkillsGalleryContent {...props} />);
  await flush();
  return utils;
}
const item = (id: number) => JSON.parse(screen.getByTestId(`item-${id}`).textContent!);

describe('SkillsPage', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
    reset();
    sessionStorage.clear();
    fetchMock.mockReset();
    (global as any).fetch = fetchMock;
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('страница-обёртка перенаправляет на вкладку skills', () => {
    render(<SkillsPage />);
    expect(screen.getByTestId('navigate').getAttribute('data-to')).toBe('/gallery?tab=skills');
  });

  describe('список', () => {
    it('запрашивает публичные skills первой страницы с токеном', async () => {
      await renderContent();
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe('http://api/api/skills/list?page=1&limit=12&view_option=public');
      expect(init.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer tok' });
    });

    it('без токена запрос не выполняется', async () => {
      mock.token = null;
      render(<SkillsGalleryContent />);
      await flush();
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('карточка: заголовок, превью, метастрока, автор', async () => {
      await renderContent({
        items: [
          skill(),
          skill({ id: 2, display_title: null, description: '', content: 'x'.repeat(200), category: null, file_count: 0, author_full_name: null }),
          skill({ id: 3, description: '', content: '', category: null, file_count: undefined }),
          skill({ id: 4, description: '', content: 'короткий', slug: '' }),
        ],
      });
      expect(item(1)).toMatchObject({
        title: 'Заголовок', preview: 'Описание', metaLine: '$my-skill · dev · 2 файл(ов)',
        authorName: 'Иван Петров · ivan', viewsCount: 5, usageCount: 1, averageRating: 4, totalVotes: 3, userRating: 2,
      });
      expect(item(2).title).toBe('Мой скилл');
      expect(item(2).preview).toBe('x'.repeat(160) + '…');
      expect(item(2).metaLine).toBe('$my-skill');
      expect(item(3).preview).toBe('$my-skill');
      expect(item(4).preview).toBe('короткий');
      expect(item(4).metaLine).toBe('dev · 2 файл(ов)');
    });

    it('пустой список — подсказки для обычного режима и закладок', async () => {
      await renderContent({ items: [] });
      expect(screen.getByText(/Публичных skills пока нет/)).toBeTruthy();
      fetchMock.mockResolvedValueOnce(ok({ items: [] }));
      fireEvent.click(screen.getByText('toggle-bookmarks'));
      await flush();
      expect(fetchMock.mock.calls[1][0]).toBe('http://api/api/skills/my/bookmarks?page=1&limit=12');
      expect(screen.getByText(/В закладках пока нет skills/)).toBeTruthy();
    });

    it('ошибка HTTP показывается снэкбаром; сетевая — тоже; не-Error — запасной текст', async () => {
      fetchMock.mockResolvedValueOnce(bad());
      const a = render(<SkillsGalleryContent />);
      await flush();
      expect(screen.getByText('Не удалось загрузить skills')).toBeTruthy();
      a.unmount();
      fetchMock.mockRejectedValueOnce('x');
      render(<SkillsGalleryContent />);
      await flush();
      expect(screen.getByText('Ошибка загрузки')).toBeTruthy();
    });

    it('спиннер во время загрузки', async () => {
      fetchMock.mockReturnValueOnce(new Promise(() => {}));
      render(<SkillsGalleryContent />);
      await flush();
      expect(screen.getByRole('progressbar')).toBeTruthy();
    });

    it('поиск с задержкой 400 мс добавляет query', async () => {
      await renderContent();
      fetchMock.mockResolvedValue(ok({ items: [skill({ id: 8 })], pages: 1 }));
      fireEvent.change(screen.getByPlaceholderText('Поиск skills…'), { target: { value: ' отчёт ' } });
      act(() => { jest.advanceTimersByTime(399); });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      act(() => { jest.advanceTimersByTime(2); });
      await flush();
      expect(fetchMock.mock.calls[1][0]).toContain('query=%D0%BE%D1%82%D1%87%D1%91%D1%82');
      expect(screen.getByTestId('card-8')).toBeTruthy();
    });

    it('пагинация запрашивает нужную страницу', async () => {
      await renderContent({ items: [skill()], pages: 3 });
      fetchMock.mockResolvedValueOnce(ok({ items: [skill({ id: 2 })], pages: 3 }));
      fireEvent.click(screen.getByLabelText('Go to page 2'));
      await flush();
      expect(fetchMock.mock.calls[1][0]).toContain('page=2');
    });

    it('событие astrachatSkillsChanged перезагружает список', async () => {
      await renderContent();
      fetchMock.mockResolvedValueOnce(ok({ items: [skill({ id: 4 })], pages: 1 }));
      await act(async () => { window.dispatchEvent(new Event('astrachatSkillsChanged')); });
      expect(screen.getByTestId('card-4')).toBeTruthy();
    });

    it('оформление: встроенный режим без заголовка; обычный с «К чату»', async () => {
      const a = await renderContent(undefined, { embedded: true });
      expect(screen.queryByText('Галерея Skills')).toBeNull();
      a.unmount();
      await renderContent();
      expect(screen.getByText('Галерея Skills')).toBeTruthy();
      fireEvent.click(screen.getByText('К чату'));
      expect(mock.navigate).toHaveBeenCalledWith('/');
    });
  });

  describe('действия над карточкой', () => {
    it('открытие: отправляет view и открывает skill в боковой панели; сбой view игнорируется', async () => {
      await renderContent();
      fetchMock.mockRejectedValueOnce(new Error('x'));
      fireEvent.click(screen.getByText('open-1'));
      expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/skills/1/view', expect.objectContaining({ method: 'POST' }));
      expect(mock.openSidebar).toHaveBeenCalledWith(1, mock.navigate);
      await flush();
    });

    it('закладка: POST при добавлении, DELETE при снятии, флаг обновляется локально', async () => {
      await renderContent({ items: [skill()], pages: 1 });
      fetchMock.mockResolvedValueOnce(ok());
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/skills/1/bookmark', expect.objectContaining({ method: 'POST' }));
      expect(mock.notify).toHaveBeenCalledWith('success', 'Добавлено в закладки');
      expect(item(1).isBookmarked).toBe(true);
      fetchMock.mockResolvedValueOnce(ok());
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][1].method).toBe('DELETE');
      expect(mock.notify).toHaveBeenLastCalledWith('success', 'Удалено из закладок');
      expect(item(1).isBookmarked).toBe(false);
    });

    it('удаление закладки в режиме закладок убирает карточку', async () => {
      await renderContent();
      fetchMock.mockResolvedValueOnce(ok({ items: [skill({ is_bookmarked: true })], pages: 1 }));
      fireEvent.click(screen.getByText('toggle-bookmarks'));
      await flush();
      fetchMock.mockResolvedValueOnce(ok());
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(screen.queryByTestId('card-1')).toBeNull();
    });

    it('закладка: ошибки detail / запасной / не-Error', async () => {
      await renderContent();
      fetchMock.mockResolvedValueOnce(bad({ detail: 'Лимит' }));
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Лимит');
      fetchMock.mockResolvedValueOnce({ ok: false, json: async () => { throw new Error('x'); } });
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Не удалось изменить закладку');
      fetchMock.mockRejectedValueOnce('s');
      await act(async () => { fireEvent.click(screen.getByText('bm-1')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Ошибка закладки');
    });

    it('оценка: успех → перезагрузка; ошибки', async () => {
      await renderContent();
      fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok({ items: [skill({ id: 6 })], pages: 1 }));
      await act(async () => { fireEvent.click(screen.getByText('rate-1')); });
      await flush();
      const [url, init] = fetchMock.mock.calls[1];
      expect(url).toBe('http://api/api/skills/1/rate');
      expect(JSON.parse(init.body)).toEqual({ rating: 5 });
      expect(mock.notify).toHaveBeenCalledWith('success', 'Оценка сохранена');
      expect(screen.getByTestId('card-6')).toBeTruthy();
      fetchMock.mockResolvedValueOnce(bad({ detail: 'Свой нельзя' }));
      await act(async () => { fireEvent.click(screen.getByText('rate-6')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Свой нельзя');
      fetchMock.mockResolvedValueOnce({ ok: false, json: async () => { throw new Error('x'); } });
      await act(async () => { fireEvent.click(screen.getByText('rate-6')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Не удалось сохранить оценку');
      fetchMock.mockRejectedValueOnce(1);
      await act(async () => { fireEvent.click(screen.getByText('rate-6')); });
      expect(mock.notify).toHaveBeenLastCalledWith('error', 'Ошибка оценки');
    });

    it('использование: use + закладка + активация в чате + переход через 600 мс', async () => {
      await renderContent();
      fetchMock.mockResolvedValue(ok());
      await act(async () => { fireEvent.click(screen.getByText('use-1')); });
      const calls = fetchMock.mock.calls.slice(1).map((c) => c[0]);
      expect(calls).toEqual(['http://api/api/skills/1/use', 'http://api/api/skills/1/bookmark']);
      expect(mock.toggleActive).toHaveBeenCalledWith('chat-1', 'my-skill', true, 'Заголовок');
      expect(screen.getByText('Skill «Заголовок» добавлен в чат. Переходим…')).toBeTruthy();
      expect(item(1).isBookmarked).toBe(true);
      expect(mock.navigate).not.toHaveBeenCalled();
      act(() => { jest.advanceTimersByTime(600); });
      expect(mock.navigate).toHaveBeenCalledWith('/');
    });

    it('использование: уже в закладках — без повторной закладки; always_apply / user_invocable=false не активируются', async () => {
      await renderContent({
        items: [skill({ is_bookmarked: true, always_apply: true }), skill({ id: 2, is_bookmarked: true, user_invocable: false, display_title: null })],
      });
      fetchMock.mockResolvedValue(ok());
      await act(async () => { fireEvent.click(screen.getByText('use-1')); });
      await act(async () => { fireEvent.click(screen.getByText('use-2')); });
      expect(fetchMock.mock.calls.slice(1).map((c) => c[0])).toEqual([
        'http://api/api/skills/1/use',
        'http://api/api/skills/2/use',
      ]);
      expect(mock.toggleActive).not.toHaveBeenCalled();
      expect(screen.getByText(/Skill «Мой скилл» добавлен в чат/)).toBeTruthy();
    });
  });

  describe('форма создания', () => {
    const openForm = async () => {
      mock.params = new URLSearchParams('create=1');
      await renderContent();
    };
    const field = (label: string) => screen.getByLabelText(label) as HTMLInputElement;

    it('?create=1 открывает форму и убирает параметр из URL', async () => {
      await openForm();
      expect(screen.getByText('Новый skill')).toBeTruthy();
      expect(mock.setParams).toHaveBeenCalledTimes(1);
      const [params, opts] = mock.setParams.mock.calls[0];
      expect(params.has('create')).toBe(false);
      expect(opts).toEqual({ replace: true });
    });

    it('черновик из sessionStorage открывает форму с данными и удаляется', async () => {
      sessionStorage.setItem('skills_md_import_draft', JSON.stringify({ name: 'Импорт', slug: 'imp', description: 'Д', content: 'К' }));
      await renderContent();
      expect(screen.getByText('Новый skill')).toBeTruthy();
      expect(field('Название').value).toBe('Импорт');
      expect(field('Служебное имя ($упоминание)').value).toBe('imp');
      expect(field('Описание (когда использовать)').value).toBe('Д');
      expect(field('Текст / SKILL.md (markdown)').value).toBe('К');
      expect(sessionStorage.getItem('skills_md_import_draft')).toBeNull();
    });

    it('черновик без полей даёт пустую форму; битый JSON игнорируется', async () => {
      sessionStorage.setItem('skills_md_import_draft', '{}');
      const a = await renderContent();
      expect(field('Название').value).toBe('');
      a.unmount();
      sessionStorage.setItem('skills_md_import_draft', '{bad');
      await renderContent();
      expect(screen.queryByText('Новый skill')).toBeNull();
    });

    it('ввод названия автоматически заполняет заголовок и slug; slug нормализуется при ручном вводе', async () => {
      await openForm();
      fireEvent.change(field('Название'), { target: { value: 'Мой Skill' } });
      expect(field('Заголовок в интерфейсе').value).toBe('Мой Skill');
      expect(field('Служебное имя ($упоминание)').value).toBe('мой-skill');
      fireEvent.change(field('Служебное имя ($упоминание)'), { target: { value: 'A B' } });
      expect(field('Служебное имя ($упоминание)').value).toBe('a-b');
    });

    it('пустые имя/содержимое не сохраняются', async () => {
      await openForm();
      fetchMock.mockClear();
      fireEvent.click(screen.getByText('Сохранить'));
      expect(screen.getByText('Укажите имя и содержимое')).toBeTruthy();
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('успешное создание: POST /create с нормализованным payload, уведомление об изменении и перезагрузка', async () => {
      await openForm();
      fireEvent.change(field('Название'), { target: { value: 'Мой Skill' } });
      fireEvent.change(field('Служебное имя ($упоминание)'), { target: { value: 'My-Slug' } });
      fireEvent.change(field('Текст / SKILL.md (markdown)'), { target: { value: 'тело' } });
      fireEvent.change(field('Описание (когда использовать)'), { target: { value: ' ' } });
      fireEvent.change(field('Категория'), { target: { value: ' cat ' } });
      fireEvent.change(field('Разрешённые инструменты (через запятую)'), { target: { value: 'a, b,, ' } });
      fireEvent.change(field('Теги (через запятую)'), { target: { value: 'x , y' } });
      fireEvent.click(screen.getByLabelText('Публичный (чтение для всех)'));
      fireEvent.click(screen.getByLabelText('Запретить вызов моделью'));
      fireEvent.click(screen.getByLabelText('Всегда применять'));
      fireEvent.click(screen.getByLabelText('Вызов пользователем ($ в чате)'));
      fireEvent.click(screen.getByLabelText('Активен'));
      fetchMock.mockResolvedValueOnce(ok({})).mockResolvedValueOnce(ok({ items: [], pages: 1 }));
      await act(async () => { fireEvent.click(screen.getByText('Сохранить')); });
      const [url, init] = fetchMock.mock.calls[1];
      expect(url).toBe('http://api/api/skills/create');
      expect(init.method).toBe('POST');
      expect(JSON.parse(init.body)).toEqual({
        slug: 'my-slug',
        name: 'Мой Skill',
        display_title: 'Мой Skill',
        description: null,
        content: 'тело',
        is_active: false,
        user_invocable: false,
        disable_model_invocation: true,
        always_apply: true,
        allowed_tools: ['a', 'b'],
        category: 'cat',
        meta: { tags: ['x', 'y'] },
        is_public: true,
      });
      expect(screen.getByText('Skill создан')).toBeTruthy();
      expect(mock.notifyChanged).toHaveBeenCalledTimes(1);
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    it('невалидный slug заменяется slugify(name); display_title обрезается до 128 символов', async () => {
      await openForm();
      fireEvent.change(field('Название'), { target: { value: 'Имя' } });
      fireEvent.change(field('Заголовок в интерфейсе'), { target: { value: 'z'.repeat(200) } });
      fireEvent.change(field('Служебное имя ($упоминание)'), { target: { value: 'имя' } }); // кириллица не проходит regexp
      fireEvent.change(field('Текст / SKILL.md (markdown)'), { target: { value: 'т' } });
      fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok({ items: [] }));
      await act(async () => { fireEvent.click(screen.getByText('Сохранить')); });
      const body = JSON.parse(fetchMock.mock.calls[1][1].body);
      expect(body.slug).toBe('имя');
      expect(body.display_title).toHaveLength(128);
    });

    it('без права публикации чекбокса нет, а is_public=false отправляется', async () => {
      mock.canPublish = false;
      await openForm();
      expect(screen.queryByLabelText('Публичный (чтение для всех)')).toBeNull();
      fireEvent.change(field('Название'), { target: { value: 'N' } });
      fireEvent.change(field('Текст / SKILL.md (markdown)'), { target: { value: 'т' } });
      fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok({ items: [] }));
      await act(async () => { fireEvent.click(screen.getByText('Сохранить')); });
      expect(JSON.parse(fetchMock.mock.calls[1][1].body).is_public).toBe(false);
    });

    it('ошибка сохранения: detail / запасной текст / не-Error; форма остаётся открытой', async () => {
      await openForm();
      fireEvent.change(field('Название'), { target: { value: 'N' } });
      fireEvent.change(field('Текст / SKILL.md (markdown)'), { target: { value: 'т' } });
      fetchMock.mockResolvedValueOnce(bad({ detail: 'Slug занят' }));
      await act(async () => { fireEvent.click(screen.getByText('Сохранить')); });
      expect(screen.getByText('Slug занят')).toBeTruthy();
      expect(screen.getByText('Новый skill')).toBeTruthy();
      fetchMock.mockResolvedValueOnce({ ok: false, json: async () => { throw new Error('x'); } });
      await act(async () => { fireEvent.click(screen.getByText('Сохранить')); });
      expect(screen.getByText('Не удалось сохранить')).toBeTruthy();
      fetchMock.mockRejectedValueOnce('s');
      await act(async () => { fireEvent.click(screen.getByText('Сохранить')); });
      expect(screen.getByText('Ошибка')).toBeTruthy();
    });

    it('«Отмена» и Esc закрывают форму', async () => {
      await openForm();
      fireEvent.click(within(screen.getByRole('dialog')).getByText('Отмена'));
      act(() => { jest.advanceTimersByTime(1000); });
      expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('Esc закрывает форму (onClose диалога)', async () => {
      await openForm();
      fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
      act(() => { jest.advanceTimersByTime(1000); });
      expect(screen.queryByRole('dialog')).toBeNull();
    });
  });

  it('снэкбар закрывается по таймеру и по крестику', async () => {
    fetchMock.mockResolvedValueOnce(bad());
    render(<SkillsGalleryContent />);
    await flush();
    expect(screen.getByText('Не удалось загрузить skills')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    act(() => { jest.advanceTimersByTime(1000); });
    expect(screen.queryByText('Не удалось загрузить skills')).toBeNull();
  });

  it('после выхода из системы (токен пропал) закладки, оценка и использование требуют входа', async () => {
    const { rerender } = await renderContent();
    mock.token = null;
    rerender(<SkillsGalleryContent />);
    await flush();
    fetchMock.mockClear();
    fireEvent.click(screen.getByText('bm-1'));
    expect(mock.notify).toHaveBeenCalledWith('error', 'Для закладок нужно войти в систему');
    fireEvent.click(screen.getByText('rate-1'));
    expect(mock.notify).toHaveBeenCalledWith('error', 'Для оценки нужно войти в систему');
    await act(async () => { fireEvent.click(screen.getByText('use-1')); });
    expect(screen.getByText('Для использования нужно войти в систему')).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('снэкбар автоматически скрывается через 4 с', async () => {
    fetchMock.mockResolvedValueOnce(bad());
    render(<SkillsGalleryContent />);
    await flush();
    expect(screen.getByText('Не удалось загрузить skills')).toBeTruthy();
    act(() => { jest.advanceTimersByTime(4500); });
    act(() => { jest.advanceTimersByTime(1000); });
    expect(screen.queryByText('Не удалось загрузить skills')).toBeNull();
  });
});
