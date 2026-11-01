import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import HistoryPage from '@/pages/HistoryPage';

const mock: any = {};
function reset() {
  mock.actions = {
    showNotification: jest.fn(),
    clearMessages: jest.fn(),
    getCurrentMessages: jest.fn(() => []),
    getCurrentChat: jest.fn(() => ({ id: 'c1' })),
  };
}
reset();

jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: {} }),
  useAppActions: () => mock.actions,
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
  getAuthFetchHeaders: () => ({ Authorization: 'Bearer t' }),
  API_ENDPOINTS: { HISTORY: '/api/history' },
}));

const fetchMock = jest.fn();
const NOW = new Date('2025-06-10T12:00:00Z');

const entries = [
  { role: 'user', content: 'Привет мир', timestamp: '2025-06-10T10:00:00Z' },
  { role: 'assistant', content: 'Ответ ' + 'x'.repeat(150), timestamp: '2025-06-01T10:00:00Z' },
  { role: 'user', content: 'Другое', timestamp: '2025-06-10T11:00:00Z' },
];

const json = (data: any) => ({ ok: true, status: 200, json: async () => data });

async function renderPage(history: any = { history: entries, count: 3 }) {
  fetchMock.mockResolvedValueOnce(json(history));
  const utils = render(<HistoryPage />);
  await act(async () => {});
  return utils;
}

