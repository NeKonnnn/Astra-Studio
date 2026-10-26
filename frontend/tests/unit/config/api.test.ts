/**
 * @jest-environment jsdom
 */
import { API_ENDPOINTS, authFetch, getAuthFetchHeaders, getAuthWsUrl } from '@/config/api';

jest.mock('axios', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn() },
}));
jest.mock('@/settings', () => ({
  getSettings: () => ({
    api: { baseUrl: 'https://app.example.com', getApiUrl: (p: string) => `https://app.example.com${p}` },
    websocket: {
      baseUrl: 'wss://app.example.com',
      getWsUrl: (p: string) => `wss://app.example.com${p}`,
    },
  }),
}));

const NASTY_ID = 'a/b?c=d#e f';
const ENCODED_ID = encodeURIComponent(NASTY_ID);

describe('API_ENDPOINTS project builders encode path segments', () => {
  it('PROJECT_DELETE', () => {
    expect(API_ENDPOINTS.PROJECT_DELETE(NASTY_ID)).toBe(`/api/projects/${ENCODED_ID}`);
    expect(API_ENDPOINTS.PROJECT_DELETE('../admin')).toBe('/api/projects/..%2Fadmin');
  });

  it('PROJECT_RAG_UPLOAD / LIST / SEARCH', () => {
    expect(API_ENDPOINTS.PROJECT_RAG_UPLOAD(NASTY_ID)).toBe(
      `/api/project-rag/projects/${ENCODED_ID}/documents`,
    );
    expect(API_ENDPOINTS.PROJECT_RAG_LIST(NASTY_ID)).toBe(
      `/api/project-rag/projects/${ENCODED_ID}/documents`,
    );
    expect(API_ENDPOINTS.PROJECT_RAG_SEARCH(NASTY_ID)).toBe(
      `/api/project-rag/projects/${ENCODED_ID}/search`,
    );
  });

  it('PROJECT_RAG_DELETE_DOC', () => {
    expect(API_ENDPOINTS.PROJECT_RAG_DELETE_DOC(NASTY_ID, 42)).toBe(
      `/api/project-rag/projects/${ENCODED_ID}/documents/42`,
    );
  });

  it('keeps plain ids unchanged', () => {
    expect(API_ENDPOINTS.PROJECT_DELETE('proj-1')).toBe('/api/projects/proj-1');
    expect(API_ENDPOINTS.PROJECT_RAG_LIST('proj-1')).toBe('/api/project-rag/projects/proj-1/documents');
  });
});

describe('getAuthFetchHeaders', () => {
  afterEach(() => localStorage.clear());

  it('uses auth_token', () => {
    localStorage.setItem('auth_token', 'jwt-1');
    expect(getAuthFetchHeaders()).toEqual({ Authorization: 'Bearer jwt-1' });
  });

  it('does NOT fall back to the legacy `token` key', () => {
    localStorage.setItem('token', 'legacy-jwt');
    expect(getAuthFetchHeaders()).toEqual({});
    expect(getAuthFetchHeaders({ 'Content-Type': 'application/json' })).toEqual({
      'Content-Type': 'application/json',
    });
  });

  it('prefers auth_token when both keys are present', () => {
    localStorage.setItem('auth_token', 'jwt-1');
    localStorage.setItem('token', 'legacy-jwt');
    expect(getAuthFetchHeaders().Authorization).toBe('Bearer jwt-1');
  });

  it('merges extra headers and lets Authorization win', () => {
    localStorage.setItem('auth_token', 'jwt-1');
    expect(
      getAuthFetchHeaders({ 'X-Test': '1', Authorization: 'Bearer spoofed' }),
    ).toEqual({ 'X-Test': '1', Authorization: 'Bearer jwt-1' });
  });
});

describe('authFetch', () => {
  afterEach(() => {
    localStorage.clear();
    jest.restoreAllMocks();
  });

  it('sends Authorization and credentials include for browser session cookie', async () => {
    localStorage.setItem('auth_token', 'jwt-attach');
    const fetchMock = jest.fn().mockResolvedValue(new Response('{}', { status: 200 }));
    global.fetch = fetchMock as typeof fetch;

    await authFetch('https://app.example.com/api/documents/attach', {
      method: 'POST',
      body: new FormData(),
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    expect(init.credentials).toBe('include');
    expect(init.method).toBe('POST');
    const headers = init.headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer jwt-attach');
  });
});

describe('getAuthWsUrl', () => {
  afterEach(() => localStorage.clear());

  it('appends the url-encoded auth_token as a query param', () => {
    localStorage.setItem('auth_token', 'a b+c');
    expect(getAuthWsUrl('/ws/voice')).toBe('wss://app.example.com/ws/voice?token=a%20b%2Bc');
  });

  it('uses & when the endpoint already has a query string', () => {
    localStorage.setItem('auth_token', 'jwt');
    expect(getAuthWsUrl('/ws/voice?x=1')).toBe('wss://app.example.com/ws/voice?x=1&token=jwt');
  });

  it('returns the bare url without a token and ignores the legacy `token` key', () => {
    expect(getAuthWsUrl('/ws/voice')).toBe('wss://app.example.com/ws/voice');
    localStorage.setItem('token', 'legacy-jwt');
    expect(getAuthWsUrl('/ws/voice')).toBe('wss://app.example.com/ws/voice');
  });
});
