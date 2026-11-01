import {
  IMAGE_GEN_PRESET_STORAGE_KEY,
  readSelectedImageGenPresetId,
  resolveImageGenPresetLabel,
  writeSelectedImageGenPresetId,
} from '@/utils/imageGenerationPresets';
import { extractCreationsFromChats, mergeCreationItems, resolveCreationPreviewSrc } from '@/utils/imageCreations';
import {
  RAG_UPLOAD_CONCURRENCY,
  commitRagUploadUiUpdate,
  createRagPendingUploads,
  dedupeFilesByContent,
  formatRagApiDetail,
  getRagFileTypeLabel,
  mapWithConcurrency,
  mergeRagDocumentsById,
  parseRagDuplicateUploadMessage,
  parseRagUploadDocumentId,
  removeRagPendingUploads,
} from '@/utils/ragPendingUpload';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a), getApiUrl: (endpoint: string) => `http://api.test${endpoint}` }));

const file = (name: string, content = 'x') => new File([content], name);

describe('pending uploads', () => {
  it('creates a unique client id per file', () => {
    const pending = createRagPendingUploads([file('a.pdf', 'abc'), file('a.pdf', 'abc')]);

    expect(pending.map((p) => [p.filename, p.size])).toEqual([['a.pdf', 3], ['a.pdf', 3]]);
    expect(pending[0].clientId).not.toBe(pending[1].clientId);
    expect(pending[0].clientId.endsWith('-a.pdf')).toBe(true);
  });

  it('removes finished uploads only', () => {
    const pending = [{ clientId: '1', filename: 'a', size: 1 }, { clientId: '2', filename: 'b', size: 1 }];

    expect(removeRagPendingUploads(pending, ['1'])).toEqual([pending[1]]);
    expect(removeRagPendingUploads(pending, [])).toBe(pending);
    expect(removeRagPendingUploads(pending, ['ghost'])).toEqual(pending);
  });

  it.each([
    ['report.PDF', 'PDF'], ['a.docx', 'Word'], ['a.doc', 'Word'], ['a.docm', 'Word'], ['t.xlsx', 'Excel'], ['t.csv', 'Excel'], ['n.md', 'TXT'],
    ['n.txt', 'TXT'], ['n.rtf', 'RTF'], ['p.JPG', 'Image (OCR)'], ['p.webp', 'Image (OCR)'], ['archive.zip', 'File'], ['noext', 'File'], ['', 'File'],
  ])('labels %p as %p', (name, label) => {
    expect(getRagFileTypeLabel(name)).toBe(label);
  });

  it('labels a file named only by its extension by the name itself (known issue)', () => {
    // Для имени без точки split('.').pop() возвращает само имя: файл «pdf» получает метку PDF.
    expect(getRagFileTypeLabel('pdf')).toBe('PDF');
  });
});

describe('upload response parsing', () => {
  it.each([
    [42, 42], [{ document_id: 7 }, 7], [{ documentId: '8' }, 8], [{ id: 9 }, 9], [{ id: 0, document_id: 5 }, 5],
    [{ document: { id: 11 } }, 11], [{ data: { result: { document_id: 12 } } }, 12],
  ])('finds the id in %p', (payload, id) => {
    expect(parseRagUploadDocumentId(payload)).toBe(id);
  });

  it.each([null, undefined, 0, -3, NaN, 'abc', {}, { id: 'x' }, { id: -1 }, { id: '' }, { document: null }, [] as unknown])('finds nothing in %p', (payload) => {
    expect(parseRagUploadDocumentId(payload)).toBeNull();
  });

  it('formats FastAPI details of every shape', () => {
    expect(formatRagApiDetail('Файл слишком большой', 'fb')).toBe('Файл слишком большой');
    expect(formatRagApiDetail([{ msg: 'поле обязательно' }, 'просто', { loc: [] }], 'fb')).toBe('поле обязательно; просто; [object Object]');
    expect(formatRagApiDetail({ message: 'из message' }, 'fb')).toBe('из message');
    expect(formatRagApiDetail({ msg: 'из msg' }, 'fb')).toBe('из msg');
    expect(formatRagApiDetail({ detail: 'вложенный' }, 'fb')).toBe('вложенный');
    expect(formatRagApiDetail('   ', 'fb')).toBe('fb');
    expect(formatRagApiDetail(null, 'fb')).toBe('fb');
    expect(formatRagApiDetail({}, 'fb')).toBe('fb');
  });
});

