import {
  injectChartThemeColorsFromStyles,
  injectPieThemeColorsFromStyles,
  normalizeMermaidInitColors,
  prepareMermaidSourceForRender,
  quoteXyChartCategoryLabels,
  repairMermaidSource,
  sanitizeArtifactBody,
  sanitizeMermaidSource,
  splitContentWithArtifacts,
  stripMermaidStyling,
} from '@/utils/artifacts';

describe('normalizeMermaidInitColors', () => {
  it('возвращает код без init-директивы как есть', () => {
    expect(normalizeMermaidInitColors('graph TD\nA-->B')).toBe('graph TD\nA-->B');
    expect(normalizeMermaidInitColors('')).toBe('');
  });

  it('оставляет блок %%{ ... }%% без init: нетронутым', () => {
    const code = "%%{ config: {'x': 1} init }%%\ngraph TD";
    expect(normalizeMermaidInitColors(code)).toBe(code);
  });

  it('вычищает эмодзи из plotColorPalette', () => {
    const code = `%%{init: {'themeVariables': {'xyChart': {'plotColorPalette': '🟦#3b82f6, 🟥#ef4444'}}}}%%\nxychart-beta`;
    const out = normalizeMermaidInitColors(code);
    expect(out).toContain("plotColorPalette': '#3b82f6, #ef4444'");
    expect(out).not.toContain('🟦');
  });

  it('палитра без hex остаётся как есть', () => {
    const code = `%%{init: {'themeVariables': {'xyChart': {'plotColorPalette': 'red, blue'}}}}%%\nxychart-beta`;
    expect(normalizeMermaidInitColors(code)).toBe(code);
  });

  it('чистит цвета pieN: берёт hex, иначе убирает мусорные символы', () => {
    const code = `%%{init: {'themeVariables': {'pie1': '🟦#3b82f6', 'pie2': 'zz#12G', 'pie3': 'blue'}}}%%\npie`;
    const out = normalizeMermaidInitColors(code);
    expect(out).toContain("'pie1': '#3b82f6'");
    expect(out).toContain("'pie2': '#12'");
    expect(out).toContain("'pie3': 'be'");
  });

  it('если после чистки ничего не осталось, подставляет исходное значение без пробелов', () => {
    const code = `%%{init: {'themeVariables': {'pie1': '  🟦  '}}}%%\npie`;
    const out = normalizeMermaidInitColors(code);
    expect(out).toContain("'pie1': '🟦'");
  });
});

describe('sanitizeArtifactBody', () => {
  it('срезает обёртку :::artifact и закрывающий :::', () => {
    expect(sanitizeArtifactBody(':::artifact{type="x"}\nline1\nline2\n:::')).toBe('line1\nline2');
  });

  it('обрезает пояснения после закрывающего fence', () => {
    expect(sanitizeArtifactBody('code\n```\nпояснение модели')).toBe('code');
  });

  it('удаляет строки-fence и ведущий fence', () => {
    expect(sanitizeArtifactBody('```mermaid\ngraph TD\nA-->B\n```')).toBe('graph TD\nA-->B');
  });

  it('убирает дублирующую первую строку с языком (fenceLang и общий список)', () => {
    expect(sanitizeArtifactBody('mermaid\ngraph TD', 'mermaid')).toBe('graph TD');
    expect(sanitizeArtifactBody('tsx\nconst a = 1')).toBe('const a = 1');
  });

  it('обрезает всё после внутреннего fence и нормализует CRLF и пустые края', () => {
    expect(sanitizeArtifactBody('\r\n\r\na\r\nb\r\n````\r\nx\r\n')).toBe('a\nb');
    expect(sanitizeArtifactBody(undefined as unknown as string)).toBe('');
  });

  it('срезает хвост после fence с содержимым в строке', () => {
    expect(sanitizeArtifactBody('a\n```js extra\nb')).toBe('a');
  });

  it('A:::class внутри тела не считается закрытием блока', () => {
    expect(sanitizeArtifactBody('graph TD\nA:::cls --> B')).toBe('graph TD\nA:::cls --> B');
  });
});

