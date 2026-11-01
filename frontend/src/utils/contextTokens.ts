import type { InlineAttachment } from '../components/ChatInputBar';
import type { Message } from '../contexts/AppContext';
import { getInlineAttachmentExtension } from './inlineAttachmentRules';

/** Событие смены модели (ModelSelector → счётчик контекста). */
export const MODEL_PATH_CHANGED_EVENT = 'astrachatModelPathChanged';

/** Настройки модели сохранены (ModelsSettings / SettingsPage). */
export const MODEL_SETTINGS_CHANGED_EVENT = 'astrachatModelSettingsChanged';

export interface ModelContextMeta {
  path: string;
  name?: string;
  context_size?: number | null;
  display_name?: string;
  extra?: Record<string, unknown>;
}

const DEFAULT_CONTEXT_LIMIT = 8192;

/** Эвристика: ~4 символа на токен (смешанный RU/EN текст). */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  const baseTokens = Math.ceil(text.length / 4);
  const specialChars = (text.match(/[^\w\sа-яё]/gi) || []).length;
  const newlines = (text.match(/\n/g) || []).length;
  return baseTokens + Math.ceil(specialChars / 2) + Math.ceil(newlines / 2);
}

/** Формат как в LibreChat: 115500 → «115,5 тыс.», 2048 → «2048». */
export function formatContextTokenLimit(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '—';
  if (value >= 1_000_000) {
    const m = value / 1_000_000;
    const rounded = Math.round(m * 10) / 10;
    const str = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1).replace('.', ',');
    return `${str} млн`;
  }
  if (value >= 10_000) {
    const k = value / 1_000;
    const rounded = Math.round(k * 10) / 10;
    const str = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1).replace('.', ',');
    return `${str} тыс.`;
  }
  return value.toLocaleString('ru-RU');
}

export function formatContextTokenCount(value: number): string {
  if (!Number.isFinite(value) || value < 0) return '0';
  if (value >= 10_000) {
    return formatContextTokenLimit(value);
  }
  return value.toLocaleString('ru-RU');
}

const CONTEXT_SIZE_KEYS = [
  'context_size',
  'context_length',
  'max_model_len',
  'max_context_tokens',
  'max_context_length',
  'n_ctx',
  'context_window',
] as const;

function coercePositiveInt(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
    return Math.floor(value);
  }
  if (typeof value === 'string') {
    const s = value.trim().replace(/\s/g, '').replace(',', '.');
    const m = s.match(/^(\d+(?:\.\d+)?)([kKmM])?$/);
    if (m) {
      const num = parseFloat(m[1]);
      if (!Number.isFinite(num) || num <= 0) return null;
      const mult = m[2]?.toLowerCase() === 'm' ? 1_000_000 : m[2] ? 1_000 : 1;
      return Math.floor(num * mult);
    }
    const n = parseInt(s, 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  }
  return null;
}

/** Размер окна модели: только поля из API / extra (без эвристик по имени). */
export function resolveModelNativeContextSize(model?: ModelContextMeta | null): number | null {
  if (!model) return null;
  const direct = coercePositiveInt(model.context_size);
  if (direct) return direct;
  const extra = model.extra;
  if (extra) {
    for (const key of CONTEXT_SIZE_KEYS) {
      const parsed = coercePositiveInt(extra[key]);
      if (parsed) return parsed;
    }
  }
  return null;
}

function findModelMeta(models: ModelContextMeta[], modelPath: string): ModelContextMeta | undefined {
  return models.find((m) => m.path === modelPath);
}

/**
 * Эффективный лимит контекста для отправки в LLM:
 * min(окно модели, пользовательский context_size из настроек).
 */
export function resolveEffectiveContextLimit(
  modelPath: string | null | undefined,
  models: ModelContextMeta[],
  configuredContextSize?: number | null,
  loadedModelCtx?: number | null,
): number {
  const configured =
    typeof configuredContextSize === 'number' && configuredContextSize > 0
      ? configuredContextSize
      : null;

  if (!modelPath) {
    return configured ?? DEFAULT_CONTEXT_LIMIT;
  }

  const row = findModelMeta(models, modelPath);
  const modelWindow = resolveModelNativeContextSize(row)
    ?? (typeof loadedModelCtx === 'number' && loadedModelCtx > 0 ? loadedModelCtx : null);

  if (modelWindow != null && configured != null) {
    return Math.min(modelWindow, configured);
  }
  return modelWindow ?? configured ?? DEFAULT_CONTEXT_LIMIT;
}

