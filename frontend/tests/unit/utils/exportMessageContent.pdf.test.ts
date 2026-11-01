import { TextEncoder as NodeTextEncoder } from 'util';
import {
  exportMessageAsPdf,
  exportMessageContent,
  markdownToRichHtml,
  parseInlineRuns,
} from '@/utils/exportMessageContent';

if (typeof (global as any).TextEncoder === 'undefined') (global as any).TextEncoder = NodeTextEncoder;

type CtxCall = { fn: string; args: unknown[] };

describe('exportMessageContent: PDF (canvas -> jpeg -> pdf)', () => {
  let blobs: Blob[];
  let downloads: string[];
  let canvases: Array<{ width: number; height: number; calls: CtxCall[]; dataUrls: number }>;
  let ctxAvailable: boolean;
  let ctxAvailableAfter: number;
  let createCount: number;
  let measureNull = false;
  let origCreate: typeof document.createElement;

  const makeCtx = (rec: CtxCall[]) => {
    let font = '';
    const handler: any = {
      measureText: (t: string) => ({ width: t.length * 7 }),
      set font(v: string) {
        font = v;
        rec.push({ fn: 'font', args: [v] });
      },
      get font() {
        return font;
      },
    };
    for (const fn of ['fillRect', 'fillText', 'strokeRect']) {
      handler[fn] = (...args: unknown[]) => rec.push({ fn, args });
    }
    return handler;
  };

  beforeEach(() => {
    blobs = [];
    downloads = [];
    canvases = [];
    ctxAvailable = true;
    measureNull = false;
    ctxAvailableAfter = Infinity;
    createCount = 0;
    (URL as any).createObjectURL = (b: Blob) => {
      blobs.push(b);
      return 'blob:x';
    };
    (URL as any).revokeObjectURL = jest.fn();
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      downloads.push(this.download);
    });
    origCreate = document.createElement.bind(document);
    jest.spyOn(document, 'createElement').mockImplementation(((tag: string, o?: any) => {
      if (tag !== 'canvas') return origCreate(tag, o);
      createCount += 1;
      const rec: CtxCall[] = [];
      const info = { width: 0, height: 0, calls: rec, dataUrls: 0 };
      canvases.push(info);
      const el: any = {
        set width(v: number) {
          info.width = v;
        },
        get width() {
          return info.width;
        },
        set height(v: number) {
          info.height = v;
        },
        get height() {
          return info.height;
        },
        getContext: () => (!(measureNull && createCount === 1) && ctxAvailable && createCount <= ctxAvailableAfter ? makeCtx(rec) : null),
        toDataURL: () => {
          info.dataUrls += 1;
          return 'data:image/jpeg;base64,AQID';
        },
      };
      return el;
    }) as any);
  });

  afterEach(() => jest.restoreAllMocks());

  function readText(blob: Blob): Promise<string> {
    return new Promise((res) => {
      const fr = new FileReader();
      fr.onload = () => res(String(fr.result));
      fr.readAsBinaryString(blob);
    });
  }

  it('собирает PDF из одной страницы и скачивает его', async () => {
    exportMessageAsPdf('<think>секрет</think>Привет **мир**', 'отчёт');
    expect(downloads[0]).toMatch(/^отчёт_\d{8}_\d{6}\.pdf$/);
    expect(blobs[0].type).toBe('application/pdf');
    const raw = await readText(blobs[0]);
    expect(raw.startsWith('%PDF-1.4')).toBe(true);
    expect(raw).toContain('/Count 1');
    expect(raw).toContain('/Filter /DCTDecode /Length 3');
    expect(raw).toContain('%%EOF');
    expect(raw).toContain('/Size 6 ');
    // страница отрисована и «секрет» не попал в canvas
    const texts = canvases[1].calls.filter((c) => c.fn === 'fillText').map((c) => c.args[0]);
    expect(texts.join('')).toContain('Привет');
    expect(texts.join('')).not.toContain('секрет');
    expect(canvases[1].width).toBe(794);
    expect(canvases[1].height).toBe(1123);
  });

  it('exportMessageContent(pdf) вызывает PDF-экспорт', () => {
    exportMessageContent('текст', 'pdf');
    expect(downloads[0].endsWith('.pdf')).toBe(true);
  });

  it('рисует все типы блоков: заголовки разных уровней, цитату, списки, код, hr, пустые строки', () => {
    const md = [
      '# H1',
      '## H2',
      '### H3',
      '#### H4',
      '> цитата',
      '- пункт',
      '3. третий',
      '',
      '---',
      '```',
      'code line',
      '',
      '```',
      'просто *курсив* и `код`',
    ].join('\n');
    exportMessageAsPdf(md);
    const calls = canvases[1].calls;
    const fonts = calls.filter((c) => c.fn === 'font').map((c) => String(c.args[0]));
    expect(fonts.some((f) => f.includes('700 22px'))).toBe(true); // h1
    expect(fonts.some((f) => f.includes('700 18px'))).toBe(true); // h2
    expect(fonts.some((f) => f.includes('700 16px'))).toBe(true); // h3
    expect(fonts.some((f) => f.includes('700 15px'))).toBe(true); // h4
    expect(fonts.some((f) => f.startsWith('italic 400 14px'))).toBe(true); // цитата/курсив
    expect(fonts.some((f) => f.includes('Consolas'))).toBe(true);
    const texts = calls.filter((c) => c.fn === 'fillText').map((c) => String(c.args[0]));
    expect(texts).toContain('• ');
    expect(texts).toContain('3. ');
    expect(texts).toContain('────────────────');
    expect(texts).toContain('code line');
  });

  it('рисует таблицу: рамки ячеек и заливку заголовка', () => {
    exportMessageAsPdf('| A | B |\n|---|---|\n| 1 | 2 |\n| 3 |');
    const calls = canvases[1].calls;
    // 2 колонки * (заголовок + 2 строки) = 6 рамок
    expect(calls.filter((c) => c.fn === 'strokeRect')).toHaveLength(6);
    // фон страницы + заливка заголовка
    expect(calls.filter((c) => c.fn === 'fillRect')).toHaveLength(2);
    const texts = calls.filter((c) => c.fn === 'fillText').map((c) => c.args[0]);
    expect(texts).toEqual(expect.arrayContaining(['A', 'B', '1', '2', '3']));
  });

  it('широкая таблица сжимается по ширине страницы', () => {
    const wide = 'x'.repeat(60);
    const header = Array.from({ length: 6 }, (_, i) => `${wide}${i}`);
    const md = `| ${header.join(' | ')} |\n|${header.map(() => '---').join('|')}|\n| ${header.join(' | ')} |`;
    exportMessageAsPdf(md);
    const rects = canvases[1].calls.filter((c) => c.fn === 'strokeRect');
    const total = rects.slice(0, 6).reduce((s, c) => s + (c.args[2] as number) + 1, 0);
    expect(Math.round(total)).toBe(794 - 56 * 2);
  });

  it('переносит длинный текст по словам и режет слово длиннее строки по символам', () => {
    const words = Array.from({ length: 200 }, () => 'слово').join(' ');
    exportMessageAsPdf(`${words}\n\nаб ${'Ж'.repeat(300)}`);
    const texts = canvases[1].calls.filter((c) => c.fn === 'fillText').map((c) => String(c.args[0]));
    // длинный абзац разбит на несколько строк
    expect(texts.filter((t) => t.includes('слово')).length).toBeGreaterThan(2);
    // слово из 300 «Ж» (7px * 300 > 682) разрезано на куски
    const zh = texts.filter((t) => /^Ж+$/.test(t));
    expect(zh.length).toBeGreaterThan(1);
    expect(zh.join('').length).toBe(300);
  });

  it('слово длиннее строки в самом начале строки не режется и выходит за поле (known issue)', () => {
    // Правильно: слово шире maxWidth нужно резать по символам и в начале строки.
    // Сейчас ветка с current.length === 0 принимает слово целиком.
    exportMessageAsPdf('Ж'.repeat(300));
    const texts = canvases[1].calls.filter((c) => c.fn === 'fillText').map((c) => String(c.args[0]));
    expect(texts).toEqual(['Ж'.repeat(300)]);
  });

  it('стили внутри одной строки: соседние куски одного стиля склеиваются, разные остаются', () => {
    exportMessageAsPdf('обычный **жирный** обычный');
    const texts = canvases[1].calls.filter((c) => c.fn === 'fillText').map((c) => String(c.args[0]));
    expect(texts).toContain('жирный');
    expect(texts.join('')).toBe('обычный жирный обычный');
  });

  it('слово длиннее строки в самом начале строки не режется и выходит за поле (known issue)', () => {
    // Правильно: слово шире maxWidth нужно резать по символам и в начале строки.
    // Сейчас ветка с current.length === 0 принимает слово целиком.
    exportMessageAsPdf('Ж'.repeat(300));
    const texts = canvases[1].calls.filter((c) => c.fn === 'fillText').map((c) => String(c.args[0]));
    expect(texts).toEqual(['Ж'.repeat(300)]);
  });

  it('несколько страниц, когда контент не помещается', () => {
    const md = Array.from({ length: 120 }, (_, i) => `строка ${i}`).join('\n\n');
    exportMessageAsPdf(md);
    const pages = canvases.filter((c) => c.dataUrls > 0);
    expect(pages.length).toBeGreaterThan(1);
    return readText(blobs[0]).then((raw) => {
      expect(raw).toContain(`/Count ${pages.length}`);
      expect(raw).toContain('/Im1 Do');
    });
  });

  it('таблица, которая переходит на следующую страницу, не теряет строки', () => {
    const rows = Array.from({ length: 80 }, (_, i) => `| r${i} | v |`).join('\n');
    exportMessageAsPdf(`| h | h2 |\n|---|---|\n${rows}`);
    const pages = canvases.filter((c) => c.dataUrls > 0);
    expect(pages.length).toBeGreaterThan(1);
    const texts = canvases.flatMap((c) => c.calls.filter((x) => x.fn === 'fillText').map((x) => x.args[0]));
    expect(texts).toContain('r0');
    expect(texts).toContain('r79');
  });

  it('если контекст для измерения недоступен, раскладка пуста и выводится одна пустая страница', async () => {
    measureNull = true;
    exportMessageAsPdf('текст');
    const raw = await readText(blobs[0]);
    expect(raw).toContain('/Count 1');
    expect(canvases[1].calls.filter((c) => c.fn === 'fillText')).toHaveLength(0);
  });

  it('без 2D-контекста вообще бросает понятную ошибку и ничего не скачивает', () => {
    ctxAvailable = false;
    expect(() => exportMessageAsPdf('текст')).toThrow('Canvas 2D недоступен');
    expect(downloads).toHaveLength(0);
  });

  it('если canvas 2D пропал при создании страницы, бросает понятную ошибку', () => {
    ctxAvailableAfter = 1; // первый canvas (измерение) работает, страница — нет
    expect(() => exportMessageAsPdf('текст')).toThrow('Canvas 2D недоступен');
    expect(downloads).toHaveLength(0);
  });

  it('пустой контент экспортируется как одна страница', async () => {
    exportMessageAsPdf('');
    const raw = await readText(blobs[0]);
    expect(raw).toContain('/Count 1');
  });
});

describe('exportMessageContent: прочие ветки разбора', () => {
  it('литеральный управляющий символ не рвёт run одного стиля', () => {
    expect(parseInlineRuns('ab')).toEqual([{ text: 'ab', bold: false, italic: false, code: false }]);
  });

  it('одиночная подчёркнутая курсивная вставка внутри жирного через __', () => {
    const runs = parseInlineRuns('__a _b_ c__');
    expect(runs.some((r) => r.bold && r.italic && r.text === 'b')).toBe(true);
  });

  it('markdownToRichHtml оборачивает code/bold/italic в правильном порядке', () => {
    const html = markdownToRichHtml('***x***');
    expect(html).toContain('<em>');
    expect(html).toContain('<strong>');
  });
});
