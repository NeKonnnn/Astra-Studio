/**
 * @jest-environment jsdom
 */
import {
  formatShareFeedback,
  resolveShareUsernames,
  searchDirectoryUsers,
  type DirectoryUserHit,
} from '@/utils/directoryUserSearch';

jest.mock('@/config/api', () => ({
  getApiUrl: (p: string) => `https://app.example.com${p}`,
}));

type HitsByQuery = Record<string, DirectoryUserHit[]>;

function mockSearch(hitsByQuery: HitsByQuery) {
  const fetchMock = jest.fn(async (url: string) => {
    const q = new URL(url).searchParams.get('q') || '';
    return {
      ok: true,
      json: async () => ({ users: hitsByQuery[q] ?? [] }),
    } as Response;
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

const user = (username: string, extra: Partial<DirectoryUserHit> = {}): DirectoryUserHit => ({
  user_id: username,
  username,
  ...extra,
});

describe('resolveShareUsernames', () => {
  const originalFetch = global.fetch;
  afterEach(() => {
    global.fetch = originalFetch;
    jest.restoreAllMocks();
  });

  it('returns empty result for blank input without searching', async () => {
    const fetchMock = mockSearch({});
    await expect(resolveShareUsernames('t', ' , ;\n ')).resolves.toEqual({
      usernames: [],
      unresolved: [],
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('accepts an explicit @login without a directory lookup', async () => {
    const fetchMock = mockSearch({});
    const res = await resolveShareUsernames('t', '@gpbu001, @ivan.petrov');
    expect(res).toEqual({ usernames: ['gpbu001', 'ivan.petrov'], unresolved: [] });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('does NOT treat a bare Latin word as a login — it must match exactly via search', async () => {
    const fetchMock = mockSearch({ gpbu002: [user('gpbu002')] });
    const res = await resolveShareUsernames('t', 'gpbu002');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(res).toEqual({ usernames: ['gpbu002'], unresolved: [] });
  });

  it('marks a bare Latin word as unresolved when nothing matches exactly', async () => {
    mockSearch({ hello: [] });
    const res = await resolveShareUsernames('t', 'hello');
    expect(res).toEqual({ usernames: [], unresolved: ['hello'] });
  });

  it('does NOT auto-accept a single partial search hit', async () => {
    mockSearch({ Ivan: [user('gpbu003', { full_name: 'Иван Иванов' })] });
    const res = await resolveShareUsernames('t', 'Ivan');
    expect(res).toEqual({ usernames: [], unresolved: ['Ivan'] });
  });

  it('does NOT auto-accept a single partial hit for a Cyrillic fragment', async () => {
    mockSearch({ Иванов: [user('gpbu004', { full_name: 'Иванов Пётр Сергеевич' })] });
    const res = await resolveShareUsernames('t', 'Иванов');
    expect(res).toEqual({ usernames: [], unresolved: ['Иванов'] });
  });

  it('accepts exact full-name match (case-insensitive)', async () => {
    mockSearch({
      'иванов пётр': [user('gpbu005', { full_name: 'Иванов Пётр' }), user('gpbu006', { full_name: 'Иванов Пётр Сергеевич' })],
    });
    const res = await resolveShareUsernames('t', 'иванов пётр');
    expect(res).toEqual({ usernames: ['gpbu005'], unresolved: [] });
  });

  it('refuses an ambiguous exact full name shared by several users', async () => {
    mockSearch({
      'Сидоров Алексей': [
        user('gpbu007', { full_name: 'Сидоров Алексей' }),
        user('gpbu008', { full_name: 'Сидоров Алексей' }),
      ],
    });
    const res = await resolveShareUsernames('t', 'Сидоров Алексей');
    expect(res).toEqual({ usernames: [], unresolved: ['Сидоров Алексей'] });
  });

  it('accepts exact email match', async () => {
    mockSearch({
      'a.b@corp.example': [user('gpbu009', { email: 'A.B@corp.example' })],
    });
    const res = await resolveShareUsernames('t', 'a.b@corp.example');
    expect(res).toEqual({ usernames: ['gpbu009'], unresolved: [] });
  });

  it('does not treat an email as an @login', async () => {
    const fetchMock = mockSearch({ 'x.y@corp.example': [] });
    const res = await resolveShareUsernames('t', 'x.y@corp.example');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(res.unresolved).toEqual(['x.y@corp.example']);
  });

  it('keeps input order, de-duplicates and reports unresolved tokens', async () => {
    mockSearch({
      gpbu010: [user('gpbu010')],
      'Нет Такого': [],
    });
    const res = await resolveShareUsernames('t', 'gpbu010; @GPBU010, Нет Такого\n@gpbu011');
    expect(res.usernames).toEqual(['gpbu010', 'gpbu011']);
    expect(res.unresolved).toEqual(['Нет Такого']);
  });

  it('treats failed directory lookups as unresolved instead of rejecting', async () => {
    jest.spyOn(console, 'warn').mockImplementation(() => undefined);
    global.fetch = jest.fn(async () => {
      throw new Error('network down');
    }) as unknown as typeof fetch;
    const res = await resolveShareUsernames('t', 'neverseen');
    expect(res).toEqual({ usernames: [], unresolved: ['neverseen'] });
  });
});

describe('searchDirectoryUsers', () => {
  const originalFetch = global.fetch;
  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('skips queries shorter than 2 chars', async () => {
    const fetchMock = mockSearch({});
    await expect(searchDirectoryUsers('t', ' a ')).resolves.toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('encodes the query and requests for_share when asked', async () => {
    const fetchMock = mockSearch({ 'a&b=c': [] });
    await searchDirectoryUsers('t', 'a&b=c', 5, undefined, true);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('q=a%26b%3Dc');
    expect(url).toContain('limit=5');
    expect(url).toContain('for_share=true');
  });
});

describe('formatShareFeedback', () => {
  it('defaults to a success message', () => {
    expect(formatShareFeedback({})).toEqual({ success: 'Доступ выдан', warning: null });
  });

  it('splits success message and warnings', () => {
    expect(
      formatShareFeedback({ success: true, message: 'ok', warnings: ['w1'] }),
    ).toEqual({ success: 'ok', warning: 'w1' });
  });
});
