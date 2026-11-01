import { authFetch, setAuthTokenRefreshHandler } from '@/utils/authFetch';

describe('authFetch', () => {
  const fetchMock = jest.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    (global as any).fetch = fetchMock;
    localStorage.clear();
    setAuthTokenRefreshHandler(null);
  });

  afterEach(() => {
    setAuthTokenRefreshHandler(null);
  });

  it('adds Authorization from auth_token', async () => {
    localStorage.setItem('auth_token', 'access-1');
    fetchMock.mockResolvedValue({ status: 200, ok: true });

    await authFetch('http://api.test/api/projects');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer access-1');
  });

  it('on 401 refreshes once and retries with new token', async () => {
    localStorage.setItem('auth_token', 'old');
    const refresh = jest.fn(async () => {
      localStorage.setItem('auth_token', 'new');
      return 'new';
    });
    setAuthTokenRefreshHandler(refresh);
    fetchMock
      .mockResolvedValueOnce({ status: 401, ok: false })
      .mockResolvedValueOnce({ status: 200, ok: true });

    const res = await authFetch('http://api.test/api/projects');

    expect(refresh).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(res.status).toBe(200);
    const retryHeaders = fetchMock.mock.calls[1][1].headers as Headers;
    expect(retryHeaders.get('Authorization')).toBe('Bearer new');
  });

  it('does not retry when refresh handler is missing', async () => {
    localStorage.setItem('auth_token', 'old');
    fetchMock.mockResolvedValue({ status: 401, ok: false });

    const res = await authFetch('http://api.test/api/projects');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(res.status).toBe(401);
  });
});
