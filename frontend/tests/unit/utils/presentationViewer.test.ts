import {
  artifactMetaLooksLikePresentation,
  classAttrHasSlideToken,
  coalescePresentationHtmlInMessage,
  countDeckSlideOpens,
  extractDeckSlideFragments,
  extractUnfencedPresentationHtml,
  getStablePresentationSnapshot,
  hasDeckSlideClass,
  hasPresentationStreamHints,
  isDeckPresentationHtml,
  isDeckPresentationStreaming,
  isHtmlFenceBlock,
  isHtmlFenceLanguage,
  openPresentationViewer,
  presentationLog,
  repairTruncatedPresentationHtml,
  sanitizePresentationHtmlSource,
  shouldOpenPresentationViewer,
  shouldTreatHtmlFenceAsPresentationStream,
} from '@/utils/presentationViewer';

const slide = (n: number, extra = '') => `<div class="slide">Слайд ${n}${extra}</div>`;
const DOC = (slides: string[], head = '<style>.slide{width:297mm}</style>') =>
  `<!DOCTYPE html><html><head>${head}</head><body>${slides.join('\n')}</body></html>`;

describe('class token detection', () => {
  it.each([
    ['slide', true],
    ['  slide  ', true],
    ['card slide active', true],
    ['slide-title', false],
    ['slides', false],
    ['my-slide', false],
    ['', false],
  ])('classAttrHasSlideToken(%j) = %s', (value, expected) => {
    expect(classAttrHasSlideToken(value)).toBe(expected);
  });

  it('hasDeckSlideClass finds quoted, single-quoted and bare class attributes', () => {
    expect(hasDeckSlideClass('<div class="slide">')).toBe(true);
    expect(hasDeckSlideClass("<div class='a slide b'>")).toBe(true);
    expect(hasDeckSlideClass('<div class=slide>')).toBe(true);
    expect(hasDeckSlideClass('<div\n  class = "x slide">')).toBe(true);
  });

  it('hasDeckSlideClass rejects lookalikes and empty input', () => {
    expect(hasDeckSlideClass('<div class="slide-title">')).toBe(false);
    expect(hasDeckSlideClass('<div class="slides">')).toBe(false);
    expect(hasDeckSlideClass('')).toBe(false);
    expect(hasDeckSlideClass('просто текст про slide')).toBe(false);
  });

  it('countDeckSlideOpens counts only real slide tags', () => {
    expect(countDeckSlideOpens(`${slide(1)}${slide(2)}<div class="slide-title">x</div>`)).toBe(2);
    expect(countDeckSlideOpens('<section class=slide>')).toBe(1);
    expect(countDeckSlideOpens('')).toBe(0);
  });
});

describe('fence detection', () => {
  it.each([
    ['html', true],
    ['HTML', true],
    [' htm ', true],
    ['xhtml', true],
    ['js', false],
    ['', false],
    [null, false],
    [undefined, false],
  ])('isHtmlFenceLanguage(%j) = %s', (lang, expected) => {
    expect(isHtmlFenceLanguage(lang as string | null | undefined)).toBe(expected);
  });

  it('isHtmlFenceBlock looks for an opening html fence anywhere', () => {
    expect(isHtmlFenceBlock('```html\n<div>')).toBe(true);
    expect(isHtmlFenceBlock('text\n```XHTML\n')).toBe(true);
    expect(isHtmlFenceBlock('```python\nx')).toBe(false);
    expect(isHtmlFenceBlock('')).toBe(false);
  });
});

describe('isDeckPresentationHtml', () => {
  it('requires a real slide class, html-looking markup and enough text', () => {
    expect(isDeckPresentationHtml(DOC([slide(1)]))).toBe(true);
    expect(isDeckPresentationHtml('<div class="slide">короткий</div>')).toBe(false);
    expect(
      isDeckPresentationHtml('<p class="slide-title">заголовок скилла, но не слайд</p>'.repeat(3)),
    ).toBe(false);
    expect(isDeckPresentationHtml('class="slide" но нет разметки, просто длинный текст про слайды')).toBe(false);
    expect(isDeckPresentationHtml('')).toBe(false);
  });
});

describe('stream hints', () => {
  it.each([
    '<div class="slide">',
    'src="/static/icons/logo_dark.svg"',
    'src="/static/icons_new/arrow.svg"',
    '.content-zone{}',
    '.slide-title{}',
    '.slide-header{}',
    'deck-slide',
    ':root{--deck-bg:#fff}',
    '<style>.page{width:297mm}</style>',
    '<style>.page{height:167mm}</style>',
  ])('recognises %s', (code) => {
    expect(hasPresentationStreamHints(code)).toBe(true);
  });

  it('ignores ordinary dashboards', () => {
    expect(hasPresentationStreamHints('<style>.card{color:red}</style><div>Продажи</div>')).toBe(false);
    expect(hasPresentationStreamHints('297mm без style')).toBe(false);
  });

  it('isDeckPresentationStreaming needs hints (or a finished deck)', () => {
    expect(isDeckPresentationStreaming('')).toBe(false);
    expect(isDeckPresentationStreaming('<style>')).toBe(false);
    expect(isDeckPresentationStreaming('.slide-title{')).toBe(true);
    expect(isDeckPresentationStreaming(DOC([slide(1)]))).toBe(true);
  });
});