describe('HistoryPage', () => {
  let clickSpy: jest.SpyInstance;
  beforeEach(() => {
    jest.useFakeTimers('modern');
    jest.setSystemTime(NOW);
    reset();
    fetchMock.mockReset();
    (global as any).fetch = fetchMock;
    (URL as any).createObjectURL = jest.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = jest.fn();
    clickSpy = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      (clickSpy as any).last = { href: this.href, download: this.download };
    });
    jest.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('грузит историю с токеном и показывает статистику и уведомление', async () => {
    await renderPage();
    expect(fetchMock).toHaveBeenCalledWith('http://api/api/history?limit=1000', {
      headers: { Authorization: 'Bearer t' },
    });
    expect(mock.actions.showNotification).toHaveBeenCalledWith('success', 'Загружено 3 записей истории');
    const total = screen.getByText('Всего сообщений:').parentElement!;
    expect(within(total).getByText('3')).toBeTruthy();
    expect(within(screen.getByText('От пользователя:').parentElement!).getByText('2')).toBeTruthy();
    expect(within(screen.getByText('От ассистента:').parentElement!).getByText('1')).toBeTruthy();
    const chars = entries.reduce((s, e) => s + e.content.length, 0);
    expect(within(screen.getByText('Всего символов:').parentElement!).getByText(chars.toLocaleString())).toBeTruthy();
  });

  it('длинное сообщение усекается до 100 символов с многоточием', async () => {
    await renderPage();
    expect(screen.getByText(/^Ответ x+\.\.\.$/).textContent).toHaveLength(103);
    expect(screen.getByText('Привет мир')).toBeTruthy();
  });

  it('свежие записи показывают время, старые — дату', async () => {
    await renderPage();
    const chips = Array.from(document.querySelectorAll('.MuiChip-label'))
      .map((n) => n.textContent || '')
      .filter((t) => /\d\d[:.]\d\d/.test(t) && !/^\d+$/.test(t));
    // свежие (<24ч): только время «ЧЧ:ММ»; старая: «ДД.ММ, ЧЧ:ММ»
    expect(chips.filter((t) => /^\d\d:\d\d$/.test(t)).length).toBe(2);
    expect(chips.filter((t) => /^\d\d\.\d\d,? \d\d:\d\d$/.test(t)).length).toBe(1);
  });

  it('ответ без history даёт пустую историю', async () => {
    await renderPage({ error: 'x' });
    expect(screen.getByText('История пуста')).toBeTruthy();
    expect(screen.queryByText('Очистить')).toBeNull();
    expect((screen.getByText('Экспорт').closest('button') as HTMLButtonElement).disabled).toBe(true);
  });

  it('ошибка сети: уведомление и откат к сообщениям текущего чата', async () => {
    mock.actions.getCurrentMessages.mockReturnValue([
      { role: 'user', content: 'локально', timestamp: '2025-06-10T11:30:00Z' },
    ]);
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    render(<HistoryPage />);
    await act(async () => {});
    expect(mock.actions.showNotification).toHaveBeenCalledWith('error', 'Не удалось загрузить историю');
    expect(screen.getByText('локально')).toBeTruthy();
  });

  it('во время загрузки показывает «Загрузка истории...» и блокирует обновление', async () => {
    fetchMock.mockReturnValueOnce(new Promise(() => {}));
    render(<HistoryPage />);
    expect(screen.getByText('Загрузка истории...')).toBeTruthy();
    expect((screen.getByTestId('RefreshIcon').closest('button') as HTMLButtonElement).disabled).toBe(true);
  });

  it('кнопка обновления перезагружает историю', async () => {
    await renderPage();
    fetchMock.mockResolvedValueOnce(json({ history: [entries[0]], count: 1 }));
    await act(async () => {
      fireEvent.click(screen.getByTestId('RefreshIcon').closest('button')!);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('Другое')).toBeNull();
  });

  it('поиск фильтрует без учёта регистра, показывает счётчик и «Ничего не найдено»', async () => {
    await renderPage();
    const box = screen.getByPlaceholderText('Поиск сообщений...');
    fireEvent.change(box, { target: { value: 'ПРИВЕТ' } });
    expect(screen.getByText('Привет мир')).toBeTruthy();
    expect(screen.queryByText('Другое')).toBeNull();
    expect(screen.getByText('Найдено: 1 из 3')).toBeTruthy();
    fireEvent.change(box, { target: { value: 'нет такого' } });
    expect(screen.getByText('Ничего не найдено')).toBeTruthy();
    // кнопка сброса поиска
    fireEvent.click(screen.getByTestId('ClearIcon').closest('button')!);
    expect(screen.getByText('Другое')).toBeTruthy();
    expect(screen.queryByText(/Найдено:/)).toBeNull();
  });

  it('пробельный поисковый запрос не фильтрует', async () => {
    await renderPage();
    fireEvent.change(screen.getByPlaceholderText('Поиск сообщений...'), { target: { value: '   ' } });
    expect(screen.getByText('Другое')).toBeTruthy();
    expect(screen.getByText('Привет мир')).toBeTruthy();
  });

  it('экспорт отфильтрованных записей создаёт txt-файл и уведомляет', async () => {
    await renderPage();
    fireEvent.change(screen.getByPlaceholderText('Поиск сообщений...'), { target: { value: 'другое' } });
    fireEvent.click(screen.getByText('Экспорт'));
    const blob: Blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0];
    expect(blob.type).toBe('text/plain; charset=utf-8');
    expect(blob.size).toBeGreaterThan(0);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect((clickSpy as any).last.download).toBe('astrachat_history_2025-06-10.txt');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x');
    expect(mock.actions.showNotification).toHaveBeenCalledWith('success', 'История экспортирована');
  });

  it('имя файла экспорта соответствует бренду AstraChat, а не «gazikii» (BUG-FE-67)', async () => {
    await renderPage();
    fireEvent.click(screen.getByText('Экспорт'));
    expect((clickSpy as any).last.download).toMatch(/^astrachat_history_/);
    expect((clickSpy as any).last.download).not.toMatch(/gazikii/);
  });

  it('содержимое экспорта: время, роль и текст каждой записи', async () => {
    let captured = '';
    const OrigBlob = global.Blob;
    (global as any).Blob = function (parts: string[], opts: any) {
      captured = parts.join('');
      return new OrigBlob(parts, opts);
    };
    await renderPage();
    fireEvent.click(screen.getByText('Экспорт'));
    (global as any).Blob = OrigBlob;
    const lines = captured.split('\n\n');
    expect(lines).toHaveLength(3);
    expect(lines[0]).toMatch(/^\[.+\] Пользователь: Привет мир$/);
    expect(lines[1]).toMatch(/^\[.+\] astrachat: Ответ x+$/);
  });

  it('клик по записи открывает диалог с полным текстом; закрытие скрывает его', async () => {
    await renderPage();
    fireEvent.click(screen.getByText(/^Ответ x+\.\.\.$/));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Ответ astrachat')).toBeTruthy();
    expect(within(dialog).getByText('Ответ ' + 'x'.repeat(150))).toBeTruthy();
    fireEvent.click(within(dialog).getByText('Закрыть'));
    act(() => { jest.advanceTimersByTime(500); });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('в диалоге пользовательской записи показан заголовок пользователя, «Скачать» сохраняет файл', async () => {
    await renderPage();
    fireEvent.click(screen.getByText('Привет мир'));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Сообщение пользователя')).toBeTruthy();
    fireEvent.click(within(dialog).getByText('Скачать'));
    const blob: Blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0];
    expect(blob.type).toBe('text/plain');
    expect((clickSpy as any).last.download).toBe('message_2025-06-10T10:00:00.000Z.txt');
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it('очистка: подтверждение → DELETE, сброс чата и уведомление', async () => {
    await renderPage();
    fireEvent.click(screen.getByText('Очистить'));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Будет удалено 3 сообщений из истории диалогов.')).toBeTruthy();
    fetchMock.mockResolvedValueOnce(json({ success: true }));
    await act(async () => {
      fireEvent.click(within(dialog).getByText('Очистить историю'));
    });
    expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/history', {
      method: 'DELETE',
      headers: { Authorization: 'Bearer t' },
    });
    expect(mock.actions.clearMessages).toHaveBeenCalledWith('c1');
    expect(mock.actions.showNotification).toHaveBeenCalledWith('success', 'История очищена');
    expect(screen.getByText('История пуста')).toBeTruthy();
  });

  it('очистка без текущего чата не вызывает clearMessages', async () => {
    mock.actions.getCurrentChat.mockReturnValue(null);
    await renderPage();
    fireEvent.click(document.querySelector('button.MuiFab-root')!);
    fetchMock.mockResolvedValueOnce(json({ success: true }));
    await act(async () => {
      fireEvent.click(within(screen.getByRole('dialog')).getByText('Очистить историю'));
    });
    expect(mock.actions.clearMessages).not.toHaveBeenCalled();
    expect(mock.actions.showNotification).toHaveBeenCalledWith('success', 'История очищена');
  });

  it('отказ сервера при очистке показывает ошибку и оставляет историю', async () => {
    await renderPage();
    fireEvent.click(screen.getByText('Очистить'));
    fetchMock.mockResolvedValueOnce(json({ success: false }));
    await act(async () => {
      fireEvent.click(within(screen.getByRole('dialog')).getByText('Очистить историю'));
    });
    expect(mock.actions.showNotification).toHaveBeenCalledWith('error', 'Не удалось очистить историю');
    expect(screen.getByText('Привет мир')).toBeTruthy();
  });

  it('сбой сети при очистке показывает ошибку', async () => {
    await renderPage();
    fireEvent.click(screen.getByText('Очистить'));
    fetchMock.mockRejectedValueOnce(new Error('x'));
    await act(async () => {
      fireEvent.click(within(screen.getByRole('dialog')).getByText('Очистить историю'));
    });
    expect(mock.actions.showNotification).toHaveBeenCalledWith('error', 'Ошибка при очистке истории');
  });

  it('«Отмена» закрывает диалог очистки без запросов', async () => {
    await renderPage();
    fireEvent.click(screen.getByText('Очистить'));
    fireEvent.click(within(screen.getByRole('dialog')).getByText('Отмена'));
    act(() => { jest.advanceTimersByTime(500); });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('HTTP-ошибка сервера при загрузке показывается как ошибка и даёт откат на локальную историю (BUG-FE-67)', async () => {
    jest.spyOn(console, 'error').mockImplementation(() => undefined);
    mock.actions.getCurrentMessages.mockReturnValue([
      { id: 'l1', role: 'user', content: 'локальное сообщение', timestamp: '2025-06-10T09:00:00.000Z' },
    ]);
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({ detail: 'boom' }) });
    render(<HistoryPage />);
    await act(async () => {});
    expect(mock.actions.showNotification).toHaveBeenCalledWith('error', 'Не удалось загрузить историю');
    expect(mock.actions.showNotification).not.toHaveBeenCalledWith('success', expect.anything());
    expect(screen.getByText('локальное сообщение')).toBeTruthy();
    expect(screen.queryByText('История пуста')).toBeNull();
  });

  it('Esc закрывает оба диалога (onClose)', async () => {
    await renderPage();
    fireEvent.click(screen.getByText('Очистить'));
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    act(() => { jest.advanceTimersByTime(500); });
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByText('Привет мир'));
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    act(() => { jest.advanceTimersByTime(500); });
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
