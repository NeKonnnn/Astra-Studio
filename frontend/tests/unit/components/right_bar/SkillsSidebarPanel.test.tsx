import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import SkillsSidebarPanel from '@/components/right_bar/SkillsSidebarPanel';
import {
  ASTRA_OPEN_SKILLS_SIDEBAR,
  ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY,
} from '@/constants/hotkeys';

// ---------- управляемые фейки ----------
let mockToken: string | null = 'tok';
let mockUser: Record<string, unknown> | null = { is_admin: false };
let mockChatId: string | null = 'chat-1';
let mockCanCreate = true;
let mockCanPublish = true;
let mockActiveIds: string[] = [];

const mockNotify = jest.fn();
const mockExportSkill = jest.fn();
const mockImportSkillFile = jest.fn();
const mockNotifySkillsChanged = jest.fn();
const mockToggleActive = jest.fn();
const mockRenameSlug = jest.fn();
const mockRemoveSlug = jest.fn();

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ token: mockToken, user: mockUser }),
}));
jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: { currentChatId: mockChatId } }),
  useAppActions: () => ({ showNotification: mockNotify }),
}));
jest.mock('@/hooks/useFeatureAccess', () => ({
  useFeatureAccess: () => ({ canCreateAgents: mockCanCreate, canPublishToLibrary: mockCanPublish }),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  API_ENDPOINTS: { SKILLS: '/api/skills' },
}));
jest.mock('@/utils/skillSelectionStorage', () => ({
  SKILL_SELECTION_CHANGED_EVENT: 'astrachatSkillSelectionChanged',
  getActiveSkillIds: () => mockActiveIds,
  toggleActiveSkill: (...a: unknown[]) => mockToggleActive(...a),
  renameSkillSlugInAllChats: (...a: unknown[]) => mockRenameSlug(...a),
  removeSkillSlugFromAllChats: (...a: unknown[]) => mockRemoveSlug(...a),
}));
jest.mock('@/utils/skillsImportExport', () => {
  const actual = jest.requireActual('@/utils/skillsImportExport');
  return {
    ...actual,
    exportSkill: (...a: unknown[]) => mockExportSkill(...a),
    importSkillFile: (...a: unknown[]) => mockImportSkillFile(...a),
    notifySkillsChanged: () => mockNotifySkillsChanged(),
  };
});
jest.mock('@/components/ShareSkillDialog', () => ({
  __esModule: true,
  default: (p: { open: boolean; onClose: () => void; skillId: number; skillName: string }) => (
    <div data-testid="share-dialog" data-open={String(p.open)} data-id={p.skillId} data-name={p.skillName}>
      <button onClick={p.onClose}>close-share</button>
    </div>
  ),
}));
jest.mock('@/components/skills/SkillFilesEditor', () => ({
  __esModule: true,
  default: (p: { skillId: number; token: string; canWrite: boolean }) => (
    <div data-testid="files-editor" data-id={p.skillId} data-write={String(p.canWrite)} />
  ),
}));

// ---------- фикстуры ----------
const LIST = [
  { id: 1, slug: 'alpha', name: 'Alpha', display_title: 'Альфа', description: 'первый навык', is_active: true, author_id: 'u1', my_permission: 'owner' },
  { id: 2, slug: 'beta', name: 'Beta', display_title: null, description: null, is_active: true, author_id: 'u2', my_permission: 'viewer' },
  { id: 3, slug: 'gamma', name: 'Gamma', description: 'редактируемый', is_active: true, author_id: 'u3', my_permission: 'editor' },
];

const DETAILS: Record<number, Record<string, unknown>> = {
  1: {
    id: 1, slug: 'alpha', name: 'Alpha', display_title: 'Альфа', description: 'первый навык',
    content: 'Тело альфы', is_active: true, is_public: false, user_invocable: true,
    disable_model_invocation: false, always_apply: false, allowed_tools: ['t1', 't2'],
    category: 'cat', meta: { tags: ['x', 'y'] }, author_id: 'u1', write_access: true, my_permission: 'owner',
  },
  2: {
    id: 2, slug: 'beta', name: 'Beta', content: 'Тело беты', is_active: true, author_id: 'u2',
    my_permission: 'viewer', write_access: false,
  },
  3: {
    id: 3, slug: 'gamma', name: 'Gamma', content: 'Тело гаммы', is_active: false, author_id: 'u3',
    my_permission: 'editor', write_access: true, is_public: true,
  },
};

const fetchMock = jest.fn();
type Reply = { ok?: boolean; status?: number; body?: unknown; bodyError?: boolean } | Error;
let routes: Record<string, Reply | ((url: string, init?: RequestInit) => Reply | Promise<Reply>)> = {};

function resp(r: Reply) {
  if (r instanceof Error) return Promise.reject(r);
  return Promise.resolve({
    ok: r.ok ?? true,
    status: r.status ?? 200,
    json: () => (r.bodyError ? Promise.reject(new Error('bad json')) : Promise.resolve(r.body ?? {})),
  });
}

function installFetch() {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const method = (init?.method || 'GET').toUpperCase();
    const path = url.replace('http://api.test', '').split('?')[0];
    const key = `${method} ${path}`;
    const handler = routes[key];
    if (!handler) return resp({ ok: false, status: 404, body: {} });
    return typeof handler === 'function' ? Promise.resolve(handler(url, init)).then(resp) : resp(handler);
  });
}

function defaultRoutes() {
  routes = {
    'GET /api/skills/list': { body: { items: LIST, pages: 1 } },
    'GET /api/skills/1': { body: DETAILS[1] },
    'GET /api/skills/2': { body: DETAILS[2] },
    'GET /api/skills/3': { body: DETAILS[3] },
  };
}

const callsTo = (method: string, path: string) =>
  fetchMock.mock.calls.filter(
    ([u, i]) =>
      (i?.method || 'GET').toUpperCase() === method && String(u).replace('http://api.test', '').split('?')[0] === path,
  );

