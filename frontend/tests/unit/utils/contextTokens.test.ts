import type { Message } from '@/contexts/AppContext';
import {
  analyzeConversationTokens,
  countInlineAttachmentsTokens,
  countMessagesTokens,
  estimateAttachmentTokensByName,
  estimateTokens,
  formatContextTokenCount,
  formatContextTokenLimit,
  isContextRequestOverLimit,
  resolveEffectiveContextLimit,
  resolveModelNativeContextSize,
  resolveMultiModelContextLimit,
  type ModelContextMeta,
} from '@/utils/contextTokens';

jest.mock('@/utils/inlineAttachmentRules', () => jest.requireActual('./inlineAttachmentRules'));

// Разделитель тысяч зависит от ICU Node (nbsp, запятая): сравниваем только цифры.
const digits = (s: string) => s.replace(/[^\d]/g, '');

function msg(partial: Partial<Message>): Message {
  return { id: 'm', role: 'user', content: '', ...partial } as Message;
}

describe('estimateTokens', () => {
  it('is zero for empty text', () => {
    expect(estimateTokens('')).toBe(0);
    expect(estimateTokens(undefined as unknown as string)).toBe(0);
  });

  it('uses about 4 characters per token for plain text', () => {
    expect(estimateTokens('abcd')).toBe(1);
    expect(estimateTokens('abcde')).toBe(2);
    expect(estimateTokens('a'.repeat(400))).toBe(100);
  });

  it('adds weight for special characters and newlines', () => {
    expect(estimateTokens('a.b.')).toBe(1 + 1);
    expect(estimateTokens('ab\ncd')).toBe(2 + 0 + 1);
  });

  it('counts Cyrillic letters as ordinary characters', () => {
    expect(estimateTokens('приве')).toBe(2);
  });

  it('grows monotonically with length', () => {
    let prev = 0;
    for (let n = 1; n < 200; n += 7) {
      const t = estimateTokens('слово '.repeat(n));
      expect(t).toBeGreaterThanOrEqual(prev);
      prev = t;
    }
  });
});

describe('formatContextTokenLimit', () => {
  it.each([0, -5, NaN, Infinity])('shows a dash for %p', (value) => {
    expect(formatContextTokenLimit(value)).toBe('—');
  });

  it('formats thousands and millions with a decimal comma', () => {
    expect(formatContextTokenLimit(115_500)).toBe('115,5 тыс.');
    expect(formatContextTokenLimit(32_000)).toBe('32 тыс.');
    expect(formatContextTokenLimit(1_000_000)).toBe('1 млн');
    expect(formatContextTokenLimit(1_250_000)).toBe('1,3 млн');
  });

  it('keeps small values as plain digits', () => {
    expect(digits(formatContextTokenLimit(2048))).toBe('2048');
  });
});

describe('formatContextTokenCount', () => {
  it('shows zero for invalid or negative values', () => {
    expect(formatContextTokenCount(-1)).toBe('0');
    expect(formatContextTokenCount(NaN)).toBe('0');
  });

  it('uses short format from 10 000', () => {
    expect(formatContextTokenCount(12_000)).toBe('12 тыс.');
    expect(digits(formatContextTokenCount(9_999))).toBe('9999');
    expect(formatContextTokenCount(0)).toBe('0');
  });
});

describe('resolveModelNativeContextSize', () => {
  it('returns null without a model or any size', () => {
    expect(resolveModelNativeContextSize(null)).toBeNull();
    expect(resolveModelNativeContextSize({ path: 'm' })).toBeNull();
  });

  it('prefers the direct context_size', () => {
    expect(resolveModelNativeContextSize({ path: 'm', context_size: 4096, extra: { n_ctx: 999 } })).toBe(4096);
  });

  it.each([
    ['context_length', 8192],
    ['max_model_len', 16384],
    ['max_context_tokens', 1000],
    ['n_ctx', 2048],
    ['context_window', 100_000],
  ])('reads %s from extra', (key, value) => {
    expect(resolveModelNativeContextSize({ path: 'm', extra: { [key]: value } })).toBe(value);
  });

  it('parses strings with k/m suffixes and decimal commas', () => {
    expect(resolveModelNativeContextSize({ path: 'm', extra: { n_ctx: '32k' } })).toBe(32_000);
    expect(resolveModelNativeContextSize({ path: 'm', extra: { n_ctx: '1.5M' } })).toBe(1_500_000);
    expect(resolveModelNativeContextSize({ path: 'm', extra: { n_ctx: '1,5k' } })).toBe(1500);
    expect(resolveModelNativeContextSize({ path: 'm', extra: { n_ctx: ' 8 192 ' } })).toBe(8192);
  });

  it('ignores zero, negative and garbage values', () => {
    expect(resolveModelNativeContextSize({ path: 'm', context_size: 0, extra: { n_ctx: -1 } })).toBeNull();
    expect(resolveModelNativeContextSize({ path: 'm', extra: { n_ctx: 'abc', context_length: null } })).toBeNull();
  });

  it('floors fractional numbers', () => {
    expect(resolveModelNativeContextSize({ path: 'm', context_size: 4096.9 })).toBe(4096);
  });
});