describe('duplicate upload message', () => {
  it('ignores other statuses', () => {
    expect(parseRagDuplicateUploadMessage(200, { detail: 'Документ уже подгружен в базу' })).toBeNull();
    expect(parseRagDuplicateUploadMessage(500, {})).toBeNull();
  });

  it('recognises the structured duplicate error', () => {
    const payload = { detail: { code: 'duplicate_document', message: 'Такой документ уже есть' } };

    expect(parseRagDuplicateUploadMessage(409, payload)).toBe('Такой документ уже есть');
    expect(parseRagDuplicateUploadMessage(422, payload, 'a.pdf')).toBe('«a.pdf»: Такой документ уже есть');
  });

  it('recognises the text form and prefixes the file name only once', () => {
    expect(parseRagDuplicateUploadMessage(409, { detail: 'Документ уже подгружен в базу' }, 'a.pdf')).toBe('«a.pdf»: Документ уже подгружен в базу');
    expect(parseRagDuplicateUploadMessage(409, { detail: 'a.pdf уже подгружен в базу' }, 'a.pdf')).toBe('a.pdf уже подгружен в базу');
    expect(parseRagDuplicateUploadMessage(409, 'duplicate_document')).toBe('duplicate_document');
  });

  it('uses the fallback text for an object without a message', () => {
    expect(parseRagDuplicateUploadMessage(409, { detail: { code: 'duplicate_document' } })).toBe('Упс... Такой документ уже подгружен в базу');
  });

  it('does not take other 409/422 conflicts for duplicates', () => {
    expect(parseRagDuplicateUploadMessage(409, { detail: 'Конфликт версий' })).toBeNull();
    expect(parseRagDuplicateUploadMessage(422, { detail: [{ msg: 'field required' }] })).toBeNull();
  });
});

describe('document lists and concurrency', () => {
  it('merges lists by id with the server fields winning', () => {
    const prev = [{ id: 1, name: 'локальный' }, { id: 2, name: 'старый' }];
    const incoming = [{ id: 2, name: 'серверный' }, { id: 3, name: 'новый' }];

    expect(mergeRagDocumentsById(prev, incoming)).toEqual([{ id: 1, name: 'локальный' }, { id: 2, name: 'серверный' }, { id: 3, name: 'новый' }]);
    expect(mergeRagDocumentsById([], [])).toEqual([]);
  });

  it('commits the UI update synchronously', () => {
    const update = jest.fn();

    commitRagUploadUiUpdate(update);

    expect(update).toHaveBeenCalledTimes(1);
  });

  it('keeps the upload concurrency modest', () => {
    expect(RAG_UPLOAD_CONCURRENCY).toBe(3);
  });

  it('runs the worker with a bounded parallelism and keeps the order of results', async () => {
    let active = 0;
    let peak = 0;

    const results = await mapWithConcurrency([5, 1, 4, 2, 3, 0], 2, async (item) => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise((resolve) => setTimeout(resolve, item));
      active -= 1;
      return item * 10;
    });

    expect(results).toEqual([50, 10, 40, 20, 30, 0]);
    expect(peak).toBe(2);
  });

  it('passes the item index and handles edge cases', async () => {
    expect(await mapWithConcurrency([], 3, async () => 1)).toEqual([]);
    expect(await mapWithConcurrency(['a', 'b'], 0, async (item, index) => `${item}${index}`)).toEqual(['a0', 'b1']);
    expect(await mapWithConcurrency(['a'], 99, async (item) => item)).toEqual(['a']);
  });

  it('rejects when any worker fails', async () => {
    await expect(mapWithConcurrency([1, 2, 3], 2, async (item) => {
      if (item === 2) throw new Error('boom');
      return item;
    })).rejects.toThrow('boom');
  });
});