beforeEach(() => {
  mockToken = 'tok';
  mockUser = { is_admin: false };
  mockChatId = 'chat-1';
  mockCanCreate = true;
  mockCanPublish = true;
  mockActiveIds = [];
  [mockNotify, mockExportSkill, mockImportSkillFile, mockNotifySkillsChanged, mockToggleActive, mockRenameSlug, mockRemoveSlug, fetchMock].forEach((m) => m.mockReset());
  (global as unknown as { fetch: unknown }).fetch = fetchMock;
  sessionStorage.clear();
  defaultRoutes();
  installFetch();
});

async function renderPanel(props: { isOpen?: boolean; dark?: boolean } = {}) {
  const theme = createTheme({ palette: { mode: props.dark ? 'dark' : 'light' } });
  const utils = render(
    <ThemeProvider theme={theme}>
      <SkillsSidebarPanel isOpen={props.isOpen} />
    </ThemeProvider>,
  );
  if (props.isOpen !== false) {
    await waitFor(() => expect(callsTo('GET', '/api/skills/list').length).toBeGreaterThan(0));
    await act(async () => {});
  }
  return utils;
}

function openDropdown() {
  fireEvent.click(screen.getByText(/^Новый skill$|^Skill$|^Альфа$|^Beta$|^Gamma$/));
}

async function pickSkill(title: string) {
  openDropdown();
  const item = await screen.findByText(title, { selector: 'span' });
  fireEvent.click(item);
  await waitFor(() => expect(screen.queryByPlaceholderText('Поиск skills по имени')).toBeNull());
  await act(async () => {});
}

const field = (re: RegExp) => screen.getByLabelText(re) as HTMLInputElement;
const btn = (name: string | RegExp) => screen.getByRole('button', { name }) as HTMLButtonElement;
const saveBtn = () => btn('Сохранить');

async function fillNew(name = 'My Skill', content = 'Тело') {
  fireEvent.change(field(/^Название/), { target: { value: name } });
  fireEvent.change(field(/^Текст \/ SKILL\.md/), { target: { value: content } });
}