describe('resolveEffectiveContextLimit', () => {
  const models: ModelContextMeta[] = [
    { path: 'a', context_size: 32_000 },
    { path: 'b', context_size: 8_000 },
    { path: 'nosize' },
  ];

  it('uses the default limit when nothing is known', () => {
    expect(resolveEffectiveContextLimit(null, [])).toBe(8192);
    expect(resolveEffectiveContextLimit('unknown', models)).toBe(8192);
  });

  it('uses the configured size without a model', () => {
    expect(resolveEffectiveContextLimit(null, models, 4000)).toBe(4000);
  });

  it('uses the model window when no size is configured', () => {
    expect(resolveEffectiveContextLimit('a', models)).toBe(32_000);
  });

  it('takes the smaller of model window and configured size', () => {
    expect(resolveEffectiveContextLimit('a', models, 16_000)).toBe(16_000);
    expect(resolveEffectiveContextLimit('a', models, 64_000)).toBe(32_000);
  });

  it('falls back to the loaded model context, then to the configured size', () => {
    expect(resolveEffectiveContextLimit('nosize', models, null, 12_000)).toBe(12_000);
    expect(resolveEffectiveContextLimit('nosize', models, 5000)).toBe(5000);
  });

  it('ignores non-positive configured values', () => {
    expect(resolveEffectiveContextLimit('a', models, 0)).toBe(32_000);
    expect(resolveEffectiveContextLimit('a', models, -1)).toBe(32_000);
  });

  it('uses the smallest limit across several models', () => {
    expect(resolveMultiModelContextLimit(['a', 'b'], models)).toBe(8_000);
    expect(resolveMultiModelContextLimit(['a', 'b'], models, 4_000)).toBe(4_000);
  });

  it('falls back to a single-model result for an empty selection', () => {
    expect(resolveMultiModelContextLimit([], models, 3000)).toBe(3000);
    expect(resolveMultiModelContextLimit(['', ''], models)).toBe(8192);
  });
});

describe('estimateAttachmentTokensByName', () => {
  it.each([
    ['a.pdf', undefined, 6000],
    ['a.docx', undefined, 4000],
    ['a.xlsx', undefined, 3000],
    ['a.pptx', undefined, 3000],
    ['a.rtf', undefined, 3000],
    ['a.png', undefined, 1000],
    ['noext', undefined, 1000],
    [undefined, undefined, 1000],
  ])('%p -> %p', (name, size, expected) => {
    expect(estimateAttachmentTokensByName(name as string | undefined, size as number | undefined)).toBe(expected);
  });

  it('derives text sizes from bytes and caps them', () => {
    expect(estimateAttachmentTokensByName('a.txt', 4000)).toBe(1000);
    expect(estimateAttachmentTokensByName('a.md')).toBe(1250);
    expect(estimateAttachmentTokensByName('a.log', 10_000_000)).toBe(20_000);
  });
});

