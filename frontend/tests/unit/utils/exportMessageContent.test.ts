import {
  buildExportFileName,
  exportMessageAsWord,
  exportMessageContent,
  markdownToPlainText,
  markdownToRichHtml,
  parseInlineRuns,
  runsToPlain,
  stripReasoningForExport,
} from '@/utils/exportMessageContent';

const sig = (text: string) =>
  parseInlineRuns(text).map((r) => `${r.text}|${r.bold ? 'b' : ''}${r.italic ? 'i' : ''}${r.code ? 'c' : ''}`);

describe('parseInlineRuns', () => {
  it('returns nothing for an empty string', () => {
    expect(parseInlineRuns('')).toEqual([]);
  });

  it('keeps plain text as a single unstyled run', () => {
    expect(sig('просто текст')).toEqual(['просто текст|']);
  });

  it('parses bold, italic and code', () => {
    expect(sig('**жирный** и *курсив* и `код`')).toEqual(['жирный|b', ' и |', 'курсив|i', ' и |', 'код|c']);
  });

  it('supports the underscore variants', () => {
    expect(sig('__жирный__ и _курсив_')).toEqual(['жирный|b', ' и |', 'курсив|i']);
  });

  it('nests italic inside bold', () => {
    expect(sig('**а *б* в**')).toEqual(['а |b', 'б|bi', ' в|b']);
  });

  it('understands the html forms of the same markup', () => {
    expect(sig('<strong>a</strong> <b>b</b> <em>c</em> <i>d</i> <code>e</code>')).toEqual(['a|b', ' |', 'b|b', ' |', 'c|i', ' |', 'd|i', ' |', 'e|c']);
  });

  it('replaces links with their text and drops the url', () => {
    expect(runsToPlain(parseInlineRuns('смотри [документ](javascript:void) тут'))).toBe('смотри документ тут');
    expect(runsToPlain(parseInlineRuns('[a](https://x.org/a) и [b](/b)'))).toBe('a и b');
  });

  it('leaves a stray bracket after a url that contains parentheses (known issue)', () => {
    expect(runsToPlain(parseInlineRuns('смотри [документ](https://ru.wikipedia.org/wiki/Тест_(значения)) тут'))).toBe('смотри документ) тут');
  });

  it('does not treat latin snake_case words as italic', () => {
    expect(sig('my_variable_name')).toEqual(['my_variable_name|']);
  });

  it('turns cyrillic snake_case into italic and drops the underscores (known issue)', () => {
    // В JS-регулярке \w не включает кириллицу, поэтому проверка «перед _ не буква» не срабатывает.
    expect(runsToPlain(parseInlineRuns('имя_переменной_в_коде'))).toBe('имяпеременнойв_коде');
  });

  it('merges neighbouring runs of the same style', () => {
    expect(parseInlineRuns('a b c')).toHaveLength(1);
  });

  it('survives unbalanced markers', () => {
    expect(runsToPlain(parseInlineRuns('**не закрыто'))).toContain('не закрыто');
    expect(runsToPlain(parseInlineRuns('`не закрыто'))).toContain('не закрыто');
  });
});

describe('markdownToRichHtml', () => {
  it('renders headings, paragraphs and styles', () => {
    const html = markdownToRichHtml('# Заголовок\n\nТекст с **жирным**');

    expect(html).toContain('<h1>Заголовок</h1>');
    expect(html).toContain('<p>Текст с <strong>жирным</strong></p>');
  });

  it('limits heading levels to six', () => {
    expect(markdownToRichHtml('###### Шесть')).toContain('<h6>Шесть</h6>');
    expect(markdownToRichHtml('####### Семь')).toContain('<p>####### Семь</p>');
  });

  it('groups list items and keeps the numbering', () => {
    const html = markdownToRichHtml('- а\n- б\n\n3. три\n4. четыре');

    expect(html).toContain('<ul>\n<li>а</li>\n<li>б</li>\n</ul>');
    expect(html).toContain('<ol>\n<li value="3">три</li>\n<li value="4">четыре</li>\n</ol>');
  });

  it('closes a list when another block starts', () => {
    expect(markdownToRichHtml('- а\nабзац')).toBe('<ul>\n<li>а</li>\n</ul>\n<p>абзац</p>');
  });

  it('renders quotes, rules and code blocks', () => {
    const html = markdownToRichHtml('> цитата\n\n---\n\n```js\nconst a = 1 < 2;\n```');

    expect(html).toContain('<blockquote>цитата</blockquote>');
    expect(html).toContain('<hr />');
    expect(html).toContain('const a = 1 &lt; 2;');
    expect(html).toContain('<pre');
  });

  it('renders GFM tables with aligned columns', () => {
    const html = markdownToRichHtml('| Имя | Сумма |\n| --- | --- |\n| Иван | 100 |\n| Пётр |');

    expect(html).toContain('<thead><tr><th');
    expect(html.match(/<th /g)).toHaveLength(2);
    expect(html.match(/<td /g)).toHaveLength(4); // короткая строка добита пустой ячейкой
    expect(html).toContain('>Иван</td>');
  });

  it('cuts extra cells of a table row', () => {
    const html = markdownToRichHtml('| a | b |\n|---|---|\n| 1 | 2 | 3 |');

    expect(html.match(/<td /g)).toHaveLength(2);
  });

  it('escapes html in every block so that exported documents cannot carry scripts', () => {
    const payload = '<script>alert(1)</script><img src=x onerror=alert(1)>';
    const html = markdownToRichHtml(`# ${payload}\n\n${payload}\n\n- ${payload}\n\n> ${payload}\n\n\`\`\`\n${payload}\n\`\`\`\n\n| ${payload} |\n|---|\n| ${payload} |`);

    expect(html).not.toMatch(/<script/i);
    expect(html).not.toMatch(/<img/i);
    expect(html).toContain('&lt;script&gt;');
  });

  it('escapes quotes and ampersands', () => {
    expect(markdownToRichHtml('Tom & "Jerry" \'s')).toBe('<p>Tom &amp; &quot;Jerry&quot; &#39;s</p>');
  });

  it('is empty for empty input', () => {
    expect(markdownToRichHtml('')).toContain('&nbsp;');
  });

  it('normalises Windows line endings', () => {
    expect(markdownToRichHtml('a\r\nb')).toBe('<p>a</p>\n<p>b</p>');
  });

  it('keeps an unterminated code block', () => {
    expect(markdownToRichHtml('```\nнезакрытый')).toContain('незакрытый');
  });
});

