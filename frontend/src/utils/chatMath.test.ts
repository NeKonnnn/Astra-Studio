import {
  extractChatMath,
  hasMathPlaceholder,
  mathPlaceholder,
  preprocessArtifactLatex,
  protectMathPlaceholders,
  protectRegions,
  renderKatexHtml,
  restoreRegions,
} from './chatMath';

describe('mathPlaceholder', () => {
  it('формирует ASCII-плейсхолдер', () => {
    expect(mathPlaceholder(0)).toBe('%%ASTRA_MATH_0%%');
    expect(mathPlaceholder(12)).toBe('%%ASTRA_MATH_12%%');
  });

  it('hasMathPlaceholder находит плейсхолдер в тексте', () => {
    expect(hasMathPlaceholder(`до ${mathPlaceholder(3)} после`)).toBe(true);
    expect(hasMathPlaceholder('обычный текст')).toBe(false);
  });
});

describe('protectRegions / restoreRegions', () => {
  it('прячет и возвращает регионы без потерь', () => {
    const src = 'a `x` b `y` c';
    const { text, regions, tokenPrefix } = protectRegions(src, /`[^`]+`/g, 'T');
    expect(text).toBe('a %%T0%% b %%T1%% c');
    expect(regions).toEqual(['`x`', '`y`']);
    expect(restoreRegions(text, regions, tokenPrefix)).toBe(src);
  });

  it('не путает T1 и T10 при восстановлении', () => {
    const src = Array.from({ length: 12 }, (_, i) => `[${i}]`).join(' ');
    const { text, regions, tokenPrefix } = protectRegions(src, /\[\d+\]/g, 'T');
    expect(restoreRegions(text, regions, tokenPrefix)).toBe(src);
  });

  it('без совпадений возвращает исходный текст', () => {
    const { text, regions } = protectRegions('ничего', /\d+/g);
    expect(text).toBe('ничего');
    expect(regions).toEqual([]);
  });
});

describe('protectMathPlaceholders', () => {
  it('прячет плейсхолдеры формул от markdown', () => {
    const src = `x ${mathPlaceholder(0)} y ${mathPlaceholder(1)}`;
    const { text, regions, tokenPrefix } = protectMathPlaceholders(src);
    expect(hasMathPlaceholder(text)).toBe(false);
    expect(restoreRegions(text, regions, tokenPrefix)).toBe(src);
  });
});

describe('extractChatMath', () => {
  it('пустая строка и текст без math не меняются', () => {
    expect(extractChatMath('')).toEqual({ text: '', blocks: [] });
    expect(extractChatMath('просто текст')).toEqual({ text: 'просто текст', blocks: [] });
  });

  it('inline $...$', () => {
    const { text, blocks } = extractChatMath('Формула $a^n + b^n$ верна');
    expect(blocks).toEqual([{ latex: 'a^n + b^n', displayMode: false }]);
    expect(text).toBe(`Формула ${mathPlaceholder(0)} верна`);
  });

  it('display $$...$$ (в том числе многострочный)', () => {
    const { text, blocks } = extractChatMath('До\n$$\n\\int_0^1 x\\,dx\n$$\nПосле');
    expect(blocks).toEqual([{ latex: '\\int_0^1 x\\,dx', displayMode: true }]);
    expect(text).toBe(`До\n${mathPlaceholder(0)}\nПосле`);
  });

  it('\\(...\\) — inline, \\[...\\] — display', () => {
    const { blocks } = extractChatMath('\\(x\\) и \\[y\\]');
    expect(blocks).toEqual([
      { latex: 'y', displayMode: true },
      { latex: 'x', displayMode: false },
    ]);
  });

  it('несколько формул нумеруются по порядку вырезания', () => {
    const { text, blocks } = extractChatMath('$a$ и $b$');
    expect(blocks.map((b) => b.latex)).toEqual(['a', 'b']);
    expect(text).toBe(`${mathPlaceholder(0)} и ${mathPlaceholder(1)}`);
  });

  it('не трогает fenced-код и inline-код', () => {
    const src = 'Код `$x$` и\n```\n$$y$$\n```\nконец';
    const { text, blocks } = extractChatMath(src);
    expect(blocks).toEqual([]);
    expect(text).toBe(src);
  });

  it('незакрытый code fence тоже защищён', () => {
    const src = 'текст\n```js\nconst a = "$x$";';
    expect(extractChatMath(src)).toEqual({ text: src, blocks: [] });
  });

  it.each([
    ['Цена $100 и $200', '$100 и $200'],
    ['Всего 100$ и 5K$', '100$ и 5K$'],
    ['Бюджет $1,000.50 на год', '$1,000.50'],
  ])('валюта не становится формулой: %s', (src, fragment) => {
    const { text, blocks } = extractChatMath(src);
    expect(blocks).toEqual([]);
    expect(text).toContain(fragment);
    expect(text).toBe(src);
  });

  it('$1$ и $0$ — это формулы, а не валюта', () => {
    const { blocks } = extractChatMath('Значения $0$ и $1$');
    expect(blocks.map((b) => b.latex)).toEqual(['0', '1']);
  });

  it('формула рядом с валютой', () => {
    const { text, blocks } = extractChatMath('Стоит $100, а $x^n$ — формула');
    expect(blocks).toEqual([{ latex: 'x^n', displayMode: false }]);
    expect(text).toContain('$100');
  });

  it('экранированный \\$ не открывает формулу', () => {
    const src = 'цена \\$x\\$ тут';
    expect(extractChatMath(src).blocks).toEqual([]);
  });

  it('inline-формула не пересекает перенос строки', () => {
    expect(extractChatMath('$a\nb$').blocks).toEqual([]);
  });

  it('trim содержимого формулы', () => {
    expect(extractChatMath('$  x  $').blocks[0].latex).toBe('x');
  });
});

