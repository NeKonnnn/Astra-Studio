import {
  countInlineAttachmentsTokens,
  estimateTokens,
  formatContextTokenCount,
  formatContextTokenLimit,
  isContextRequestOverLimit,
  resolveEffectiveContextLimit,
  resolveModelNativeContextSize,
} from './contextTokens';

/** toLocaleString('ru-RU') разделяет разряды неразрывным пробелом — сравниваем без пробелов. */
const noSpaces = (s: string) => s.replace(/\s/g, '');

describe('estimateTokens', () => {
  it('пустая строка — 0 токенов', () => {
    expect(estimateTokens('')).toBe(0);
  });

  it('~4 символа на токен для простого текста', () => {
    expect(estimateTokens('abcd')).toBe(1);
    expect(estimateTokens('abcdefgh')).toBe(2);
  });

  it('округляет вверх', () => {
    expect(estimateTokens('abcde')).toBe(2);
  });

  it('спецсимволы добавляют поправку', () => {
    expect(estimateTokens('!!!!')).toBeGreaterThan(estimateTokens('abcd'));
  });

  it('переводы строк добавляют поправку', () => {
    expect(estimateTokens('a\nb\nc\nd')).toBeGreaterThan(estimateTokens('abcdefg'));
  });

  it('монотонно растёт с длиной текста', () => {
    expect(estimateTokens('слово '.repeat(100))).toBeGreaterThan(estimateTokens('слово '.repeat(10)));
  });
});

describe('formatContextTokenLimit', () => {
  it.each([0, -5, NaN, Infinity])('некорректное значение %p → «—»', (value) => {
    expect(formatContextTokenLimit(value)).toBe('—');
  });

  it('до 10 000 — обычное число', () => {
    expect(noSpaces(formatContextTokenLimit(2048))).toBe('2048');
  });

  it.each([
    [10_000, '10 тыс.'],
    [32_768, '32,8 тыс.'],
    [115_500, '115,5 тыс.'],
    [128_000, '128 тыс.'],
  ])('%p → %s', (value, expected) => {
    expect(formatContextTokenLimit(value)).toBe(expected);
  });

  it.each([
    [1_000_000, '1 млн'],
    [1_500_000, '1,5 млн'],
    [2_000_000, '2 млн'],
  ])('%p → %s', (value, expected) => {
    expect(formatContextTokenLimit(value)).toBe(expected);
  });
});

describe('formatContextTokenCount', () => {
  it.each([-1, NaN, Infinity])('некорректное %p → «0»', (value) => {
    expect(formatContextTokenCount(value)).toBe('0');
  });

  it('малые значения — полным числом', () => {
    expect(formatContextTokenCount(0)).toBe('0');
    expect(noSpaces(formatContextTokenCount(1234))).toBe('1234');
  });

  it('от 10 000 — сокращённо', () => {
    expect(formatContextTokenCount(12_000)).toBe('12 тыс.');
  });
});

describe('resolveModelNativeContextSize', () => {
  const base = { path: 'p' };

  it.each([undefined, null])('нет модели (%p) → null', (model) => {
    expect(resolveModelNativeContextSize(model)).toBeNull();
  });

  it('берёт context_size напрямую', () => {
    expect(resolveModelNativeContextSize({ ...base, context_size: 8192 })).toBe(8192);
  });

  it('дробное значение округляется вниз', () => {
    expect(resolveModelNativeContextSize({ ...base, context_size: 4096.9 })).toBe(4096);
  });

  it.each([0, -1, null, undefined])('context_size=%p игнорируется', (value) => {
    expect(resolveModelNativeContextSize({ ...base, context_size: value as number | null })).toBeNull();
  });

  it.each([
    ['context_length', 16384],
    ['max_model_len', 32768],
    ['max_context_tokens', 4096],
    ['max_context_length', 2048],
    ['n_ctx', 1024],
    ['context_window', 65536],
  ])('ищет в extra.%s', (key, value) => {
    expect(resolveModelNativeContextSize({ ...base, extra: { [key]: value } })).toBe(value);
  });

  it.each([
    ['32k', 32_000],
    ['32K', 32_000],
    ['1m', 1_000_000],
    ['8 192', 8192],
    ['4096', 4096],
    ['1,5k', 1500],
  ])('разбирает строку %j как %p', (raw, expected) => {
    expect(resolveModelNativeContextSize({ ...base, extra: { n_ctx: raw } })).toBe(expected);
  });

  it.each(['abc', '', '-5', '0'])('мусорная строка %j → null', (raw) => {
    expect(resolveModelNativeContextSize({ ...base, extra: { n_ctx: raw } })).toBeNull();
  });

  it('прямое поле приоритетнее extra', () => {
    expect(
      resolveModelNativeContextSize({ ...base, context_size: 1000, extra: { n_ctx: 9999 } }),
    ).toBe(1000);
  });
});

