import React from 'react';
import { render } from '@testing-library/react';
import '@testing-library/jest-dom';
import ChatInlineHtml from '@/components/ChatInlineHtml';

let mockOverrideNodes: any[] | null = null;
jest.mock('@/utils/chatInlineHtml', () => {
  const actual = jest.requireActual('@/utils/chatInlineHtml');
  return {
    ...actual,
    parseChatInlineHtml: (t: string) =>
      mockOverrideNodes ? mockOverrideNodes : actual.parseChatInlineHtml(t),
  };
});

function html(text: string, keyPrefix?: string) {
  const { container } = render(<ChatInlineHtml text={text} keyPrefix={keyPrefix} />);
  return container;
}

describe('ChatInlineHtml: рендер', () => {
  beforeEach(() => {
    mockOverrideNodes = null;
  });

  it('пустой текст -> ничего', () => {
    expect(html('')).toBeEmptyDOMElement();
  });

  it('текст, из которого не получилось ни одного узла, -> ничего', () => {
    mockOverrideNodes = [];
    expect(html('что-то')).toBeEmptyDOMElement();
  });

  it('обычный текст рендерится как текстовый узел', () => {
    const c = html('просто текст');
    expect(c.textContent).toBe('просто текст');
    expect(c.children).toHaveLength(0);
  });

  it('синонимы форматирования приводятся к каноническим тегам', () => {
    const c = html('<b>ж</b> <i>к</i> <s>з</s> <u>п</u> <ins>в</ins> <del>у</del> <mark>м</mark>');
    expect(c.querySelector('strong')).toHaveTextContent('ж');
    expect(c.querySelector('em')).toHaveTextContent('к');
    expect(c.querySelectorAll('del')).toHaveLength(2);
    expect(c.querySelector('u')).toHaveTextContent('п');
    expect(c.querySelector('ins')).toHaveTextContent('в');
    expect(c.querySelector('mark')).toHaveTextContent('м');
    expect(['bold', '700']).toContain(getComputedStyle(c.querySelector('strong')!).fontWeight);
    expect(getComputedStyle(c.querySelector('u')!).textDecoration).toContain('underline');
  });

  it('small/big/sup/sub/cite', () => {
    const c = html('<small>a</small><big>b</big><sup>c</sup><sub>d</sub><cite>e</cite>');
    expect(c.querySelector('small')).toHaveTextContent('a');
    expect(c.querySelector('span')).toHaveTextContent('b');
    expect(getComputedStyle(c.querySelector('span')!).fontSize).toBe('1.2em');
    expect(c.querySelector('sup')).toHaveTextContent('c');
    expect(c.querySelector('sub')).toHaveTextContent('d');
    expect(getComputedStyle(c.querySelector('cite')!).fontStyle).toBe('italic');
  });

  it('код: code/kbd/samp/var/tt и pre', () => {
    const c = html('<code>a</code><kbd>b</kbd><samp>c</samp><var>d</var><tt>e</tt><pre>f\ng</pre>');
    expect(c.querySelectorAll('code')).toHaveLength(2);
    expect(c.querySelector('kbd')).toHaveTextContent('b');
    expect(c.querySelector('samp')).toHaveTextContent('c');
    expect(c.querySelector('var')).toHaveTextContent('d');
    const pre = c.querySelector('pre')!;
    expect(pre.textContent).toBe('f\ng');
    expect(getComputedStyle(pre).whiteSpace).toBe('pre-wrap');
  });

  it('br, hr и wbr', () => {
    const c = html('а<br>б<hr>в<wbr>г');
    expect(c.querySelector('br')).not.toBeNull();
    expect(c.querySelector('hr')).not.toBeNull();
    expect(c.querySelector('wbr')).not.toBeNull();
  });

  it('заголовки h1..h6 с убывающим размером шрифта', () => {
    const c = html('<h1>1</h1><h2>2</h2><h3>3</h3><h4>4</h4><h5>5</h5><h6>6</h6>');
    const sizes = ['1.55em', '1.35em', '1.2em', '1.1em', '1.05em', '1em'];
    sizes.forEach((size, i) => {
      const el = c.querySelector(`h${i + 1}`)!;
      expect(el).toHaveTextContent(String(i + 1));
      expect(getComputedStyle(el).fontSize).toBe(size);
    });
  });

  it('блочные контейнеры сохраняют свой тег', () => {
    const tags = ['p', 'div', 'address', 'article', 'section', 'aside', 'header', 'footer', 'main', 'nav', 'figure', 'figcaption'];
    const c = html(tags.map((t) => `<${t}>${t}</${t}>`).join(''));
    tags.forEach((t) => expect(c.querySelector(t)).toHaveTextContent(t));
  });

  it('blockquote с безопасной cite', () => {
    const c = html('<blockquote cite="https://example.com/src">цитата</blockquote>');
    const q = c.querySelector('blockquote')!;
    expect(q).toHaveTextContent('цитата');
    expect(q.getAttribute('cite')).toBe('https://example.com/src');
  });

  it('q с cite, data, time, abbr, dfn', () => {
    const c = html(
      '<q cite="https://e.org">q</q><data value="7" title="д">d</data>' +
        '<time datetime="2024" title="т">t</time><abbr title="расш">ab</abbr><acronym title="а">ac</acronym><dfn title="опр">df</dfn>',
    );
    expect(c.querySelector('q')!.getAttribute('cite')).toBe('https://e.org');
    expect(c.querySelector('data')!.getAttribute('value')).toBe('7');
    expect(c.querySelector('data')!.getAttribute('title')).toBe('д');
    expect(c.querySelector('time')!.getAttribute('datetime')).toBe('2024');
    expect(c.querySelectorAll('abbr')).toHaveLength(2);
    expect(c.querySelector('abbr')!.getAttribute('title')).toBe('расш');
    expect(c.querySelector('dfn')!.getAttribute('title')).toBe('опр');
  });

  it('span: title/lang/dir; ruby/rt/rp/bdi с dir, bdo по умолчанию ltr', () => {
    const c = html(
      '<span title="т" lang="ru" dir="rtl">s</span><ruby>漢<rp>(</rp><rt>kan</rt><rp>)</rp></ruby><bdi dir="rtl">b</bdi><bdo>o</bdo><bdo dir="rtl">r</bdo>',
    );
    const span = c.querySelector('span')!;
    expect(span.getAttribute('title')).toBe('т');
    expect(span.getAttribute('lang')).toBe('ru');
    expect(span.getAttribute('dir')).toBe('rtl');
    expect(c.querySelector('ruby')).not.toBeNull();
    expect(c.querySelector('rt')).toHaveTextContent('kan');
    expect(c.querySelectorAll('rp')).toHaveLength(2);
    expect(c.querySelector('bdi')!.getAttribute('dir')).toBe('rtl');
    const bdos = c.querySelectorAll('bdo');
    expect(bdos[0].getAttribute('dir')).toBe('ltr');
    expect(bdos[1].getAttribute('dir')).toBe('rtl');
  });

  it('font: цвет, гарнитура и размеры (1-7, относительные, абсолютные)', () => {
    const c = html(
      '<font color="#ff0000" face="Arial" size="5">a</font>' +
        '<font size="+2">b</font><font size="-5">c</font><font size="+9">d</font><font size="12px">e</font><font>f</font>',
    );
    const spans = Array.from(c.querySelectorAll('span'));
    expect(spans).toHaveLength(6);
    expect(getComputedStyle(spans[0]).color).toBe('rgb(255, 0, 0)');
    expect(getComputedStyle(spans[0]).fontFamily).toBe('Arial');
    expect(getComputedStyle(spans[0]).fontSize).toBe('1.35em');
    expect(getComputedStyle(spans[1]).fontSize).toBe('1.35em'); // 3+2
    expect(getComputedStyle(spans[2]).fontSize).toBe('0.65em'); // clamp до 1
    expect(spans[3].textContent).toBe('d'); // "+9" отвергнут парсером как небезопасный размер
    expect(getComputedStyle(spans[4]).fontSize).toBe('12px');
  });

  it('ссылки: безопасный href открывается в новой вкладке с noopener', () => {
    const c = html('<a href="https://example.com/x" title="т">link</a>');
    const a = c.querySelector('a')!;
    expect(a.getAttribute('href')).toBe('https://example.com/x');
    expect(a.getAttribute('target')).toBe('_blank');
    expect(a.getAttribute('rel')).toBe('noopener noreferrer');
    expect(a.getAttribute('title')).toBe('т');
  });

  it('картинки: src/alt/title', () => {
    const c = html('<img src="https://example.com/p.png" alt="альт" title="подсказка">');
    const img = c.querySelector('img')!;
    expect(img.getAttribute('src')).toBe('https://example.com/p.png');
    expect(img.getAttribute('alt')).toBe('альт');
    expect(img.getAttribute('title')).toBe('подсказка');
  });

  it('списки: ul/ol (start, type, reversed), li value', () => {
    const c = html('<ul><li>a</li></ul><ol start="3" type="a" reversed><li value="9">b</li></ol>');
    expect(c.querySelector('ul')).not.toBeNull();
    const ol = c.querySelector('ol')!;
    expect(ol.getAttribute('start')).toBe('3');
    expect(ol.getAttribute('type')).toBe('a');
    expect(ol.hasAttribute('reversed')).toBe(true);
    expect(c.querySelector('ol li')!.getAttribute('value')).toBe('9');
    expect(getComputedStyle(c.querySelector('ol')!).listStyleType).toBe('decimal');
    expect(getComputedStyle(c.querySelector('ul')!).listStyleType).toBe('disc');
  });

  it('списки без необязательных атрибутов не получают их', () => {
    const c = html('<ol><li>a</li></ol>');
    expect(c.querySelector('ol')!.hasAttribute('start')).toBe(false);
    expect(c.querySelector('ol')!.hasAttribute('reversed')).toBe(false);
    expect(c.querySelector('li')!.hasAttribute('value')).toBe(false);
  });

  it('dl/dt/dd и details/summary (open)', () => {
    const c = html('<dl><dt>t</dt><dd>d</dd></dl><details open><summary>s</summary>тело</details>');
    expect(c.querySelector('dt')).toHaveTextContent('t');
    expect(c.querySelector('dd')).toHaveTextContent('d');
    expect(c.querySelector('summary')).toHaveTextContent('s');
    expect((c.querySelector('details') as HTMLDetailsElement).open).toBe(true);
  });

  it('details без open закрыт', () => {
    const c = html('<details><summary>s</summary>тело</details>');
    expect((c.querySelector('details') as HTMLDetailsElement).open).toBe(false);
  });

  it('таблица с colgroup/col, thead/tbody/tfoot/caption, colspan/rowspan/scope', () => {
    const c = html(
      '<table><caption>cap</caption><colgroup><col span="2"></colgroup>' +
        '<thead><tr><th scope="col" colspan="2">H</th></tr></thead>' +
        '<tbody><tr><td rowspan="2">1</td><td>2</td></tr></tbody><tfoot><tr><td>f</td></tr></tfoot></table>',
    );
    expect(c.querySelector('caption')).toHaveTextContent('cap');
    expect(c.querySelector('col')!.getAttribute('span')).toBe('2');
    const th = c.querySelector('th')!;
    expect(th.getAttribute('scope')).toBe('col');
    expect(th.getAttribute('colspan')).toBe('2');
    expect(c.querySelector('td')!.getAttribute('rowspan')).toBe('2');
    expect(c.querySelector('thead')).not.toBeNull();
    expect(c.querySelector('tbody')).not.toBeNull();
    expect(c.querySelector('tfoot')).not.toBeNull();
  });

  it('ячейки без colspan/rowspan не получают этих атрибутов', () => {
    const c = html('<table><tbody><tr><td>1</td></tr></tbody></table>');
    expect(c.querySelector('td')!.hasAttribute('colspan')).toBe(false);
    expect(c.querySelector('td')!.hasAttribute('rowspan')).toBe(false);
  });

  it('неизвестный тег узла из парсера рендерится как span с детьми', () => {
    mockOverrideNodes = [
      { type: 'element', tag: 'marquee', attrs: {}, children: [{ type: 'text', value: 'внутри' }] },
    ];
    const c = html('x');
    const span = c.querySelector('span')!;
    expect(span).toHaveTextContent('внутри');
    expect(c.querySelector('marquee')).toBeNull();
  });

  it('keyPrefix принимается и не ломает рендер', () => {
    expect(html('<b>x</b>', 'custom').querySelector('strong')).toHaveTextContent('x');
  });
});

