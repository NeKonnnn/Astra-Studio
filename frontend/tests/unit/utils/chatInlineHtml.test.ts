import { parseChatInlineHtml, type ChatInlineNode } from '@/utils/chatInlineHtml';

function firstElementAttrs(nodes: ChatInlineNode[]): Record<string, string> | null {
  const el = nodes.find((n) => n.type === 'element');
  return el && el.type === 'element' ? el.attrs : null;
}

function firstImg(nodes: ChatInlineNode[]) {
  return nodes.find((n) => n.type === 'img');
}

describe('chatInlineHtml href safety', () => {
  it.each([
    'https://example.com/a',
    'http://example.com',
    'mailto:user@example.com',
    'tel:+123456',
    '#anchor',
    '/relative/path',
  ])('keeps safe href %s', (href) => {
    const attrs = firstElementAttrs(parseChatInlineHtml(`<a href="${href}">x</a>`));
    expect(attrs?.href).toBe(href);
    expect(attrs?.target).toBe('_blank');
    expect(attrs?.rel).toBe('noopener noreferrer');
  });

  it.each([
    '//evil.example.com/x',
    '  //evil.example.com/x',
    '\\\\evil.example.com/x',
    '/\\evil.example.com/x',
    '\\/evil.example.com/x',
  ])('rejects protocol-relative href %s', (href) => {
    const nodes = parseChatInlineHtml(`<a href="${href}">x</a>`);
    expect(nodes.some((n) => n.type === 'element')).toBe(false);
  });

  it.each(['javascript:alert(1)', 'data:text/html,hi', 'vbscript:x', ''])(
    'rejects dangerous href %j',
    (href) => {
      const nodes = parseChatInlineHtml(`<a href="${href}">x</a>`);
      expect(nodes.some((n) => n.type === 'element')).toBe(false);
    },
  );
});

describe('chatInlineHtml img src safety', () => {
  it.each(['https://example.com/a.png', '/static/a.png', 'data:image/png;base64,AAAA'])(
    'keeps safe src %s',
    (src) => {
      const img = firstImg(parseChatInlineHtml(`<img src="${src}" alt="a">`));
      expect(img).toBeDefined();
      expect(img && img.type === 'img' && img.src).toBe(src);
    },
  );

  it.each(['//evil.example.com/a.png', '\\\\evil.example.com/a.png', '/\\evil.example.com/a.png'])(
    'rejects protocol-relative src %s',
    (src) => {
      expect(firstImg(parseChatInlineHtml(`<img src="${src}" alt="a">`))).toBeUndefined();
    },
  );

  it.each(['javascript:alert(1)', 'data:text/html,hi'])('rejects dangerous src %s', (src) => {
    expect(firstImg(parseChatInlineHtml(`<img src="${src}">`))).toBeUndefined();
  });
});

describe('chatInlineHtml cite attribute', () => {
  it('drops protocol-relative cite on blockquote', () => {
    const attrs = firstElementAttrs(
      parseChatInlineHtml('<blockquote cite="//evil.example.com">q</blockquote>'),
    );
    expect(attrs?.cite).toBeUndefined();
  });
});