describe('resolveEffectiveContextLimit', () => {
  const models = [
    { path: 'a', context_size: 32_768 },
    { path: 'b', extra: { max_model_len: 8192 } },
    { path: 'c' },
  ];

  it('без модели — настройка пользователя либо дефолт 8192', () => {
    expect(resolveEffectiveContextLimit(null, models, 4000)).toBe(4000);
    expect(resolveEffectiveContextLimit(undefined, models)).toBe(8192);
  });

  it('берёт окно модели', () => {
    expect(resolveEffectiveContextLimit('a', models)).toBe(32_768);
  });

  it('минимум из окна модели и пользовательской настройки', () => {
    expect(resolveEffectiveContextLimit('a', models, 4096)).toBe(4096);
    expect(resolveEffectiveContextLimit('b', models, 100_000)).toBe(8192);
  });

  it('если окно модели неизвестно — используется настройка', () => {
    expect(resolveEffectiveContextLimit('c', models, 6000)).toBe(6000);
  });

  it('окно загруженной модели — запасной источник', () => {
    expect(resolveEffectiveContextLimit('c', models, null, 16_000)).toBe(16_000);
  });

  it('данные каталога приоритетнее окна загруженной модели', () => {
    expect(resolveEffectiveContextLimit('a', models, null, 1000)).toBe(32_768);
  });

  it('ничего не известно — дефолт 8192', () => {
    expect(resolveEffectiveContextLimit('c', models)).toBe(8192);
    expect(resolveEffectiveContextLimit('unknown', models)).toBe(8192);
  });

  it.each([0, -10, null, undefined])('некорректная настройка %p игнорируется', (configured) => {
    expect(resolveEffectiveContextLimit('a', models, configured)).toBe(32_768);
  });
});

describe('isContextRequestOverLimit', () => {
  it('строго больше лимита', () => {
    expect(isContextRequestOverLimit(100, 100)).toBe(false);
    expect(isContextRequestOverLimit(101, 100)).toBe(true);
  });

  it('нулевой и отрицательный лимит считается как 1', () => {
    expect(isContextRequestOverLimit(1, 0)).toBe(false);
    expect(isContextRequestOverLimit(2, 0)).toBe(true);
    expect(isContextRequestOverLimit(2, -50)).toBe(true);
  });
});

describe('countInlineAttachmentsTokens', () => {
  const file = (over: Record<string, unknown>) =>
    ({ name: 'f', contentType: 'text', content: '', ...over }) as never;

  it('без вложений — 0', () => {
    expect(countInlineAttachmentsTokens([])).toBe(0);
  });

  it('обёртка бэкенда добавляет 40 токенов', () => {
    expect(countInlineAttachmentsTokens([file({ content: '' })])).toBe(40);
  });

  it('изображение — фиксированные 512 токенов', () => {
    expect(countInlineAttachmentsTokens([file({ contentType: 'image', content: 'x'.repeat(10_000) })])).toBe(
      512 + 40,
    );
  });

  it('текст считается эвристикой, суммируется по файлам', () => {
    const a = file({ content: 'abcd' });
    const b = file({ content: 'abcdefgh' });
    expect(countInlineAttachmentsTokens([a, b])).toBe(estimateTokens('abcd') + estimateTokens('abcdefgh') + 40);
  });
});
