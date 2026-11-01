import { mapChainStepsFromMeta } from '@/agents/chain/chainSteps';
import { getMcpSuggestions } from '@/mcp/plugins/mcpSuggestions';
import { atlassianSuggestionChips, getAtlassianSuggestions } from '@/mcp/plugins/atlassianSuggestions';
import { getWebSearchSuggestions, webSearchSuggestionChips } from '@/mcp/plugins/webSearchSuggestions';
import {
  buildVerdictMarkdown,
  extractMarkdownFromResponse,
  formatDuration,
  normalizeInvokeResult,
  notificationForResultStatus,
  summaryForResultStatus,
} from '@/plugins/verdict';
import type { PluginInvokeResult } from '@/plugins/types';
import {
  FOLLOW_UP_AUTO_GENERATE_KEY,
  FOLLOW_UP_CLICK_ACTION_KEY,
  FOLLOW_UP_SHOW_SCOPE_KEY,
  loadFollowUpSettings,
  saveFollowUpAutoGenerate,
  saveFollowUpClickAction,
  saveFollowUpShowScope,
} from '@/chat/followUpSettings';
import { getChatInputSuggestions } from '@/chat/getChatInputSuggestions';
import {
  ChatInputSuggestion,
  filterChatInputSuggestions,
  generalChatSuggestions,
  pickVisibleSuggestions,
  shuffleSuggestions,
} from '@/chat/inputSuggestions';
import { getRagSuggestions, ragSuggestionItems } from '@/chat/ragSuggestions';

const ids = (items: ChatInputSuggestion[]) => items.map((s) => s.id);

describe('suggestion catalogs', () => {
  it('have unique ids across all catalogs', () => {
    const all = [...generalChatSuggestions, ...ragSuggestionItems, ...getMcpSuggestions(['atlassian', 'websearch'])];

    expect(new Set(ids(all)).size).toBe(all.length);
  });

  it('keep sources and server ids consistent', () => {
    expect(generalChatSuggestions.every((s) => s.source === 'general')).toBe(true);
    expect(ragSuggestionItems.every((s) => s.source === 'rag')).toBe(true);
    expect(getAtlassianSuggestions(['atlassian']).every((s) => s.source === 'mcp' && s.mcpServerId === 'atlassian')).toBe(true);
    expect(getWebSearchSuggestions(['websearch']).every((s) => s.source === 'mcp' && s.mcpServerId === 'websearch')).toBe(true);
  });

  it('expose the legacy chip lists as the contents of the items', () => {
    expect(atlassianSuggestionChips).toHaveLength(3);
    expect(webSearchSuggestionChips).toHaveLength(5);
  });

  it('return MCP suggestions only for enabled servers', () => {
    expect(getAtlassianSuggestions([])).toEqual([]);
    expect(getAtlassianSuggestions(['websearch'])).toEqual([]);
    expect(getWebSearchSuggestions(['atlassian'])).toEqual([]);
    expect(ids(getMcpSuggestions(['websearch', 'atlassian']))).toEqual([
      ...ids(getAtlassianSuggestions(['atlassian'])),
      ...ids(getWebSearchSuggestions(['websearch'])),
    ]);
    expect(getMcpSuggestions(['unknown'])).toEqual([]);
  });

  it('return library suggestions only when the library is active', () => {
    expect(getRagSuggestions(false)).toEqual([]);
    expect(getRagSuggestions(true)).toBe(ragSuggestionItems);
  });
});