describe('dedupeFilesByContent', () => {
  const originalCrypto = (global as unknown as { crypto?: unknown }).crypto;

  function withHashing(failFor?: string) {
    Object.defineProperty(global, 'crypto', {
      configurable: true,
      value: {
        subtle: {
          digest: async (_algo: string, data: ArrayBuffer) => {
            const bytes = new Uint8Array(data);
            if (failFor && String.fromCharCode(...bytes) === failFor) throw new Error('digest failed');
            return Uint8Array.from([bytes.length, ...Array.from(bytes).slice(0, 7)]).buffer;
          },
        },
      },
    });
  }

  function contentFile(name: string, content: string): File {
    const f = new File([content], name);
    Object.defineProperty(f, 'arrayBuffer', { value: async () => Uint8Array.from(content, (c) => c.charCodeAt(0)).buffer });
    return f;
  }

  afterEach(() => {
    Object.defineProperty(global, 'crypto', { configurable: true, value: originalCrypto });
  });

  it('keeps a single file and an empty list as they are', async () => {
    const one = [contentFile('a.txt', 'abc')];

    expect(await dedupeFilesByContent(one)).toEqual({ unique: one, skipped: [] });
    expect(await dedupeFilesByContent([])).toEqual({ unique: [], skipped: [] });
  });

  it('drops copies with identical content inside one batch', async () => {
    withHashing();
    const files = [contentFile('a.txt', 'same-content'), contentFile('copy.txt', 'same-content'), contentFile('b.txt', 'other-content')];

    const { unique, skipped } = await dedupeFilesByContent(files);

    expect(unique.map((f) => f.name)).toEqual(['a.txt', 'b.txt']);
    expect(skipped).toEqual(['copy.txt']);
  });

  it('does not hash files whose size is unique in the batch', async () => {
    const digest = jest.fn();
    Object.defineProperty(global, 'crypto', { configurable: true, value: { subtle: { digest } } });

    const { unique } = await dedupeFilesByContent([contentFile('a.txt', 'aa'), contentFile('b.txt', 'bbb')]);

    expect(digest).not.toHaveBeenCalled();
    expect(unique).toHaveLength(2);
  });

  it('keeps files whose hash cannot be computed', async () => {
    withHashing('same-content');
    const files = [contentFile('a.txt', 'same-content'), contentFile('b.txt', 'same-content')];

    const { unique, skipped } = await dedupeFilesByContent(files);

    expect(unique).toHaveLength(2);
    expect(skipped).toEqual([]);
  });

  it('keeps everything without a secure context for crypto.subtle', async () => {
    Object.defineProperty(global, 'crypto', { configurable: true, value: undefined });
    const files = [contentFile('a.txt', 'same-content'), contentFile('b.txt', 'same-content')];

    expect((await dedupeFilesByContent(files)).unique).toHaveLength(2);
  });
});

