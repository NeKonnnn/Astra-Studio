/**
 * @jest-environment jsdom
 */

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  API_ENDPOINTS: { KNOWLEDGE_BASES: '/api/knowledge-bases' },
  getApiUrl: (endpoint: string) => endpoint,
  getAuthFetchHeaders: () => ({}),
}));

import {
  getCachedPersonalLibraryKbId,
  setCachedPersonalLibraryKbId,
} from '@/utils/personalLibraryKb';

describe('personalLibraryKb cache (local)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('stores and clears personal library kb id', () => {
    expect(getCachedPersonalLibraryKbId()).toBeNull();
    setCachedPersonalLibraryKbId(42);
    expect(getCachedPersonalLibraryKbId()).toBe(42);
    setCachedPersonalLibraryKbId(null);
    expect(getCachedPersonalLibraryKbId()).toBeNull();
  });

  it('ignores invalid ids', () => {
    setCachedPersonalLibraryKbId(0);
    expect(getCachedPersonalLibraryKbId()).toBeNull();
    setCachedPersonalLibraryKbId(-1);
    expect(getCachedPersonalLibraryKbId()).toBeNull();
  });
});
