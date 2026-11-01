import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import SkillMentionAutocomplete from '@/components/chat/SkillMentionAutocomplete';

let mockToken: string | null = 'tok';
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ token: mockToken }),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
  API_ENDPOINTS: { SKILLS: '/skills' },
}));

const SKILLS = [
  { id: 1, slug: 'sum', name: 'Саммари', description: 'Кратко' },
  { id: 2, slug: 'tr', name: 'Перевод', description: null },
  { id: 3, slug: 'off', name: 'Выключен', is_active: false },
  { id: 4, slug: 'hid', name: 'Скрытый', user_invocable: false },
];

const fetchMock = jest.fn();

function anchor(): HTMLElement {
  const el = document.createElement('div');
  el.getBoundingClientRect = () =>
    ({ left: 5, top: 400, width: 500, height: 20, right: 505, bottom: 420 } as DOMRect);
  return el;
}

async function load() {
  await act(async () => {
    jest.advanceTimersByTime(200);
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('SkillMentionAutocomplete', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockToken = 'tok';
    fetchMock.mockReset();
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ items: SKILLS }) });
    (global as any).fetch = fetchMock;
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('не рендерится и не делает запросов, пока закрыт', async () => {
    const { container } = render(
      <SkillMentionAutocomplete query="" open={false} anchorEl={anchor()} onSelect={jest.fn()} />,
    );
    await load();
    expect(container).toBeEmptyDOMElement();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('без токена запроса нет, «Ничего не найдено»', async () => {
    mockToken = null;
    render(<SkillMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />);
    await load();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
  });

  it('без anchorEl ничего не рисует', async () => {
    const { container } = render(
      <SkillMentionAutocomplete query="" open anchorEl={null} onSelect={jest.fn()} />,
    );
    await load();
    expect(container).toBeEmptyDOMElement();
  });

  it('запрос с дебаунсом 180 мс: URL, query-параметры и Bearer-заголовок', async () => {
    render(<SkillMentionAutocomplete query="  сум " open anchorEl={anchor()} onSelect={jest.fn()} />);
    expect(fetchMock).not.toHaveBeenCalled();
    act(() => {
      jest.advanceTimersByTime(179);
    });
    expect(fetchMock).not.toHaveBeenCalled();
    await load();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api/skills/list?page=1&limit=20&query=%D1%81%D1%83%D0%BC');
    expect(init.headers).toEqual({ Authorization: 'Bearer tok' });
  });

  it('пустой query не добавляется в параметры', async () => {
    render(<SkillMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />);
    await load();
    expect(fetchMock.mock.calls[0][0]).toBe('http://api/skills/list?page=1&limit=20');
  });

  it('показывает загрузку, затем только активные и вызываемые пользователем навыки', async () => {
    let resolveJson!: (v: unknown) => void;
    fetchMock.mockResolvedValue({ ok: true, json: () => new Promise((r) => (resolveJson = r)) });
    render(<SkillMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />);
    await load();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    await act(async () => {
      resolveJson({ items: SKILLS });
    });
    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(screen.getByText('Саммари')).toBeInTheDocument();
    expect(screen.getByText('Перевод')).toBeInTheDocument();
    expect(screen.queryByText('Выключен')).toBeNull();
    expect(screen.queryByText('Скрытый')).toBeNull();
    // подпись: $slug и описание только если оно есть
    expect(screen.getByText(/\$sum — Кратко/)).toBeInTheDocument();
    expect(screen.getByText('$tr')).toBeInTheDocument();
  });

  it('data.items отсутствует -> пустой список', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    render(<SkillMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />);
    await load();
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
  });

  it('HTTP-ошибка и сетевой сбой дают пустой список без исключений', async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, json: async () => ({}) });
    const { unmount } = render(
      <SkillMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />,
    );
    await load();
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
    unmount();
    fetchMock.mockRejectedValueOnce(new Error('net'));
    render(<SkillMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />);
    await load();
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
  });

  it('ввод в поле поиска вызывает новый запрос с этим текстом', async () => {
    render(<SkillMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />);
    await load();
    fireEvent.change(screen.getByPlaceholderText('Поиск skills по имени'), {
      target: { value: 'abc' },
    });
    await load();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toContain('query=abc');
  });

  it('устаревший успешный ответ (закрыли до ответа) не меняет состояние', async () => {
    let resolveJson!: (v: unknown) => void;
    fetchMock.mockResolvedValue({ ok: true, json: () => new Promise((r) => (resolveJson = r)) });
    const a = anchor();
    const { rerender } = render(
      <SkillMentionAutocomplete query="" open anchorEl={a} onSelect={jest.fn()} />,
    );
    await load();
    rerender(<SkillMentionAutocomplete query="" open={false} anchorEl={a} onSelect={jest.fn()} />);
    await act(async () => {
      resolveJson({ items: SKILLS });
    });
    rerender(<SkillMentionAutocomplete query="" open anchorEl={a} onSelect={jest.fn()} />);
    expect(screen.queryByText('Саммари')).toBeNull();
  });

  it('устаревший сбой (cancelled) тоже игнорируется', async () => {
    let rejectJson!: (e: unknown) => void;
    fetchMock.mockResolvedValue({ ok: true, json: () => new Promise((_, r) => (rejectJson = r)) });
    const a = anchor();
    const { rerender } = render(
      <SkillMentionAutocomplete query="" open anchorEl={a} onSelect={jest.fn()} />,
    );
    await load();
    rerender(<SkillMentionAutocomplete query="" open={false} anchorEl={a} onSelect={jest.fn()} />);
    await act(async () => {
      rejectJson(new Error('x'));
    });
    expect(document.body).not.toHaveTextContent('Ничего не найдено');
  });

  it('клик выбирает навык; клавиатура Arrow/Enter/Tab; наведение переносит выбор', async () => {
    const onSelect = jest.fn();
    render(<SkillMentionAutocomplete query="" open anchorEl={anchor()} onSelect={onSelect} />);
    await load();
    fireEvent.mouseDown(screen.getByText('Перевод'));
    expect(onSelect).toHaveBeenLastCalledWith({ id: 2, slug: 'tr', name: 'Перевод', description: null });

    fireEvent.keyDown(window, { key: 'ArrowUp' });
    fireEvent.keyDown(window, { key: 'ArrowDown' });
    fireEvent.keyDown(window, { key: 'ArrowDown' });
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: 2 }));
    fireEvent.keyDown(window, { key: 'ArrowUp' });
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: 1 }));
    fireEvent.mouseEnter(screen.getByText('Перевод'));
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: 2 }));
    const n = onSelect.mock.calls.length;
    fireEvent.keyDown(window, { key: 'x' });
    expect(onSelect).toHaveBeenCalledTimes(n);
  });

  it('клавиши игнорируются при пустом списке и в закрытом состоянии', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    const onSelect = jest.fn();
    const a = anchor();
    const { rerender } = render(
      <SkillMentionAutocomplete query="" open anchorEl={a} onSelect={onSelect} />,
    );
    await load();
    fireEvent.keyDown(window, { key: 'Enter' });
    rerender(<SkillMentionAutocomplete query="" open={false} anchorEl={a} onSelect={onSelect} />);
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('mousedown в поле поиска не всплывает; светлая тема; позиция и ширина панели', async () => {
    const outer = jest.fn();
    render(
      <div onMouseDown={outer}>
        <SkillMentionAutocomplete
          query=""
          open
          anchorEl={anchor()}
          onSelect={jest.fn()}
          isDarkMode={false}
        />
      </div>,
    );
    await load();
    const input = screen.getByPlaceholderText('Поиск skills по имени');
    fireEvent.mouseDown(input);
    expect(outer).not.toHaveBeenCalled();
    expect(getComputedStyle(input).color).toBe('rgba(0, 0, 0, 0.87)');
    const paper = document.querySelector('.MuiPaper-root') as HTMLElement;
    expect(getComputedStyle(paper).left).toBe('5px');
    expect(getComputedStyle(paper).width).toBe('360px');
  });
});