// ---------- тесты ----------
describe('SkillsSidebarPanel: загрузка списка', () => {
  it('isOpen=false: ничего не рендерит и не ходит в сеть', async () => {
    const { container } = await renderPanel({ isOpen: false });
    expect(container.textContent).toBe('');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('при открытии запрашивает список с Authorization и показывает «Новый skill»', async () => {
    await renderPanel();
    const [url, init] = callsTo('GET', '/api/skills/list')[0];
    expect(url).toBe('http://api.test/api/skills/list?limit=200&page=1');
    expect(init.headers).toEqual({ Authorization: 'Bearer tok' });
    expect(screen.getByText('Новый skill')).toBeTruthy();
  });

  it('без токена заголовок Authorization не отправляется', async () => {
    mockToken = null;
    await renderPanel();
    expect(callsTo('GET', '/api/skills/list')[0][1].headers).toEqual({});
  });

  it('листает все страницы', async () => {
    routes['GET /api/skills/list'] = (url: string) =>
      url.includes('page=1')
        ? { body: { items: [LIST[0]], pages: 2 } }
        : { body: { items: [LIST[1]], pages: 2 } };
    await renderPanel();
    expect(callsTo('GET', '/api/skills/list')).toHaveLength(2);
    openDropdown();
    expect(await screen.findByText('Альфа')).toBeTruthy();
    expect(screen.getByText('Beta')).toBeTruthy();
  });

  it('пустая страница прерывает цикл даже если сервер обещает больше страниц', async () => {
    routes['GET /api/skills/list'] = { body: { items: [], pages: 5 } };
    await renderPanel();
    expect(callsTo('GET', '/api/skills/list')).toHaveLength(1);
    openDropdown();
    expect(await screen.findByText('Не найдено')).toBeTruthy();
  });

  it('ошибка на первой странице -> пустой список', async () => {
    routes['GET /api/skills/list'] = { ok: false, status: 500 };
    await renderPanel();
    openDropdown();
    expect(await screen.findByText('Не найдено')).toBeTruthy();
  });

  it('ошибка на второй странице сохраняет уже загруженное', async () => {
    routes['GET /api/skills/list'] = (url: string) =>
      url.includes('page=1') ? { body: { items: [LIST[0]], pages: 2 } } : { ok: false, status: 500 };
    await renderPanel();
    openDropdown();
    expect(await screen.findByText('Альфа')).toBeTruthy();
  });

  it('сетевое исключение -> пустой список без падения', async () => {
    routes['GET /api/skills/list'] = new Error('offline');
    await renderPanel();
    openDropdown();
    expect(await screen.findByText('Не найдено')).toBeTruthy();
  });

  it('ответ без items и pages трактуется как пустой список', async () => {
    routes['GET /api/skills/list'] = { body: {} };
    await renderPanel();
    openDropdown();
    expect(await screen.findByText('Не найдено')).toBeTruthy();
  });

  it('во время загрузки в выпадающем списке показан индикатор', async () => {
    let release!: (v: Reply) => void;
    routes['GET /api/skills/list'] = () => new Promise<Reply>((r) => { release = r; });
    render(<SkillsSidebarPanel />);
    fireEvent.click(screen.getByText('Новый skill'));
    expect(await screen.findByRole('progressbar')).toBeTruthy();
    await act(async () => {
      release({ body: { items: LIST, pages: 1 } });
    });
    expect(await screen.findByText('Альфа')).toBeTruthy();
  });
});

describe('SkillsSidebarPanel: выпадающий список', () => {
  it('поиск фильтрует по названию, slug и описанию', async () => {
    await renderPanel();
    openDropdown();
    const search = await screen.findByPlaceholderText('Поиск skills по имени');
    fireEvent.change(search, { target: { value: 'гамм' } });
    expect(screen.queryByText('Альфа')).toBeNull();
    fireEvent.change(search, { target: { value: 'редактируем' } });
    expect(screen.getByText('Gamma')).toBeTruthy();
    fireEvent.change(search, { target: { value: 'BETA' } });
    expect(screen.getByText('Beta')).toBeTruthy();
    expect(screen.queryByText('Gamma')).toBeNull();
    fireEvent.change(search, { target: { value: 'ничего-нет' } });
    expect(screen.getByText('Не найдено')).toBeTruthy();
    fireEvent.change(search, { target: { value: '   ' } });
    expect(screen.getByText('Альфа')).toBeTruthy();
  });

  it('без права создавать пункт «+ Новый skill» скрыт', async () => {
    mockCanCreate = false;
    await renderPanel();
    openDropdown();
    await screen.findByText('Альфа');
    expect(screen.queryByText('+ Новый skill')).toBeNull();
  });

  it('закрытие поповера сбрасывает поиск', async () => {
    await renderPanel();
    openDropdown();
    const search = await screen.findByPlaceholderText('Поиск skills по имени');
    fireEvent.change(search, { target: { value: 'abc' } });
    fireEvent.keyDown(search, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByPlaceholderText('Поиск skills по имени')).toBeNull());
    openDropdown();
    expect((await screen.findByPlaceholderText('Поиск skills по имени') as HTMLInputElement).value).toBe('');
  });

  it('выбор «+ Новый skill» сбрасывает форму на пустую', async () => {
    await renderPanel();
    await pickSkill('Альфа');
    expect(field(/^Название/).value).toBe('Alpha');
    openDropdown();
    fireEvent.click(await screen.findByText('+ Новый skill'));
    await waitFor(() => expect(field(/^Название/).value).toBe(''));
    expect(field(/Служебное имя/).value).toBe('');
    expect(screen.queryByTestId('files-editor')).toBeNull();
  });

  it('заголовок элемента: display_title, иначе name', async () => {
    await renderPanel();
    openDropdown();
    expect(await screen.findByText('Альфа')).toBeTruthy();
    expect(screen.getByText('Beta')).toBeTruthy();
    expect(screen.getByText('$alpha')).toBeTruthy();
  });
});

describe('SkillsSidebarPanel: загрузка карточки', () => {
  it('заполняет форму из ответа и показывает редактор файлов', async () => {
    await renderPanel();
    await pickSkill('Альфа');
    expect(field(/^Название/).value).toBe('Alpha');
    expect(field(/Заголовок в интерфейсе/).value).toBe('Альфа');
    expect(field(/Служебное имя/).value).toBe('alpha');
    expect(field(/^Описание/).value).toBe('первый навык');
    expect(field(/^Текст \/ SKILL\.md/).value).toBe('Тело альфы');
    expect(field(/^Категория/).value).toBe('cat');
    expect(field(/^Разрешённые инструменты \(/).value).toBe('t1, t2');
    expect(field(/^Теги/).value).toBe('x, y');
    const files = screen.getByTestId('files-editor');
    expect(files.getAttribute('data-id')).toBe('1');
    expect(files.getAttribute('data-write')).toBe('true');
    expect(callsTo('GET', '/api/skills/1')[0][1].headers).toEqual({
      'Content-Type': 'application/json',
      Authorization: 'Bearer tok',
    });
  });

  it('дефолты: display_title берётся из name, пустые поля -> пустые строки, is_active по умолчанию true', async () => {
    await renderPanel();
    await pickSkill('Beta');
    expect(field(/Заголовок в интерфейсе/).value).toBe('Beta');
    expect(field(/^Описание/).value).toBe('');
    expect(field(/^Категория/).value).toBe('');
    expect(field(/^Теги/).value).toBe('');
  });

  it('ошибка загрузки -> уведомление и возврат к «Новый skill»', async () => {
    routes['GET /api/skills/1'] = { ok: false, status: 404 };
    await renderPanel();
    openDropdown();
    fireEvent.click(await screen.findByText('Альфа', { selector: 'span' }));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось загрузить skill'));
    expect(screen.getByText('Новый skill')).toBeTruthy();
    expect(field(/^Название/).value).toBe('');
  });

  it('не-Error исключение при загрузке -> «Ошибка загрузки»', async () => {
    routes['GET /api/skills/1'] = () => {
      throw 'строка';
    };
    await renderPanel();
    openDropdown();
    fireEvent.click(await screen.findByText('Альфа', { selector: 'span' }));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки'));
  });

  it('во время загрузки карточки форма заменена индикатором', async () => {
    let release!: (v: Reply) => void;
    routes['GET /api/skills/1'] = () => new Promise<Reply>((r) => { release = r; });
    await renderPanel();
    openDropdown();
    fireEvent.click(await screen.findByText('Альфа', { selector: 'span' }));
    await waitFor(() => expect(screen.queryByLabelText(/^Название/)).toBeNull());
    expect(saveBtn().disabled).toBe(true);
    await act(async () => {
      release({ body: DETAILS[1] });
    });
    expect(field(/^Название/).value).toBe('Alpha');
  });

  it('id из sessionStorage открывает карточку и очищает ключ', async () => {
    sessionStorage.setItem(ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY, '3');
    await renderPanel();
    await waitFor(() => expect(field(/^Название/).value).toBe('Gamma'));
    expect(sessionStorage.getItem(ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY)).toBeNull();
  });

  it.each(['abc', '0', '-5'])('некорректный id %s в sessionStorage игнорируется', async (raw) => {
    sessionStorage.setItem(ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY, raw);
    await renderPanel();
    expect(callsTo('GET', '/api/skills/1')).toHaveLength(0);
    expect(callsTo('GET', '/api/skills/3')).toHaveLength(0);
    expect(sessionStorage.getItem(ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY)).toBeNull();
  });

  it('sessionStorage бросает исключение -> панель не падает', async () => {
    // Бросаем только на ключе панели: прочие чтения localStorage (цвет сайдбара) должны работать.
    const spy = jest.spyOn(Storage.prototype, 'getItem').mockImplementation((key: string) => {
      if (key === ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY) throw new Error('denied');
      return null;
    });
    await renderPanel();
    spy.mockRestore();
    expect(screen.getByText('Новый skill')).toBeTruthy();
  });

  it('событие ASTRA_OPEN_SKILLS_SIDEBAR с skillId открывает карточку', async () => {
    await renderPanel();
    act(() => {
      window.dispatchEvent(new CustomEvent(ASTRA_OPEN_SKILLS_SIDEBAR, { detail: { skillId: 2 } }));
    });
    await waitFor(() => expect(field(/^Название/).value).toBe('Beta'));
  });

  it.each([
    ['без detail', undefined],
    ['skillId не число', { skillId: '2' }],
    ['skillId=0', { skillId: 0 }],
    ['skillId=NaN', { skillId: NaN }],
  ])('событие открытия %s игнорируется', async (_n, detail) => {
    await renderPanel();
    act(() => {
      window.dispatchEvent(new CustomEvent(ASTRA_OPEN_SKILLS_SIDEBAR, { detail }));
    });
    expect(callsTo('GET', '/api/skills/2')).toHaveLength(0);
  });
});

describe('SkillsSidebarPanel: права', () => {
  it('owner: все поля активны, есть Импорт/Экспорт/Поделиться/Опубликовать/Удалить/Сохранить', async () => {
    await renderPanel();
    await pickSkill('Альфа');
    expect(field(/^Название/).disabled).toBe(false);
    ['Импорт', /Экспорт/, 'Поделиться скиллом', 'Опубликовать в галерее', 'Удалить skill', 'Сохранить'].forEach(
      (n) => expect(btn(n)).toBeTruthy(),
    );
    expect(screen.queryByText(/Общий skill/)).toBeNull();
  });

  it('viewer: поля заблокированы, нет сохранения/импорта/шаринга/удаления, есть пояснение', async () => {
    await renderPanel();
    await pickSkill('Beta');
    expect(field(/^Название/).disabled).toBe(true);
    expect(field(/^Текст \/ SKILL\.md/).disabled).toBe(true);
    expect(screen.getByText(/роль «Зритель»/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Сохранить' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Импорт' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Поделиться скиллом' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Удалить skill' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Опубликовать/ })).toBeNull();
    expect(screen.getByTestId('files-editor').getAttribute('data-write')).toBe('false');
    // экспорт доступен для чтения
    expect(btn(/Экспорт/).disabled).toBe(false);
  });

  it('editor: можно сохранять и импортировать, но нет шаринга/удаления/публикации', async () => {
    await renderPanel();
    await pickSkill('Gamma');
    expect(field(/^Название/).disabled).toBe(false);
    expect(screen.getByText(/роль «Редактор»/)).toBeTruthy();
    expect(btn('Сохранить')).toBeTruthy();
    expect(btn('Импорт')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Поделиться скиллом' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Удалить skill' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Опубликовать|Снять/ })).toBeNull();
  });

  it('администратор получает права владельца на чужом skill', async () => {
    mockUser = { is_admin: true };
    await renderPanel();
    await pickSkill('Beta');
    expect(field(/^Название/).disabled).toBe(false);
    expect(btn('Удалить skill')).toBeTruthy();
    expect(btn('Поделиться скиллом')).toBeTruthy();
    expect(screen.queryByText(/Общий skill/)).toBeNull();
  });

  it('неизвестное значение my_permission трактуется как viewer', async () => {
    routes['GET /api/skills/2'] = { body: { ...DETAILS[2], my_permission: 'weird' } };
    await renderPanel();
    await pickSkill('Beta');
    expect(field(/^Название/).disabled).toBe(true);
  });

  it('без права публикации в галерею кнопка публикации и чекбокс «Публичный» скрыты', async () => {
    mockCanPublish = false;
    await renderPanel();
    await pickSkill('Альфа');
    expect(screen.queryByRole('button', { name: /Опубликовать/ })).toBeNull();
    expect(screen.queryByLabelText('Публичный (чтение для всех)')).toBeNull();
    expect(btn('Удалить skill')).toBeTruthy();
  });
});