describe('sanitizeMermaidSource', () => {
  it('отбрасывает мусор перед ключевым словом диаграммы', () => {
    expect(sanitizeMermaidSource('вот диаграмма\ngraph TD\nA-->B')).toBe('graph TD\nA-->B');
  });

  it('переносит ведущий init-блок и нормализует его цвета', () => {
    const src = `%%{init: {'themeVariables': {'pie1': '🟦#111'}}}%%\npie\n"A" : 1`;
    const out = sanitizeMermaidSource(src);
    expect(out.startsWith("%%{init: {'themeVariables': {'pie1': '#111'}}}%%\npie")).toBe(true);
    expect(out).toContain('"A" : 1');
  });

  it('оставляет не-init %%{ }%% блок на месте', () => {
    const out = sanitizeMermaidSource('graph TD\n%%{ other }%%\nA-->B');
    expect(out).toContain('%%{ other }%%');
  });

  it('удаляет одиночные строки из backticks', () => {
    expect(sanitizeMermaidSource('graph TD\n``\nA-->B')).toBe('graph TD\nA-->B');
  });

  it('обрезает русские пояснения после диаграммы', () => {
    const out = sanitizeMermaidSource('graph TD\nA-->B\n\nГотово, диаграмма построена');
    expect(out).toBe('graph TD\nA-->B');
  });

  it('обрезает маркированный список-пояснение и разные виды prose', () => {
    expect(sanitizeMermaidSource('graph TD\nA-->B\n**Итог** всё хорошо')).toBe('graph TD\nA-->B');
    expect(sanitizeMermaidSource('graph TD\nA-->B\n- пункт на русском')).toBe('graph TD\nA-->B');
    expect(sanitizeMermaidSource('graph TD\nA-->B\nПримечание: тут')).toBe('graph TD\nA-->B');
  });

  it('данные pie с кириллицей не считаются prose', () => {
    const out = sanitizeMermaidSource('pie title Еда\n"Отварной" : 226\n"Жареный" : 100\nвывод: всё');
    expect(out).toBe('pie title Еда\n"Отварной" : 226\n"Жареный" : 100');
  });

  it('распознаёт строки диаграмм разных типов (узлы, sequence, participant)', () => {
    const src = [
      'sequenceDiagram',
      'participant A',
      'A->>B: привет',
      'Alice->Bob: x',
      'B[Узел]',
      'C:::cls',
      'D --> E',
      '%% комментарий',
      'subgraph S',
      'end',
    ].join('\n');
    expect(sanitizeMermaidSource(src)).toBe(src);
  });

  it('пустой ввод даёт пустую строку', () => {
    expect(sanitizeMermaidSource('')).toBe('');
  });

  it('до начала диаграммы сохраняет всё, после — режет по закрывающему :::', () => {
    expect(sanitizeMermaidSource('graph TD\nA-->B\n:::\nхвост')).toBe('graph TD\nA-->B');
  });

  it('пустая строка внутри диаграммы сохраняется, а непонятная ASCII-строка тоже', () => {
    const out = sanitizeMermaidSource('graph TD\nA-->B\n\nwhatever text');
    expect(out).toBe('graph TD\nA-->B\n\nwhatever text');
  });
});

