import {
  extractChatMath,
  hasMathPlaceholder,
  mathPlaceholder,
  preprocessArtifactLatex,
  protectMathPlaceholders,
  protectRegions,
  renderKatexHtml,
  restoreRegions,
  MATH_PLACEHOLDER_ONE_RE,
  MATH_PLACEHOLDER_SPLIT_RE,
} from '@/utils/chatMath';

describe('placeholders', () => {
  it('builds and detects placeholders', () => {
    expect(mathPlaceholder(3)).toBe('%%ASTRA_MATH_3%%');
    expect(hasMathPlaceholder('текст %%ASTRA_MATH_0%% текст')).toBe(true);
    expect(hasMathPlaceholder('обычный текст')).toBe(false);
  });

  it('exposes regexes that match the placeholder format', () => {
    expect(MATH_PLACEHOLDER_ONE_RE.exec('%%ASTRA_MATH_12%%')?.[1]).toBe('12');
    expect(MATH_PLACEHOLDER_ONE_RE.test('x%%ASTRA_MATH_1%%')).toBe(false);
    expect('a %%ASTRA_MATH_0%% b %%ASTRA_MATH_1%%'.split(MATH_PLACEHOLDER_SPLIT_RE)).toEqual([
      'a ',
      '%%ASTRA_MATH_0%%',
      ' b ',
      '%%ASTRA_MATH_1%%',
      '',
    ]);
  });
});

describe('protectRegions / restoreRegions', () => {
  it('round-trips protected regions', () => {
    const original = 'до `a` и `b` после';
    const { text, regions, tokenPrefix } = protectRegions(original, /`[^`]+`/g, 'X');

    expect(text).toBe('до %%X0%% и %%X1%% после');
    expect(regions).toEqual(['`a`', '`b`']);
    expect(restoreRegions(text, regions, tokenPrefix)).toBe(original);
  });

  it('does not confuse token 1 with token 10', () => {
    const original = Array.from({ length: 12 }, (_, i) => `[${i}]`).join(' ');
    const { text, regions, tokenPrefix } = protectRegions(original, /\[\d+\]/g, 'T');

    expect(restoreRegions(text, regions, tokenPrefix)).toBe(original);
  });

  it('protects math placeholders from markdown transforms', () => {
    const { text, regions } = protectMathPlaceholders('a %%ASTRA_MATH_0%% b');

    expect(text).not.toContain('ASTRA_MATH_0');
    expect(regions).toEqual(['%%ASTRA_MATH_0%%']);
  });
});

describe('extractChatMath', () => {
  it('returns the input when there is nothing math-like', () => {
    expect(extractChatMath('')).toEqual({ text: '', blocks: [] });
    expect(extractChatMath('просто текст')).toEqual({ text: 'просто текст', blocks: [] });
  });

  it('extracts display math with $$', () => {
    const { text, blocks } = extractChatMath('до $$ x^2 $$ после');

    expect(blocks).toEqual([{ latex: 'x^2', displayMode: true }]);
    expect(text).toBe(`до ${mathPlaceholder(0)} после`);
  });

  it('extracts multiline display math', () => {
    const { blocks } = extractChatMath('$$\na + b\n$$');

    expect(blocks).toEqual([{ latex: 'a + b', displayMode: true }]);
  });

  it('extracts \\[...\\] as display and \\(...\\) as inline', () => {
    const { blocks } = extractChatMath('\\[ E=mc^2 \\] и \\( a_1 \\)');

    expect(blocks).toEqual([
      { latex: 'E=mc^2', displayMode: true },
      { latex: 'a_1', displayMode: false },
    ]);
  });

  it('extracts inline $...$', () => {
    const { text, blocks } = extractChatMath('формула $E = mc^2$ здесь');

    expect(blocks).toEqual([{ latex: 'E = mc^2', displayMode: false }]);
    expect(text).toBe(`формула ${mathPlaceholder(0)} здесь`);
  });

  it('numbers placeholders in order of extraction', () => {
    const { text, blocks } = extractChatMath('$$A$$ и $b$');

    expect(blocks.map((b) => b.latex)).toEqual(['A', 'b']);
    expect(text).toContain(mathPlaceholder(0));
    expect(text).toContain(mathPlaceholder(1));
  });

  it.each(['Стоит $100 сегодня', 'Стоит 100$ сегодня', 'Бюджет $1,000.50 и $5K', 'Цена 5K$'])(
    'leaves currency alone: %p',
    (input) => {
      expect(extractChatMath(input)).toEqual({ text: input, blocks: [] });
    },
  );

  it('does not join two currency amounts into one formula', () => {
    const input = 'Купил за $100 и продал за 150$';

    expect(extractChatMath(input)).toEqual({ text: input, blocks: [] });
  });

  it('keeps single-digit math like $1$ and $0$', () => {
    const { blocks } = extractChatMath('значения $1$ и $0$');

    expect(blocks.map((b) => b.latex)).toEqual(['1', '0']);
  });

  it('does not treat a trailing digit before the closing $ as currency', () => {
    const { blocks } = extractChatMath('$E = mc2$');

    expect(blocks).toEqual([{ latex: 'E = mc2', displayMode: false }]);
  });

  it('ignores math inside fenced code', () => {
    const input = 'текст\n```\n$$ x $$ и $y$\n```\nконец';

    expect(extractChatMath(input)).toEqual({ text: input, blocks: [] });
  });

  it('ignores math inside an unterminated code fence', () => {
    const input = '```js\nconst a = "$x$";';

    expect(extractChatMath(input)).toEqual({ text: input, blocks: [] });
  });

  it('ignores math inside inline code but still finds math outside', () => {
    const { text, blocks } = extractChatMath('код `$x$` и формула $y$');

    expect(blocks).toEqual([{ latex: 'y', displayMode: false }]);
    expect(text).toContain('`$x$`');
  });

  it('does not match an escaped dollar', () => {
    const input = 'цена \\$5 и \\$6';

    expect(extractChatMath(input).blocks).toEqual([]);
  });

  it('does not run inline math across lines', () => {
    const input = 'первая $a\nвторая b$';

    expect(extractChatMath(input).blocks).toEqual([]);
  });
});

