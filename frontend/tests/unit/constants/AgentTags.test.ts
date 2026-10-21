/**
 * @jest-environment jsdom
 */
jest.mock('@/config/api', () => ({
  getApiUrl: (path: string) => `http://api.test${path}`,
  getAuthFetchHeaders: () => ({}),
}));

import { fetchAgentTags, invalidateAgentTagsCache } from '@/constants/AgentTags';

const okResponse = (tags: unknown[]) =>
  ({ ok: true, status: 200, json: async () => ({ tags }) }) as unknown as Response;
const errorResponse = (status: number) =>
  ({ ok: false, status, json: async () => ({}) }) as unknown as Response;

describe('fetchAgentTags error caching', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    invalidateAgentTagsCache();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('does not cache a 5xx response: the next call hits the API again', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(errorResponse(503))
      .mockResolvedValueOnce(okResponse([{ id: 1, name: 'data', color: '#fff' }]));
    global.fetch = fetchMock as unknown as typeof fetch;

    expect(await fetchAgentTags()).toEqual([]);
    const second = await fetchAgentTags();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(second).toEqual([{ id: 1, name: 'data', color: '#fff' }]);
  });

  it('keeps serving the previous good list when a forced refresh fails with 5xx', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(okResponse([{ id: 2, name: 'ops' }]))
      .mockResolvedValueOnce(errorResponse(500));
    global.fetch = fetchMock as unknown as typeof fetch;

    const first = await fetchAgentTags();
    const afterFailure = await fetchAgentTags(true);

    expect(afterFailure).toEqual(first);
    expect(afterFailure).toEqual([{ id: 2, name: 'ops', color: null }]);
  });

  it('still caches a successful response', async () => {
    const fetchMock = jest.fn().mockResolvedValue(okResponse([{ id: 3, name: 'x' }]));
    global.fetch = fetchMock as unknown as typeof fetch;

    await fetchAgentTags();
    await fetchAgentTags();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
