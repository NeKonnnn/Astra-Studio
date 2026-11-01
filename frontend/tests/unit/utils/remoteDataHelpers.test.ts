jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (path: string) => `http://api.test${path}`,
  getAuthFetchHeaders: (extra: Record<string, string> = {}) => ({ Authorization: 'Bearer t', ...extra }),
}));

import { fetchMergedUserAgents } from '@/utils/fetchMergedUserAgents';
import {
  fetchProjectsFromServer,
  migrateLocalProjectsToServer,
  syncProjectCreate,
  syncProjectUpdate,
} from '@/utils/projectsApi';

type RagMod = typeof import('../constants/ragEntityDefaults');
function loadRag(): RagMod {
  let mod!: RagMod;
  jest.isolateModules(() => {
    mod = require('../constants/ragEntityDefaults');
  });
  return mod;
}

const json = (body: unknown, ok = true, status = ok ? 200 : 500) =>
  ({ ok, status, json: async () => body, text: async () => (typeof body === 'string' ? body : JSON.stringify(body)) }) as unknown as Response;

let fetchMock: jest.Mock;
beforeEach(() => {
  fetchMock = jest.fn();
  (global as any).fetch = fetchMock;
});
afterEach(() => jest.restoreAllMocks());

const A = (id: number, extra: Record<string, unknown> = {}) => ({ id, name: `agent-${id}`, system_prompt: 'p', ...extra });

