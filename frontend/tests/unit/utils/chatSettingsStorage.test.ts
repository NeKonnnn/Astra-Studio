import {
  AGENT_ARTIFACTS_CHANGED_EVENT,
  AGENT_ARTIFACTS_ENABLED_KEY,
  AGENT_PRESENTATION_SKILLS_KEY,
  AGENT_SHADCN_ENABLED_KEY,
  AGENT_USER_PROMPT_MODE_KEY,
  artifactsConfigFromAgentDetail,
  clearAgentArtifactsEnabled,
  persistAgentArtifactsEnabled,
  readAgentArtifactsDefaults,
  readAgentArtifactsEnabled,
  readAgentPresentationSkills,
  readAgentViewerGate,
} from '@/utils/agentArtifactsEnabled';
import {
  ARTIFACTS_SELECTION_CHANGED_EVENT,
  clearArtifactsSettingsForChat,
  disableChatArtifactsToolsForChat,
  getArtifactsSettingsForChat,
  getChatArtifactsToolsState,
  getEffectiveArtifactsSettings,
  hasChatArtifactsOverride,
  isChatArtifactsToolsActive,
  normalizeArtifactsToolsState,
  setChatArtifactsToolMode,
} from '@/utils/artifactsSelectionStorage';
import { clearActiveAgent } from '@/utils/clearActiveAgent';
import { KNOWLEDGE_RAG_STORAGE_EVENT, isKnowledgeRagEnabled, setKnowledgeRagEnabled } from '@/utils/knowledgeRagStorage';
import {
  isMessageArtifactsViewerPinned,
  pinMessageArtifactsViewer,
  skillImpliesArtifactsViewer,
  skillImpliesPresentation,
} from '@/utils/messageArtifactsViewerStorage';
import { KEEP_CHAT_SETTINGS_KEY, isKeepChatSettingsEnabled, prepareNewChatSettings, setKeepChatSettingsEnabled } from '@/utils/newChatSettings';
import { getPluginBookmarkIds, isPluginBookmarked, togglePluginBookmark } from '@/utils/pluginBookmarks';
import { ragDocumentDisplayIndex } from '@/utils/ragDocumentDisplayIndex';
import {
  SKILL_SELECTION_CHANGED_EVENT,
  clearActiveSkills,
  copyActiveSkills,
  getActiveSkillIds,
  getActiveSkillRefs,
  isSkillActive,
  renameSkillSlugInAllChats,
  setActiveSkillIds,
  setActiveSkillRefs,
  toggleActiveSkill,
} from '@/utils/skillSelectionStorage';
import { getMcpToolIdsForChat, setMcpToolIdsForChat } from '@/mcp/selectionStorage';

function captureEvents(name: string) {
  const events: CustomEvent[] = [];
  const handler = (event: Event) => events.push(event as CustomEvent);
  window.addEventListener(name, handler);
  return { events, stop: () => window.removeEventListener(name, handler) };
}

beforeEach(() => localStorage.clear());