describe('shouldOpenPresentationViewer', () => {
  it('opens for finished decks regardless of options', () => {
    expect(shouldOpenPresentationViewer(DOC([slide(1)]))).toBe(true);
  });

  it('ordinary html stays an artifact card', () => {
    const html = '<html><body><div>Дашборд</div></body></html>';
    expect(shouldOpenPresentationViewer(html)).toBe(false);
    expect(shouldOpenPresentationViewer(html, { presentationExpected: true })).toBe(false);
  });

  it('while streaming with a presentation skill the viewer opens early', () => {
    const opts = { presentationExpected: true, isStreaming: true };
    expect(shouldOpenPresentationViewer('', { ...opts, language: 'html' })).toBe(true);
    expect(shouldOpenPresentationViewer('<!DOCTYPE html>', opts)).toBe(true);
    expect(shouldOpenPresentationViewer('<style>@font-face{}</style>', opts)).toBe(true);
    expect(shouldOpenPresentationViewer('font-family: Cera CY', opts)).toBe(true);
    expect(shouldOpenPresentationViewer('<div>просто</div>', opts)).toBe(false);
  });

  it('not streaming but expected: only with deck hints', () => {
    expect(shouldOpenPresentationViewer('.slide-title{}', { presentationExpected: true })).toBe(true);
    expect(shouldOpenPresentationViewer('<!DOCTYPE html>', { presentationExpected: true })).toBe(false);
  });
});

describe('shouldTreatHtmlFenceAsPresentationStream', () => {
  const on = { isStreaming: true, presentationExpected: true };

  it('needs both streaming and an expected presentation', () => {
    expect(shouldTreatHtmlFenceAsPresentationStream('', '```html\n', 'html', {})).toBe(false);
    expect(shouldTreatHtmlFenceAsPresentationStream('', '```html\n', 'html', { isStreaming: true })).toBe(false);
    expect(shouldTreatHtmlFenceAsPresentationStream('', '```html\n', 'html', on)).toBe(true);
  });

  it('code fences of other languages are never presentations', () => {
    expect(shouldTreatHtmlFenceAsPresentationStream('.slide-title{}', '```python\n', 'python', on)).toBe(false);
    expect(shouldTreatHtmlFenceAsPresentationStream('x', '```mermaid\n', 'MERMAID', on)).toBe(false);
  });

  it('a fence without language can be decided by its body', () => {
    expect(shouldTreatHtmlFenceAsPresentationStream('.content-zone{}', '```\n', '', on)).toBe(true);
    expect(shouldTreatHtmlFenceAsPresentationStream('обычный текст', '```\n', '', on)).toBe(false);
  });
});

describe('extractUnfencedPresentationHtml', () => {
  it('returns text untouched when there is no markup', () => {
    expect(extractUnfencedPresentationHtml('просто ответ')).toEqual({ before: 'просто ответ', html: null, after: '' });
    expect(extractUnfencedPresentationHtml('')).toEqual({ before: '', html: null, after: '' });
  });

  it('does not touch text that already contains a markdown fence', () => {
    const text = `вот\n\`\`\`html\n${DOC([slide(1)])}\n\`\`\``;
    expect(extractUnfencedPresentationHtml(text)).toEqual({ before: text, html: null, after: '' });
  });

  it('splits intro text from a raw deck document', () => {
    const out = extractUnfencedPresentationHtml(`Готово!\n${DOC([slide(1), slide(2)])}`);
    expect(out.before).toBe('Готово!\n');
    expect(out.html?.startsWith('<!DOCTYPE html>')).toBe(true);
  });

  it('a bare slide fragment is treated as a deck', () => {
    const out = extractUnfencedPresentationHtml(`${slide(1)} ${'x'.repeat(50)}`);
    expect(out.html).toContain('class="slide"');
  });

  it('ordinary html with a style tag is not a presentation', () => {
    const text = 'вот стиль <style>.a{color:red}</style> и всё';
    expect(extractUnfencedPresentationHtml(text)).toEqual({ before: text, html: null, after: '' });
  });

  it('while streaming with a skill, an early doctype is enough', () => {
    const out = extractUnfencedPresentationHtml('Делаю\n<!DOCTYPE html><html><head><style>', {
      presentationExpected: true,
      isStreaming: true,
    });
    expect(out.before).toBe('Делаю\n');
    expect(out.html).toContain('<!DOCTYPE html>');
  });
});