describe('injectChartThemeColorsFromStyles', () => {
  it('пустой код -> пустая строка', () => {
    expect(injectChartThemeColorsFromStyles('')).toBe('');
  });

  it('не трогает код, где уже есть init', () => {
    const withInit = "%%{init: {'theme':'base'}}%%\npie\n\"A\" : 1\nstyle A fill:#f00";
    expect(injectChartThemeColorsFromStyles(withInit)).toBe(withInit);
  });

  it('frontmatter перед диаграммой отбрасывается санитайзером, защитная ветка недостижима (known issue)', () => {
    // Правильно: YAML-frontmatter (--- title ---) должен сохраняться, а цвета не добавляться.
    // Сейчас sanitizeMermaidSource срезает всё до ключевого слова, и цвета инжектируются.
    const withFm = '---\ntitle: x\n---\npie\n"A" : 1\nstyle A fill:#f00';
    const out = injectChartThemeColorsFromStyles(withFm);
    expect(out).not.toContain('title: x');
    expect(out.startsWith("%%{init: {'theme':'base', 'themeVariables': {'pie1':'#f00'}}}%%")).toBe(true);
  });

  it('без цветов возвращает код без изменений', () => {
    expect(injectChartThemeColorsFromStyles('pie\n"A" : 1')).toBe('pie\n"A" : 1');
  });

  it('pie: переносит fill из style/classDef и из комментариев в themeVariables, без дублей', () => {
    const src = 'pie\n"A" : 1\nstyle A fill:#FF0000\nclassDef B fill:#00ff00\n%% color: #0000ff #ff0000';
    const out = injectChartThemeColorsFromStyles(src);
    expect(out.startsWith("%%{init: {'theme':'base', 'themeVariables': {'pie1':'#FF0000', 'pie2':'#00ff00', 'pie3':'#0000ff'}}}%%\n")).toBe(true);
  });

  it('pie: если pieN уже заданы в коде, ничего не добавляет', () => {
    const src = 'pie\n"A" : 1\n%% pie1: #ff0000\n%% "pie1": x';
    expect(injectChartThemeColorsFromStyles(src)).toBe(src);
  });

  it('xychart: добавляет plotColorPalette', () => {
    const src = 'xychart-beta\nx-axis [a, b]\nbar [1, 2]\nstyle X fill:#abc';
    const out = injectChartThemeColorsFromStyles(src);
    expect(out).toContain("'xyChart': {'plotColorPalette': '#abc'}");
    expect(out.endsWith(src)).toBe(true);
  });

  it('xychart: существующая plotColorPalette сохраняется', () => {
    const src = 'xychart-beta\nplotColorPalette: x\nstyle X fill:#abc';
    expect(injectChartThemeColorsFromStyles(src)).toBe(src);
  });

  it('другие диаграммы с style не меняются', () => {
    const src = 'graph TD\nA-->B\nstyle A fill:#abc';
    expect(injectChartThemeColorsFromStyles(src)).toBe(src);
  });

  it('устаревший алиас делает то же самое', () => {
    const src = 'pie\n"A" : 1\nstyle A fill:#abc';
    expect(injectPieThemeColorsFromStyles(src)).toBe(injectChartThemeColorsFromStyles(src));
  });
});

describe('quoteXyChartCategoryLabels', () => {
  it('не трогает не-xychart', () => {
    expect(quoteXyChartCategoryLabels('graph TD\nx-axis [а, б]')).toBe('graph TD\nx-axis [а, б]');
    expect(quoteXyChartCategoryLabels(undefined as unknown as string)).toBe('');
  });

  it('заключает кириллические и составные категории в кавычки, ASCII оставляет', () => {
    const out = quoteXyChartCategoryLabels('xychart-beta\n  x-axis [Янв, Feb, "Мар", \'Апр\', a b]\nbar [1,2]');
    expect(out).toContain('x-axis ["Янв", Feb, "Мар", \'Апр\', "a b"]');
  });

  it('числовые диапазоны и пустые списки не трогаются', () => {
    const range = 'xychart-beta\nx-axis [0 --> 100]';
    expect(quoteXyChartCategoryLabels(range)).toBe(range);
    const empty = 'xychart-beta\nx-axis [ , ]';
    expect(quoteXyChartCategoryLabels(empty)).toBe(empty);
  });

  it('экранирует кавычки и слеши внутри некавыченных категорий и учитывает экранирование в кавычках', () => {
    const out = quoteXyChartCategoryLabels('xychart-beta\nx-axis [a"b, c\\d, "q\\"x", e]');
    expect(out).toContain('"a\\"b"');
    expect(out).toContain('"c\\\\d"');
    expect(out).toContain('"q\\"x"');
  });

  it('незакрытая кавычка не ломает разбор', () => {
    const out = quoteXyChartCategoryLabels('xychart-beta\nx-axis ["abc, d]');
    expect(out).toContain('x-axis [');
  });
});