describe('agent artifacts flags', () => {
  it('defaults to everything off', () => {
    expect(readAgentArtifactsDefaults()).toEqual({ artifacts_enabled: false, shadcn_enabled: false, user_prompt_mode: false });
    expect(readAgentViewerGate()).toBe(false);
  });

  it('persists the flags from an agent config and announces them', () => {
    const { events, stop } = captureEvents(AGENT_ARTIFACTS_CHANGED_EVENT);

    persistAgentArtifactsEnabled({ artifacts_enabled: true, shadcn_enabled: true, user_prompt_mode: false, skill_ids: ['pptx-builder', 'coder'] });
    stop();

    expect(readAgentArtifactsDefaults()).toEqual({ artifacts_enabled: true, shadcn_enabled: true, user_prompt_mode: false });
    expect(readAgentPresentationSkills()).toBe(true);
    expect(events[0].detail).toEqual({ enabled: true, artifactsEnabled: true, presentationSkills: true });
  });

  it('opens the viewer gate for a presentation skill even when artifacts are off', () => {
    persistAgentArtifactsEnabled({ skill_ids: [' Презентация '] });

    expect(readAgentArtifactsEnabled()).toBe(false);
    expect(readAgentViewerGate()).toBe(true);
  });

  it('resets the flags when the agent is cleared or the config is empty', () => {
    persistAgentArtifactsEnabled({ artifacts_enabled: true, user_prompt_mode: true, skill_ids: ['slides'] });

    persistAgentArtifactsEnabled(null);

    for (const key of [AGENT_ARTIFACTS_ENABLED_KEY, AGENT_SHADCN_ENABLED_KEY, AGENT_USER_PROMPT_MODE_KEY, AGENT_PRESENTATION_SKILLS_KEY]) {
      expect(localStorage.getItem(key)).toBeNull();
    }
  });

  it('ignores a malformed skill list', () => {
    persistAgentArtifactsEnabled({ artifacts_enabled: false, skill_ids: 'slides' });

    expect(readAgentPresentationSkills()).toBe(false);
  });

  it('announces the reset', () => {
    persistAgentArtifactsEnabled({ artifacts_enabled: true });
    const { events, stop } = captureEvents(AGENT_ARTIFACTS_CHANGED_EVENT);

    clearAgentArtifactsEnabled();
    stop();

    expect(readAgentArtifactsEnabled()).toBe(false);
    expect(events[0].detail).toEqual({ enabled: false, artifactsEnabled: false, presentationSkills: false });
  });

  it('extracts the config from an agentSelected event detail', () => {
    expect(artifactsConfigFromAgentDetail({ config: { a: 1 } })).toEqual({ a: 1 });
    for (const detail of [null, undefined, 'x', {}, { config: 'x' }, { config: null }]) {
      expect(artifactsConfigFromAgentDetail(detail)).toBeNull();
    }
  });
});