describe('getChatInputSuggestions', () => {
  it('falls back to general suggestions without context', () => {
    expect(getChatInputSuggestions([], false)).toBe(generalChatSuggestions);
    expect(getChatInputSuggestions(['unknown'])).toBe(generalChatSuggestions);
  });

  it('puts the library first, then MCP, then general ones', () => {
    const result = getChatInputSuggestions(['atlassian'], true);

    expect(ids(result).slice(0, ragSuggestionItems.length)).toEqual(ids(ragSuggestionItems));
    expect(result.slice(ragSuggestionItems.length, ragSuggestionItems.length + 3).every((s) => s.source === 'mcp')).toBe(true);
    expect(result.slice(-generalChatSuggestions.length).every((s) => s.source === 'general')).toBe(true);
  });

  it('drops general suggestions that duplicate contextual ids', () => {
    const collidingId = generalChatSuggestions[0].id;
    const original = ragSuggestionItems[0].id;
    ragSuggestionItems[0].id = collidingId;
    try {
      const result = getChatInputSuggestions([], true);

      expect(result.filter((s) => s.id === collidingId)).toHaveLength(1);
      expect(result.find((s) => s.id === collidingId)?.source).toBe('rag');
    } finally {
      ragSuggestionItems[0].id = original;
    }
  });
});

describe('filterChatInputSuggestions', () => {
  const items: ChatInputSuggestion[] = [
    { id: '1', title: 'Объясни', subtitle: 'простыми словами', content: 'Объясни: ', keywords: ['просто'] },
    { id: '2', title: 'Код', content: 'Напиши код', keywords: ['python'] },
    { id: '3', title: 'Перевод', content: 'Переведи' },
  ];

  it('returns everything for a blank input', () => {
    expect(filterChatInputSuggestions(items, '')).toBe(items);
    expect(filterChatInputSuggestions(items, '   ')).toBe(items);
  });

  it('matches title, subtitle, content and keywords case-insensitively', () => {
    expect(ids(filterChatInputSuggestions(items, 'ОБЪЯСНИ'))).toEqual(['1']);
    expect(ids(filterChatInputSuggestions(items, 'словами'))).toEqual(['1']);
    expect(ids(filterChatInputSuggestions(items, 'напиши'))).toEqual(['2']);
    expect(ids(filterChatInputSuggestions(items, 'PYTHON'))).toEqual(['2']);
    expect(filterChatInputSuggestions(items, 'нет такого')).toEqual([]);
  });

  it('trims the query but keeps inner spaces', () => {
    expect(ids(filterChatInputSuggestions(items, '  простыми словами '))).toEqual(['1']);
  });
});

describe('pickVisibleSuggestions', () => {
  const contextual: ChatInputSuggestion[] = [
    { id: 'r1', title: 'R1', content: 'r', source: 'rag' },
    { id: 'm1', title: 'M1', content: 'm', source: 'mcp' },
  ];
  const general: ChatInputSuggestion[] = [1, 2, 3, 4].map((n) => ({ id: `g${n}`, title: `G${n}`, content: 'g', source: 'general' as const }));

  afterEach(() => jest.restoreAllMocks());

  it('shows filtered matches in order when the user types', () => {
    const result = pickVisibleSuggestions([...general, ...contextual], 'g', 2);

    expect(result).toHaveLength(2);
    expect(ids(result)).toEqual(['g1', 'g2']);
  });

  it('prefers contextual suggestions for an empty input and fills the rest with general ones', () => {
    const result = pickVisibleSuggestions([...general, ...contextual], '', 4);

    expect(result).toHaveLength(4);
    expect(new Set(ids(result)).has('r1')).toBe(true);
    expect(new Set(ids(result)).has('m1')).toBe(true);
    expect(result.filter((s) => s.source === 'general')).toHaveLength(2);
  });

  it('never returns more than requested and handles empty catalogs', () => {
    expect(pickVisibleSuggestions(contextual, '', 1)).toHaveLength(1);
    expect(pickVisibleSuggestions([], '', 3)).toEqual([]);
    expect(pickVisibleSuggestions(general, '', 0)).toEqual([]);
  });

  it('shuffles without losing or duplicating items', () => {
    jest.spyOn(Math, 'random').mockReturnValue(0.5);
    const source = [1, 2, 3, 4, 5];

    const shuffled = shuffleSuggestions(source);

    expect([...shuffled].sort()).toEqual(source);
    expect(source).toEqual([1, 2, 3, 4, 5]);
  });
});