describe('repairMermaidSource / stripMermaidStyling', () => {
  it('пустой ввод -> пустая строка', () => {
    expect(repairMermaidSource('')).toBe('');
  });

  it('выкидывает style с CSS и кириллицей, оставляет валидный style', () => {
    const src = [
      'graph TD',
      'A-->B',
      'style A text-align:center',
      'style Б fill:#fff',
      'style B fill:#fff,stroke:#000',
    ].join('\n');
    expect(repairMermaidSource(src)).toBe('graph TD\nA-->B\nstyle B fill:#fff,stroke:#000');
  });

  it('style в pie/xychart отбрасывается всегда', () => {
    expect(repairMermaidSource('pie\n"A" : 1\nstyle A fill:#fff')).toBe('pie\n"A" : 1');
  });

  it('classDef/class/linkStyle/click: чистый ASCII остаётся, кириллица и CSS выкидываются', () => {
    const src = [
      'graph TD',
      'A-->B',
      'classDef ok fill:#fff',
      'classDef плохой fill:#fff',
      'class A ok',
      'linkStyle 0 stroke:#f00',
      'click A call fn()',
      'classDef w width:3px',
    ].join('\n');
    expect(repairMermaidSource(src)).toBe(
      ['graph TD', 'A-->B', 'classDef ok fill:#fff', 'class A ok', 'linkStyle 0 stroke:#f00', 'click A call fn()'].join('\n'),
    );
  });

  it('чинит `Node :: class` и `Node(Label) ::: class`', () => {
    const out = repairMermaidSource('graph TD\nStart(Старт) ::: step\nA :: step --> B');
    expect(out).toBe('graph TD\nStart(Старт)\nA --> B');
  });

  it('кавычит категории xychart после ремонта', () => {
    const out = repairMermaidSource('xychart-beta\nx-axis [Янв, Фев]\nbar [1, 2]');
    expect(out).toContain('x-axis ["Янв", "Фев"]');
  });

  it('stripMermaidStyling убирает все стилевые строки после ремонта', () => {
    const out = stripMermaidStyling('graph TD\nA-->B\nclassDef ok fill:#fff\nclass A ok\nclick A cb');
    expect(out).toBe('graph TD\nA-->B');
  });
});

describe('prepareMermaidSourceForRender', () => {
  it('цвета pie из style переносятся в init, а сам style удаляется', () => {
    const out = prepareMermaidSourceForRender('pie\n"A" : 1\nstyle A fill:#abcdef');
    expect(out.startsWith("%%{init: {'theme':'base', 'themeVariables': {'pie1':'#abcdef'}}}%%")).toBe(true);
    expect(out).not.toContain('style A');
    expect(out).toContain('"A" : 1');
  });
});

describe('splitContentWithArtifacts: ветки без fence', () => {
  it('unfenced-артефакт, уже разобранный fenced-парсером, не дублируется', () => {
    const text = ':::artifact{identifier="a" type="text/html" title="T"}\n```html\n<p>1</p>\n```\n:::\nпосле';
    const segs = splitContentWithArtifacts(text, { messageId: 'm' });
    expect(segs.filter((s) => s.kind === 'artifact')).toHaveLength(1);
    expect(segs[segs.length - 1]).toEqual({ kind: 'text', text: expect.stringContaining('после') });
  });

  it('unfenced-артефакт с хвостовым текстом: текст до и после сохраняется', () => {
    const text = 'до\n:::artifact{identifier="a" type="text/html" title="T"}\n<p>1</p>\n:::\nпосле';
    const segs = splitContentWithArtifacts(text);
    expect(segs.map((s) => s.kind)).toEqual(['text', 'artifact', 'text']);
    const art = segs[1] as any;
    expect(art.artifact.content).toContain('<p>1</p>');
    expect(art.artifact.closed).not.toBe(false);
  });
});
