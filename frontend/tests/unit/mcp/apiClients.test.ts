/**
 * @jest-environment jsdom
 */
import { downloadMcpFile } from '@/mcp/api';

jest.mock('@/config/api', () => ({
  getApiUrl: (path: string) => `https://app.example.com${path.startsWith('/') ? path : `/${path}`}`,
}));

describe('downloadMcpFile', () => {
  const originalFetch = global.fetch;
  const originalCreateObjectURL = URL.createObjectURL;
  const originalRevokeObjectURL = URL.revokeObjectURL;

  beforeEach(() => {
    localStorage.setItem('auth_token', 'jwt-app-token');
    URL.createObjectURL = jest.fn(() => 'blob:mock');
    URL.revokeObjectURL = jest.fn();
    document.body.innerHTML = '';
  });

  afterEach(() => {
    global.fetch = originalFetch;
    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
    localStorage.clear();
    jest.restoreAllMocks();
  });

  it('does not send the application token to an absolute third-party url', async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob([new Uint8Array([1, 2, 3, 4])]),
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    await downloadMcpFile('https://evil.mcp.example/file.docx', 'file.docx');

    expect(fetchMock).toHaveBeenCalledWith(
      'https://evil.mcp.example/file.docx',
      expect.objectContaining({ headers: {} }),
    );
  });

  it('sends the application token to same-origin absolute api urls', async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob([new Uint8Array([1, 2, 3, 4])]),
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    await downloadMcpFile('https://app.example.com/api/mcp/download/x', 'x.bin');

    expect(fetchMock).toHaveBeenCalledWith(
      'https://app.example.com/api/mcp/download/x',
      expect.objectContaining({
        headers: { Authorization: 'Bearer jwt-app-token' },
      }),
    );
  });

  it('sends the application token for relative api paths', async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob([new Uint8Array([1, 2, 3, 4])]),
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    await downloadMcpFile('/api/mcp/files/1', '1.bin');

    expect(fetchMock).toHaveBeenCalledWith(
      'https://app.example.com/api/mcp/files/1',
      expect.objectContaining({
        headers: { Authorization: 'Bearer jwt-app-token' },
      }),
    );
  });
});
