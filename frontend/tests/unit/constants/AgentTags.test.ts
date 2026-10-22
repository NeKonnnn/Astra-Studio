/**
 * @jest-environment jsdom
 */
jest.mock('@/config/api', () => ({
  getApiUrl: (path: string) => `http://api.test${path}`,
  getAuthFetchHeaders: () => ({}),
}));

import { fetchAgentTags, invalidateAgentTagsCache, parseTagIds } from '@/constants/AgentTags';

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

describe('strict tag id parsing (BUG-FE-07)', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    invalidateAgentTagsCache();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('skips tag rows with null / NaN / fractional / non-numeric ids instead of coercing to 0', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      okResponse([
        { id: null, name: 'null-id' },
        { id: undefined, name: 'no-id' },
        { id: 'abc', name: 'text-id' },
        { id: 1.5, name: 'fraction' },
        { id: 0, name: 'zero' },
        { id: true, name: 'bool' },
        { id: 7, name: 'ok' },
        { id: '8', name: 'numeric-string' },
      ]),
    ) as unknown as typeof fetch;

    const tags = await fetchAgentTags();

    expect(tags.map((t) => t.id)).toEqual([7, 8]);
  });

  it('parseTagIds rejects fractions, zero, null and NaN (no truncation to 0)', () => {
    expect(parseTagIds([0.5])).toEqual([]);
    expect(parseTagIds([1.5, 2])).toEqual([2]);
    expect(parseTagIds([null, undefined, NaN, 0, -1, 'x', true])).toEqual([]);
    expect(parseTagIds([3, '4', 3])).toEqual([3, 4]);
    expect(parseTagIds('not-an-array')).toEqual([]);
  });
});