describe('follow-up settings', () => {
  beforeEach(() => localStorage.clear());

  it('has sensible defaults', () => {
    expect(loadFollowUpSettings()).toEqual({ followUpAutoGenerate: true, followUpShowScope: 'last', followUpClickAction: 'insert' });
  });

  it('round-trips saved values', () => {
    saveFollowUpAutoGenerate(false);
    saveFollowUpShowScope('all');
    saveFollowUpClickAction('send');

    expect(loadFollowUpSettings()).toEqual({ followUpAutoGenerate: false, followUpShowScope: 'all', followUpClickAction: 'send' });
    expect(localStorage.getItem(FOLLOW_UP_AUTO_GENERATE_KEY)).toBe('false');
  });

  it('falls back to defaults for unknown stored values', () => {
    localStorage.setItem(FOLLOW_UP_SHOW_SCOPE_KEY, 'everything');
    localStorage.setItem(FOLLOW_UP_CLICK_ACTION_KEY, 'execute');
    localStorage.setItem(FOLLOW_UP_AUTO_GENERATE_KEY, 'maybe');

    expect(loadFollowUpSettings()).toEqual({ followUpAutoGenerate: false, followUpShowScope: 'last', followUpClickAction: 'insert' });
  });
});

describe('plugin verdict', () => {
  const invoke = (partial: Partial<PluginInvokeResult>): PluginInvokeResult => ({ success: true, plugin_id: 'cash-flow', result: {}, ...partial });

  it.each([
    [0, '0:00'],
    [59, '0:59'],
    [60, '1:00'],
    [125.6, '2:06'],
    [3600, '60:00'],
    [-5, '0:00'],
  ])('formats %p seconds as %p', (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });

  it('unwraps a doubly nested result', () => {
    expect(normalizeInvokeResult({ result: { status: 'ok' } } as never)).toEqual({ status: 'ok' });
    expect(normalizeInvokeResult({ status: 'ok' })).toEqual({ status: 'ok' });
    expect(normalizeInvokeResult({ result: [1] } as never)).toEqual({ result: [1] });
    expect(normalizeInvokeResult(undefined)).toEqual({});
    expect(normalizeInvokeResult('x' as never)).toEqual({});
  });

  it('prefers the verdict markdown from the service', () => {
    expect(buildVerdictMarkdown({ verdict_markdown: '  ## Вердикт  ', status: 'ok' })).toBe('## Вердикт');
  });

  it('builds a fallback report from the findings and steps', () => {
    const md = buildVerdictMarkdown({
      status: 'ok',
      message: 'Проверено 3 листа',
      steps: { parse: true, llm: false },
      deterministic_findings: [{ type: 'FORMULA', where: 'A1', detail: 'ссылка на пустую ячейку' }, { where: 'B2' }],
    });

    expect(md).toContain('# Аудит денежного потока');
    expect(md).toContain('**Статус:** `ok`');
    expect(md).toContain('Проверено 3 листа');
    expect(md).toContain('- parse: ok');
    expect(md).toContain('- llm: —');
    expect(md).toContain('- **[FORMULA]** A1 — ссылка на пустую ячейку');
    expect(md).toContain('- **[?]** B2');
  });

  it('caps the number of findings at 50', () => {
    const findings = Array.from({ length: 80 }, (_, i) => ({ type: 'T', where: `R${i}` }));

    const md = buildVerdictMarkdown({ status: 'ok', deterministic_findings: findings });

    expect(md.match(/- \*\*\[T\]\*\*/g)).toHaveLength(50);
  });

  it('explains the degraded mode without findings', () => {
    expect(buildVerdictMarkdown({ status: 'degraded' })).toContain('Полный вердикт недоступен без LLM');
  });

  it('is empty for an empty payload', () => {
    expect(buildVerdictMarkdown({})).toBe('');
    expect(buildVerdictMarkdown(undefined)).toBe('');
  });

  it('extracts markdown from the response in priority order', () => {
    expect(extractMarkdownFromResponse(invoke({ markdown: '  готово  ' }))).toBe('готово');
    expect(extractMarkdownFromResponse(invoke({ result: { status: 'ok', verdict_markdown: 'из результата' } }))).toBe('из результата');
    expect(extractMarkdownFromResponse(invoke({ result: { result: { verdict_markdown: 'вложенный' } } as never }))).toBe('вложенный');
    expect(extractMarkdownFromResponse(invoke({ result: {} }))).toBe('');
  });

  it('summarises statuses for the UI', () => {
    expect(summaryForResultStatus('ok')).toContain('Аудит завершён — отчёт ниже');
    expect(summaryForResultStatus('degraded')).toContain('degraded-режиме');
    expect(summaryForResultStatus()).toBe('Аудит завершён со статусом: unknown.');
    expect(notificationForResultStatus('ok')).toBe('Аудит завершён');
    expect(notificationForResultStatus('degraded')).toContain('degraded');
    expect(notificationForResultStatus('failed')).toBe('Статус: failed');
    expect(notificationForResultStatus()).toBe('Статус: done');
  });
});