describe('per-chat artifacts settings', () => {
  it('lets only one mode be active at a time', () => {
    expect(normalizeArtifactsToolsState({ shadcn_enabled: true, user_prompt_mode: true })).toEqual({ shadcn_enabled: true, user_prompt_mode: false });
    expect(normalizeArtifactsToolsState({})).toEqual({ shadcn_enabled: false, user_prompt_mode: false });
    expect(isChatArtifactsToolsActive({ shadcn_enabled: false, user_prompt_mode: true })).toBe(true);
    expect(isChatArtifactsToolsActive({ shadcn_enabled: false, user_prompt_mode: false })).toBe(false);
  });

  it('inherits the agent defaults until the chat has its own choice', () => {
    persistAgentArtifactsEnabled({ shadcn_enabled: true });

    expect(getChatArtifactsToolsState('c1')).toEqual({ shadcn_enabled: true, user_prompt_mode: false });
    expect(getChatArtifactsToolsState(null)).toEqual({ shadcn_enabled: true, user_prompt_mode: false });
    expect(hasChatArtifactsOverride('c1')).toBe(false);
    expect(hasChatArtifactsOverride(null)).toBe(false);
  });

  it('switches modes exclusively and announces each change', () => {
    const { events, stop } = captureEvents(ARTIFACTS_SELECTION_CHANGED_EVENT);

    expect(setChatArtifactsToolMode('c1', 'shadcn_enabled', true)).toEqual({ shadcn_enabled: true, user_prompt_mode: false });
    expect(setChatArtifactsToolMode('c1', 'user_prompt_mode', true)).toEqual({ shadcn_enabled: false, user_prompt_mode: true });
    expect(setChatArtifactsToolMode('c1', 'user_prompt_mode', false)).toEqual({ shadcn_enabled: false, user_prompt_mode: false });
    stop();

    expect(events.map((e) => e.detail)).toEqual([{ chatId: 'c1' }, { chatId: 'c1' }, { chatId: 'c1' }]);
    expect(hasChatArtifactsOverride('c1')).toBe(true);
  });

  it('keeps the chat choice separate from the agent defaults', () => {
    persistAgentArtifactsEnabled({ shadcn_enabled: true });

    disableChatArtifactsToolsForChat('c1');

    expect(getChatArtifactsToolsState('c1')).toEqual({ shadcn_enabled: false, user_prompt_mode: false });
    expect(getChatArtifactsToolsState('c2')).toEqual({ shadcn_enabled: true, user_prompt_mode: false });
  });

  it('merges a partial stored choice with the agent defaults', () => {
    persistAgentArtifactsEnabled({ user_prompt_mode: true });
    localStorage.setItem('chat:c1:artifacts_settings', JSON.stringify({ shadcn_enabled: true }));

    expect(getChatArtifactsToolsState('c1')).toEqual({ shadcn_enabled: true, user_prompt_mode: false }); // конфликт решается в пользу shadcn
  });

  it.each(['{broken', 'null', '"text"', '[]'])('survives damaged storage %p', (raw) => {
    localStorage.setItem('chat:c1:artifacts_settings', raw);

    expect(getChatArtifactsToolsState('c1')).toEqual({ shadcn_enabled: false, user_prompt_mode: false });
  });

  it('enables artifacts when the agent allows them or a tool mode is on', () => {
    expect(getEffectiveArtifactsSettings('c1').artifacts_enabled).toBe(false);

    setChatArtifactsToolMode('c1', 'user_prompt_mode', true);
    expect(getEffectiveArtifactsSettings('c1')).toEqual({ artifacts_enabled: true, shadcn_enabled: false, user_prompt_mode: true });
    expect(getArtifactsSettingsForChat('c1')).toEqual(getEffectiveArtifactsSettings('c1'));

    persistAgentArtifactsEnabled({ artifacts_enabled: true });
    disableChatArtifactsToolsForChat('c2');
    expect(getEffectiveArtifactsSettings('c2').artifacts_enabled).toBe(true);
  });

  it('clears a chat override and announces it', () => {
    setChatArtifactsToolMode('c1', 'shadcn_enabled', true);
    const { events, stop } = captureEvents(ARTIFACTS_SELECTION_CHANGED_EVENT);

    clearArtifactsSettingsForChat('c1');
    stop();

    expect(hasChatArtifactsOverride('c1')).toBe(false);
    expect(events).toHaveLength(1);
  });
});