describe('ChatInlineHtml: безопасность (XSS)', () => {
  beforeEach(() => {
    mockOverrideNodes = null;
    (window as any).__xss = 0;
  });

  function assertNoActiveContent(c: HTMLElement) {
    expect(c.querySelector('script')).toBeNull();
    expect(c.querySelector('iframe')).toBeNull();
    expect(c.querySelector('object')).toBeNull();
    expect(c.querySelector('embed')).toBeNull();
    expect(c.querySelector('style')).toBeNull();
    c.querySelectorAll('*').forEach((el) => {
      Array.from(el.attributes).forEach((attr) => {
        expect(attr.name.startsWith('on')).toBe(false);
        expect(attr.name).not.toBe('style');
        expect(attr.name).not.toBe('srcdoc');
      });
    });
    expect((window as any).__xss).toBe(0);
  }

  it('<script> не попадает в DOM', () => {
    const c = html('до<script>window.__xss=1</script>после');
    assertNoActiveContent(c);
  });

  it('обработчики событий удаляются: onerror, onclick, onmouseover', () => {
    const c = html(
      '<img src="https://e.org/a.png" onerror="window.__xss=1">' +
        '<a href="https://e.org" onclick="window.__xss=1">l</a>' +
        '<span onmouseover="window.__xss=1">s</span><p onload="x()">p</p>',
    );
    assertNoActiveContent(c);
    expect(c.querySelector('img')).not.toBeNull();
    expect(c.querySelector('a')).not.toBeNull();
  });

  it.each([
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    ' javascript:alert(1)',
    'java\tscript:alert(1)',
    'vbscript:msgbox(1)',
    'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
  ])('ссылка с опасным href «%s» не превращается в <a>: тег показывается литеральным текстом', (href) => {
    const c = html(`<a href="${href}">клик</a>`);
    expect(c.querySelector('a')).toBeNull();
    expect(c.textContent).toContain('клик');
    assertNoActiveContent(c);
  });

  it.each(['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'file:///etc/passwd'])(
    'картинка с опасным src «%s» не получает src',
    (src) => {
      const c = html(`<img src="${src}" alt="x">`);
      const img = c.querySelector('img');
      expect(img === null || !img.getAttribute('src')).toBe(true);
      assertNoActiveContent(c);
    },
  );

  it('<iframe>, <object>, <embed>, <style>, <link> не рендерятся как элементы', () => {
    const c = html(
      '<iframe src="https://evil.example"></iframe><object data="x"></object><embed src="x"><style>*{display:none}</style><link rel="stylesheet" href="x">',
    );
    assertNoActiveContent(c);
    expect(c.querySelector('link')).toBeNull();
  });

  it('атрибут style и srcdoc отбрасываются', () => {
    const c = html('<span style="background:url(javascript:alert(1))" srcdoc="x">s</span>');
    assertNoActiveContent(c);
    expect(c.querySelector('span')).toHaveTextContent('s');
  });

  it('цвет <font> с url()/expression() отвергается', () => {
    const c = html('<font color="red;background:url(//e.org/x)">a</font><font color="expression(alert(1))">b</font>');
    const spans = Array.from(c.querySelectorAll('span'));
    spans.forEach((s) => expect(s.getAttribute('style') || '').not.toMatch(/url|expression/i));
    expect(c.textContent).toBe('ab');
  });

  it('экранированный HTML остаётся текстом, а не разметкой', () => {
    const c = html('&lt;img src=x onerror=alert(1)&gt; и &amp;lt;b&amp;gt;');
    expect(c.querySelector('img')).toBeNull();
    assertNoActiveContent(c);
  });

  it('вложенная попытка обхода: <scr<script>ipt>', () => {
    const c = html('<scr<script>ipt>window.__xss=1</scr</script>ipt>');
    assertNoActiveContent(c);
  });
});
