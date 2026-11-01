import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import SkillFilesEditor from '@/components/skills/SkillFilesEditor';
import PluginRunBanner from '@/components/PluginRunBanner';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  API_ENDPOINTS: { SKILLS: '/api/skills' },
}));

const mockNavigate = jest.fn();
let mockPath = '/chat';
let mockSearch = '';
jest.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: mockPath, search: mockSearch }),
}));

let mockAuth = { isAuthenticated: true };
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => mockAuth }));

let mockRuns: any;
jest.mock('@/contexts/PluginRunContext', () => ({ usePluginRuns: () => mockRuns }));

const flush = async () => {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
};

describe('SkillFilesEditor', () => {
  let fetchMock: jest.Mock;
  const FILES = [
    { id: 1, relative_path: 'scripts/run.sh', category: 'scripts', bytes: 120 },
    { id: 2, relative_path: 'references/a.md', category: 'references', bytes: 5 },
  ];

  beforeEach(() => {
    fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => FILES });
    (global as any).fetch = fetchMock;
  });
  afterEach(() => jest.restoreAllMocks());

  it('загружает список файлов с токеном и показывает путь, категорию и размер', async () => {
    render(<SkillFilesEditor skillId={7} token="T" canWrite />);
    await flush();
    expect(fetchMock).toHaveBeenCalledWith('http://api.test/api/skills/7/files', {
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer T' },
    });
    expect(screen.getByText('scripts/run.sh')).toBeInTheDocument();
    expect(screen.getByText('scripts · 120 Б')).toBeInTheDocument();
  });

  it('пустой токен - без Authorization; пустой список - «Файлов пока нет»; ошибка загрузки оставляет пусто', async () => {
    fetchMock.mockResolvedValue({ ok: false });
    render(<SkillFilesEditor skillId={1} token="" canWrite={false} />);
    await flush();
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ 'Content-Type': 'application/json' });
    expect(screen.getByText('Файлов пока нет.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Удалить файл')).toBeNull();
    expect(screen.queryByText('Сохранить файл')).toBeNull();
  });

  it('сохранение файла: POST с обрезанным путём, очистка содержимого и перезагрузка', async () => {
    render(<SkillFilesEditor skillId={7} token="T" canWrite />);
    await flush();
    fireEvent.change(screen.getByLabelText('Относительный путь'), { target: { value: '  scripts/new.sh  ' } });
    fireEvent.change(screen.getByLabelText('Содержимое файла'), { target: { value: 'echo 1' } });
    fetchMock.mockClear();
    fetchMock.mockResolvedValue({ ok: true, json: async () => FILES });
    await act(async () => {
      fireEvent.click(screen.getByText('Сохранить файл'));
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/api/skills/7/files');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ relative_path: 'scripts/new.sh', content: 'echo 1', mime_type: 'text/plain' });
    expect(fetchMock).toHaveBeenCalledTimes(2); // POST + повторная загрузка
    expect((screen.getByLabelText('Содержимое файла') as HTMLTextAreaElement).value).toBe('');
  });

  it('пустой путь не отправляется', async () => {
    render(<SkillFilesEditor skillId={7} token="T" canWrite />);
    await flush();
    fireEvent.change(screen.getByLabelText('Относительный путь'), { target: { value: '   ' } });
    fetchMock.mockClear();
    await act(async () => {
      fireEvent.click(screen.getByText('Сохранить файл'));
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('удаление файла: DELETE и перезагрузка списка', async () => {
    render(<SkillFilesEditor skillId={7} token="T" canWrite />);
    await flush();
    fetchMock.mockClear();
    fetchMock.mockResolvedValue({ ok: true, json: async () => [FILES[1]] });
    await act(async () => {
      fireEvent.click(screen.getAllByLabelText('Удалить файл')[0]);
    });
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/skills/7/files/1');
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE');
    expect(screen.queryByText('scripts/run.sh')).toBeNull();
  });

  it('справка показывает подсказки', () => {
    render(<SkillFilesEditor skillId={7} token="T" canWrite />);
    expect(screen.getByLabelText('Справка: вложенные файлы')).toBeInTheDocument();
    expect(screen.getByLabelText('Справка: относительный путь')).toBeInTheDocument();
  });
});

describe('PluginRunBanner', () => {
  const now = new Date('2025-05-05T10:00:00Z').getTime();
  const run = (over: Record<string, unknown> = {}) => ({
    pluginId: 'p1',
    pluginName: 'Аудит',
    status: 'running',
    startedAtMs: now - 5000,
    ...over,
  });

  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(now);
    mockNavigate.mockReset();
    mockPath = '/chat';
    mockSearch = '';
    mockAuth = { isAuthenticated: true };
    mockRuns = { runningPlugins: [], runs: {}, elapsedSec: () => 0 };
  });
  afterEach(() => jest.useRealTimers());

  it('ничего не показывает без активных запусков', () => {
    const { container } = render(<PluginRunBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it('идущий запуск: текст со временем и кнопка «Открыть»', () => {
    const r = run();
    mockRuns = { runningPlugins: [r], runs: { p1: r }, elapsedSec: (id: string) => (id === 'p1' ? 75 : 0) };
    render(<PluginRunBanner />);
    expect(screen.getByRole('status')).toHaveTextContent('Аудит: аудит выполняется — 1:15');
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Открыть'));
    expect(mockNavigate).toHaveBeenCalledWith('/gallery?tab=plugins');
  });

  it('свежий успешный результат показывается и сам исчезает через 10 секунд', () => {
    mockRuns = { runningPlugins: [], runs: { p1: run({ status: 'done', finishedAtMs: now - 1000 }) }, elapsedSec: () => 0 };
    render(<PluginRunBanner />);
    expect(screen.getByRole('status')).toHaveTextContent('Аудит: аудит готов');
    expect(screen.getByText('Показать результат')).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(10_000);
    });
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('ошибка: красный текст «не завершён»', () => {
    mockRuns = { runningPlugins: [], runs: { p1: run({ status: 'error', finishedAtMs: now - 100 }) }, elapsedSec: () => 0 };
    render(<PluginRunBanner />);
    expect(screen.getByRole('status')).toHaveTextContent('Аудит: аудит не завершён');
  });

  it('устаревший результат не показывается; из нескольких берётся самый свежий', () => {
    mockRuns = {
      runningPlugins: [],
      runs: {
        old: run({ pluginId: 'old', pluginName: 'Старый', status: 'done', finishedAtMs: now - 60_000 }),
      },
      elapsedSec: () => 0,
    };
    const { container, rerender } = render(<PluginRunBanner />);
    expect(container).toBeEmptyDOMElement();
    mockRuns = {
      runningPlugins: [],
      runs: {
        a: run({ pluginId: 'a', pluginName: 'А', status: 'done', finishedAtMs: now - 5000 }),
        b: run({ pluginId: 'b', pluginName: 'Б', status: 'done', finishedAtMs: now - 1000 }),
        c: run({ pluginId: 'c', pluginName: 'В', status: 'running' }),
        d: run({ pluginId: 'd', pluginName: 'Г', status: 'done' }),
      },
      elapsedSec: () => 0,
    };
    rerender(<PluginRunBanner />);
    expect(screen.getByRole('status')).toHaveTextContent('Б: аудит готов');
  });

  it('крестик скрывает именно этот результат', () => {
    mockRuns = { runningPlugins: [], runs: { p1: run({ status: 'done', finishedAtMs: now - 100 }) }, elapsedSec: () => 0 };
    render(<PluginRunBanner />);
    fireEvent.click(screen.getByLabelText('Скрыть уведомление о запуске плагина'));
    expect(screen.queryByRole('status')).toBeNull();
  });

  it.each([
    ['/gallery', '?tab=plugins'],
    ['/login', ''],
    ['/share/abc', ''],
  ])('на маршруте %s%s плашка скрыта', (path, search) => {
    mockPath = path;
    mockSearch = search;
    const r = run();
    mockRuns = { runningPlugins: [r], runs: { p1: r }, elapsedSec: () => 1 };
    const { container } = render(<PluginRunBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it('без авторизации плашка скрыта', () => {
    mockAuth = { isAuthenticated: false };
    const r = run();
    mockRuns = { runningPlugins: [r], runs: { p1: r }, elapsedSec: () => 1 };
    const { container } = render(<PluginRunBanner />);
    expect(container).toBeEmptyDOMElement();
  });
});
