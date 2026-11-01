/**
 * Общий каркас для тестов UnifiedChatPage (F2).
 *
 * Модуль регистрирует jest.mock для контекстов, хуков и тяжёлых дочерних компонентов
 * (лёгкие заглушки, которые выставляют колбэки страницы в виде кнопок) и экспортирует
 * изменяемый объект `hx` — управляемое «состояние мира» для страницы.
 *
 * ВАЖНО: файл НЕ должен импортировать UnifiedChatPage: jest.mock должны отработать
 * до первого require страницы (тест сначала импортирует каркас, потом страницу).
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
import React from 'react';

// ---------------------------------------------------------------------------
// Управляемое состояние
// ---------------------------------------------------------------------------
export const mockHx: any = {};
export const hx = mockHx;

export function resetHarness(): void {
  const chat = {
    id: 'c1',
    title: 'Новый чат',
    projectId: undefined as string | undefined,
    messagesUnloaded: false,
  };
  mockHx.chat = chat;
  mockHx.messages = [];
  mockHx.project = null;
  mockHx.state = {
    isInitialized: true,
    loadingChatIds: [] as string[],
    modelSettings: { context_size: 4096 },
    currentModel: null,
    currentChatId: 'c1',
  };
  mockHx.actions = {
    clearMessages: jest.fn(),
    showNotification: jest.fn(),
    setSpeaking: jest.fn(),
    setRecording: jest.fn(),
    updateMessage: jest.fn(),
    patchMessageFields: jest.fn(),
    getCurrentMessages: jest.fn(() => mockHx.messages),
    getCurrentChat: jest.fn(() => mockHx.chat),
    createChat: jest.fn(() => 'new-chat'),
    setCurrentChat: jest.fn(),
    updateChatTitle: jest.fn(),
    getProjectById: jest.fn(() => mockHx.project),
    setLoading: jest.fn(),
    branchChatAtMessage: jest.fn(async () => 'branch-id'),
  };
  mockHx.socket = {
    sendMessage: jest.fn(),
    regenerateResponse: jest.fn(),
    regenerateMultiLlmSlot: jest.fn(),
    isConnected: true,
    isConnecting: false,
    stopGeneration: jest.fn(),
  };
  mockHx.auth = {
    user: { username: 'ivan', full_name: 'Иван Петров' },
    token: 'tok',
  };
  mockHx.navigate = jest.fn();
  // hooks
  mockHx.featureAccess = { canUseAgents: true };
  mockHx.myAgent = null;
  mockHx.rag = {
    shouldBlockRagSend: jest.fn(() => false),
    blockMessage: '',
    status: { memory: { reindexing: false } },
    memoryRagEnabled: true,
  };
  mockHx.activeSkills = [];
  mockHx.artifactsIndicator = { active: false, tooltip: '' };
  mockHx.activeMcpServers = [];
  mockHx.activeMcpTools = [];
  mockHx.contextUsage = { used: 0 };
  mockHx.followUp = jest.fn();
  mockHx.workZoneMode = 'default';
  mockHx.virt = {
    scrollToIndex: jest.fn(),
    scrollerEl: null as HTMLDivElement | null,
    lastProps: null as any,
    noHandle: false,
  };
  mockHx.noAnchor = false;
  mockHx.exports = { exportMessageContent: jest.fn() };
  mockHx.inlineImage = {
    prepareInlineImageFile: jest.fn(async (f: File) => ({
      dataUrl: 'data:image/png;base64,AAAA',
      filename: f.name,
      wasCompressed: false,
      originalSize: f.size,
      compressedSize: f.size,
    })),
    formatFileSize: jest.fn((n: number) => `${n}B`),
    dataUrlToFile: jest.fn((_u: string, name: string) => new File(['x'], name, { type: 'image/png' })),
    getClipboardImageFile: jest.fn(() => null),
  };
  mockHx.applyAgent = {
    applyAgentMcpToChat: jest.fn(),
    applyStoredAgentMcpToChat: jest.fn(),
  };
  mockHx.misc = {
    clearActiveAgent: jest.fn(),
    clearActiveSkills: jest.fn(),
    disableChatArtifactsToolsForChat: jest.fn(),
    logChatAttach: jest.fn(),
    logChatAttachError: jest.fn(),
  };
}
resetHarness();

// ---------------------------------------------------------------------------
// jest.mock: контексты, роутер, сеть
// ---------------------------------------------------------------------------
jest.mock('react-router-dom', () => ({
  useNavigate: () => mockHx.navigate,
}));

jest.mock('../../contexts/AuthContext', () => ({
  useAuth: () => mockHx.auth,
}));

jest.mock('../../contexts/AppContext', () => ({
  useAppContext: () => ({ state: mockHx.state }),
  useAppActions: () => mockHx.actions,
}));

jest.mock('../../contexts/SocketContext', () => ({
  useSocket: () => mockHx.socket,
}));

jest.mock('../../config/api', () => ({
  getApiUrl: (p: string) => `http://api${p}`,
  getWsUrl: (p: string) => `ws://api${p}`,
  API_ENDPOINTS: {
    MESSAGE_FEEDBACK: '/api/messages',
    UPDATE_MESSAGE: '/api/messages',
    VOICE_SYNTHESIZE: '/api/voice/synthesize',
    DOCUMENTS_ATTACH: '/api/documents/attach',
  },
  getAuthFetchHeaders: () => ({ Authorization: 'Bearer stored' }),
  authFetch: (...a: any[]) => require('@tests/unit/helpers/authFetchMock').authFetchMock(...a),
}));

jest.mock('../../utils/debugReact185', () => ({
  astra185Mark: jest.fn(),
  astra185MarkRender: jest.fn(),
}));

// ---------------------------------------------------------------------------
// jest.mock: хуки
// ---------------------------------------------------------------------------
jest.mock('../../hooks/useFeatureAccess', () => ({
  useFeatureAccess: () => mockHx.featureAccess,
}));
jest.mock('../../hooks/useChatInputAgentIndicators', () => ({
  useMyAgentSelection: () => mockHx.myAgent,
}));
jest.mock('../../hooks/useRagReindexStatus', () => ({
  useRagReindexStatus: () => mockHx.rag,
}));
jest.mock('../../hooks/useActiveSkillIndicators', () => ({
  useActiveSkillIndicators: () => mockHx.activeSkills,
}));
jest.mock('../../hooks/useChatInputArtifactsIndicator', () => ({
  useChatInputArtifactsIndicator: () => mockHx.artifactsIndicator,
}));
jest.mock('../../hooks/useChatContextUsage', () => ({
  useChatContextUsage: (args: any) => {
    mockHx.lastContextUsageArgs = args;
    return mockHx.contextUsage;
  },
}));
jest.mock('../../mcp/hooks/useChatInputMcpIndicators', () => ({
  useChatInputMcpIndicators: () => mockHx.activeMcpServers,
}));
jest.mock('../../mcp/hooks/useMcpStreamingTools', () => ({
  useMcpStreamingTools: () => ({ activeMcpTools: mockHx.activeMcpTools }),
}));
jest.mock('../../hooks/useFollowUpSuggestions', () => ({
  useFollowUpSuggestions: (args: any) => mockHx.followUp(args),
}));
jest.mock('../../hooks/useWorkZoneBgMode', () => ({
  useWorkZoneBgMode: () => mockHx.workZoneMode,
}));

// ---------------------------------------------------------------------------
// jest.mock: утилиты
// ---------------------------------------------------------------------------
jest.mock('../../utils/inlineImage', () => ({
  prepareInlineImageFile: (...a: any[]) => mockHx.inlineImage.prepareInlineImageFile(...a),
  formatFileSize: (...a: any[]) => mockHx.inlineImage.formatFileSize(...a),
  dataUrlToFile: (...a: any[]) => mockHx.inlineImage.dataUrlToFile(...a),
  getClipboardImageFile: (...a: any[]) => mockHx.inlineImage.getClipboardImageFile(...a),
}));
jest.mock('../../utils/chatAttachDebug', () => ({
  logChatAttach: (...a: any[]) => mockHx.misc.logChatAttach(...a),
  logChatAttachError: (...a: any[]) => mockHx.misc.logChatAttachError(...a),
}));
jest.mock('../../utils/exportMessageContent', () => ({
  exportMessageContent: (...a: any[]) => mockHx.exports.exportMessageContent(...a),
  markdownToPlainText: (s: string) => `plain:${s}`,
  markdownToRichHtml: (s: string) => `<p>${s}</p>`,
}));
jest.mock('../../utils/clearActiveAgent', () => ({
  clearActiveAgent: (...a: any[]) => mockHx.misc.clearActiveAgent(...a),
}));
jest.mock('../../utils/skillSelectionStorage', () => ({
  clearActiveSkills: (...a: any[]) => mockHx.misc.clearActiveSkills(...a),
}));
jest.mock('../../utils/artifactsSelectionStorage', () => ({
  disableChatArtifactsToolsForChat: (...a: any[]) =>
    mockHx.misc.disableChatArtifactsToolsForChat(...a),
}));
jest.mock('../../utils/applyAgentMcp', () => ({
  applyAgentMcpToChat: (...a: any[]) => mockHx.applyAgent.applyAgentMcpToChat(...a),
  applyStoredAgentMcpToChat: (...a: any[]) => mockHx.applyAgent.applyStoredAgentMcpToChat(...a),
}));
jest.mock('../../chat/getChatInputSuggestions', () => ({
  getChatInputSuggestions: (ids: string[], rag: boolean) => [
    { id: 'sg1', title: `mcp:${ids.join(',')}|rag:${String(rag)}` },
  ],
}));

// ---------------------------------------------------------------------------
// jest.mock: react-virtuoso — простой список без виртуализации
// ---------------------------------------------------------------------------
jest.mock('react-virtuoso', () => {
  const R = require('react');
  const Virtuoso = R.forwardRef((props: any, ref: any) => {
    mockHx.virt.lastProps = props;
    R.useImperativeHandle(ref, () => (mockHx.virt.noHandle ? null : { scrollToIndex: mockHx.virt.scrollToIndex }));
    const holder = R.useRef(null);
    const { scrollerRef } = props;
    R.useLayoutEffect(() => {
      mockHx.virt.scrollerEl = holder.current;
      scrollerRef?.(holder.current);
      return () => {
        scrollerRef?.(null);
      };
    }, [scrollerRef]);
    const Footer = props.components?.Footer;
    return (
      <div data-testid="virtuoso" ref={holder}>
        {props.data.map((m: any, i: number) => (
          <div key={props.computeItemKey(i, m)} data-testid="virt-item">
            {props.itemContent(i, m)}
          </div>
        ))}
        {Footer ? <Footer context={props.context} /> : null}
      </div>
    );
  });
  return { Virtuoso };
});

// ---------------------------------------------------------------------------
// jest.mock: дочерние компоненты (заглушки)
// ---------------------------------------------------------------------------
jest.mock('../../components/MessageRenderer', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="renderer" data-streaming={String(Boolean(p.isStreaming))} data-mid={p.messageId}>
      {p.content}
      {p.onSendMessage ? (
        <button data-testid={`renderer-send-${p.messageId}`} onClick={() => p.onSendMessage('prompt-from-renderer')}>
          r-send
        </button>
      ) : null}
    </div>
  ),
}));
jest.mock('../../components/DocumentSearchPanel', () => ({
  DocumentSearchPanel: (p: any) => <div data-testid="doc-search">{p.trace?.query}</div>,
}));
jest.mock('../../components/UserAvatar', () => ({
  __esModule: true,
  default: (p: any) => <span data-testid="user-avatar">{p.username}</span>,
}));
jest.mock('../../mcp/components/McpToolCallsPanel', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="mcp-panel">{p.toolCalls.length}</div>,
}));
jest.mock('../../mcp/components/McpLiveToolsIndicator', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="mcp-live">{p.tools.length}</div>,
}));
jest.mock('../../components/InlineAttachmentsList', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="inline-list">
      {p.files.map((f: any, i: number) => (
        <span key={i} data-testid="inline-file">
          {f.name}
          <button
            data-testid={`expand-${f.name}`}
            onClick={() => p.onImageExpand?.(f.imageSrc || `resolved:${f.name}`, f.name)}
          >
            expand
          </button>
        </span>
      ))}
    </div>
  ),
}));
jest.mock('../../components/InlineImageLightbox', () => ({
  __esModule: true,
  default: (p: any) =>
    p.open ? (
      <div data-testid="lightbox" data-src={p.src} data-name={p.name}>
        <button data-testid="lightbox-close" onClick={p.onClose}>
          x
        </button>
      </div>
    ) : null,
}));
jest.mock('../../components/ImageGenerationPlaceholder', () => ({
  __esModule: true,
  default: () => <div data-testid="img-gen" />,
}));
jest.mock('../../components/MessageFeedbackBar', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="feedback-bar" data-compact={String(Boolean(p.compact))} data-disabled={String(Boolean(p.disabled))}>
      <button data-testid="fb-like" onClick={p.onLike}>like</button>
      <button data-testid="fb-dislike" onClick={() => p.onDislikeSubmit({ tags: ['t1'], comment: 'cm' })}>dislike</button>
      <button data-testid="fb-clear" onClick={p.onClear}>clear</button>
    </div>
  ),
}));
jest.mock('../../components/MessageMoreActionsMenu', () => ({
  __esModule: true,
  default: (p: any) => (
    <div
      data-testid="more-menu"
      data-show-branch={String(Boolean(p.showBranch))}
      data-branch-disabled={String(Boolean(p.branchDisabled))}
      data-show-export={String(Boolean(p.showExport))}
      data-export-disabled={String(Boolean(p.exportDisabled))}
      data-has-export={String(typeof p.onExport === 'function')}
    >
      <button data-testid="mm-edit" onClick={p.onEdit}>edit</button>
      <button data-testid="mm-read" onClick={p.onReadAloud}>read</button>
      <button data-testid="mm-branch" onClick={p.onBranch}>branch</button>
      {p.onExport ? <button data-testid="mm-export" onClick={() => p.onExport('md')}>export</button> : null}
    </div>
  ),
}));
jest.mock('../../components/MessageFollowUpSuggestions', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="follow-up" data-disabled={String(Boolean(p.disabled))}>
      {(p.suggestions || []).map((s: any, i: number) => (
        <button key={i} data-testid={`follow-up-${i}`} onClick={() => p.onSelect(s.prompt || s.title)}>
          {s.title}
        </button>
      ))}
    </div>
  ),
}));
jest.mock('../../components/chat/ThinkingShimmerText', () => ({
  __esModule: true,
  default: (p: any) => <span data-testid="shimmer">{p.children}</span>,
}));
jest.mock('../../components/ModelSelector', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="model-selector">
      <button data-testid="model-selector-pick" onClick={() => p.onModelSelect('some/model')}>pick</button>
    </div>
  ),
}));
jest.mock('../../components/AgentSelector', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="agent-selector">
      <button data-testid="agent-selector-pick" onClick={() => p.onModelSelect()}>pick</button>
    </div>
  ),
}));
jest.mock('../../components/MessageNavigationBar', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="nav-bar" data-count={p.messages.length}>
      <button data-testid="nav-0" onClick={() => p.onNavigate(0)}>n0</button>
      <button data-testid="nav-1" onClick={() => p.onNavigate(1)}>n1</button>
      <button data-testid="nav-99" onClick={() => p.onNavigate(99)}>n99</button>
    </div>
  ),
}));
jest.mock('../../components/right_bar', () => ({
  ASTRA_INSERT_CHAT_TEXT: 'astra_insert_chat_text',
  ASTRA_INSERT_CHAT_TEXT_KEY: 'astra_insert_chat_text_pending',
}));
jest.mock('../../components/ShareConfirmDialog', () => ({
  __esModule: true,
  default: (p: any) =>
    p.open ? (
      <div data-testid="share-dialog" data-count={p.selectedCount}>
        <button
          data-testid="share-confirm"
          onClick={() => {
            Promise.resolve(p.onConfirm()).then(
              (url: string) => {
                mockHx.shareResult = { ok: true, url };
              },
              (err: unknown) => {
                mockHx.shareResult = { ok: false, err };
              },
            );
          }}
        >
          ok
        </button>
        <button data-testid="share-close" onClick={p.onClose}>close</button>
      </div>
    ) : null,
}));
jest.mock('../../components/ChatInputBar', () => ({
  __esModule: true,
  default: (p: any) => {
    mockHx.lastInputBarProps = p;
    return (
      <div
        data-testid="input-bar"
        ref={mockHx.noAnchor ? undefined : p.toolsMenuAnchorRef}
        data-placeholder={p.placeholder}
        data-send-disabled={String(Boolean(p.sendDisabled))}
        data-input-disabled={String(Boolean(p.inputDisabled))}
        data-attach-disabled={String(Boolean(p.attachDisabled))}
        data-settings-disabled={String(Boolean(p.settingsDisabled))}
        data-voice-disabled={String(Boolean(p.voiceDisabled))}
        data-show-stop={String(Boolean(p.showStopButton))}
        data-uploading={String(Boolean(p.isUploading))}
        data-style={p.styleVariant}
      >
        <input
          data-testid="input-field"
          ref={p.inputRef}
          value={p.value}
          disabled={p.inputDisabled}
          onChange={(e) => p.onChange(e.target.value)}
          onKeyPress={p.onKeyPress}
          onPaste={p.onPaste}
        />
        <input
          data-testid="input-file"
          type="file"
          ref={p.fileInputRef}
          onChange={(e) => p.onFileSelect(e.target.files)}
        />
        <button data-testid="ib-attach" onClick={p.onAttachClick}>attach</button>
        <button data-testid="ib-settings" onClick={p.onSettingsClick}>settings</button>
        <button data-testid="ib-stop" onClick={p.onStopClick}>stop</button>
        <button data-testid="ib-send" onClick={p.onSendClick}>send</button>
        <button data-testid="ib-voice" onClick={p.onVoiceClick}>voice</button>
        <button data-testid="ib-file-empty" onClick={() => p.onFileSelect(null)}>file-empty</button>
        {(p.inlineFiles || []).map((f: any, i: number) => (
          <button key={i} data-testid={`ib-remove-${i}`} onClick={() => p.onInlineFileRemove(i)}>
            {f.name}
          </button>
        ))}
        <div data-testid="ib-library">{p.libraryBadge}</div>
        <div data-testid="ib-suggestions">{p.inputSuggestions}</div>
        <div data-testid="ib-extra">{p.extraActions}</div>
        <div data-testid="ib-counter">{p.contextCounter}</div>
      </div>
    );
  },
}));
jest.mock('../../components/ChatGearAgentsPanel', () => ({
  __esModule: true,
  default: () => <div data-testid="gear-agents" />,
}));
jest.mock('../../components/ChatGearArtifactsPanel', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="gear-artifacts" data-chat={p.chatId} />,
}));
jest.mock('../../components/ChatGearMcpPanel', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="gear-mcp" data-chat={p.chatId} />,
}));
jest.mock('../../components/ChatGearSkillsPanel', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="gear-skills" data-chat={p.chatId} />,
}));
jest.mock('../../components/ChatGearKnowledgeBasesPanel', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="gear-kb" data-chat={p.chatId} />,
}));
jest.mock('../../components/ChatGearGenerationPanel', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="gear-generation" data-chat={p.chatId} />,
}));
jest.mock('../../components/ChatGearCodingPanel', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="gear-coding" data-chat={p.chatId} />,
}));
// Загрузка весов модели (ensure/load) в тестах страницы не нужна: модель считается готовой.
jest.mock('../../utils/applyAgentServer', () => ({
  isSelectedModelWeightsReady: () => true,
  ensureSelectedModelWeights: async () => ({ ok: true }),
  loadAgentModelOnly: async () => ({ ok: true }),
  MODEL_WEIGHTS_ENSURE_EVENT: 'astrachatModelWeightsEnsure',
}));
jest.mock('../../components/VoiceChatDialog', () => ({
  __esModule: true,
  default: (p: any) =>
    p.open ? (
      <div data-testid="voice-dialog">
        <button data-testid="voice-close" onClick={p.onClose}>vc</button>
      </div>
    ) : null,
}));
jest.mock('../../components/ChatInputStatusCluster', () => ({
  __esModule: true,
  default: (p: any) => {
    mockHx.lastClusterProps = p;
    return (
      <div data-testid="cluster" data-library={String(Boolean(p.libraryActive))} data-agent={p.myAgentName ?? ''}>
        <button data-testid="cl-library" onClick={p.onLibraryToggle}>lib</button>
        {p.onAgentToggle ? <button data-testid="cl-agent" onClick={p.onAgentToggle}>agent</button> : null}
        {p.onSkillsToggle ? <button data-testid="cl-skills" onClick={p.onSkillsToggle}>skills</button> : null}
        <button data-testid="cl-mcp" onClick={p.onMcpClick}>mcp</button>
        {p.onArtifactsToggle ? <button data-testid="cl-artifacts" onClick={p.onArtifactsToggle}>art</button> : null}
      </div>
    );
  },
}));
jest.mock('../../components/ChatContextUsagePopover', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="ctx-usage" data-label={p.modelLabel ?? ''} />,
}));
jest.mock('../../components/ChatInputSuggestions', () => ({
  __esModule: true,
  default: (p: any) => {
    mockHx.lastSuggestionsProps = p;
    return (
      <div data-testid="input-suggestions" data-disabled={String(Boolean(p.disabled))}>
        <button data-testid="sg-pick" onClick={() => p.onSelect('подсказка')}>pick</button>
      </div>
    );
  },
}));
jest.mock('../../components/TopErrorBanner', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="top-banner">
      <span data-testid="top-banner-msg">{p.message}</span>
      <button data-testid="top-banner-close" onClick={p.onClose}>x</button>
    </div>
  ),
}));
jest.mock('../../components/WorkZoneStarrySky', () => ({
  __esModule: true,
  default: () => <div data-testid="starry" />,
}));
jest.mock('../../components/WorkZoneSnowfall', () => ({
  __esModule: true,
  default: () => <div data-testid="snowfall" />,
}));


// ---------------------------------------------------------------------------
// fetch-мок
// ---------------------------------------------------------------------------
export function fakeResponse(
  data: unknown,
  opts: { ok?: boolean; status?: number; text?: string; badJson?: boolean; headers?: Record<string, string> } = {},
): any {
  const ok = opts.ok ?? true;
  return {
    ok,
    status: opts.status ?? (ok ? 200 : 500),
    statusText: ok ? 'OK' : 'ERR',
    headers: { get: (k: string) => opts.headers?.[k.toLowerCase()] ?? null },
    json: async () => {
      if (opts.badJson) throw new Error('bad json');
      return data;
    },
    text: async () => (opts.text !== undefined ? opts.text : JSON.stringify(data)),
    blob: async () => new Blob(['audio']),
  };
}

/** Ставит global.fetch: маршрут по подстроке URL; /api/models/available по умолчанию никогда не отвечает. */
export function installFetch(
  routes: Record<string, (url: string, init?: any) => any> = {},
): jest.Mock {
  const fn = jest.fn(async (url: string, init?: any) => {
    for (const key of Object.keys(routes)) {
      if (String(url).includes(key)) return routes[key](String(url), init);
    }
    if (String(url).includes('/api/models/available')) return new Promise(() => {}); // по умолчанию — «вечная» загрузка (без setState после теста)
    return fakeResponse({});
  });
  (global as any).fetch = fn;
  return fn;
}

// ---------------------------------------------------------------------------
// Вспомогательные фабрики данных
// ---------------------------------------------------------------------------
let mockSeq = 0;
export function msg(partial: Record<string, unknown> = {}): any {
  mockSeq += 1;
  return {
    id: `m${mockSeq}`,
    role: 'assistant',
    content: 'ответ',
    timestamp: '2025-01-02T10:20:00.000Z',
    ...partial,
  };
}
export function userMsg(partial: Record<string, unknown> = {}): any {
  return msg({ role: 'user', content: 'вопрос', ...partial });
}

/** Подставляет в jsdom-элемент геометрию прокрутки. */
export function setScrollGeometry(
  el: HTMLElement,
  g: { scrollHeight: number; scrollTop: number; clientHeight: number },
): void {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: g.scrollHeight });
  Object.defineProperty(el, 'scrollTop', { configurable: true, writable: true, value: g.scrollTop });
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: g.clientHeight });
}

export const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

export default React;