describe('analyzeConversationTokens', () => {
  const user = (content: string, extra: Partial<Message> = {}) => msg({ role: 'user', content, ...extra });
  const assistant = (content: string, extra: Partial<Message> = {}) =>
    msg({ role: 'assistant', content, ...extra });

  it('is all zeros for an empty chat', () => {
    expect(analyzeConversationTokens([])).toEqual({
      memoryTokens: 0,
      currentUserTokens: 0,
      generationTokens: 0,
      ragContentTokens: 0,
      draftTokens: 0,
      attachmentTokens: 0,
    });
  });

  it('puts a finished conversation entirely into memory', () => {
    const result = analyzeConversationTokens([user('привет'), assistant('здравствуйте')]);

    expect(result.memoryTokens).toBe(estimateTokens('привет') + 4 + estimateTokens('здравствуйте') + 4);
    expect(result.currentUserTokens).toBe(0);
    expect(result.generationTokens).toBe(0);
  });

  it('treats the last user message as current while waiting for an answer', () => {
    const result = analyzeConversationTokens([user('первый'), assistant('ответ'), user('второй')]);

    expect(result.currentUserTokens).toBe(estimateTokens('второй') + 4);
    expect(result.memoryTokens).toBe(estimateTokens('первый') + 4 + estimateTokens('ответ') + 4);
    expect(result.generationTokens).toBe(0);
  });

  it('counts a streaming answer as generation', () => {
    const result = analyzeConversationTokens([
      user('вопрос'),
      assistant('частичный отв', { isStreaming: true }),
    ]);

    expect(result.generationTokens).toBe(estimateTokens('частичный отв') + 4);
    expect(result.currentUserTokens).toBe(estimateTokens('вопрос') + 4);
  });

  it('does not treat a finished answer as streaming even if the flag is stuck', () => {
    const result = analyzeConversationTokens([
      user('вопрос'),
      assistant('готово', { isStreaming: true, generationDurationSec: 3 }),
    ]);

    expect(result.generationTokens).toBe(0);
    expect(result.memoryTokens).toBeGreaterThan(0);
  });

  it('adds the draft to the current user tokens', () => {
    const result = analyzeConversationTokens([], 'черновик текста');

    expect(result.draftTokens).toBe(estimateTokens('черновик текста'));
    expect(result.currentUserTokens).toBe(result.draftTokens);
  });

  it('counts attachments and RAG hits separately', () => {
    const result = analyzeConversationTokens(
      [
        user('q', { inlineAttachments: [{ name: 'a.pdf', contentType: 'text' }] as never }),
        assistant('a', { documentSearch: { query: 'q', sourceFiles: [], hits: [{ content: 'x'.repeat(40) } as never] } }),
      ],
      '',
      [{ contentType: 'image' } as never],
    );

    expect(result.attachmentTokens).toBe(6000 + 512 + 40);
    expect(result.ragContentTokens).toBe(estimateTokens('x'.repeat(40)));
  });

  it('includes reasoning and the selected alternative in the core tokens', () => {
    const base = analyzeConversationTokens([assistant('основа')]).memoryTokens;
    const withExtra = analyzeConversationTokens([
      assistant('основа', {
        reasoningContent: 'мысли мысли',
        alternativeResponses: ['другой вариант'],
        currentResponseIndex: 0,
      }),
    ]).memoryTokens;

    expect(withExtra).toBe(base + estimateTokens('мысли мысли') + estimateTokens('другой вариант'));
  });
});

describe('countMessagesTokens / attachments', () => {
  it('sums text, attachments and RAG hits per message', () => {
    const total = countMessagesTokens([
      msg({ content: 'abcd' }),
      msg({ role: 'assistant', content: 'abcd', inlineAttachments: [{ name: 'i.png', contentType: 'image' }] as never }),
    ]);

    expect(total).toBe(1 + 4 + (1 + 4 + 512));
  });

  it('prefers an explicit token estimate, then the preview, then a size guess', () => {
    const tokens = (att: object) =>
      countMessagesTokens([msg({ inlineAttachments: [att] as never })]) - estimateTokens('') - 4;

    expect(tokens({ name: 'a.pdf', contentType: 'text', tokenEstimate: 10.2 })).toBe(11);
    expect(tokens({ name: 'a.pdf', contentType: 'text', preview: 'abcd' })).toBe(1);
    expect(tokens({ name: 'a.pdf', contentType: 'text' })).toBe(6000);
  });

  it('counts inline files with a fixed wrapper overhead', () => {
    expect(countInlineAttachmentsTokens([])).toBe(0);
    expect(countInlineAttachmentsTokens([{ contentType: 'image' } as never])).toBe(512 + 40);
    expect(countInlineAttachmentsTokens([{ contentType: 'text', content: 'abcd' } as never])).toBe(1 + 40);
  });
});

describe('isContextRequestOverLimit', () => {
  it('compares tokens with the window', () => {
    expect(isContextRequestOverLimit(101, 100)).toBe(true);
    expect(isContextRequestOverLimit(100, 100)).toBe(false);
  });

  it('never treats a zero limit as unlimited', () => {
    expect(isContextRequestOverLimit(2, 0)).toBe(true);
    expect(isContextRequestOverLimit(1, 0)).toBe(false);
  });
});
