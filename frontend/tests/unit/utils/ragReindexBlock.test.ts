import {
  RAG_REINDEX_BLOCK_PLACEHOLDER,
  isRagEntityRebuilding,
  isRagReindexBannerVisible,
  ragReindexBlockMessage,
  shouldBlockRagSend,
  type RagReindexStatusPayload,
  type RagSendBlockContext,
} from '@/utils/ragReindexBlock';

function status(overrides: Partial<RagReindexStatusPayload> = {}): RagReindexStatusPayload {
  return {
    memory: { reindexing: false },
    project: { reindexing: false },
    kb: { reindexing: false },
    any_reindexing: false,
    agent_has_kb: true,
    project_has_documents: true,
    memory_rag_enabled: true,
    message: '',
    ...overrides,
  };
}

const ctx: RagSendBlockContext = {
  libraryEnabled: true,
  projectHasDocuments: true,
  agentHasKb: true,
  agentId: 7,
  projectId: 'p1',
};

describe('isRagEntityRebuilding', () => {
  it('is false without a status or an id', () => {
    expect(isRagEntityRebuilding(null, 'agent', 7)).toBe(false);
    expect(isRagEntityRebuilding(status(), 'agent', null)).toBe(false);
    expect(isRagEntityRebuilding(status(), 'agent', '  ')).toBe(false);
  });

  it('looks the entity up in the active list (ids compared as strings)', () => {
    const s = status({ active: [{ scope: 'agent', entity_id: '7' }] });

    expect(isRagEntityRebuilding(s, 'agent', 7)).toBe(true);
    expect(isRagEntityRebuilding(s, 'agent', 8)).toBe(false);
    expect(isRagEntityRebuilding(s, 'project', 7)).toBe(false);
  });

  it('treats an empty active list as "nobody is rebuilding" even if the store flag is up', () => {
    const s = status({ active: [], kb: { reindexing: true } });

    expect(isRagEntityRebuilding(s, 'agent', 7)).toBe(false);
  });

  it('falls back to store flags for an old backend without active', () => {
    expect(isRagEntityRebuilding(status({ kb: { reindexing: true } }), 'agent', 7)).toBe(true);
    expect(isRagEntityRebuilding(status({ project: { reindexing: true } }), 'project', 'p1')).toBe(true);
    expect(isRagEntityRebuilding(status({ kb: { reindexing: true } }), 'project', 'p1')).toBe(false);
  });
});

describe('shouldBlockRagSend', () => {
  it('does not block without a status', () => {
    expect(shouldBlockRagSend(null, ctx)).toBe(false);
  });

  it('blocks while the library is rebuilding and enabled', () => {
    expect(shouldBlockRagSend(status({ memory: { reindexing: true } }), ctx)).toBe(true);
    expect(
      shouldBlockRagSend(status({ memory: { reindexing: true } }), { ...ctx, libraryEnabled: false }),
    ).toBe(false);
  });

  it("blocks when this chat's agent is rebuilding", () => {
    const s = status({ active: [{ scope: 'agent', entity_id: '7' }] });

    expect(shouldBlockRagSend(s, ctx)).toBe(true);
  });

  it("does not block when somebody else's agent is rebuilding", () => {
    const s = status({ kb: { reindexing: true }, active: [{ scope: 'agent', entity_id: '99' }] });

    expect(shouldBlockRagSend(s, ctx)).toBe(false);
  });

  it('ignores a rebuilding agent that has no KB documents', () => {
    const s = status({ active: [{ scope: 'agent', entity_id: '7' }] });

    expect(shouldBlockRagSend(s, { ...ctx, agentHasKb: false })).toBe(false);
  });

  it("blocks for the chat's project only when it has documents", () => {
    const s = status({ active: [{ scope: 'project', entity_id: 'p1' }] });

    expect(shouldBlockRagSend(s, ctx)).toBe(true);
    expect(shouldBlockRagSend(s, { ...ctx, projectHasDocuments: false })).toBe(false);
  });

  it('uses store flags for an old backend', () => {
    expect(shouldBlockRagSend(status({ kb: { reindexing: true } }), ctx)).toBe(true);
    expect(shouldBlockRagSend(status({ kb: { reindexing: true } }), { ...ctx, agentHasKb: false })).toBe(false);
    expect(shouldBlockRagSend(status({ project: { reindexing: true } }), ctx)).toBe(true);
  });
});