describe('active skills per chat', () => {
  it('stores skills per chat and never leaks the legacy global key', () => {
    localStorage.setItem('active_skill_ids', JSON.stringify(['legacy']));

    setActiveSkillIds('c1', ['a', 'b']);

    expect(getActiveSkillIds('c1')).toEqual(['a', 'b']);
    expect(getActiveSkillIds('c2')).toEqual([]);
    expect(localStorage.getItem('active_skill_ids')).toBeNull();
  });

  it('normalises strings, objects and duplicates', () => {
    localStorage.setItem('chat:c1:active_skill_ids', JSON.stringify(['a', { slug: 'b', name: ' Бэ ' }, { id: 'c' }, 'a', '', null, { name: 'no slug' }, 5]));

    expect(getActiveSkillRefs('c1')).toEqual([{ slug: 'a' }, { slug: 'b', name: 'Бэ' }, { slug: 'c' }]);
  });

  it('does nothing without a chat id', () => {
    setActiveSkillIds(null, ['a']);
    setActiveSkillRefs(undefined, [{ slug: 'a' }]);
    clearActiveSkills(undefined);

    expect(getActiveSkillIds(null)).toEqual([]);
    expect(toggleActiveSkill(null, 'a', true)).toEqual([]);
    expect(localStorage.length).toBe(0);
  });

  it('announces changes with the full selection', () => {
    const { events, stop } = captureEvents(SKILL_SELECTION_CHANGED_EVENT);

    setActiveSkillRefs('c1', [{ slug: 'a', name: 'А' }]);
    stop();

    expect(events[0].detail).toEqual({ chatId: 'c1', skillIds: ['a'], skills: [{ slug: 'a', name: 'А' }] });
  });

  it('keeps known names when only the ids are replaced', () => {
    setActiveSkillRefs('c1', [{ slug: 'a', name: 'А' }, { slug: 'b' }]);

    setActiveSkillIds('c1', ['b', 'a', 'c']);

    expect(getActiveSkillRefs('c1')).toEqual([{ slug: 'b' }, { slug: 'a', name: 'А' }, { slug: 'c' }]);
  });

  it('toggles skills and refreshes names', () => {
    expect(toggleActiveSkill('c1', ' a ', true, ' Имя ')).toEqual(['a']);
    expect(getActiveSkillRefs('c1')).toEqual([{ slug: 'a', name: 'Имя' }]);
    expect(toggleActiveSkill('c1', 'a', true, 'Новое')).toEqual(['a']);
    expect(getActiveSkillRefs('c1')[0].name).toBe('Новое');
    expect(toggleActiveSkill('c1', '', true)).toEqual(['a']);
    expect(toggleActiveSkill('c1', 'a', false)).toEqual([]);
    expect(isSkillActive('c1', 'a')).toBe(false);
    expect(isSkillActive(null, 'a')).toBe(false);
  });

  it('copies a selection only when there is one and the chats differ', () => {
    copyActiveSkills('src', 'dst');
    expect(getActiveSkillIds('dst')).toEqual([]);

    setActiveSkillIds('src', ['a']);
    copyActiveSkills('src', 'src');
    copyActiveSkills('', 'dst');
    expect(getActiveSkillIds('dst')).toEqual([]);

    copyActiveSkills('src', 'dst');
    expect(getActiveSkillIds('dst')).toEqual(['a']);
  });

  it('renames a slug in every chat that uses it', () => {
    setActiveSkillIds('c1', ['old', 'keep']);
    setActiveSkillIds('c2', ['keep']);
    setActiveSkillIds('c3', ['old']);

    renameSkillSlugInAllChats('old', 'new', 'Новое имя');

    expect(getActiveSkillIds('c1')).toEqual(['new', 'keep']);
    expect(getActiveSkillIds('c2')).toEqual(['keep']);
    expect(getActiveSkillRefs('c3')).toEqual([{ slug: 'new', name: 'Новое имя' }]);
  });

  it('ignores meaningless renames', () => {
    setActiveSkillIds('c1', ['a']);

    renameSkillSlugInAllChats('a', 'a');
    renameSkillSlugInAllChats('', 'b');
    renameSkillSlugInAllChats('a', ' ');

    expect(getActiveSkillIds('c1')).toEqual(['a']);
  });

  it('survives damaged storage', () => {
    localStorage.setItem('chat:c1:active_skill_ids', '{broken');

    expect(getActiveSkillIds('c1')).toEqual([]);
  });
});

describe('presentation skill detection', () => {
  it.each(['presentation', 'html-presentation', 'deck-html', 'pptx-builder', 'Презентация', 'слайды-для-отчёта', 'slides', 'my-slide', 'slide_deck'])('%p implies a presentation', (slug) => {
    expect(skillImpliesPresentation(slug)).toBe(true);
  });

  it.each(['mermaid', 'coder', 'translator', '', '   ', 'slider', 'hyperslide'])('%p does not imply a presentation', (slug) => {
    expect(skillImpliesPresentation(slug)).toBe(false);
  });

  it('matches the substring "present" inside unrelated words (known issue)', () => {
    // «representative-analysis» содержит «present» — такой навык ошибочно считается презентационным.
    expect(skillImpliesPresentation('representative-analysis')).toBe(true);
  });

  it('treats diagram-like skills as viewer skills but not as presentations', () => {
    expect(skillImpliesArtifactsViewer('Mermaid-diagrams')).toBe(true);
    expect(skillImpliesArtifactsViewer('visual-explainer')).toBe(true);
    expect(skillImpliesArtifactsViewer('pptx')).toBe(true);
    expect(skillImpliesArtifactsViewer('coder')).toBe(false);
    expect(skillImpliesArtifactsViewer('')).toBe(false);
  });

  it('remembers which messages already showed a viewer', () => {
    expect(isMessageArtifactsViewerPinned('m1')).toBe(false);

    pinMessageArtifactsViewer('m1');
    pinMessageArtifactsViewer('m1');
    pinMessageArtifactsViewer(null);
    pinMessageArtifactsViewer('');

    expect(isMessageArtifactsViewerPinned('m1')).toBe(true);
    expect(isMessageArtifactsViewerPinned('m2')).toBe(false);
    expect(isMessageArtifactsViewerPinned(undefined)).toBe(false);
    expect(JSON.parse(localStorage.getItem('astrachat:message_artifacts_viewer_v1')!)).toEqual(['m1']);
  });

  it('survives damaged viewer storage', () => {
    localStorage.setItem('astrachat:message_artifacts_viewer_v1', '{broken');

    expect(isMessageArtifactsViewerPinned('m1')).toBe(false);
    pinMessageArtifactsViewer('m1');
    expect(isMessageArtifactsViewerPinned('m1')).toBe(true);
  });
});