describe('preprocessArtifactLatex', () => {
  it('returns text without dollars unchanged', () => {
    expect(preprocessArtifactLatex('')).toBe('');
    expect(preprocessArtifactLatex('нет денег')).toBe('нет денег');
  });

  it('escapes currency so remark-math does not parse it', () => {
    expect(preprocessArtifactLatex('Цена $100 и 200$')).toBe('Цена \\$100 и 200\\$');
  });

  it('keeps real formulas untouched', () => {
    expect(preprocessArtifactLatex('формула $x^2$ и $$y$$')).toBe('формула $x^2$ и $$y$$');
  });

  it('does not touch code', () => {
    const input = 'код `cost = $100` и\n```\n$5\n```';

    expect(preprocessArtifactLatex(input)).toBe(input);
  });
});

describe('renderKatexHtml', () => {
  it('renders valid latex to katex html', () => {
    const html = renderKatexHtml('x^2', false);

    expect(html).toContain('katex');
    expect(html).not.toContain('katex-display');
  });

  it('renders display mode', () => {
    expect(renderKatexHtml('x^2', true)).toContain('katex-display');
  });

  it('does not throw on invalid latex', () => {
    expect(() => renderKatexHtml('\\frac{', false)).not.toThrow();
    expect(renderKatexHtml('\\frac{', false)).toContain('katex');
  });

  it('does not allow raw HTML or script injection', () => {
    const html = renderKatexHtml('\\text{<img src=x onerror=alert(1)>}', false);

    expect(html).not.toMatch(/<img/i);
    expect(html).not.toContain('<script');
  });

  it('does not follow links or inline styles without trust', () => {
    const html = renderKatexHtml('\\href{javascript:alert(1)}{click}', false);

    expect(html).not.toContain('javascript:');
    expect(html).not.toMatch(/<a\b/i);
  });
});