describe('SkillsSidebarPanel: форма', () => {
  it('новый skill: ввод имени автоматически заполняет заголовок и slug', async () => {
    await renderPanel();
    fireEvent.change(field(/^Название/), { target: { value: 'My Cool Skill' } });
    expect(field(/Заголовок в интерфейсе/).value).toBe('My Cool Skill');
    expect(field(/Служебное имя/).value).toBe('my-cool-skill');
  });

  it('у существующего skill смена имени не трогает заголовок и slug', async () => {
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.change(field(/^Название/), { target: { value: 'Другое' } });
    expect(field(/Заголовок в интерфейсе/).value).toBe('Альфа');
    expect(field(/Служебное имя/).value).toBe('alpha');
  });

  it('slug нормализуется при ручном вводе', async () => {
    await renderPanel();
    fireEvent.change(field(/Служебное имя/), { target: { value: 'Мой  Skill_1!' } });
    expect(field(/Служебное имя/).value).toBe('skill_1');
  });

  it('все текстовые поля и чекбоксы обновляют форму', async () => {
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.change(field(/Заголовок в интерфейсе/), { target: { value: 'Заг' } });
    fireEvent.change(field(/^Описание/), { target: { value: 'опис' } });
    fireEvent.change(field(/^Категория/), { target: { value: 'кат' } });
    fireEvent.change(field(/^Разрешённые инструменты \(/), { target: { value: 'a, b' } });
    fireEvent.change(field(/^Теги/), { target: { value: 'q, w' } });
    expect(field(/Заголовок в интерфейсе/).value).toBe('Заг');
    expect(field(/^Описание/).value).toBe('опис');
    expect(field(/^Категория/).value).toBe('кат');
    expect(field(/^Разрешённые инструменты \(/).value).toBe('a, b');
    expect(field(/^Теги/).value).toBe('q, w');

    for (const label of [
      'Публичный (чтение для всех)',
      'Вызов пользователем ($ в чате)',
      'Запретить вызов моделью',
      'Всегда применять',
      'Активен',
    ]) {
      const cb = screen.getByLabelText(label) as HTMLInputElement;
      const before = cb.checked;
      fireEvent.click(cb);
      expect((screen.getByLabelText(label) as HTMLInputElement).checked).toBe(!before);
    }
  });

  it('«Использовать в чате» отражает активность и переключает через toggleActiveSkill', async () => {
    mockActiveIds = ['alpha'];
    await renderPanel();
    await pickSkill('Альфа');
    const cb = screen.getByLabelText('Использовать в чате') as HTMLInputElement;
    expect(cb.checked).toBe(true);
    fireEvent.click(cb);
    expect(mockToggleActive).toHaveBeenCalledWith('chat-1', 'alpha', false, 'Альфа');
  });

  it('«Использовать в чате» скрыт, если включено «Всегда применять» или вызов пользователем выключен', async () => {
    await renderPanel();
    await pickSkill('Альфа');
    expect(screen.getByLabelText('Использовать в чате')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Всегда применять'));
    expect(screen.queryByLabelText('Использовать в чате')).toBeNull();
    fireEvent.click(screen.getByLabelText('Всегда применять'));
    fireEvent.click(screen.getByLabelText('Вызов пользователем ($ в чате)'));
    expect(screen.queryByLabelText('Использовать в чате')).toBeNull();
  });

  it('«Использовать в чате» для неактивного skill включает его', async () => {
    mockActiveIds = [];
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(screen.getByLabelText('Использовать в чате'));
    expect(mockToggleActive).toHaveBeenCalledWith('chat-1', 'alpha', true, 'Альфа');
  });

  it('событие смены выбора skill обновляет чекбокс; чужой чат игнорируется', async () => {
    await renderPanel();
    await pickSkill('Альфа');
    expect((screen.getByLabelText('Использовать в чате') as HTMLInputElement).checked).toBe(false);
    mockActiveIds = ['alpha'];
    act(() => {
      window.dispatchEvent(new CustomEvent('astrachatSkillSelectionChanged', { detail: { chatId: 'other' } }));
    });
    expect((screen.getByLabelText('Использовать в чате') as HTMLInputElement).checked).toBe(false);
    act(() => {
      window.dispatchEvent(new CustomEvent('astrachatSkillSelectionChanged', { detail: { chatId: 'chat-1' } }));
    });
    expect((screen.getByLabelText('Использовать в чате') as HTMLInputElement).checked).toBe(true);
    act(() => {
      window.dispatchEvent(new CustomEvent('astrachatSkillSelectionChanged'));
    });
    expect((screen.getByLabelText('Использовать в чате') as HTMLInputElement).checked).toBe(true);
  });

  it('событие sidebarColorChanged и тёмная тема не ломают рендер', async () => {
    await renderPanel({ dark: true });
    act(() => {
      window.dispatchEvent(new Event('sidebarColorChanged'));
    });
    expect(screen.getByText('Новый skill')).toBeTruthy();
  });

  it('без токена редактор файлов не показывается', async () => {
    mockToken = null;
    await renderPanel();
    await pickSkill('Альфа');
    expect(screen.queryByTestId('files-editor')).toBeNull();
  });

  it('заголовок выбранного: подставляет name, когда нет display_title; «Skill» как запасной вариант', async () => {
    routes['GET /api/skills/list'] = { body: { items: [], pages: 1 } };
    routes['GET /api/skills/1'] = { body: { ...DETAILS[1], display_title: '', name: '' } };
    await renderPanel();
    act(() => {
      window.dispatchEvent(new CustomEvent(ASTRA_OPEN_SKILLS_SIDEBAR, { detail: { skillId: 1 } }));
    });
    await waitFor(() => expect(screen.getByText('Skill')).toBeTruthy());
  });
});

describe('SkillsSidebarPanel: сохранение', () => {
  it('пустое название/текст -> ошибка валидации без запроса', async () => {
    await renderPanel();
    fireEvent.click(saveBtn());
    const msg = 'Укажите название и текст skill (поле «Текст / SKILL.md»)';
    expect(await screen.findByText(msg)).toBeTruthy();
    expect(mockNotify).toHaveBeenCalledWith('error', msg);
    expect(callsTo('POST', '/api/skills/create')).toHaveLength(0);
  });

  it('только название без текста — тоже ошибка', async () => {
    await renderPanel();
    fireEvent.change(field(/^Название/), { target: { value: 'X' } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(mockNotify).toHaveBeenCalled());
    expect(callsTo('POST', '/api/skills/create')).toHaveLength(0);
  });

  it('создание: POST /create с нормализованным payload, успех и обновление списка', async () => {
    routes['POST /api/skills/create'] = {
      body: { id: 10, slug: 'my-skill', name: 'My Skill', display_title: 'My Skill', content: 'Тело', is_public: false, author_id: 'u1' },
    };
    await renderPanel();
    await fillNew();
    fireEvent.change(field(/^Разрешённые инструменты \(/), { target: { value: ' a , ,b ' } });
    fireEvent.change(field(/^Теги/), { target: { value: 't1,, t2 ' } });
    fireEvent.change(field(/^Категория/), { target: { value: '  ' } });
    fireEvent.click(saveBtn());

    expect(await screen.findByText('Skill успешно сохранён')).toBeTruthy();
    const [url, init] = callsTo('POST', '/api/skills/create')[0];
    expect(url).toBe('http://api.test/api/skills/create');
    expect(JSON.parse(init.body)).toEqual({
      slug: 'my-skill',
      name: 'My Skill',
      display_title: 'My Skill',
      description: null,
      content: 'Тело',
      is_active: true,
      user_invocable: true,
      disable_model_invocation: false,
      always_apply: false,
      allowed_tools: ['a', 'b'],
      category: null,
      meta: { tags: ['t1', 't2'] },
      is_public: false,
    });
    expect(mockNotify).toHaveBeenCalledWith('success', 'Skill создан');
    expect(mockNotifySkillsChanged).toHaveBeenCalledTimes(1);
    expect(mockRenameSlug).not.toHaveBeenCalled();
    // список перезагружен
    expect(callsTo('GET', '/api/skills/list').length).toBeGreaterThanOrEqual(2);
    // после создания карточка становится существующей
    expect(screen.getByTestId('files-editor').getAttribute('data-id')).toBe('10');
  });

  it('сообщение об успехе исчезает через 4 секунды', async () => {
    routes['POST /api/skills/create'] = { body: { id: 10, slug: 's', name: 'S', content: 'c' } };
    await renderPanel();
    await fillNew('S', 'c');
    jest.useFakeTimers();
    try {
      fireEvent.click(saveBtn());
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });
      await waitFor(() => expect(screen.getByText('Skill успешно сохранён')).toBeTruthy());
      act(() => {
        jest.advanceTimersByTime(4001);
      });
      expect(screen.queryByText('Skill успешно сохранён')).toBeNull();
    } finally {
      jest.useRealTimers();
    }
  });

  it('slug из формы валидный -> используется в нижнем регистре; невалидный -> slugify имени', async () => {
    routes['POST /api/skills/create'] = { body: { id: 11, slug: 'x', name: 'N', content: 'c' } };
    await renderPanel();
    await fillNew('Имя на русском', 'c');
    // slug из кириллицы пустой -> slugify даёт skill-<ts>; ручной ввод невалидного невозможен через onChange,
    // поэтому проверяем сгенерированный
    fireEvent.click(saveBtn());
    await waitFor(() => expect(callsTo('POST', '/api/skills/create')).toHaveLength(1));
    const body = JSON.parse(callsTo('POST', '/api/skills/create')[0][1].body);
    expect(body.slug).toMatch(/^skill-[a-z0-9]+$/);
  });

  it('display_title обрезается до 128 символов', async () => {
    routes['POST /api/skills/create'] = { body: { id: 11, slug: 'x', name: 'N', content: 'c' } };
    await renderPanel();
    await fillNew('Q', 'c');
    fireEvent.change(field(/Заголовок в интерфейсе/), { target: { value: 'я'.repeat(128) } });
    // maxLength не мешает программной установке; проверим усечение через name > 128 при пустом заголовке
    fireEvent.change(field(/Заголовок в интерфейсе/), { target: { value: '' } });
    fireEvent.change(field(/^Название/), { target: { value: 'n'.repeat(200) } });
    fireEvent.change(field(/Заголовок в интерфейсе/), { target: { value: '' } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(callsTo('POST', '/api/skills/create')).toHaveLength(1));
    const body = JSON.parse(callsTo('POST', '/api/skills/create')[0][1].body);
    expect(body.display_title).toHaveLength(128);
    expect(body.name).toHaveLength(200);
  });

  it('без права публикации новый skill всегда приватный, is_public не уходит из формы', async () => {
    mockCanPublish = false;
    routes['POST /api/skills/create'] = { body: { id: 12, slug: 'x', name: 'N', content: 'c' } };
    await renderPanel();
    await fillNew('N', 'c');
    fireEvent.click(saveBtn());
    await waitFor(() => expect(callsTo('POST', '/api/skills/create')).toHaveLength(1));
    expect(JSON.parse(callsTo('POST', '/api/skills/create')[0][1].body).is_public).toBe(false);
  });

  it('без права публикации при редактировании поле is_public в PUT отсутствует', async () => {
    mockCanPublish = false;
    routes['PUT /api/skills/1'] = { body: { ...DETAILS[1] } };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(saveBtn());
    await waitFor(() => expect(callsTo('PUT', '/api/skills/1')).toHaveLength(1));
    expect('is_public' in JSON.parse(callsTo('PUT', '/api/skills/1')[0][1].body)).toBe(false);
  });

  it('редактирование: PUT с id, переименование slug в чатах, уведомление «обновлён»', async () => {
    routes['PUT /api/skills/1'] = {
      body: { ...DETAILS[1], slug: 'alpha-2', display_title: 'Альфа 2', is_public: true },
    };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.change(field(/Служебное имя/), { target: { value: 'alpha-2' } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Skill обновлён'));
    const [, init] = callsTo('PUT', '/api/skills/1')[0];
    expect(JSON.parse(init.body).slug).toBe('alpha-2');
    expect(JSON.parse(init.body).is_public).toBe(false);
    expect(mockRenameSlug).toHaveBeenCalledWith('alpha', 'alpha-2', 'Альфа 2');
    expect(field(/Служебное имя/).value).toBe('alpha-2');
    // публичность берётся из ответа сервера
    expect(btn('Снять с публикации')).toBeTruthy();
  });

  it('ответ сохранения без slug/display_title: берётся slug из запроса, имя для чатов — name', async () => {
    routes['PUT /api/skills/1'] = {
      body: { id: 1, name: 'Alpha', content: 'Тело альфы', author_id: 'u1' },
    };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(saveBtn());
    await waitFor(() => expect(mockRenameSlug).toHaveBeenCalledWith('alpha', 'alpha', 'Alpha'));
    // пустые поля ответа не затирают введённые
    expect(field(/^Разрешённые инструменты \(/).value).toBe('t1, t2');
    expect(field(/^Теги/).value).toBe('x, y');
    expect(field(/^Категория/).value).toBe('cat');
  });

  it('ответ сохранения с новыми значениями применяется к форме', async () => {
    routes['PUT /api/skills/1'] = {
      body: {
        id: 1, slug: 'alpha', name: 'Alpha!', display_title: 'T!', description: 'D!', content: 'C!',
        is_active: false, user_invocable: false, disable_model_invocation: true, always_apply: true,
        allowed_tools: ['z'], category: 'K!', meta: { tags: ['g'] }, author_id: 'u1', my_permission: 'owner',
      },
    };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(saveBtn());
    await waitFor(() => expect(field(/^Название/).value).toBe('Alpha!'));
    expect(field(/Заголовок в интерфейсе/).value).toBe('T!');
    expect(field(/^Описание/).value).toBe('D!');
    expect(field(/^Текст \/ SKILL\.md/).value).toBe('C!');
    expect(field(/^Категория/).value).toBe('K!');
    expect(field(/^Разрешённые инструменты \(/).value).toBe('z');
    expect(field(/^Теги/).value).toBe('g');
    expect((screen.getByLabelText('Активен') as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText('Всегда применять') as HTMLInputElement).checked).toBe(true);
  });

  it('ошибка API: detail показывается в панели и в уведомлении', async () => {
    routes['POST /api/skills/create'] = { ok: false, status: 422, body: { detail: 'slug уже занят' } };
    await renderPanel();
    await fillNew();
    fireEvent.click(saveBtn());
    expect(await screen.findByText('slug уже занят')).toBeTruthy();
    expect(mockNotify).toHaveBeenCalledWith('error', 'slug уже занят');
    expect(screen.queryByText('Skill успешно сохранён')).toBeNull();
    expect(mockNotifySkillsChanged).not.toHaveBeenCalled();
    expect(saveBtn().disabled).toBe(false);
  });

  it('ошибка API с массивом detail (pydantic) склеивается в строку', async () => {
    routes['POST /api/skills/create'] = {
      ok: false, status: 422,
      body: { detail: [{ loc: ['body', 'name'], msg: 'слишком длинное' }] },
    };
    await renderPanel();
    await fillNew();
    fireEvent.click(saveBtn());
    expect(await screen.findByText('body.name: слишком длинное')).toBeTruthy();
  });

  it('ошибка API без detail и с нечитаемым телом -> запасное сообщение', async () => {
    routes['POST /api/skills/create'] = { ok: false, status: 500, bodyError: true };
    await renderPanel();
    await fillNew();
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Не удалось сохранить skill')).toBeTruthy();
  });

  it('успешный ответ без id -> «Сервер не вернул сохранённый skill»', async () => {
    routes['POST /api/skills/create'] = { body: { name: 'x' } };
    await renderPanel();
    await fillNew();
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Сервер не вернул сохранённый skill')).toBeTruthy();
  });

  it('сетевая ошибка при сохранении -> текст ошибки; не-Error -> «Ошибка сохранения»', async () => {
    routes['POST /api/skills/create'] = new Error('Network down');
    await renderPanel();
    await fillNew();
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Network down')).toBeTruthy();

    routes['POST /api/skills/create'] = () => {
      throw 42;
    };
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Ошибка сохранения')).toBeTruthy();
  });

  it('во время сохранения кнопка заблокирована и показывает «Сохраняю...»', async () => {
    let release!: (v: Reply) => void;
    routes['POST /api/skills/create'] = () => new Promise<Reply>((r) => { release = r; });
    await renderPanel();
    await fillNew();
    fireEvent.click(saveBtn());
    const busy = await screen.findByRole('button', { name: 'Сохраняю...' });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    await act(async () => {
      release({ body: { id: 1, slug: 's', name: 'N', content: 'c' } });
    });
    expect(await screen.findByRole('button', { name: 'Сохранить' })).toBeTruthy();
  });

  it('viewer не может сохранить даже при программном вызове: кнопки сохранения нет', async () => {
    await renderPanel();
    await pickSkill('Beta');
    expect(screen.queryByRole('button', { name: 'Сохранить' })).toBeNull();
    expect(callsTo('PUT', '/api/skills/2')).toHaveLength(0);
  });
});

describe('SkillsSidebarPanel: публикация, шаринг, удаление', () => {
  it('опубликовать: PUT {is_public:true}, оптимистичное обновление и уведомление', async () => {
    routes['PUT /api/skills/1'] = { body: { ...DETAILS[1], is_public: true } };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Skill опубликован в галерее'));
    expect(JSON.parse(callsTo('PUT', '/api/skills/1')[0][1].body)).toEqual({ is_public: true });
    expect(btn('Снять с публикации')).toBeTruthy();
    expect(mockNotifySkillsChanged).toHaveBeenCalledTimes(1);
  });

  it('снять с публикации: PUT {is_public:false}', async () => {
    routes['GET /api/skills/1'] = { body: { ...DETAILS[1], is_public: true } };
    routes['PUT /api/skills/1'] = { body: { ...DETAILS[1], is_public: false } };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn('Снять с публикации'));
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('success', 'Skill снят с публикации в галерее'),
    );
    expect(JSON.parse(callsTo('PUT', '/api/skills/1')[0][1].body)).toEqual({ is_public: false });
    expect(btn('Опубликовать в галерее')).toBeTruthy();
  });

  it('ответ без is_public — берётся запрошенное значение', async () => {
    routes['PUT /api/skills/1'] = { body: { id: 1 } };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Skill опубликован в галерее'));
  });

  it('ошибка публикации откатывает состояние и показывает detail', async () => {
    routes['PUT /api/skills/1'] = { ok: false, status: 403, body: { detail: 'Запрещено' } };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Запрещено'));
    expect(btn('Опубликовать в галерее')).toBeTruthy();
    expect(mockNotifySkillsChanged).not.toHaveBeenCalled();
  });

  it('ошибка публикации без detail и без JSON -> «Не удалось изменить публикацию»', async () => {
    routes['PUT /api/skills/1'] = { ok: false, status: 500, bodyError: true };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось изменить публикацию'),
    );
  });

  it('не-Error исключение при публикации -> запасное сообщение', async () => {
    routes['PUT /api/skills/1'] = () => {
      throw 'x';
    };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось изменить публикацию'),
    );
  });

  it('во время публикации кнопка заблокирована', async () => {
    let release!: (v: Reply) => void;
    routes['PUT /api/skills/1'] = () => new Promise<Reply>((r) => { release = r; });
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() => expect((btn(/Снять с публикации|Опубликовать/)).disabled).toBe(true));
    await act(async () => {
      release({ body: { id: 1, is_public: true } });
    });
  });

  it('«Поделиться скиллом» открывает диалог с id и названием', async () => {
    await renderPanel();
    await pickSkill('Альфа');
    expect(screen.getByTestId('share-dialog').getAttribute('data-open')).toBe('false');
    fireEvent.click(btn('Поделиться скиллом'));
    const dlg = screen.getByTestId('share-dialog');
    expect(dlg.getAttribute('data-open')).toBe('true');
    expect(dlg.getAttribute('data-id')).toBe('1');
    expect(dlg.getAttribute('data-name')).toBe('Alpha');
    fireEvent.click(screen.getByText('close-share'));
    expect(screen.getByTestId('share-dialog').getAttribute('data-open')).toBe('false');
  });

  it('у нового skill диалог шаринга и кнопки владельца не отображаются', async () => {
    await renderPanel();
    expect(screen.queryByTestId('share-dialog')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Удалить skill' })).toBeNull();
  });

  it('удаление: подтверждение, DELETE, уведомление, сброс формы и перезагрузка списка', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    routes['DELETE /api/skills/1'] = { body: {} };
    await renderPanel();
    await pickSkill('Альфа');
    const listCalls = callsTo('GET', '/api/skills/list').length;
    fireEvent.click(btn('Удалить skill'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Skill удалён'));
    expect(mockRemoveSlug).toHaveBeenCalledWith('alpha');
    expect(confirm).toHaveBeenCalledWith('Удалить skill «Alpha»?');
    expect(callsTo('DELETE', '/api/skills/1')).toHaveLength(1);
    expect(field(/^Название/).value).toBe('');
    expect(callsTo('GET', '/api/skills/list').length).toBe(listCalls + 1);
    expect(mockNotifySkillsChanged).toHaveBeenCalledTimes(1);
    confirm.mockRestore();
  });

  it('отмена подтверждения не отправляет DELETE', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(false);
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn('Удалить skill'));
    expect(callsTo('DELETE', '/api/skills/1')).toHaveLength(0);
    expect(field(/^Название/).value).toBe('Alpha');
    confirm.mockRestore();
  });

  it('ошибка удаления -> уведомление, форма остаётся', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    routes['DELETE /api/skills/1'] = { ok: false, status: 403 };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn('Удалить skill'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось удалить'));
    expect(field(/^Название/).value).toBe('Alpha');
    confirm.mockRestore();
  });

  it('не-Error исключение при удалении -> «Ошибка удаления»', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    routes['DELETE /api/skills/1'] = () => {
      throw 'x';
    };
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn('Удалить skill'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка удаления'));
    confirm.mockRestore();
  });

  it('в подтверждении удаления используется имя из формы, а если пусто — имя из списка/«skill»', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(false);
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.change(field(/^Название/), { target: { value: '' } });
    fireEvent.click(btn('Удалить skill'));
    expect(confirm).toHaveBeenCalledWith('Удалить skill «Alpha»?');
    confirm.mockRestore();
  });
});

describe('SkillsSidebarPanel: экспорт и импорт', () => {
  it('экспорт отключён, пока нет названия и текста', async () => {
    await renderPanel();
    expect(btn(/Экспорт/).disabled).toBe(true);
    await fillNew();
    expect(btn(/Экспорт/).disabled).toBe(false);
  });

  it.each([
    ['JSON (.json)', 'json', 'Skill экспортирован в JSON'],
    ['Markdown (.md)', 'md', 'Skill экспортирован в Markdown'],
  ])('экспорт %s вызывает exportSkill(form, %s)', async (item, fmt, note) => {
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn(/Экспорт/));
    fireEvent.click(await screen.findByText(item));
    expect(mockExportSkill).toHaveBeenCalledTimes(1);
    expect(mockExportSkill.mock.calls[0][1]).toBe(fmt);
    expect(mockExportSkill.mock.calls[0][0]).toMatchObject({ name: 'Alpha', slug: 'alpha', content: 'Тело альфы' });
    expect(mockNotify).toHaveBeenCalledWith('success', note);
  });

  it('меню экспорта закрывается по Escape без экспорта', async () => {
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn(/Экспорт/));
    const item = await screen.findByText('JSON (.json)');
    fireEvent.keyDown(item, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('JSON (.json)')).toBeNull());
    expect(mockExportSkill).not.toHaveBeenCalled();
  });

  it('ошибка exportSkill -> уведомление; не-Error -> «Ошибка export»', async () => {
    mockExportSkill.mockImplementationOnce(() => {
      throw new Error('диск полон');
    });
    await renderPanel();
    await pickSkill('Альфа');
    fireEvent.click(btn(/Экспорт/));
    fireEvent.click(await screen.findByText('JSON (.json)'));
    expect(mockNotify).toHaveBeenCalledWith('error', 'диск полон');

    mockExportSkill.mockImplementationOnce(() => {
      throw 'x';
    });
    fireEvent.click(btn(/Экспорт/));
    fireEvent.click(await screen.findByText('Markdown (.md)'));
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка export');
  }, 20000);

  it('кнопка «Импорт» открывает выбор файла', async () => {
    const { container } = await renderPanel();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const click = jest.spyOn(input, 'click');
    fireEvent.click(btn('Импорт'));
    expect(click).toHaveBeenCalled();
  });

  function pick(container: HTMLElement, file: File | null) {
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: file ? [file] : [] } });
  }

  it('импорт JSON: вызывает importSkillFile с токеном, перезагружает список', async () => {
    mockImportSkillFile.mockResolvedValue({ kind: 'json', imported: 3 });
    const { container } = await renderPanel();
    const before = callsTo('GET', '/api/skills/list').length;
    const file = new File(['[]'], 's.json', { type: 'application/json' });
    pick(container, file);
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Импорт завершён (3)'));
    expect(mockImportSkillFile).toHaveBeenCalledWith(file, 'tok');
    expect(callsTo('GET', '/api/skills/list').length).toBe(before + 1);
  });

  it('импорт MD: данные подставляются в форму нового skill', async () => {
    mockImportSkillFile.mockResolvedValue({
      kind: 'md', slug: 'imp', name: 'Imp', display_title: 'Imp T', description: 'imp d', content: 'imp c',
      is_active: false, is_public: true, user_invocable: false, disable_model_invocation: true,
      always_apply: true, allowed_tools: 'q', category: 'c', tags: 'tg',
    });
    const { container } = await renderPanel();
    await pickSkill('Альфа');
    pick(container, new File(['x'], 's.md'));
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('info', 'Данные из MD подставлены в форму — сохраните skill'),
    );
    expect(screen.getByText('Новый skill')).toBeTruthy();
    expect(field(/^Название/).value).toBe('Imp');
    expect(field(/Служебное имя/).value).toBe('imp');
    expect(field(/^Текст \/ SKILL\.md/).value).toBe('imp c');
    expect(field(/^Теги/).value).toBe('tg');
    expect((screen.getByLabelText('Активен') as HTMLInputElement).checked).toBe(false);
    expect(screen.queryByTestId('files-editor')).toBeNull();
  });

  it('ошибка импорта -> уведомление; не-Error -> «Ошибка import»', async () => {
    mockImportSkillFile.mockRejectedValueOnce(new Error('битый файл'));
    const { container } = await renderPanel();
    pick(container, new File(['x'], 's.json'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'битый файл'));

    mockImportSkillFile.mockRejectedValueOnce('x');
    pick(container, new File(['x'], 's.json'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка import'));
  });

  it('пустой выбор файла игнорируется', async () => {
    const { container } = await renderPanel();
    pick(container, null);
    expect(mockImportSkillFile).not.toHaveBeenCalled();
  });

  it('скрытый input доступен, но без прав редактирования импорт отклоняется', async () => {
    const { container } = await renderPanel();
    await pickSkill('Beta');
    pick(container, new File(['x'], 's.json'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Нет прав на импорт skill'));
    expect(mockImportSkillFile).not.toHaveBeenCalled();
  });
});