/** Минимальный лимит среди выбранных моделей (multi-LLM). */
export function resolveMultiModelContextLimit(
  modelPaths: string[],
  models: ModelContextMeta[],
  configuredContextSize?: number | null,
): number {
  const paths = modelPaths.filter(Boolean);
  if (paths.length === 0) {
    return resolveEffectiveContextLimit(null, models, configuredContextSize);
  }
  return Math.min(
    ...paths.map((p) => resolveEffectiveContextLimit(p, models, configuredContextSize)),
  );
}

function messageAttachmentTokens(msg: Message): number {
  if (!msg.inlineAttachments?.length) return 0;
  let total = 0;
  for (const att of msg.inlineAttachments) {
    const tokenEstimate = (att as { tokenEstimate?: number }).tokenEstimate;
    if (typeof tokenEstimate === 'number' && Number.isFinite(tokenEstimate) && tokenEstimate > 0) {
      total += Math.ceil(tokenEstimate);
    } else if (att.contentType === 'text' && att.preview) {
      total += estimateTokens(att.preview);
    } else if (att.contentType === 'text') {
      total += estimateAttachmentTokensByName(att.name, att.size);
    } else if (att.contentType === 'image') {
      total += 512;
    }
  }
  return total;
}

const TEXT_ATTACHMENT_MAX_TOKENS = 20_000;

/**

* Оценка токенов inline-вложения по имени файла (fallback, когда нет tokenEstimate).
* Текстовые форматы — от размера файла (байт ≈ символы), бинарные — типичное
* количество извлечённого текста на страницу (допускается 20 МБ на файл).
 */
export function estimateAttachmentTokensByName(name: string | undefined, size?: number): number {
  const ext = getInlineAttachmentExtension(name || '');
  switch (ext) {
    case '.txt':
    case '.md':
    case '.log': {
      const chars = typeof size === 'number' && size > 0 ? size : 5_000;
      return Math.min(Math.ceil(chars / 4), TEXT_ATTACHMENT_MAX_TOKENS);
    }
    case '.rtf':
      return 3_000;
    case '.pdf':
      return 6_000;
    case '.docx':
    case '.docm':
    case '.doc':
      return 4_000;
    case '.xlsx':
    case '.xls':
    case '.xlsm':
    case '.ppt':
    case '.pptx':
      return 3_000;
    default:
      return 1_000;
  }
}

function messageCoreTokens(msg: Message): number {
  let total = estimateTokens(msg.content || '');
  if (msg.reasoningContent) {
    total += estimateTokens(msg.reasoningContent);
  }
  if (msg.multiLLMResponses?.length) {
    for (const slot of msg.multiLLMResponses) {
      total += estimateTokens(slot.content || '');
      const alts = slot.alternativeResponses ?? [];
      const idx = slot.currentResponseIndex ?? 0;
      if (alts[idx] && alts[idx] !== slot.content) {
        total += estimateTokens(alts[idx]);
      }
    }
  } else if (msg.alternativeResponses?.length) {
    const idx = msg.currentResponseIndex ?? 0;
    const alt = msg.alternativeResponses[idx];
    if (alt && alt !== msg.content) {
      total += estimateTokens(alt);
    }
  }
  return total + 4;
}

function messageRagContentTokens(msg: Message): number {
  if (!msg.documentSearch?.hits?.length) return 0;
  return msg.documentSearch.hits.reduce(
    (sum, hit) => sum + estimateTokens(hit.content || ''),
    0,
  );
}

function messageTextTokens(msg: Message): number {
  return messageCoreTokens(msg) + messageAttachmentTokens(msg) + messageRagContentTokens(msg);
}

export interface ConversationTokenBreakdown {
  memoryTokens: number;
  currentUserTokens: number;
  generationTokens: number;
  ragContentTokens: number;
  draftTokens: number;
  attachmentTokens: number;
}

function isMessageStreaming(msg: Message): boolean {
  // Завершённый ответ (есть длительность) не считаем стримом, даже если флаг залип.
  if (msg.generationDurationSec != null && msg.generationDurationSec > 0) {
    return false;
  }
  if (msg.isStreaming) return true;
  return Boolean(msg.multiLLMResponses?.some((r) => r.isStreaming));
}

/**
 * Ход «открыт», пока ждём/стримим ответ на последний user.
 * После complete весь диалог уходит в память, «текущее» = только черновик.
 */
function isOpenUserTurn(messages: Message[], lastUserIdx: number): boolean {
  if (lastUserIdx < 0) return false;
  const after = messages.slice(lastUserIdx + 1);
  const assistantAfter = after.filter((m) => m.role === 'assistant');
  if (assistantAfter.length === 0) return true;
  return assistantAfter.some(isMessageStreaming);
}

