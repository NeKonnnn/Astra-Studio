import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import ShareViewPage from '@/pages/ShareViewPage';

let mockParams: { shareId?: string } = { shareId: 'sh1' };
jest.mock('react-router-dom', () => ({ useParams: () => mockParams }));
jest.mock('@/components/MessageRenderer', () => ({
  __esModule: true,
  default: (p: { content: string; isStreaming: boolean; forceArtifacts?: boolean }) => (
    <div data-testid="msg" data-streaming={String(p.isStreaming)} data-force={String(p.forceArtifacts)}>
      {p.content}
    </div>
  ),
}));

const fetchMock = jest.fn();

function okJson(data: unknown) {
  return { ok: true, status: 200, json: async () => data };
}

describe('ShareViewPage', () => {
  beforeEach(() => {
    mockParams = { shareId: 'sh1' };
    fetchMock.mockReset();
    (global as any).fetch = fetchMock;
  });
  afterEach(() => jest.restoreAllMocks());

  it('показывает спиннер, пока идёт загрузка', () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    render(<ShareViewPage />);
    expect(screen.getByRole('progressbar')).toBeTruthy();
  });

  it('без shareId сразу показывает ошибку и не ходит в сеть', async () => {
    mockParams = {};
    render(<ShareViewPage />);
    expect(await screen.findByText('ID публичной ссылки не указан')).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('404 даёт сообщение «ссылка не найдена»', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 404 });
    render(<ShareViewPage />);
    expect(await screen.findByText('Публичная ссылка не найдена')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith('/api/share/sh1', {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('прочие HTTP-ошибки дают общее сообщение', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500 });
    render(<ShareViewPage />);
    expect(await screen.findByText('Ошибка при загрузке')).toBeTruthy();
  });

  it('не-Error исключение даёт текст по умолчанию', async () => {
    fetchMock.mockRejectedValue('x');
    render(<ShareViewPage />);
    expect(await screen.findByText('Произошла ошибка')).toBeTruthy();
  });

  it('сетевая Error показывает её текст', async () => {
    fetchMock.mockRejectedValue(new Error('offline'));
    render(<ShareViewPage />);
    expect(await screen.findByText('offline')).toBeTruthy();
  });

  it('рендерит сообщения: роли, модель, время и режим forceArtifacts', async () => {
    fetchMock.mockResolvedValue(
      okJson({
        share_id: 'sh1',
        created_at: '2025-03-04T10:20:00Z',
        messages: [
          { id: 'm1', role: 'user', content: 'Привет', timestamp: '2025-03-04T10:21:00Z' },
          { id: '', role: 'assistant', content: 'Здравствуйте', model: 'gpt-x' },
        ],
      }),
    );
    render(<ShareViewPage />);
    expect(await screen.findByText('Публичная беседа')).toBeTruthy();
    const msgs = screen.getAllByTestId('msg');
    expect(msgs.map((m) => m.textContent)).toEqual(['Привет', 'Здравствуйте']);
    expect(msgs[0].getAttribute('data-force')).toBe('true');
    expect(msgs[0].getAttribute('data-streaming')).toBe('false');
    expect(screen.getByText('Пользователь')).toBeTruthy();
    expect(screen.getByText('AstraChat')).toBeTruthy();
    expect(screen.getByText('Модель: gpt-x')).toBeTruthy();
    expect(screen.getByText(/Создано: 0?4\.03\.2025/)).toBeTruthy();
    expect(screen.getByText('Создано с помощью AstraChat')).toBeTruthy();
  });

  it('сообщение без timestamp и модели не рисует лишних подписей', async () => {
    fetchMock.mockResolvedValue(
      okJson({ share_id: 's', created_at: '', messages: [{ id: 'a', role: 'user', content: 'x' }] }),
    );
    render(<ShareViewPage />);
    await screen.findByText('Публичная беседа');
    expect(screen.queryByText(/Модель:/)).toBeNull();
    expect(screen.getByText(/Создано:/).textContent).toBe('Создано: ');
  });

  it('если форматирование даты бросает исключение, подпись времени пустая', async () => {
    fetchMock.mockResolvedValue(
      okJson({ share_id: 's', created_at: '2025-01-01T00:00:00Z', messages: [] }),
    );
    jest.spyOn(Date.prototype, 'toLocaleString').mockImplementation(() => {
      throw new RangeError('bad');
    });
    render(<ShareViewPage />);
    await waitFor(() => expect(screen.getByText('Публичная беседа')).toBeTruthy());
    expect(screen.getByText(/Создано:/).textContent).toBe('Создано: ');
  });

  it('невалидная дата выводится как «Invalid Date» (known issue)', async () => {
    // Как правильно: невалидный created_at не должен показываться пользователю как
    // «Invalid Date» — функция должна вернуть пустую строку (catch в formatTimestamp недостижим).
    fetchMock.mockResolvedValue(okJson({ share_id: 's', created_at: 'не дата', messages: [] }));
    render(<ShareViewPage />);
    expect(await screen.findByText('Создано: Invalid Date')).toBeTruthy();
  });

  it('null-ответ сервера (conversation=null) рендерит пустую страницу', async () => {
    fetchMock.mockResolvedValue(okJson(null));
    const { container } = render(<ShareViewPage />);
    await waitFor(() => expect(screen.queryByRole('progressbar')).toBeNull());
    expect(container.firstChild).toBeNull();
  });
});