describe('mapChainStepsFromMeta', () => {
  it('returns undefined for empty or invalid metadata', () => {
    for (const raw of [null, undefined, [], 'x', {}, 5]) {
      expect(mapChainStepsFromMeta(raw)).toBeUndefined();
    }
    expect(mapChainStepsFromMeta([null, 'x', 5])).toBeUndefined();
  });

  it('maps snake_case and camelCase fields with defaults', () => {
    const steps = mapChainStepsFromMeta([
      { agent_id: '3', agent_name: 'Юрист', content: 'ответ', reasoning_content: '  думаю  ' },
      { agentId: 4, agentName: 'Аналитик', content: 'второй', reasoning: '   ' },
      {},
    ]);

    expect(steps).toEqual([
      { agentId: 3, agentName: 'Юрист', content: 'ответ', reasoning: 'думаю', documentSearch: undefined },
      { agentId: 4, agentName: 'Аналитик', content: 'второй', reasoning: undefined, documentSearch: undefined },
      { agentId: 0, agentName: 'Агент', content: '', reasoning: undefined, documentSearch: undefined },
    ]);
  });

  it('maps the document search trace', () => {
    const [step] = mapChainStepsFromMeta([
      {
        content: 'x',
        document_search: {
          query: 'налоги',
          hits: [
            { file: 'a.pdf', anchor: '#1', relevance: '0.9', content: 'текст', chunk_index: 2, document_id: 7, store: 'kb' },
            'junk',
            { file: 'b.pdf' },
          ],
        },
      },
    ])!;

    expect(step.documentSearch).toEqual({
      query: 'налоги',
      sourceFiles: ['a.pdf', 'b.pdf'],
      hits: [
        { file: 'a.pdf', anchor: '#1', relevance: 0.9, content: 'текст', chunkIndex: 2, documentId: 7, store: 'kb' },
        { file: 'b.pdf', anchor: '', relevance: 0, content: '', chunkIndex: 0, documentId: 0, store: '' },
      ],
    });
  });

  it('prefers explicit source files and understands camelCase traces', () => {
    const [explicit, camel] = mapChainStepsFromMeta([
      { document_search: { query: 'q', source_files: ['x.docx'], hits: [{ file: 'y.docx' }] } },
      { documentSearch: { sourceFiles: ['z.docx'], hits: [{ file: 'z.docx', chunkIndex: 5, documentId: 9 }] } },
    ])!;

    expect(explicit.documentSearch?.sourceFiles).toEqual(['x.docx']);
    expect(camel.documentSearch?.hits[0]).toMatchObject({ chunkIndex: 5, documentId: 9 });
  });

  it('drops an empty trace', () => {
    const [step] = mapChainStepsFromMeta([{ content: 'x', document_search: { query: '   ', hits: [], source_files: [] } }, { content: 'y', document_search: 'junk' }])!;

    expect(step.documentSearch).toBeUndefined();
  });
});