/** Разбивает переписку на память / текущий запрос / генерацию / RAG-фрагменты без пересечений. */
export function analyzeConversationTokens(
  messages: Message[],
  draftText = '',
  inlineAttachments: InlineAttachment[] = [],
): ConversationTokenBreakdown {
  let lastUserIdx = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'user') {
      lastUserIdx = i;
      break;
    }
  }

  const openTurn = isOpenUserTurn(messages, lastUserIdx);

  let memoryTokens = 0;
  let currentUserTokens = 0;
  let generationTokens = 0;
  let ragContentTokens = 0;
  let attachmentTokens = 0;

  for (let i = 0; i < messages.length; i += 1) {
    const msg = messages[i];
    ragContentTokens += messageRagContentTokens(msg);
    attachmentTokens += messageAttachmentTokens(msg);
    const core = messageCoreTokens(msg);

    if (!openTurn) {
      // Ход завершён: user+assistant уже история следующего запроса.
      memoryTokens += core;
      continue;
    }

    if (msg.role === 'assistant') {
      if (lastUserIdx >= 0 && i > lastUserIdx) {
        generationTokens += core;
      } else if (lastUserIdx < 0) {
        generationTokens += core;
      } else {
        memoryTokens += core;
      }
    } else if (lastUserIdx >= 0 && i === lastUserIdx) {
      currentUserTokens += core;
    } else {
      memoryTokens += core;
    }
  }

  const draftTokens = estimateTokens(draftText || '');
  attachmentTokens += countInlineAttachmentsTokens(inlineAttachments);
  currentUserTokens += draftTokens;

  return {
    memoryTokens,
    currentUserTokens,
    generationTokens,
    ragContentTokens,
    draftTokens,
    attachmentTokens,
  };
}

export function countMessagesTokens(messages: Message[]): number {
  return messages.reduce((sum, msg) => sum + messageTextTokens(msg), 0);
}

export function countInlineAttachmentsTokens(inlineFiles: InlineAttachment[]): number {
  if (!inlineFiles.length) return 0;
  const contentTokens = inlineFiles.reduce((sum, f) => {
    if (f.contentType === 'image') return sum + 512;
    return sum + estimateTokens(f.content || '');
  }, 0);
  // Обёртки бэкенда: [Прикреплённый документ], [Вопрос пользователя], [filename]
  return contentTokens + 40;
}

/** Превышение оценки над окном модели (только индикатор, без блокировки отправки). */
export function isContextRequestOverLimit(currentTokens: number, maxTokens: number): boolean {
  return currentTokens > Math.max(1, maxTokens);
}

export interface ChatContextUsage {
  currentTokens: number;
  maxTokens: number;
  percent: number;
  messageTokens: number;
  draftTokens: number;
  attachmentTokens: number;
  overheadTokens?: number;
  segments?: ContextUsageSegment[];
}

export interface ContextUsageSegment {
  id: string;
  label: string;
  tokens: number;
  active?: boolean;
  /** Показывать строку даже при 0 токенов (функция включена). */
  showWhenEmpty?: boolean;
}

export const CONTEXT_SEGMENT_COLORS: Record<string, string> = {
  context_instructions: '#9e9e9e',
  project: '#388e3c',
  agent: '#7b1fa2',
  skills: '#00838f',
  artifacts: '#5c6bc0',
  rag_rules: '#f57c00',
  mcp_tools: '#ba68c8',
  memory: '#5d4037',
  rag_content: '#ff9800',
  attachments: '#78909c',
  current_user: '#0288d1',
  generation: '#6d4c41',
};

export const CONTEXT_SEGMENT_ORDER: string[] = [
  'context_instructions',
  'project',
  'agent',
  'skills',
  'artifacts',
  'rag_rules',
  'mcp_tools',
  'memory',
  'rag_content',
  'attachments',
  'current_user',
  'generation',
];

export interface ContextFeatureFlags {
  hasAgent?: boolean;
  hasMcp?: boolean;
  hasRag?: boolean;
  hasProject?: boolean;
  hasSkills?: boolean;
  hasArtifacts?: boolean;
}

