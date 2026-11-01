import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import ShareDialog from '@/components/ShareDialog';
import ManageSharesDialog from '@/components/ManageSharesDialog';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a), getApiUrl: (p: string) => `http://api.test${p}` }));

const msg = (id: string, role: 'user' | 'assistant', content: string) => ({ id, role, content, timestamp: new Date() } as any);
const MESSAGES = [
  msg('u1', 'user', '# Заголовок *вопроса*'),
  msg('a1', 'assistant', 'ответ `код` ' + 'x'.repeat(200)),
  msg('u2', 'user', 'второй вопрос'),
  msg('a2', 'assistant', 'второй ответ'),
  msg('u3', 'user', 'без ответа'),
];

describe('ShareDialog', () => {
  let fetchMock: jest.Mock;
  beforeEach(() => {
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: jest.fn() } });
  });
  afterEach(() => jest.restoreAllMocks());

  const setup = (over: Record<string, unknown> = {}) => {
    const onClose = jest.fn();
    render(<ShareDialog open onClose={onClose} messages={MESSAGES} isDarkMode={false} {...(over as any)} />);
    return { onClose };
  };

  it('строит пары вопрос-ответ, убирает markdown-символы и обрезает ответ до 150 символов', () => {
    setup();
    expect(screen.getByText('Вопрос 1:')).toBeInTheDocument();
    expect(screen.getByText('Вопрос 2:')).toBeInTheDocument();
    expect(screen.queryByText('Вопрос 3:')).toBeNull();
    expect(screen.getByText('Заголовок вопроса')).toBeInTheDocument();
    const longAnswer = screen.getByText(/^ответ код x+\.\.\.$/);
    expect(longAnswer.textContent!.length).toBe(153);
  });

  it('кнопка создания недоступна без выбора; ошибка при пустом выборе недостижима из UI', () => {
    setup({ isDarkMode: true });
    expect(screen.getByText('Создать публичную ссылку').closest('button')).toBeDisabled();
    expect(screen.getByText('Выбрано пар сообщений: 0')).toBeInTheDocument();
  });

  it('клик по паре выбирает/снимает обе записи; «Выбрать все» синхронизируется', () => {
    setup();
    const all = screen.getByLabelText('Выбрать все') as HTMLInputElement;
    fireEvent.click(screen.getByText('Вопрос 1:'));
    expect(screen.getByText('Выбрано пар сообщений: 1')).toBeInTheDocument();
    expect(all.checked).toBe(false);
    fireEvent.click(screen.getByText('Вопрос 2:'));
    expect(all.checked).toBe(true);
    fireEvent.click(screen.getByText('Вопрос 1:'));
    expect(screen.getByText('Выбрано пар сообщений: 1')).toBeInTheDocument();
    expect(all.checked).toBe(false);
    fireEvent.click(all);
    expect(screen.getByText('Выбрано пар сообщений: 2')).toBeInTheDocument();
    fireEvent.click(all);
    expect(screen.getByText('Выбрано пар сообщений: 0')).toBeInTheDocument();
  });

  it('создание ссылки: отправляет выбранные сообщения по порядку и показывает ссылку', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ share_id: 'abc123' }) });
    setup();
    fireEvent.click(screen.getByText('Вопрос 2:'));
    fireEvent.click(screen.getByText('Вопрос 1:'));
    await act(async () => {
      fireEvent.click(screen.getByText('Создать публичную ссылку'));
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/share/create');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(JSON.parse(init.body).messages.map((m: any) => m.id)).toEqual(['u1', 'a1', 'u2', 'a2']);
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', `${window.location.origin}/share/abc123`);
    expect(screen.getByText('Публичная ссылка успешно создана!')).toBeInTheDocument();
    expect(screen.getByText('Закрыть')).toBeInTheDocument();
  });

  it('запрос без заголовка авторизации и по относительному адресу (known issue)', async () => {
    // Правильно: как в ManageSharesDialog — getApiUrl(...) и Authorization: Bearer <token>.
    // Сейчас вызывается относительный '/api/share/create' без токена: работает только при совпадении origin и cookie.
    localStorage.setItem('auth_token', 'tok');
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ share_id: 's' }) });
    setup();
    fireEvent.click(screen.getByText('Вопрос 1:'));
    await act(async () => {
      fireEvent.click(screen.getByText('Создать публичную ссылку'));
    });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/share/create');
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ 'Content-Type': 'application/json' });
    localStorage.clear();
  });

  it('ошибка HTTP показывает сообщение, не-Error — общий текст', async () => {
    setup();
    fireEvent.click(screen.getByText('Вопрос 1:'));
    fetchMock.mockResolvedValueOnce({ ok: false });
    await act(async () => {
      fireEvent.click(screen.getByText('Создать публичную ссылку'));
    });
    expect(screen.getByText('Ошибка создания публичной ссылки')).toBeInTheDocument();
    fetchMock.mockRejectedValueOnce('boom');
    await act(async () => {
      fireEvent.click(screen.getByText('Создать публичную ссылку'));
    });
    expect(screen.getByText('Произошла ошибка')).toBeInTheDocument();
  });

  it('индикатор загрузки блокирует кнопку', async () => {
    let resolve!: (v: unknown) => void;
    fetchMock.mockReturnValue(new Promise((r) => (resolve = r)));
    setup();
    fireEvent.click(screen.getByText('Вопрос 1:'));
    fireEvent.click(screen.getByText('Создать публичную ссылку'));
    expect(screen.getByText('Создание...').closest('button')).toBeDisabled();
    await act(async () => resolve({ ok: true, json: async () => ({ share_id: 'z' }) }));
    expect(screen.getByRole('link')).toBeInTheDocument();
  });

  it('копирование ссылки и закрытие со сбросом состояния', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ share_id: 'k' }) });
    const { onClose } = setup();
    fireEvent.click(screen.getByText('Вопрос 1:'));
    await act(async () => {
      fireEvent.click(screen.getByText('Создать публичную ссылку'));
    });
    const dialog = screen.getByRole('dialog');
    const buttons = within(dialog).getAllByRole('button');
    // [крестик, копирование, закрыть]
    fireEvent.click(buttons[1]);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(`${window.location.origin}/share/k`);
    fireEvent.click(screen.getByText('Закрыть'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('«Отмена» закрывает; закрытый диалог не рисуется', () => {
    const { onClose } = setup();
    fireEvent.click(screen.getByText('Отмена'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('ManageSharesDialog', () => {
  let fetchMock: jest.Mock;
  const NOW = new Date('2025-06-15T12:00:00Z');
  const day = (n: number) => new Date(NOW.getTime() - n * 86400000).toISOString();
  const shares = [
    { share_id: 's1', created_at: day(0), messages: [{ role: 'user', content: 'короткий вопрос' }] },
    { share_id: 's2', created_at: day(1), messages: [{ role: 'assistant', content: 'нет user' }] },
    { share_id: 's3', created_at: day(3), messages: [{ role: 'user', content: 'в'.repeat(60) }] },
    { share_id: 's4', created_at: '2024-01-05T10:00:00Z', messages: [{ role: 'user' }] },
  ];

  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(NOW);
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
    localStorage.clear();
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: jest.fn().mockResolvedValue(undefined) } });
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  const flush = async () => {
    await act(async () => {
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
    });
  };

  it('закрытый диалог не делает запросов', () => {
    render(<ManageSharesDialog open={false} onClose={jest.fn()} isDarkMode />);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('загружает список с токеном (auth_token, затем token) и показывает записи', async () => {
    localStorage.setItem('token', 'legacy');
    fetchMock.mockResolvedValue({ ok: true, json: async () => shares });
    render(<ManageSharesDialog open onClose={jest.fn()} isDarkMode={false} />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    await flush();
    expect(fetchMock).toHaveBeenCalledWith('http://api.test/api/share/my-shares', {
      credentials: 'include',
      headers: { Authorization: 'Bearer legacy' },
    });
    expect(screen.getByText('короткий вопрос')).toBeInTheDocument();
    expect(screen.getByText('Без названия')).toBeInTheDocument();
    expect(screen.getByText('в'.repeat(50) + '...')).toBeInTheDocument();
    expect(screen.getByText('Опубликовано Сегодня')).toBeInTheDocument();
    expect(screen.getByText('Опубликовано Вчера')).toBeInTheDocument();
    expect(screen.getByText('Опубликовано 3 дн. назад')).toBeInTheDocument();
    expect(screen.getByText(/Опубликовано 5 января 2024/)).toBeInTheDocument();
  });

  it('без токена заголовок авторизации не добавляется', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] });
    render(<ManageSharesDialog open onClose={jest.fn()} isDarkMode />);
    await flush();
    expect(fetchMock.mock.calls[0][1].headers).toEqual({});
    expect(screen.getByText('У вас пока нет публичных ссылок')).toBeInTheDocument();
  });

  it('ошибки загрузки логируются, список остаётся пустым', async () => {
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500, statusText: 'ISE' });
    const { unmount } = render(<ManageSharesDialog open onClose={jest.fn()} isDarkMode />);
    await flush();
    expect(err).toHaveBeenCalledWith('Ошибка при загрузке публичных ссылок:', 500, 'ISE');
    unmount();
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    render(<ManageSharesDialog open onClose={jest.fn()} isDarkMode />);
    await flush();
    expect(err).toHaveBeenCalledWith('Ошибка при загрузке публичных ссылок:', expect.any(Error));
  });

  it('копирование ссылки показывает «Скопировано!» две секунды', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => [shares[0]] });
    render(<ManageSharesDialog open onClose={jest.fn()} isDarkMode />);
    await flush();
    const [copyBtn] = within(screen.getByRole('dialog')).getAllByRole('button').filter((b) => b.querySelector('svg[data-testid="ContentCopyIcon"]'));
    await act(async () => {
      fireEvent.click(copyBtn);
    });
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(`${window.location.origin}/share/s1`);
    act(() => {
      jest.advanceTimersByTime(2100);
    });
    expect(copyBtn).toBeInTheDocument();
  });

  it('удаление: отмена подтверждения, успех, ошибка ответа и исключение', async () => {
    const alertSpy = jest.spyOn(window, 'alert').mockImplementation(() => {});
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [shares[0], shares[2]] });
    render(<ManageSharesDialog open onClose={jest.fn()} isDarkMode />);
    await flush();
    const delButtons = () =>
      within(screen.getByRole('dialog'))
        .getAllByRole('button')
        .filter((b) => b.querySelector('svg[data-testid="DeleteIcon"]'));
    const confirm = jest.spyOn(window, 'confirm').mockReturnValueOnce(false);
    await act(async () => {
      fireEvent.click(delButtons()[0]);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    confirm.mockReturnValue(true);
    fetchMock.mockResolvedValueOnce({ ok: false });
    await act(async () => {
      fireEvent.click(delButtons()[0]);
    });
    expect(alertSpy).toHaveBeenCalledWith('Ошибка при удалении ссылки');
    expect(screen.getByText('короткий вопрос')).toBeInTheDocument();

    fetchMock.mockRejectedValueOnce(new Error('net'));
    await act(async () => {
      fireEvent.click(delButtons()[0]);
    });
    expect(err).toHaveBeenCalledWith('Ошибка при удалении публичной ссылки:', expect.any(Error));

    localStorage.setItem('auth_token', 'T');
    fetchMock.mockResolvedValueOnce({ ok: true });
    await act(async () => {
      fireEvent.click(delButtons()[0]);
    });
    const last = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
    expect(last[0]).toBe('http://api.test/api/share/s1');
    expect(last[1]).toEqual({ method: 'DELETE', credentials: 'include', headers: { Authorization: 'Bearer T' } });
    expect(screen.queryByText('короткий вопрос')).toBeNull();
    expect(screen.getByText(/в{50}/)).toBeInTheDocument();
  });

  it('крестик закрывает окно', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] });
    const onClose = jest.fn();
    render(<ManageSharesDialog open onClose={onClose} isDarkMode={false} />);
    await flush();
    fireEvent.click(within(screen.getByRole('dialog')).getAllByRole('button')[0]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