describe('plugin bookmarks', () => {
  it('toggles and persists', () => {
    expect(togglePluginBookmark('cash-flow')).toBe(true);
    expect(isPluginBookmarked('cash-flow')).toBe(true);
    expect(togglePluginBookmark('other')).toBe(true);
    expect([...getPluginBookmarkIds()]).toEqual(['cash-flow', 'other']);

    expect(togglePluginBookmark('cash-flow')).toBe(false);
    expect(isPluginBookmarked('cash-flow')).toBe(false);
    expect([...getPluginBookmarkIds()]).toEqual(['other']);
  });

  it.each(['{broken', '"text"', '{"a":1}', 'null'])('survives damaged storage %p', (raw) => {
    localStorage.setItem('astrachat_plugin_bookmarks', raw);

    expect(getPluginBookmarkIds().size).toBe(0);
    expect(togglePluginBookmark('x')).toBe(true);
  });

  it('drops non-string entries', () => {
    localStorage.setItem('astrachat_plugin_bookmarks', JSON.stringify(['a', 5, null, 'b']));

    expect([...getPluginBookmarkIds()]).toEqual(['a', 'b']);
  });
});

describe('library (memory RAG) flag', () => {
  it('is on if either legacy key is on', () => {
    expect(isKnowledgeRagEnabled()).toBe(false);
    localStorage.setItem('use_kb_rag', 'true');
    expect(isKnowledgeRagEnabled()).toBe(true);
    localStorage.clear();
    localStorage.setItem('use_memory_library_rag', 'true');
    expect(isKnowledgeRagEnabled()).toBe(true);
  });

  it('sets both keys together and announces the change', () => {
    const { events, stop } = captureEvents(KNOWLEDGE_RAG_STORAGE_EVENT);

    setKnowledgeRagEnabled(true);
    setKnowledgeRagEnabled(false);
    stop();

    expect(localStorage.getItem('use_kb_rag')).toBe('false');
    expect(localStorage.getItem('use_memory_library_rag')).toBe('false');
    expect(events.map((e) => e.detail)).toEqual([{ enabled: true }, { enabled: false }]);
    expect(isKnowledgeRagEnabled()).toBe(false);
  });
});

describe('clearActiveAgent', () => {
  it('removes the agent keys, artifacts flags and announces a null selection', () => {
    localStorage.setItem('active_agent_id', '7');
    localStorage.setItem('active_agent_name', 'Юрист');
    localStorage.setItem('active_agent_prompt', 'Ты юрист');
    persistAgentArtifactsEnabled({ artifacts_enabled: true });
    const { events, stop } = captureEvents('agentSelected');

    clearActiveAgent();
    stop();

    for (const key of ['active_agent_id', 'active_agent_name', 'active_agent_prompt', AGENT_ARTIFACTS_ENABLED_KEY]) {
      expect(localStorage.getItem(key)).toBeNull();
    }
    expect(events).toHaveLength(1);
    expect(events[0].detail).toBeNull();
  });
});