export function mergeContextUsageWithOverhead(
  conversation: ConversationTokenBreakdown,
  overheadSegments: ContextUsageSegment[],
  maxTokens: number,
  features: ContextFeatureFlags = {},
): ChatContextUsage {
  const overheadById = new Map(overheadSegments.map((s) => [s.id, s]));

  const convSegments: ContextUsageSegment[] = [
    {
      id: 'memory',
      label: 'Память (история диалога)',
      tokens: conversation.memoryTokens,
      active: true,
      showWhenEmpty: true,
    },
    {
      id: 'rag_content',
      label: 'RAG (найденные фрагменты)',
      tokens: conversation.ragContentTokens,
      active: features.hasRag ?? false,
      showWhenEmpty: features.hasRag ?? false,
    },
    {
      id: 'attachments',
      label: 'Вложения / документы',
      tokens: conversation.attachmentTokens,
      active: true,
      showWhenEmpty: conversation.attachmentTokens > 0,
    },
    {
      id: 'current_user',
      label: 'Текущее сообщение',
      tokens: conversation.currentUserTokens,
      active: true,
      showWhenEmpty: true,
    },
    {
      id: 'generation',
      label: 'Генерация (ответ модели)',
      tokens: conversation.generationTokens,
      active: true,
      showWhenEmpty: true,
    },
  ];

  const overheadIds = [
    'context_instructions',
    'project',
    'agent',
    'skills',
    'artifacts',
    'rag_rules',
    'mcp_tools',
  ];
  const overheadOrdered: ContextUsageSegment[] = overheadIds.map((id) => {
    const existing = overheadById.get(id);
    if (existing) {
      return {
        ...existing,
        label:
          id === 'context_instructions'
            ? 'Системный промпт'
            : id === 'project'
              ? 'Проект'
              : id === 'agent'
                ? 'Агент'
                : id === 'skills'
                  ? 'Скиллы'
                  : id === 'artifacts'
                    ? 'Artifacts'
                : id === 'rag_rules'
                  ? 'RAG (правила)'
                  : id === 'mcp_tools'
                    ? 'MCP (инструменты)'
                    : existing.label,
        showWhenEmpty:
          (id === 'agent' && features.hasAgent) ||
          (id === 'skills' && features.hasSkills) ||
          (id === 'artifacts' && features.hasArtifacts) ||
          (id === 'mcp_tools' && features.hasMcp) ||
          (id === 'rag_rules' && features.hasRag) ||
          (id === 'project' && features.hasProject) ||
          id === 'context_instructions',
      };
    }
    if (id === 'context_instructions') {
      return {
        id,
        label: 'Системный промпт',
        tokens: 0,
        active: true,
        showWhenEmpty: true,
      };
    }
    if (id === 'project' && features.hasProject) {
      return { id, label: 'Проект', tokens: 0, active: true, showWhenEmpty: true };
    }
    if (id === 'agent' && features.hasAgent) {
      return { id, label: 'Агент', tokens: 0, active: true, showWhenEmpty: true };
    }
    if (id === 'skills' && features.hasSkills) {
      return { id, label: 'Скиллы', tokens: 0, active: true, showWhenEmpty: true };
    }
    if (id === 'artifacts' && features.hasArtifacts) {
      return { id, label: 'Artifacts', tokens: 0, active: true, showWhenEmpty: true };
    }
    if (id === 'rag_rules' && features.hasRag) {
      return { id, label: 'RAG (правила)', tokens: 0, active: true, showWhenEmpty: true };
    }
    if (id === 'mcp_tools' && features.hasMcp) {
      return { id, label: 'MCP (инструменты)', tokens: 0, active: true, showWhenEmpty: true };
    }
    return null;
  }).filter(Boolean) as ContextUsageSegment[];

  const segmentMap = new Map<string, ContextUsageSegment>();
  for (const seg of [...overheadOrdered, ...convSegments]) {
    segmentMap.set(seg.id, seg);
  }

  const segments = CONTEXT_SEGMENT_ORDER.map((id) => segmentMap.get(id)).filter(
    (seg): seg is ContextUsageSegment =>
      Boolean(seg && (seg.tokens > 0 || seg.showWhenEmpty || seg.active === false)),
  );

  const overheadActive = overheadSegments
    .filter((s) => s.active !== false && s.tokens > 0)
    .reduce((sum, s) => sum + s.tokens, 0);

  const conversationTotal =
    conversation.memoryTokens +
    conversation.currentUserTokens +
    conversation.generationTokens +
    conversation.ragContentTokens +
    conversation.attachmentTokens;

  const currentTokens = overheadActive + conversationTotal;
  const safeMax = Math.max(1, maxTokens);
  const percent = Math.round((currentTokens / safeMax) * 100);

  return {
    maxTokens: safeMax,
    currentTokens,
    percent,
    messageTokens:
      conversation.memoryTokens + conversation.generationTokens + conversation.ragContentTokens,
    draftTokens: conversation.draftTokens,
    attachmentTokens: conversation.attachmentTokens,
    overheadTokens: overheadActive,
    segments,
  };
}

export function computeChatContextUsage(params: {
  messages: Message[];
  draftText?: string;
  inlineAttachments?: InlineAttachment[];
  maxTokens: number;
}): ConversationTokenBreakdown {
  return analyzeConversationTokens(
    params.messages,
    params.draftText,
    params.inlineAttachments ?? [],
  );
}