describe('isRagReindexBannerVisible', () => {
  it('is hidden without a status or activity', () => {
    expect(isRagReindexBannerVisible(null, {})).toBe(false);
    expect(isRagReindexBannerVisible(status(), {})).toBe(false);
  });

  it('is shown for a library rebuild', () => {
    expect(isRagReindexBannerVisible(status({ memory: { reindexing: true } }), {})).toBe(true);
  });

  it('is shown for any rebuilding agent', () => {
    const s = status({ active: [{ scope: 'agent', entity_id: '99' }] });

    expect(isRagReindexBannerVisible(s, {})).toBe(true);
  });

  it('is shown for a project only when it is the open one', () => {
    const s = status({ active: [{ scope: 'project', entity_id: 'p1' }] });

    expect(isRagReindexBannerVisible(s, { projectId: 'p1' })).toBe(true);
    expect(isRagReindexBannerVisible(s, { projectId: 'other' })).toBe(false);
    expect(isRagReindexBannerVisible(s, {})).toBe(false);
  });

  it('ignores unknown scopes', () => {
    expect(isRagReindexBannerVisible(status({ active: [{ scope: 'weird', entity_id: '1' }] }), {})).toBe(false);
  });

  it('uses store flags for an old backend', () => {
    expect(isRagReindexBannerVisible(status({ kb: { reindexing: true } }), {})).toBe(true);
    expect(isRagReindexBannerVisible(status({ project: { reindexing: true } }), {})).toBe(false);
    expect(isRagReindexBannerVisible(status({ project: { reindexing: true } }), { projectId: 'p1' })).toBe(true);
  });
});

describe('ragReindexBlockMessage', () => {
  it('uses the placeholder without a status', () => {
    expect(ragReindexBlockMessage(null)).toBe(RAG_REINDEX_BLOCK_PLACEHOLDER);
  });

  it('returns the raw server message when no context is given', () => {
    expect(ragReindexBlockMessage(status({ message: ' Идёт работа ' }))).toBe('Идёт работа');
    expect(ragReindexBlockMessage(status({ message: '  ' }))).toBe(RAG_REINDEX_BLOCK_PLACEHOLDER);
  });

  it('uses the server message for a library rebuild', () => {
    expect(ragReindexBlockMessage(status({ memory: { reindexing: true }, message: 'Библиотека' }), {})).toBe(
      'Библиотека',
    );
  });

  it('names a single rebuilding entity', () => {
    const s = status({ active: [{ scope: 'agent', entity_id: '7', name: 'Юрист' }] });

    expect(ragReindexBlockMessage(s, {})).toContain('агента «Юрист»');
  });

  it('lists several entities with "и" and removes duplicates', () => {
    const s = status({
      active: [
        { scope: 'agent', entity_id: '1', name: 'Один' },
        { scope: 'agent', entity_id: '2', name: 'Два' },
        { scope: 'agent', entity_id: '1', name: 'Один' },
        { scope: 'project', entity_id: 'p1', name: 'Проект' },
      ],
    });

    const message = ragReindexBlockMessage(s, { projectId: 'p1' });

    expect(message).toContain('агента «Один», агента «Два» и проекта «Проект»');
  });

  it('falls back to the id when the entity has no name', () => {
    const s = status({ active: [{ scope: 'agent', entity_id: '7' }] });

    expect(ragReindexBlockMessage(s, {})).toContain('агента 7');
  });

  it('is empty when nothing relevant is rebuilding', () => {
    const s = status({ active: [{ scope: 'project', entity_id: 'other' }] });

    expect(ragReindexBlockMessage(s, { projectId: 'p1' })).toBe('');
  });

  it('is empty for an old backend when the banner is hidden', () => {
    expect(ragReindexBlockMessage(status(), {})).toBe('');
  });

  it('uses the server message for an old backend when the banner is visible', () => {
    expect(ragReindexBlockMessage(status({ kb: { reindexing: true }, message: 'KB' }), {})).toBe('KB');
  });
});