describe('renderKatexHtml', () => {
  it('рендерит корректный LaTeX в HTML KaTeX', () => {
    const html = renderKatexHtml('x^2', false);
    expect(html).toContain('katex');
    expect(html).not.toContain('katex-display');
  });

  it('display-режим добавляет katex-display', () => {
    expect(renderKatexHtml('x^2', true)).toContain('katex-display');
  });

  it('некорректный LaTeX не бросает исключение', () => {
    expect(() => renderKatexHtml('\\frac{', false)).not.toThrow();
    expect(typeof renderKatexHtml('\\unknowncommand{x}', false)).toBe('string');
  });

  it('trust отключён: \\href с javascript: не даёт ссылку', () => {
    const html = renderKatexHtml('\\href{javascript:alert(1)}{x}', false);
    expect(html).not.toMatch(/href="javascript:/i);
  });
});

describe('preprocessArtifactLatex', () => {
  it('пустой текст и текст без $ возвращаются как есть', () => {
    expect(preprocessArtifactLatex('')).toBe('');
    expect(preprocessArtifactLatex('без долларов')).toBe('без долларов');
  });

  it('экранирует валюту', () => {
    expect(preprocessArtifactLatex('Цена $100 и 50$')).toBe('Цена \\$100 и 50\\$');
  });

  it('формулы остаются нетронутыми', () => {
    expect(preprocessArtifactLatex('Формула $x^n$')).toBe('Формула $x^n$');
    expect(preprocessArtifactLatex('Формула $$x^2$$')).toBe('Формула $$x^2$$');
  });

  it('код не меняется', () => {
    const src = 'Цена `$100` и\n```\n$200\n```';
    expect(preprocessArtifactLatex(src)).toBe(src);
  });
});

// Регрессия: CURRENCY_POSTFIX_RE принимал `2$` в конце формулы за валюту `100$`:
// inline-формула, оканчивающаяся цифрой, не распознавалась, а preprocessArtifactLatex
// экранировал её закрывающий `$`. Теперь `5$` считается валютой, только если перед ним
// на строке чётное число «математических» `$`.
describe('inline-формулы, оканчивающиеся цифрой', () => {
  it.each(['$x^2$', '$E=mc^2$', '$a_1$', '$n=5$', '$x^2 + 1$', '$a_{10}$', '$f(x) = x + 2$'])(
    'extractChatMath распознаёт %s',
    (src) => {
      const { blocks, text } = extractChatMath(src);
      expect(blocks).toHaveLength(1);
      expect(blocks[0].displayMode).toBe(false);
      expect(text).toBe(mathPlaceholder(0));
    },
  );

  it.each(['$x^2$', '$E=mc^2$', '$a_1$', '$x^2 + 1$', '$n=5$'])('preprocessArtifactLatex не портит %s', (src) => {
    expect(preprocessArtifactLatex(src)).toBe(src);
  });

  it('несколько формул с цифрой на конце в одной строке', () => {
    const { blocks, text } = extractChatMath('Даны $x^2$ и $y_1$, тогда $x^2 + y_1$.');
    expect(blocks.map((b) => b.latex)).toEqual(['x^2', 'y_1', 'x^2 + y_1']);
    expect(text).toBe(`Даны ${mathPlaceholder(0)} и ${mathPlaceholder(1)}, тогда ${mathPlaceholder(2)}.`);
  });

  it('формула с цифрой на конце и валюта в одной строке', () => {
    const { blocks, text } = extractChatMath('Формула $x^2$ стоит 5$.');
    expect(blocks.map((b) => b.latex)).toEqual(['x^2']);
    expect(text).toBe(`Формула ${mathPlaceholder(0)} стоит 5$.`);
  });

  it('валюта до формулы не ломает формулу', () => {
    const { blocks, text } = extractChatMath('Цена 100$, формула $x^2$.');
    expect(blocks.map((b) => b.latex)).toEqual(['x^2']);
    expect(text).toBe(`Цена 100$, формула ${mathPlaceholder(0)}.`);
  });

  it('две суммы в постфиксной записи по-прежнему валюта', () => {
    const src = 'Всего 100$ и 50$';
    expect(extractChatMath(src)).toEqual({ text: src, blocks: [] });
    expect(preprocessArtifactLatex(src)).toBe('Всего 100\\$ и 50\\$');
  });

  it('подсчёт открытых $ идёт по строкам: формула на предыдущей строке не влияет', () => {
    const src = 'Формула $x^2$\nЦена 5$';
    const { blocks, text } = extractChatMath(src);
    expect(blocks.map((b) => b.latex)).toEqual(['x^2']);
    expect(text).toBe(`Формула ${mathPlaceholder(0)}\nЦена 5$`);
  });

  it('незакрытый $ на предыдущей строке не превращает валюту в формулу', () => {
    const src = 'Одинокий $\nЦена 5$';
    expect(extractChatMath(src).blocks).toEqual([]);
  });

  it('экранированный \\$ не считается открывающим', () => {
    expect(preprocessArtifactLatex('Цена \\$5 и 7$')).toBe('Цена \\$5 и 7\\$');
  });

  it('display-формула с цифрой на конце не затрагивается', () => {
    const { blocks } = extractChatMath('$$x^2$$');
    expect(blocks).toEqual([{ latex: 'x^2', displayMode: true }]);
    expect(preprocessArtifactLatex('$$x^2$$')).toBe('$$x^2$$');
  });
});
