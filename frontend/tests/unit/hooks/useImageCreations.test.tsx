import { act, renderHook, waitFor } from '@testing-library/react';

let mockToken: string | null = 'tok-1';
let mockChats: any[] = [];

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  getAuthFetchHeaders: () => ({ Authorization: 'Bearer test' }),
}));
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ token: mockToken }),
}));
jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: { chats: mockChats } }),
}));

import { useImageCreations } from '@/hooks/useImageCreations';

const fetchMock = jest.fn();

const okJson = (data: unknown) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });

const localChat = {
  id: 'chat-1',
  title: 'Чат',
  messages: [
    {
      id: 'm1',
      role: 'assistant',
      content: 'Сгенерировал изображение по запросу: «кот»',
      timestamp: '2024-01-02T00:00:00Z',
      inlineAttachments: [{ name: 'generated_1.png', contentType: 'image', preview: 'data:image/png;base64,AAA' }],
    },
  ],
};

beforeEach(() => {
  fetchMock.mockReset();
  (global as any).fetch = fetchMock;
  mockToken = 'tok-1';
  mockChats = [];
});

describe('useImageCreations', () => {
  it('загружает галерею с сервера с лимитом по умолчанию и заголовками авторизации', async () => {
    fetchMock.mockReturnValue(okJson({ items: [{ id: 'a', name: 'img.png' }] }));
    const { result } = renderHook(() => useImageCreations());
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/api/image-generation/creations?limit=500',
      { headers: { Authorization: 'Bearer test' } },
    );
    expect(result.current.items.map((i) => i.id)).toEqual(['a']);
  });

  it('использует переданный limit', async () => {
    fetchMock.mockReturnValue(okJson({ items: [] }));
    const { result } = renderHook(() => useImageCreations(10));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetchMock.mock.calls[0][0]).toContain('limit=10');
  });

  it('объединяет серверные элементы с локальными из чатов', async () => {
    mockChats = [localChat];
    fetchMock.mockReturnValue(okJson({ items: [{ id: 'srv', name: 's.png' }] }));
    const { result } = renderHook(() => useImageCreations());
    await waitFor(() => expect(result.current.loading).toBe(false));
    const ids = result.current.items.map((i) => i.id).sort();
    expect(ids).toEqual(['m1:0:0', 'srv']);
  });

  it('при HTTP-ошибке остаются только локальные элементы', async () => {
    mockChats = [localChat];
    fetchMock.mockResolvedValue({ ok: false, status: 500, json: () => Promise.resolve({}) });
    const { result } = renderHook(() => useImageCreations());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items.map((i) => i.id)).toEqual(['m1:0:0']);
  });

  it('при сетевой ошибке не падает и снимает loading', async () => {
    fetchMock.mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => useImageCreations());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toEqual([]);
  });

  it('ответ без массива items трактуется как пустой', async () => {
    fetchMock.mockReturnValue(okJson({ items: 'oops' }));
    const { result } = renderHook(() => useImageCreations());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toEqual([]);
  });

  it('перезагружается при смене токена', async () => {
    fetchMock.mockReturnValue(okJson({ items: [] }));
    const { result, rerender } = renderHook(() => useImageCreations());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    mockToken = 'tok-2';
    rerender();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it('событие astrachatCreationsUpdated вызывает перезагрузку', async () => {
    fetchMock.mockReturnValueOnce(okJson({ items: [] }));
    const { result } = renderHook(() => useImageCreations());
    await waitFor(() => expect(result.current.loading).toBe(false));
    fetchMock.mockReturnValueOnce(okJson({ items: [{ id: 'new' }] }));
    act(() => {
      window.dispatchEvent(new Event('astrachatCreationsUpdated'));
    });
    await waitFor(() => expect(result.current.items.map((i) => i.id)).toEqual(['new']));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('reload возвращает функцию ручной перезагрузки', async () => {
    fetchMock.mockReturnValue(okJson({ items: [] }));
    const { result } = renderHook(() => useImageCreations());
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await result.current.reload();
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('после размонтирования событие обновления больше не вызывает fetch', async () => {
    fetchMock.mockReturnValue(okJson({ items: [] }));
    const { result, unmount } = renderHook(() => useImageCreations());
    await waitFor(() => expect(result.current.loading).toBe(false));
    unmount();
    window.dispatchEvent(new Event('astrachatCreationsUpdated'));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
