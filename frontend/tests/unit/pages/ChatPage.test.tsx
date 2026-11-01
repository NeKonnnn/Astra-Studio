import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import ChatPage from '@/pages/ChatPage';

// ChatPage нигде не импортируется (мёртвая страница, заменена UnifiedChatPage), но покрываем её.
const mock: any = {};
function reset() {
  mock.state = { isLoading: false };
  mock.chat = { id: 'c1' };
  mock.messages = [];
  mock.actions = {
    clearMessages: jest.fn(),
    showNotification: jest.fn(),
    getCurrentMessages: jest.fn(() => mock.messages),
    getCurrentChat: jest.fn(() => mock.chat),
  };
  mock.socket = {
    sendMessage: jest.fn(),
    isConnected: true,
    isConnecting: false,
    reconnect: jest.fn(),
    stopGeneration: jest.fn(),
  };
}
reset();

jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: mock.state }),
  useAppActions: () => mock.actions,
}));
jest.mock('@/contexts/SocketContext', () => ({ useSocket: () => mock.socket }));
jest.mock('@/components/MessageRenderer', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="renderer" data-streaming={String(p.isStreaming)}>
      {p.content}
      <button onClick={() => p.onSendMessage('follow-up')}>follow</button>
    </div>
  ),
}));

const userMsg = { id: 'u1', role: 'user', content: 'Привет', timestamp: '2025-01-01T10:05:00Z' };
const botMsg = { id: 'a1', role: 'assistant', content: 'Ответ', timestamp: '2025-01-01T10:06:00Z', isStreaming: true };

const input = () => screen.getByPlaceholderText('Введите сообщение...') as HTMLTextAreaElement;