describe('slide fragments', () => {
  it('splits at every slide opening', () => {
    const frags = extractDeckSlideFragments(`<body>${slide(1)}\n${slide(2)}\n${slide(3)}</body>`);
    expect(frags).toHaveLength(3);
    expect(frags[0].startsWith('<div class="slide">Слайд 1')).toBe(true);
    expect(frags[2]).toContain('Слайд 3');
  });

  it('works on a truncated stream', () => {
    const frags = extractDeckSlideFragments(`${slide(1)}<div class="slide"><p>обор`);
    expect(frags).toHaveLength(2);
    expect(frags[1]).toContain('обор');
  });

  it('empty or slide-less input yields nothing', () => {
    expect(extractDeckSlideFragments('')).toEqual([]);
    expect(extractDeckSlideFragments('<div class="slide-title">x</div>')).toEqual([]);
  });
});

describe('sanitize and repair', () => {
  it('strips BOM and a surrounding fence', () => {
    expect(sanitizePresentationHtmlSource('﻿```html\n<div class="slide">1</div>\n```')).toBe(
      '<div class="slide">1</div>',
    );
  });

  it('strips an opening fence of a stream and an unterminated trailing comment', () => {
    expect(sanitizePresentationHtmlSource('```html\n<div class="slide">1</div><!-- не закрыт')).toBe(
      '<div class="slide">1</div>',
    );
  });

  it('keeps balanced comments', () => {
    expect(sanitizePresentationHtmlSource('<div><!-- ok --></div>')).toBe('<div><!-- ok --></div>');
  });

  it('is a no-op for empty input', () => {
    expect(sanitizePresentationHtmlSource('')).toBe('');
    expect(sanitizePresentationHtmlSource('   ')).toBe('');
  });

  it('repairs a tag cut off by max_tokens', () => {
    expect(repairTruncatedPresentationHtml('<div class="slide">1</div><div class="sl')).toBe(
      '<div class="slide">1</div>',
    );
    expect(repairTruncatedPresentationHtml('<div>ok</div>')).toBe('<div>ok</div>');
    expect(repairTruncatedPresentationHtml('')).toBe('');
  });
});