describe('markdownToPlainText', () => {
  it('flattens blocks for the clipboard', () => {
    const md = '# Заголовок\n\n**Жирный** текст\n\n- один\n- два\n\n2. вторая\n3. третья\n\n> цитата\n\n---\n\n```\nкод\n```';

    expect(markdownToPlainText(md)).toBe('Заголовок\n\nЖирный текст\n\n• один\n• два\n\n2. вторая\n3. третья\n\nцитата\n\n---\n\nкод');
  });

  it('prints tables with tabs', () => {
    expect(markdownToPlainText('| a | b |\n|---|---|\n| 1 | 2 |')).toBe('a\tb\n1\t2');
  });

  it('collapses runs of blank lines and trims', () => {
    expect(markdownToPlainText('\n\n\nа\n\n\n\n\nб\n\n')).toBe('а\n\nб');
  });
});

describe('file names and reasoning stripping', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(2026, 0, 2, 3, 4, 5));
  });

  afterEach(() => jest.useRealTimers());

  it('builds a timestamped name', () => {
    expect(buildExportFileName('ответ', 'doc')).toBe('ответ_20260102_030405.doc');
  });

  it('sanitises forbidden characters and long names', () => {
    expect(buildExportFileName('a/b\\c:d*e?f"g<h>i|j', 'pdf')).toBe('a_b_c_d_e_f_g_h_i_j_20260102_030405.pdf');
    expect(buildExportFileName('я'.repeat(200), 'pdf').split('_20260102')[0]).toHaveLength(80);
    expect(buildExportFileName('', 'pdf')).toBe('message_20260102_030405.pdf');
  });

  it('removes the reasoning block from the exported text', () => {
    expect(stripReasoningForExport('<think>рассуждение</think>Итог')).toBe('Итог');
    expect(stripReasoningForExport('Без рассуждений')).toBe('Без рассуждений');
  });
});

describe('Word export', () => {
  let created: Blob[];
  let downloads: Array<{ download: string }>;

  beforeEach(() => {
    created = [];
    downloads = [];
    (URL as unknown as { createObjectURL: (b: Blob) => string }).createObjectURL = (blob: Blob) => {
      created.push(blob);
      return 'blob:test';
    };
    (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = jest.fn();
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      downloads.push({ download: this.download });
    });
  });

  afterEach(() => jest.restoreAllMocks());

  async function readBlob(blob: Blob): Promise<string> {
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(blob);
    });
  }

  async function readBuffer(blob: Blob): Promise<ArrayBuffer> {
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.readAsArrayBuffer(blob);
    });
  }

  it('downloads a .doc with a UTF-8 BOM and an html body without the reasoning', async () => {
    exportMessageAsWord('<think>скрыто</think># Отчёт\n\nТекст **важный**', 'мой отчёт');

    expect(downloads).toHaveLength(1);
    expect(downloads[0].download).toMatch(/^мой отчёт_\d{8}_\d{6}\.doc$/);
    const html = await readBlob(created[0]);
    const bytes = new Uint8Array(await readBuffer(created[0]));
    expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
    expect(html).toContain('<h1>Отчёт</h1>');
    expect(html).toContain('<strong>важный</strong>');
    expect(html).not.toContain('скрыто');
    expect(created[0].type).toContain('application/msword');
  });

  it('escapes the document title taken from the file name', async () => {
    exportMessageAsWord('x', '<img src=x onerror=alert(1)>');

    expect(await readBlob(created[0])).toContain('<title>&lt;img src=x onerror=alert(1)&gt;</title>');
  });

  it('dispatches by format', () => {
    exportMessageContent('текст', 'word');

    expect(downloads[0].download.endsWith('.doc')).toBe(true);
    expect(document.querySelector('a[download]')).toBeNull(); // временная ссылка убрана из DOM
  });
});