describe('generated image creations', () => {
  const att = (over: Record<string, unknown> = {}) => ({ contentType: 'image', preview: 'data:image/png;base64,AAA', name: 'generated_1.png', ...over });
  const msg = (over: Record<string, unknown> = {}) => ({ id: 'm1', role: 'assistant', content: 'Сгенерировал изображение по запросу: «рыжий кот»', timestamp: '2026-01-02T00:00:00Z', inlineAttachments: [att()], ...over });
  const chat = (messages: unknown[], over: Record<string, unknown> = {}) => ({ id: 'c1', title: 'Чат', messages, ...over } as never);

  it('collects generated images with the prompt from the answer', () => {
    const items = extractCreationsFromChats([chat([msg()])]);

    expect(items).toEqual([
      { id: 'm1:0:0', message_id: 'm1', conversation_id: 'c1', conversation_title: 'Чат', prompt: 'рыжий кот', name: 'generated_1.png', created_at: '2026-01-02T00:00:00Z', preview_url: 'data:image/png;base64,AAA' },
    ]);
  });

  it('skips user messages, non-images and images without a preview', () => {
    const items = extractCreationsFromChats([
      chat([
        msg({ role: 'user' }),
        msg({ id: 'm2', inlineAttachments: [att({ contentType: 'text' })] }),
        msg({ id: 'm3', inlineAttachments: [att({ preview: undefined })] }),
        msg({ id: 'm4', content: 'Вот твоя картинка', inlineAttachments: [att({ name: 'photo.png' })] }),
      ]),
    ]);

    expect(items).toEqual([]);
  });

  it('accepts a generated_ name or an image_generation marker without the phrase', () => {
    const items = extractCreationsFromChats([
      chat([msg({ id: 'a', content: 'готово' }), msg({ id: 'b', content: 'image_generation done', inlineAttachments: [att({ name: 'x.png' })] })]),
    ]);

    expect(items.map((i) => i.message_id).sort()).toEqual(['a', 'b']);
  });

  it('lists every variant of a regenerated answer and sorts newest first', () => {
    const items = extractCreationsFromChats([
      chat([
        msg({ id: 'old', timestamp: '2026-01-01T00:00:00Z' }),
        msg({ id: 'new', timestamp: '2026-01-03T00:00:00Z', inlineAttachments: undefined, inlineAttachmentVariants: [[att()], [att(), att({ name: 'generated_2.png' })]] }),
      ]),
    ]);

    expect(items.map((i) => i.id)).toEqual(['new:0:0', 'new:1:0', 'new:1:1', 'old:0:0']);
    expect(items[0].created_at).toBe('2026-01-03T00:00:00Z');
    expect(items[items.length - 1].message_id).toBe('old');
  });

  it('uses the attachment name when the prompt cannot be extracted', () => {
    const [item] = extractCreationsFromChats([chat([msg({ content: 'Сгенерировал изображение' })])]);

    expect(item.prompt).toBe('generated_1.png');
  });

  it('merges API items with local ones preferring inline previews', () => {
    const api = [{ id: 'a', preview_url: '/api/img/a', prompt: '', created_at: '2026-01-02' }, { id: 'b', preview_url: null, created_at: '2026-01-01' }];
    const local = [{ id: 'a', preview_url: 'data:image/png;base64,AAA', prompt: 'локальный', created_at: '2025-12-31' }, { id: 'c', preview_url: 'blob:x', created_at: '2026-01-03' }];

    const merged = mergeCreationItems(api, local);

    expect(merged.map((m) => m.id)).toEqual(['c', 'a', 'b']);
    expect(merged.find((m) => m.id === 'a')).toMatchObject({ preview_url: 'data:image/png;base64,AAA', prompt: 'локальный', created_at: '2026-01-02' });
  });

  it('keeps the API preview when the local one is a plain url', () => {
    const [item] = mergeCreationItems([{ id: 'a', preview_url: '/api/img/a' }], [{ id: 'a', preview_url: '/other' }]);

    expect(item.preview_url).toBe('/api/img/a');
  });

  it('resolves preview sources', () => {
    expect(resolveCreationPreviewSrc(null)).toBeNull();
    expect(resolveCreationPreviewSrc('')).toBeNull();
    expect(resolveCreationPreviewSrc('data:image/png;base64,AAA')).toBe('data:image/png;base64,AAA');
    expect(resolveCreationPreviewSrc('blob:http://x/1')).toBe('blob:http://x/1');
    expect(resolveCreationPreviewSrc('HTTPS://cdn.test/a.png')).toBe('HTTPS://cdn.test/a.png');
    expect(resolveCreationPreviewSrc('/api/images/1')).toBe('http://api.test/api/images/1');
  });

  it('passes through javascript: urls to getApiUrl as a relative path (known issue)', () => {
    // Схемы, кроме data/blob/http(s), не отсекаются: «javascript:…» превращается в «http://api.test» + строка и не исполняется,
    // но и не отбрасывается — защита держится на том, что getApiUrl всегда добавляет префикс.
    expect(resolveCreationPreviewSrc('javascript:alert(1)')).toBe('http://api.testjavascript:alert(1)');
  });
});

describe('image generation preset', () => {
  beforeEach(() => localStorage.clear());

  it('reads and writes the selected preset and announces it', () => {
    const events: CustomEvent[] = [];
    const handler = (e: Event) => events.push(e as CustomEvent);
    window.addEventListener('astrachatImageGenPresetChanged', handler);

    expect(readSelectedImageGenPresetId()).toBeNull();
    writeSelectedImageGenPresetId('sdxl');
    window.removeEventListener('astrachatImageGenPresetChanged', handler);

    expect(readSelectedImageGenPresetId()).toBe('sdxl');
    expect(events[0].detail).toEqual({ presetId: 'sdxl' });
  });

  it('treats a blank stored value as not selected and trims the rest', () => {
    localStorage.setItem(IMAGE_GEN_PRESET_STORAGE_KEY, '   ');
    expect(readSelectedImageGenPresetId()).toBeNull();

    localStorage.setItem(IMAGE_GEN_PRESET_STORAGE_KEY, ' flux ');
    expect(readSelectedImageGenPresetId()).toBe('flux');
  });

  it('shows the label of a known preset and the id of an unknown one', () => {
    const presets = [{ id: 'sdxl', label: 'SDXL быстрый' }, { id: 'flux', label: '' }];

    expect(resolveImageGenPresetLabel(presets, 'sdxl')).toBe('SDXL быстрый');
    expect(resolveImageGenPresetLabel(presets, 'flux')).toBe('flux');
    expect(resolveImageGenPresetLabel(presets, 'ghost')).toBe('ghost');
    expect(resolveImageGenPresetLabel(presets, null)).toBe('');
    expect(resolveImageGenPresetLabel([], undefined)).toBe('');
  });
});