describe('ChatPage', () => {
  beforeEach(() => {
    reset();
    localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = jest.fn();
  });

  it('пустой чат показывает приветствие и не показывает кнопку очистки', () => {
    render(<ChatPage />);
    expect(screen.getByText(/Добро пожаловать в AstraChat/)).toBeTruthy();
    expect(screen.queryByLabelText('Очистить историю')).toBeNull();
    expect(screen.getByText('Подключено')).toBeTruthy();
  });

  it('автофокус на поле ввода не срабатывает: ref повешен на корневой div TextField (known issue)', () => {
    // Как правильно: после монтирования и после отправки фокус должен оказываться в textarea
    // (inputRef надо передавать через inputRef=, а не ref= — ref указывает на корневой div).
    render(<ChatPage />);
    expect(document.activeElement).not.toBe(input());
    expect(document.activeElement).toBe(document.body);
  });

  it('отправка по Enter шлёт trim-текст в текущий чат, очищает поле', () => {
    jest.useFakeTimers();
    render(<ChatPage />);
    fireEvent.change(input(), { target: { value: '  привет  ' } });
    expect(screen.getByText('10 символов')).toBeTruthy();
    fireEvent.keyPress(input(), { key: 'Enter', code: 'Enter', charCode: 13 });
    expect(mock.socket.sendMessage).toHaveBeenCalledWith('привет', 'c1');
    expect(input().value).toBe('');
    // отложенный возврат фокуса (10 мс) не должен падать
    expect(() => act(() => { jest.advanceTimersByTime(20); })).not.toThrow();
    jest.useRealTimers();
  });

  it('Shift+Enter и другие клавиши не отправляют', () => {
    render(<ChatPage />);
    fireEvent.change(input(), { target: { value: 'текст' } });
    fireEvent.keyPress(input(), { key: 'Enter', code: 'Enter', charCode: 13, shiftKey: true });
    fireEvent.keyPress(input(), { key: 'a', code: 'KeyA', charCode: 97 });
    expect(mock.socket.sendMessage).not.toHaveBeenCalled();
  });

  it('кнопка отправки работает; пустой ввод блокирует её', () => {
    render(<ChatPage />);
    const btn = screen.getByTestId('SendIcon').closest('button') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.change(input(), { target: { value: 'x' } });
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(mock.socket.sendMessage).toHaveBeenCalledWith('x', 'c1');
  });

  it('пробельный ввод не отправляется даже по Enter', () => {
    render(<ChatPage />);
    fireEvent.change(input(), { target: { value: '   ' } });
    fireEvent.keyPress(input(), { key: 'Enter', code: 'Enter', charCode: 13 });
    expect(mock.socket.sendMessage).not.toHaveBeenCalled();
  });

  it('без текущего чата отправка не происходит', () => {
    mock.chat = null;
    render(<ChatPage />);
    fireEvent.change(input(), { target: { value: 'x' } });
    fireEvent.keyPress(input(), { key: 'Enter', code: 'Enter', charCode: 13 });
    expect(mock.socket.sendMessage).not.toHaveBeenCalled();
  });

  it('при загрузке ответа поле заблокировано, виден индикатор и кнопка остановки (заглушка)', () => {
    mock.state = { isLoading: true };
    render(<ChatPage />);
    expect(input().disabled).toBe(true);
    expect(screen.getByRole('progressbar')).toBeTruthy();
    fireEvent.click(screen.getByTestId('StopIcon').closest('button')!);
    expect(mock.actions.showNotification).toHaveBeenCalledWith('info', 'Функция остановки в разработке');
    expect(mock.socket.stopGeneration).not.toHaveBeenCalled();
  });

  it('при разрыве соединения: статус «Отключено», кнопка переподключения вызывает reconnect', () => {
    mock.socket.isConnected = false;
    render(<ChatPage />);
    expect(screen.getByText('Отключено')).toBeTruthy();
    expect(screen.getByText('Переподключение...')).toBeTruthy();
    expect(input().disabled).toBe(true);
    fireEvent.click(screen.getByTestId('RefreshIcon').closest('button')!);
    expect(mock.socket.reconnect).toHaveBeenCalledTimes(1);
    expect(mock.actions.showNotification).toHaveBeenCalledWith('info', 'Переподключение...');
  });

  it('при подключении (isConnecting) ввод доступен, а кнопки переподключения нет', () => {
    mock.socket.isConnected = false;
    mock.socket.isConnecting = true;
    render(<ChatPage />);
    expect(input().disabled).toBe(false);
    expect(screen.queryByTestId('RefreshIcon')).toBeNull();
    fireEvent.change(input(), { target: { value: 'q' } });
    fireEvent.keyPress(input(), { key: 'Enter', code: 'Enter', charCode: 13 });
    expect(mock.socket.sendMessage).toHaveBeenCalledWith('q', 'c1');
  });

  it('рендерит сообщения пользователя и ассистента, индикатор стриминга и время', () => {
    mock.messages = [userMsg, botMsg];
    render(<ChatPage />);
    expect(screen.getByText('Привет')).toBeTruthy();
    expect(screen.getByTestId('renderer').textContent).toContain('Ответ');
    expect(screen.getByTestId('renderer').getAttribute('data-streaming')).toBe('true');
    expect(screen.getByText('Вы')).toBeTruthy();
    expect(screen.getByText('Генерирую...')).toBeTruthy();
    expect(screen.queryByText(/Добро пожаловать/)).toBeNull();
    expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it('размер шрифта пользовательского сообщения берётся из chat-font-size', () => {
    mock.messages = [userMsg];
    localStorage.setItem('chat-font-size', 'small');
    const a = render(<ChatPage />);
    expect(getComputedStyle(screen.getByText('Привет')).fontSize).toBe('0.875rem');
    a.unmount();
    localStorage.setItem('chat-font-size', 'large');
    const b = render(<ChatPage />);
    expect(getComputedStyle(screen.getByText('Привет')).fontSize).toBe('1.125rem');
    b.unmount();
    localStorage.removeItem('chat-font-size');
    render(<ChatPage />);
    expect(getComputedStyle(screen.getByText('Привет')).fontSize).toBe('1rem');
  });

  it('метка ассистента — устаревшее название «ГазикИИ» (known issue)', () => {
    // Как правильно: продукт называется AstraChat (ср. приветствие на этой же странице).
    mock.messages = [{ ...botMsg, isStreaming: false }];
    render(<ChatPage />);
    expect(screen.getByText('ГазикИИ')).toBeTruthy();
  });

  it('follow-up из рендерера отправляется в чат, если есть соединение', () => {
    mock.messages = [{ ...botMsg, isStreaming: false }];
    render(<ChatPage />);
    fireEvent.click(screen.getByText('follow'));
    expect(mock.socket.sendMessage).toHaveBeenCalledWith('follow-up', 'c1');
  });

  it('follow-up игнорируется при загрузке', () => {
    mock.messages = [{ ...botMsg, isStreaming: false }];
    mock.state = { isLoading: true };
    render(<ChatPage />);
    fireEvent.click(screen.getByText('follow'));
    expect(mock.socket.sendMessage).not.toHaveBeenCalled();
  });

  it('копирование сообщения: успех и ошибка показывают уведомления', async () => {
    mock.messages = [userMsg];
    const writeText = jest.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    render(<ChatPage />);
    const copy = screen.getByTestId('ContentCopyIcon').closest('button')!;
    await act(async () => { fireEvent.click(copy); });
    expect(writeText).toHaveBeenCalledWith('Привет');
    expect(mock.actions.showNotification).toHaveBeenCalledWith('success', 'Копирование в буфер обмена прошло успешно!');
    await act(async () => { fireEvent.click(copy); });
    expect(mock.actions.showNotification).toHaveBeenLastCalledWith('error', 'Не удалось скопировать текст');
  });

  it('кнопка очистки истории очищает текущий чат', () => {
    mock.messages = [userMsg];
    render(<ChatPage />);
    fireEvent.click(screen.getByTestId('ClearIcon').closest('button')!);
    expect(mock.actions.clearMessages).toHaveBeenCalledWith('c1');
  });

  it('очистка при отсутствии чата ничего не вызывает', () => {
    mock.messages = [userMsg];
    mock.chat = null;
    render(<ChatPage />);
    fireEvent.click(screen.getByTestId('ClearIcon').closest('button')!);
    expect(mock.actions.clearMessages).not.toHaveBeenCalled();
  });
});