describe('coalescePresentationHtmlInMessage', () => {
  it('leaves messages without slides alone', () => {
    expect(coalescePresentationHtmlInMessage('обычный ответ')).toBe('обычный ответ');
    expect(coalescePresentationHtmlInMessage('')).toBe('');
  });

  it('merges several fenced blocks into one document', () => {
    const text = ['Вот презентация', '```html', DOC([slide(1)]), '```', 'продолжение', '```html', slide(2), '```'].join('\n');
    const out = coalescePresentationHtmlInMessage(text);
    expect(out.startsWith('Вот презентация')).toBe(true);
    expect(out.match(/```html/g)).toHaveLength(1);
    expect(out).toContain('Слайд 1');
    expect(out).toContain('Слайд 2');
    expect(out.trimEnd().endsWith('```')).toBe(true);
    expect(out).toContain('</html>');
  });

  it('merges a fenced block with a raw doctype block', () => {
    const text = `\`\`\`html\n${slide(1)}\n\`\`\`\n${DOC([slide(2)])}`;
    expect(extractDeckSlideFragments(coalescePresentationHtmlInMessage(text))).toHaveLength(2);
  });

  it('drops duplicated slides', () => {
    const out = coalescePresentationHtmlInMessage(`${DOC([slide(1)])}\n${slide(1)}`);
    expect(extractDeckSlideFragments(out)).toHaveLength(1);
  });

  it('adds fallback chrome when the model returned an empty head', () => {
    const out = coalescePresentationHtmlInMessage(`<html><head></head><body>${slide(1)}</body></html>`);
    expect(out).toContain('<style>');
    expect(out).toContain('297mm');
  });

  it('removes fence noise and the language word that leaked on its own line', () => {
    const out = coalescePresentationHtmlInMessage(`\`\`\`\nhtml\n${DOC([slide(1)])}\n\`\`\``);
    expect(out).not.toMatch(/^html$/m);
    expect(extractDeckSlideFragments(out)).toHaveLength(1);
  });
});

describe('artifactMetaLooksLikePresentation', () => {
  it.each([
    [{ title: 'Квартальная презентация' }, true],
    [{ identifier: 'sales-deck-html' }, true],
    [{ title: 'Слайды' }, true],
    [{ type: 'application/vnd.presentation' }, true],
    [{ title: 'Дашборд продаж', identifier: 'dash', type: 'html' }, false],
    [undefined, false],
    [{}, false],
  ])('%j -> %s', (meta, expected) => {
    expect(artifactMetaLooksLikePresentation(meta as any)).toBe(expected);
  });
});

describe('getStablePresentationSnapshot', () => {
  const three = DOC([slide(1), slide(2), slide(3)]);

  it('while streaming the last, possibly truncated slide is withheld', () => {
    const snap = getStablePresentationSnapshot(three, true);
    expect(snap.pending).toBe(true);
    expect(snap.startedCount).toBe(3);
    expect(snap.readyCount).toBe(2);
    expect(extractDeckSlideFragments(snap.html as string)).toHaveLength(2);
  });

  it('shows nothing until a second slide has started', () => {
    expect(getStablePresentationSnapshot(DOC([slide(1)]), true)).toEqual({
      html: null,
      readyCount: 0,
      startedCount: 1,
      pending: true,
    });
  });

  it('maxReadyCount limits the reveal', () => {
    const snap = getStablePresentationSnapshot(three, true, { maxReadyCount: 1 });
    expect(snap.readyCount).toBe(1);
    expect(extractDeckSlideFragments(snap.html as string)).toHaveLength(1);
    expect(getStablePresentationSnapshot(three, true, { maxReadyCount: 0 }).html).toBeNull();
  });

  it('empty source is pending only while streaming', () => {
    expect(getStablePresentationSnapshot('', true)).toEqual({ html: null, readyCount: 0, startedCount: 0, pending: true });
    expect(getStablePresentationSnapshot('', false)).toEqual({ html: null, readyCount: 0, startedCount: 0, pending: false });
  });

  it('a finished deck shows all slides', () => {
    const snap = getStablePresentationSnapshot(three, false);
    expect(snap).toMatchObject({ readyCount: 3, startedCount: 3, pending: false });
    expect(snap.html).toContain('Слайд 3');
  });

  it('a finished deck cut off mid-tag is repaired instead of losing slides', () => {
    const cut = `${DOC([slide(1), slide(2)]).replace('</body></html>', '')}<div class="sl`;
    const snap = getStablePresentationSnapshot(cut, false);
    expect(snap.readyCount).toBe(2);
    expect(snap.pending).toBe(false);
  });

  it('a finished text without slides yields no html', () => {
    expect(getStablePresentationSnapshot('<p>просто</p>', false)).toEqual({
      html: null,
      readyCount: 0,
      startedCount: 0,
      pending: false,
    });
  });

  it('adds fallback styles when the document has no <style>', () => {
    const snap = getStablePresentationSnapshot(`<html><head></head><body>${slide(1)}${slide(2)}</body></html>`, false);
    expect(snap.html).toContain('297mm');
  });
});

describe('openPresentationViewer', () => {
  const originalOpen = window.open;
  afterEach(() => {
    window.open = originalOpen;
    localStorage.clear();
    jest.restoreAllMocks();
  });

  it('stores sanitized html under a unique key and opens the viewer page', () => {
    const open = jest.fn(() => ({}) as Window);
    window.open = open as unknown as typeof window.open;

    openPresentationViewer('```html\n<div class="slide">1</div>\n```');

    const url = (open.mock.calls[0] as unknown[])[0] as string;
    const key = decodeURIComponent(url.split('key=')[1]);
    expect(url).toContain('/presentation-viewer.html?key=');
    expect(localStorage.getItem(`astrachat:presentation:${key}`)).toBe('<div class="slide">1</div>');
  });

  it('removes the stored html and throws when the popup is blocked', () => {
    window.open = jest.fn(() => null) as unknown as typeof window.open;
    expect(() => openPresentationViewer('<div class="slide">1</div>')).toThrow('заблокировано');
    expect(Object.keys(localStorage).filter((k) => k.startsWith('astrachat:presentation:'))).toEqual([]);
  });

  it('throws a readable error when storage is full', () => {
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    window.open = jest.fn() as unknown as typeof window.open;
    expect(() => openPresentationViewer('<div class="slide">1</div>')).toThrow('слишком большой объём');
    expect(window.open).not.toHaveBeenCalled();
  });
});

describe('presentationLog', () => {
  afterEach(() => {
    localStorage.clear();
    jest.restoreAllMocks();
  });

  it('is silent unless the debug flag is set', () => {
    const info = jest.spyOn(console, 'info').mockImplementation(() => undefined);
    presentationLog('event', { a: 1 });
    expect(info).not.toHaveBeenCalled();
  });

  it('logs with a prefix when the flag is on', () => {
    const info = jest.spyOn(console, 'info').mockImplementation(() => undefined);
    localStorage.setItem('astra:debug:presentation', '1');
    presentationLog('event', { a: 1 });
    presentationLog('bare');
    expect(info).toHaveBeenNthCalledWith(1, '[presentation] event', { a: 1 });
    expect(info).toHaveBeenNthCalledWith(2, '[presentation] bare', {});
  });
});