describe('settings of a new chat', () => {
  function configurePreviousChat() {
    setActiveSkillIds('prev', ['coder']);
    setMcpToolIdsForChat('prev', ['server:mcp:docs']);
    setChatArtifactsToolMode('prev', 'shadcn_enabled', true);
  }

  it('starts clean by default: agent and library are reset', () => {
    localStorage.setItem('active_agent_id', '7');
    setKnowledgeRagEnabled(true);
    configurePreviousChat();

    prepareNewChatSettings('prev', 'next');

    expect(localStorage.getItem('active_agent_id')).toBeNull();
    expect(isKnowledgeRagEnabled()).toBe(false);
    expect(getActiveSkillIds('next')).toEqual([]);
    expect(getMcpToolIdsForChat('next')).toEqual([]);
    expect(hasChatArtifactsOverride('next')).toBe(false);
  });

  it('copies skills, MCP and artifacts when the user asked to keep the settings', () => {
    setKeepChatSettingsEnabled(true);
    localStorage.setItem('active_agent_id', '7');
    setKnowledgeRagEnabled(true);
    configurePreviousChat();

    prepareNewChatSettings('prev', 'next');

    expect(getActiveSkillIds('next')).toEqual(['coder']);
    expect(getMcpToolIdsForChat('next')).toEqual(['server:mcp:docs']);
    expect(getChatArtifactsToolsState('next')).toEqual({ shadcn_enabled: true, user_prompt_mode: false });
    expect(localStorage.getItem('active_agent_id')).toBe('7'); // агент и библиотека глобальны и остаются
    expect(isKnowledgeRagEnabled()).toBe(true);
  });

  it('copies an explicit "artifacts off" choice too', () => {
    setKeepChatSettingsEnabled(true);
    persistAgentArtifactsEnabled({ shadcn_enabled: true });
    disableChatArtifactsToolsForChat('prev');

    prepareNewChatSettings('prev', 'next');

    expect(getChatArtifactsToolsState('next')).toEqual({ shadcn_enabled: false, user_prompt_mode: false });
  });

  it('copies nothing from itself or from a missing chat', () => {
    setKeepChatSettingsEnabled(true);
    configurePreviousChat();

    prepareNewChatSettings(null, 'next');
    prepareNewChatSettings('prev', 'prev');

    expect(getActiveSkillIds('next')).toEqual([]);
  });

  it('stores the preference as a boolean string', () => {
    expect(isKeepChatSettingsEnabled()).toBe(false);
    setKeepChatSettingsEnabled(true);
    expect(localStorage.getItem(KEEP_CHAT_SETTINGS_KEY)).toBe('true');
    setKeepChatSettingsEnabled(false);
    expect(isKeepChatSettingsEnabled()).toBe(false);
  });
});

describe('ragDocumentDisplayIndex', () => {
  const docs = [
    { id: 30, created_at: '2026-01-03T00:00:00Z' },
    { id: 10, created_at: '2026-01-01T00:00:00Z' },
    { id: 20, created_at: '2026-01-02T00:00:00Z' },
  ];

  it('numbers documents by upload order starting from 1', () => {
    expect(docs.map((d) => ragDocumentDisplayIndex(docs, d.id))).toEqual([3, 1, 2]);
  });

  it('breaks ties and missing dates by id, with dated documents first', () => {
    const mixed = [{ id: 5 }, { id: 3, created_at: 'not a date' }, { id: 9, created_at: '2026-01-01T00:00:00Z' }, { id: 4, created_at: '2026-01-01T00:00:00Z' }];

    expect(mixed.map((d) => ragDocumentDisplayIndex(mixed, d.id))).toEqual([4, 3, 2, 1]);
  });

  it('returns 0 for unknown documents or an empty list', () => {
    expect(ragDocumentDisplayIndex(docs, 999)).toBe(0);
    expect(ragDocumentDisplayIndex([], 1)).toBe(0);
  });
});
