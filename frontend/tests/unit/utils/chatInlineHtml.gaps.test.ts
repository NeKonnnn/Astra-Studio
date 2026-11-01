import { findClosingTag, isSafeCssColor, parseChatInlineHtml, type ChatInlineNode } from '@/utils/chatInlineHtml';

type El = Extract<ChatInlineNode, { type: 'element' }>;

function firstElement(nodes: ChatInlineNode[]): El {
  const el = nodes.find((n) => n.type === 'element');
  if (!el || el.type !== 'element') throw new Error(`no element in ${JSON.stringify(nodes)}`);
  return el as El;
}

describe('chatInlineHtml: недостающие ветки санитайзера', () => {
  it('findClosingTag: вложенный тот же тег с атрибутами не закрывает внешний раньше времени', () => {
    const s = '<b>a<b class="x">b</b>c</b>tail';
    expect(findClosingTag(s, 'b', 0)).toBe('<b>a<b class="x">b</b>c</b>'.length);
    expect(findClosingTag('<b>нет закрытия', 'b', 0)).toBe(-1);
  });

  it('font: именованный цвет сохраняется, пустые face и size отбрасываются', () => {
    const el = firstElement(parseChatInlineHtml('<font color="red" face="" size="">x</font>'));
    expect(el.attrs).toEqual({ color: 'red' });
    expect(isSafeCssColor('#12')).toBe(false);
  });

  it('font: допустимые face и size проходят, небезопасные нет', () => {
    const ok = firstElement(parseChatInlineHtml('<font face="Arial, sans-serif" size="+2">x</font>'));
    expect(ok.attrs).toEqual({ face: 'Arial, sans-serif', size: '+2' });
    const bad = firstElement(parseChatInlineHtml('<font face="a{b}" size="99" color="red;x">x</font>'));
    expect(bad.attrs).toEqual({});
  });

  it('blockquote cite: https сохраняется, небезопасные схемы отбрасываются', () => {
    const ok = firstElement(parseChatInlineHtml('<blockquote cite="https://example.com/q">t</blockquote>'));
    expect(ok.attrs.cite).toBe('https://example.com/q');
    const bad = firstElement(parseChatInlineHtml('<blockquote cite="javascript:alert(1)">t</blockquote>'));
    expect(bad.attrs.cite).toBeUndefined();
  });

  it('img: нечисловые width/height отбрасываются, числовые и проценты остаются', () => {
    const nodes = parseChatInlineHtml('<img src="https://example.com/a.png" width="abc" height="50%">');
    const img: any = nodes.find((n) => n.type !== 'text');
    expect(img).toBeTruthy();
    const dump = JSON.stringify(nodes);
    expect(dump).toContain('https://example.com/a.png');
    expect(dump).not.toContain('abc');
  });

  it('неизвестные теги пропускаются, их содержимое остаётся текстом', () => {
    const nodes = parseChatInlineHtml('до <foo>внутри</foo> после');
    expect(nodes.every((n) => n.type === 'text')).toBe(true);
    expect(nodes.map((n: any) => n.value).join('')).toContain('внутри');
  });

  it('самозакрывающийся парный тег не создаёт элемента', () => {
    const nodes = parseChatInlineHtml('текст <b/> ещё');
    expect(nodes.some((n) => n.type === 'element')).toBe(false);
  });
});