describe('fetchMergedUserAgents', () => {
  function routes(map: Record<string, unknown>) {
    fetchMock.mockImplementation(async (url: string) => {
      for (const [part, body] of Object.entries(map)) {
        if (url.includes(part)) return body === 'fail' ? json({}, false) : json(body);
      }
      return json({}, false);
    });
  }

  it('returns nothing without a token', async () => {
    expect(await fetchMergedUserAgents(null)).toEqual([]);
    expect(await fetchMergedUserAgents(undefined)).toEqual([]);
    expect(await fetchMergedUserAgents('')).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('merges own, shared and bookmarked agents with flags and default permissions', async () => {
    routes({
      '/my/agents': { agents: [A(1)] },
      '/my/shared': { agents: [A(2, { my_permission: 'editor' })] },
      '/my/bookmarks': { agents: [A(3)] },
    });

    const out = await fetchMergedUserAgents('tok', 50);

    expect(out.map((a) => a.id)).toEqual([1, 2, 3]);
    expect(out[0].my_permission).toBe('owner');
    expect(out[1]).toMatchObject({ my_permission: 'editor', is_shared_with_me: true });
    expect(out[2]).toMatchObject({ my_permission: 'viewer', is_bookmarked: true });
    for (const [url, init] of fetchMock.mock.calls) {
      expect(url).toContain('limit=50');
      // заголовок Authorization подставляет authFetch (getAuthFetchHeaders), а не аргумент token
      expect(init.headers.Authorization).toBe('Bearer t');
    }
  });

  it('isAdmin: walks /api/agents/admin/all pages and defaults permission to owner', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes('page=1')) return json({ agents: [A(1), A(2, { my_permission: 'viewer' })], pages: 2 });
      if (url.includes('page=2')) return json({ agents: [A(3)], pages: 2 });
      return json({}, false);
    });
    const out = await fetchMergedUserAgents('tok', 100, { isAdmin: true });
    expect(out.map((a) => a.id)).toEqual([1, 2, 3]);
    expect(out.map((a) => a.my_permission)).toEqual(['owner', 'viewer', 'owner']);
    expect(fetchMock.mock.calls.every(([url]) => String(url).includes('/api/agents/admin/all'))).toBe(true);
  });

  it('own copy wins over shared and bookmark duplicates', async () => {
    routes({
      '/my/agents': { agents: [A(1, { name: 'mine' })] },
      '/my/shared': { agents: [A(1, { name: 'shared' })] },
      '/my/bookmarks': { agents: [A(1, { name: 'bookmark' })] },
    });
    const out = await fetchMergedUserAgents('tok');
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ name: 'mine', my_permission: 'owner' });
    expect(out[0].is_shared_with_me).toBeUndefined();
  });

  it('shared wins over bookmark', async () => {
    routes({
      '/my/agents': { agents: [] },
      '/my/shared': { agents: [A(5, { my_permission: 'editor' })] },
      '/my/bookmarks': { agents: [A(5)] },
    });
    const out = await fetchMergedUserAgents('tok');
    expect(out).toHaveLength(1);
    expect(out[0].is_shared_with_me).toBe(true);
    expect(out[0].is_bookmarked).toBeUndefined();
  });

  it('an unavailable source is skipped silently', async () => {
    routes({ '/my/agents': { agents: [A(1)] }, '/my/shared': 'fail', '/my/bookmarks': { agents: [A(3)] } });
    expect((await fetchMergedUserAgents('tok')).map((a) => a.id)).toEqual([1, 3]);
  });

  it('missing agents arrays are treated as empty', async () => {
    routes({ '/my/agents': {}, '/my/shared': {}, '/my/bookmarks': {} });
    expect(await fetchMergedUserAgents('tok')).toEqual([]);
  });

  it('a network failure of any source rejects the whole call (known issue)', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes('/my/shared')) throw new TypeError('offline');
      return json({ agents: [A(1)] });
    });
    await expect(fetchMergedUserAgents('tok')).rejects.toThrow('offline');
  });

  describe('admin mode', () => {
    it('walks all pages and marks everything as owned', async () => {
      fetchMock.mockImplementation(async (url: string) => {
        const page = Number(new URL(url).searchParams.get('page'));
        return json({ agents: [A(page * 10), A(page * 10 + 1, { my_permission: 'viewer' })], pages: 3 });
      });

      const out = await fetchMergedUserAgents('tok', 100, { isAdmin: true });

      expect(out.map((a) => a.id)).toEqual([10, 11, 20, 21, 30, 31]);
      expect(out[0].my_permission).toBe('owner');
      expect(out[1].my_permission).toBe('viewer');
      expect(fetchMock).toHaveBeenCalledTimes(3);
      expect(fetchMock.mock.calls[0][0]).toContain('/api/agents/admin/all?limit=200&page=1');
    });

    it('stops at the first failing page and keeps what it has', async () => {
      fetchMock
        .mockResolvedValueOnce(json({ agents: [A(1)], pages: 3 }))
        .mockResolvedValueOnce(json({}, false));
      expect((await fetchMergedUserAgents('tok', 100, { isAdmin: true })).map((a) => a.id)).toEqual([1]);
    });

    it('an empty page stops the walk even if pages claims more', async () => {
      fetchMock.mockResolvedValueOnce(json({ agents: [A(1)], pages: 99 })).mockResolvedValueOnce(json({ agents: [], pages: 99 }));
      const out = await fetchMergedUserAgents('tok', 100, { isAdmin: true });
      expect(out).toHaveLength(1);
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('garbage pagination falls back to a single page', async () => {
      fetchMock.mockResolvedValue(json({ agents: [A(1)], pages: 'many' }));
      const out = await fetchMergedUserAgents('tok', 100, { isAdmin: true });
      expect(out).toHaveLength(1);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
  });
});

describe('projects API', () => {
  const project = {
    id: 'p1',
    name: 'Проект',
    instructions: undefined,
    memory: 'default',
    icon: 'folder',
    iconType: 'icon',
    iconColor: '#fff',
    extra: 'ignored',
  } as any;

  it('fetchProjectsFromServer returns the list, empty on odd bodies, throws on HTTP errors', async () => {
    fetchMock.mockResolvedValueOnce(json({ projects: [{ id: 'a' }] }));
    expect(await fetchProjectsFromServer()).toEqual([{ id: 'a' }]);
    fetchMock.mockResolvedValueOnce(json({ projects: 'x' }));
    expect(await fetchProjectsFromServer()).toEqual([]);
    fetchMock.mockResolvedValueOnce(json({}, false, 403));
    await expect(fetchProjectsFromServer()).rejects.toThrow('GET /api/projects: 403');
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer t');
  });

  it('syncProjectCreate posts only known fields', async () => {
    fetchMock.mockResolvedValue(json({}));
    await syncProjectCreate(project);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/api/projects');
    expect(init.method).toBe('POST');
    expect(init.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(init.body)).toEqual({
      id: 'p1',
      name: 'Проект',
      instructions: '',
      memory: 'default',
      icon: 'folder',
      iconType: 'icon',
      iconColor: '#fff',
      knowledgeBaseIds: [],
      knowledgeBasesEnabled: false,
    });
  });

  it('syncProjectCreate includes server details in the error', async () => {
    fetchMock.mockResolvedValue(json('имя занято', false, 409));
    await expect(syncProjectCreate(project)).rejects.toThrow('POST /api/projects: 409 — имя занято');
  });

  it('syncProjectUpdate sends only changed fields and encodes the id', async () => {
    fetchMock.mockResolvedValue(json({}));
    await syncProjectUpdate('a/b c', { name: 'Новое', instructions: '', icon: undefined });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/api/projects/a%2Fb%20c');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body)).toEqual({ name: 'Новое', instructions: '' });
  });

  it('syncProjectUpdate with no changes still sends an empty object', async () => {
    fetchMock.mockResolvedValue(json({}));
    await syncProjectUpdate('p', {});
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({});
  });

  it('syncProjectUpdate reports failures', async () => {
    fetchMock.mockResolvedValue(json('нет прав', false, 403));
    await expect(syncProjectUpdate('p', { name: 'x' })).rejects.toThrow('PUT /api/projects/p: 403 — нет прав');
  });

  it('migration continues past failed projects', async () => {
    jest.spyOn(console, 'debug').mockImplementation(() => undefined);
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => undefined);
    fetchMock.mockResolvedValueOnce(json({})).mockResolvedValueOnce(json('boom', false, 500)).mockResolvedValueOnce(json({}));
    await migrateLocalProjectsToServer([{ ...project, id: 'a' }, { ...project, id: 'b' }, { ...project, id: 'c' }]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(warn).toHaveBeenCalledTimes(1);
  });
});

describe('ragEntityDefaults', () => {
  const ok = (body: Record<string, unknown>) => json(body);

  it('loads and trims the defaults, then serves them from the cache', async () => {
    fetchMock.mockResolvedValue(ok({ rag_embedding_model_path: ' local/emb ', rag_reranker_model_path: 'local/rr' }));
    const { fetchRagEntityDefaults, getCachedRagEntityDefaults } = loadRag();

    expect(getCachedRagEntityDefaults()).toBeNull();
    const first = await fetchRagEntityDefaults('agent');
    const second = await fetchRagEntityDefaults('project');

    expect(first).toEqual({ embeddingPath: 'local/emb', rerankerPath: 'local/rr' });
    expect(second).toBe(first);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/rag/settings?scope=agent');
    expect(getCachedRagEntityDefaults()).toEqual(first);
  });

  it('concurrent callers share one request', async () => {
    fetchMock.mockResolvedValue(ok({}));
    const { fetchRagEntityDefaults } = loadRag();
    await Promise.all([fetchRagEntityDefaults(), fetchRagEntityDefaults()]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('missing fields become empty strings', async () => {
    fetchMock.mockResolvedValue(ok({}));
    const { fetchRagEntityDefaults } = loadRag();
    expect(await fetchRagEntityDefaults()).toEqual({ embeddingPath: '', rerankerPath: '' });
  });

  it('a failed load is remembered forever until reset (known issue)', async () => {
    fetchMock.mockResolvedValueOnce(json({}, false)).mockResolvedValueOnce(ok({ rag_embedding_model_path: 'local/emb' }));
    const { fetchRagEntityDefaults, resetRagEntityDefaultsCache, getCachedRagEntityDefaults } = loadRag();

    expect(await fetchRagEntityDefaults()).toEqual({ embeddingPath: '', rerankerPath: '' });
    expect(getCachedRagEntityDefaults()).toBeNull();
    // повторный вызов НЕ идёт в сеть: сбой закэширован в loadPromise
    expect(await fetchRagEntityDefaults()).toEqual({ embeddingPath: '', rerankerPath: '' });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resetRagEntityDefaultsCache();
    expect((await fetchRagEntityDefaults()).embeddingPath).toBe('local/emb');
  });

  it('a network error also yields empty defaults', async () => {
    fetchMock.mockRejectedValue(new TypeError('offline'));
    const { fetchRagEntityDefaults } = loadRag();
    expect(await fetchRagEntityDefaults()).toEqual({ embeddingPath: '', rerankerPath: '' });
  });

  it('the scope of later calls is ignored once cached (known issue)', async () => {
    fetchMock.mockResolvedValue(ok({ rag_embedding_model_path: 'project-model' }));
    const { fetchRagEntityDefaults } = loadRag();
    await fetchRagEntityDefaults('project');
    const agent = await fetchRagEntityDefaults('agent');
    expect(agent.embeddingPath).toBe('project-model');
  });

  it('reset forces a new request', async () => {
    fetchMock.mockResolvedValue(ok({ rag_embedding_model_path: 'a' }));
    const { fetchRagEntityDefaults, resetRagEntityDefaultsCache } = loadRag();
    await fetchRagEntityDefaults();
    resetRagEntityDefaultsCache();
    await fetchRagEntityDefaults();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  describe('path resolution', () => {
    it('an explicit path wins and is trimmed', () => {
      const { resolveRagEmbeddingModelPath, resolveRagRerankerModelPath } = loadRag();
      expect(resolveRagEmbeddingModelPath('  local/x ', 'fb')).toBe('local/x');
      expect(resolveRagRerankerModelPath('local/r', 'fb')).toBe('local/r');
    });

    it('empty values fall back to the argument, then to the cached default, then to empty', async () => {
      fetchMock.mockResolvedValue(ok({ rag_embedding_model_path: 'cached-emb', rag_reranker_model_path: 'cached-rr' }));
      const { resolveRagEmbeddingModelPath, resolveRagRerankerModelPath, fetchRagEntityDefaults } = loadRag();

      expect(resolveRagEmbeddingModelPath('', undefined)).toBe('');
      expect(resolveRagEmbeddingModelPath(null, ' fb ')).toBe('fb');

      await fetchRagEntityDefaults();
      expect(resolveRagEmbeddingModelPath(undefined)).toBe('cached-emb');
      expect(resolveRagRerankerModelPath('   ')).toBe('cached-rr');
      expect(resolveRagRerankerModelPath(undefined, 'explicit-fb')).toBe('explicit-fb');
    });
  });
});
