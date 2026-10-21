/**
 * Безопасный разбор HTML-фрагментов от LLM в ответах чата.
 *
 * НЕ полный браузерный HTML и НЕ для артефактов/презентаций:
 * только whitelist безопасных тегов → AST. script/iframe/on* — нет.
 */

/** Канонические теги после нормализации синонимов. */
export type ChatInlineTag =
  // форматирование
  | 'strong'
  | 'em'
  | 'u'
  | 'ins'
  | 'del'
  | 'mark'
  | 'small'
  | 'big'
  | 'font'
  | 'sup'
  | 'sub'
  // код / моноширинные
  | 'code'
  | 'kbd'
  | 'samp'
  | 'var'
  | 'pre'
  // ссылки / медиа / разрывы
  | 'a'
  | 'img'
  | 'br'
  | 'hr'
  | 'wbr'
  // семантика фраз
  | 'span'
  | 'abbr'
  | 'dfn'
  | 'cite'
  | 'q'
  | 'data'
  | 'time'
  | 'ruby'
  | 'rt'
  | 'rp'
  | 'bdi'
  | 'bdo'
  // блоки
  | 'p'
  | 'div'
  | 'blockquote'
  | 'h1'
  | 'h2'
  | 'h3'
  | 'h4'
  | 'h5'
  | 'h6'
  | 'ul'
  | 'ol'
  | 'li'
  | 'dl'
  | 'dt'
  | 'dd'
  | 'figure'
  | 'figcaption'
  | 'details'
  | 'summary'
  | 'address'
  | 'article'
  | 'section'
  | 'aside'
  | 'header'
  | 'footer'
  | 'main'
  | 'nav'
  // таблицы
  | 'table'
  | 'thead'
  | 'tbody'
  | 'tfoot'
  | 'tr'
  | 'th'
  | 'td'
  | 'caption'
  | 'colgroup'
  | 'col';

export type ChatVoidTag = 'br' | 'hr' | 'wbr' | 'img' | 'col';

export type ChatInlineNode =
  | { type: 'text'; value: string }
  | {
      type: 'element';
      tag: Exclude<ChatInlineTag, ChatVoidTag>;
      attrs: Record<string, string>;
      children: ChatInlineNode[];
    }
  | { type: 'br' }
  | { type: 'hr' }
  | { type: 'wbr' }
  | { type: 'img'; src: string; alt: string; title?: string }
  | { type: 'col'; attrs: Record<string, string> };

/** Синонимы → канон (HTML4 / HTML5 / привычки моделей). */
const TAG_CANON: Record<string, ChatInlineTag> = {
  // format
  strong: 'strong',
  b: 'strong',
  em: 'em',
  i: 'em',
  u: 'u',
  ins: 'ins',
  del: 'del',
  s: 'del',
  strike: 'del',
  mark: 'mark',
  small: 'small',
  big: 'big',
  font: 'font',
  sup: 'sup',
  sub: 'sub',
  // mono
  code: 'code',
  kbd: 'kbd',
  samp: 'samp',
  var: 'var',
  pre: 'pre',
  tt: 'code',
  // link / media / breaks
  a: 'a',
  img: 'img',
  br: 'br',
  hr: 'hr',
  wbr: 'wbr',
  // phrasing
  span: 'span',
  abbr: 'abbr',
  acronym: 'abbr',
  dfn: 'dfn',
  cite: 'cite',
  q: 'q',
  data: 'data',
  time: 'time',
  ruby: 'ruby',
  rt: 'rt',
  rp: 'rp',
  bdi: 'bdi',
  bdo: 'bdo',
  // blocks
  p: 'p',
  div: 'div',
  blockquote: 'blockquote',
  h1: 'h1',
  h2: 'h2',
  h3: 'h3',
  h4: 'h4',
  h5: 'h5',
  h6: 'h6',
  ul: 'ul',
  ol: 'ol',
  li: 'li',
  dl: 'dl',
  dt: 'dt',
  dd: 'dd',
  figure: 'figure',
  figcaption: 'figcaption',
  details: 'details',
  summary: 'summary',
  address: 'address',
  article: 'article',
  section: 'section',
  aside: 'aside',
  header: 'header',
  footer: 'footer',
  main: 'main',
  nav: 'nav',
  // tables
  table: 'table',
  thead: 'thead',
  tbody: 'tbody',
  tfoot: 'tfoot',
  tr: 'tr',
  th: 'th',
  td: 'td',
  caption: 'caption',
  colgroup: 'colgroup',
  col: 'col',
};

const VOID_TAGS = new Set<ChatInlineTag>(['br', 'hr', 'wbr', 'img', 'col']);

/** Теги форматирования, для которых чиним пары / сирот на уровне сообщения. */
const PAIR_FORMAT_SPECS: Array<{ open: string; close: string; canon: 'em' | 'strong' | 'del' }> = [
  { open: 'i', close: 'i', canon: 'em' },
  { open: 'em', close: 'em', canon: 'em' },
  { open: 'b', close: 'b', canon: 'strong' },
  { open: 'strong', close: 'strong', canon: 'strong' },
  { open: 's', close: 's', canon: 'del' },
  { open: 'strike', close: 'strike', canon: 'del' },
  { open: 'del', close: 'del', canon: 'del' },
];

const PAIR_ORPHAN_NAMES = ['em', 'i', 'strong', 'b', 's', 'strike', 'del'] as const;

const OPEN_TAG_NAMES = Object.keys(TAG_CANON).sort((a, b) => b.length - a.length);
const OPEN_TAG_PATTERN = new RegExp(
  `<(${OPEN_TAG_NAMES.join('|')})(\\s[^>]*)?\\/?>`,
  'gi'
);

/** Разрешённые атрибуты (без on*, style, srcdoc и т.п.). */
const ATTR_ALLOW: Record<string, Set<string>> = {
  a: new Set(['href', 'title', 'target', 'rel']),
  img: new Set(['src', 'alt', 'title', 'width', 'height']),
  abbr: new Set(['title']),
  dfn: new Set(['title']),
  q: new Set(['cite']),
  blockquote: new Set(['cite']),
  time: new Set(['datetime', 'title']),
  data: new Set(['value', 'title']),
  bdo: new Set(['dir']),
  bdi: new Set(['dir']),
  ol: new Set(['start', 'type', 'reversed']),
  li: new Set(['value']),
  th: new Set(['colspan', 'rowspan', 'scope', 'headers']),
  td: new Set(['colspan', 'rowspan', 'headers']),
  col: new Set(['span']),
  colgroup: new Set(['span']),
  details: new Set(['open']),
  font: new Set(['color', 'size', 'face']),
  '*': new Set(['title', 'lang', 'dir']),
};

export function canonicalizeInlineTag(name: string): ChatInlineTag | null {
  const key = String(name || '').toLowerCase().trim();
  return TAG_CANON[key] ?? null;
}

/**
 * Декодирует экранированные и «сломанные» открывающие/закрывающие теги
 * из ответов LLM (&lt;em&gt;, <\/strong>, пробелы внутри тега).
 */
export function decodeEscapedInlineTags(raw: string): string {
  if (!raw) return raw;
  if (!raw.includes('<') && !raw.includes('&lt;') && !raw.includes('\\/')) return raw;
  let s = raw;

  for (const name of OPEN_TAG_NAMES) {
    const reOpen = new RegExp(`&lt;\\s*${name}\\b([^&]*)&gt;`, 'gi');
    const reClose = new RegExp(`&lt;\\s*\\/\\s*${name}\\s*&gt;`, 'gi');
    const reEscClose = new RegExp(`<\\\\\\/\\s*${name}\\s*>`, 'gi');
    s = s.replace(reOpen, (_m, attrs: string) => `<${name}${attrs || ''}>`);
    s = s.replace(reClose, `</${name}>`);
    s = s.replace(reEscClose, `</${name}>`);
  }
  return s;
}

/**
 * Нормализация инлайн-HTML на уровне всего сообщения:
 * - unescape тегов
 * - корректные пары format → канон (i→em, b→strong, s→del)
 * - одиночные open/close format-тегов удаляются
 */
export function normalizeChatInlineHtml(raw: string): string {
  if (!raw) return raw;
  let s = decodeEscapedInlineTags(raw);

  const collapseInsideTag = (inner: string) => inner.replace(/\s+/g, ' ').trim();
  const preserved: string[] = [];
  const mark = (html: string) => {
    const token = `__ASTRACHAT_FMT_BLOCK_${preserved.length}__`;
    preserved.push(html);
    return token;
  };

  for (const { open, close, canon } of PAIR_FORMAT_SPECS) {
    const re = new RegExp(`<${open}\\b[^>]*>\\s*([\\s\\S]*?)\\s*<\\/${close}>`, 'gi');
    s = s.replace(re, (_m, inner: string) => mark(`<${canon}>${collapseInsideTag(inner)}</${canon}>`));
  }

  PAIR_ORPHAN_NAMES.forEach((name) => {
    s = s.replace(new RegExp(`<\\/?${name}\\b[^>]*>`, 'gi'), '');
  });

  for (let i = preserved.length - 1; i >= 0; i--) {
    s = s.split(`__ASTRACHAT_FMT_BLOCK_${i}__`).join(preserved[i]);
  }

  return s;
}

/**
 * На одной строке снимает непарные <em>/<strong>/<i>/<b>.
 * Корректные пары на той же строке оставляем. Фрагменты <code>/<pre> не трогаем.
 */
export function stripOrphanInlineFormatTagsOnLine(str: string): string {
  if (!str.includes('<')) return str;
  const protectedBlocks: string[] = [];
  let s = str.replace(/<(code|pre)\b[^>]*>[\s\S]*?<\/\1>/gi, (full) => {
    const token = `__ASTRA_CODE_${protectedBlocks.length}__`;
    protectedBlocks.push(full);
    return token;
  });

  type Tag = { index: number; len: number; close: boolean; kind: 'em' | 'strong' };
  const tags: Tag[] = [];
  const openRe = /<\s*(em|i|strong|b)\b[^>]*>/gi;
  const closeRe = /<\s*\/\s*(em|i|strong|b)\s*>/gi;
  let m: RegExpExecArray | null;
  while ((m = openRe.exec(s)) !== null) {
    const name = m[1].toLowerCase();
    tags.push({
      index: m.index,
      len: m[0].length,
      close: false,
      kind: name === 'strong' || name === 'b' ? 'strong' : 'em',
    });
  }
  while ((m = closeRe.exec(s)) !== null) {
    const name = m[1].toLowerCase();
    tags.push({
      index: m.index,
      len: m[0].length,
      close: true,
      kind: name === 'strong' || name === 'b' ? 'strong' : 'em',
    });
  }
  tags.sort((a, b) => a.index - b.index);

  const stackEm: number[] = [];
  const stackStrong: number[] = [];
  const removeIdx = new Set<number>();
  tags.forEach((t, idx) => {
    const stack = t.kind === 'strong' ? stackStrong : stackEm;
    if (!t.close) stack.push(idx);
    else if (stack.length > 0) stack.pop();
    else removeIdx.add(idx);
  });
  stackEm.forEach((idx) => removeIdx.add(idx));
  stackStrong.forEach((idx) => removeIdx.add(idx));

  let out = s;
  Array.from(removeIdx)
    .sort((a, b) => tags[b].index - tags[a].index)
    .forEach((idx) => {
      const t = tags[idx];
      out = out.slice(0, t.index) + out.slice(t.index + t.len);
    });

  protectedBlocks.forEach((block, i) => {
    out = out.split(`__ASTRA_CODE_${i}__`).join(block);
  });
  return out;
}

/** Поиск закрывающего тега с учётом вложенности (без учёта регистра). */
export function findClosingTag(str: string, tagName: string, startIndex: number): number {
  const openPrefix = `<${tagName}`.toLowerCase();
  const closeTag = `</${tagName}>`.toLowerCase();
  let depth = 1;
  let i = startIndex + tagName.length + 1;

  while (i < str.length && str[i] !== '>') i++;
  i++;

  while (i < str.length && depth > 0) {
    const sliceLower = str.substring(i).toLowerCase();
    if (
      sliceLower.startsWith(openPrefix) &&
      (str[i + openPrefix.length] === '>' || /\s/.test(str[i + openPrefix.length] || ''))
    ) {
      depth++;
      i += openPrefix.length;
      while (i < str.length && str[i] !== '>') i++;
      i++;
    } else if (sliceLower.startsWith(closeTag)) {
      depth--;
      if (depth === 0) {
        return i + closeTag.length;
      }
      i += closeTag.length;
    } else {
      i++;
    }
  }

  return -1;
}

function parseRawAttrs(attrStr: string | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!attrStr) return out;
  const re = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(attrStr)) !== null) {
    const name = m[1].toLowerCase();
    if (!name || name === '/') continue;
    out[name] = m[2] ?? m[3] ?? m[4] ?? '';
  }
  return out;
}

/**
 * Protocol-relative URL (`//host`) и его браузерные варианты (`\\host`, `/\host`, `\/host`):
 * браузер трактует их как внешний хост, поэтому как «локальный путь» не пропускаем.
 */
function isProtocolRelativeUrl(value: string): boolean {
  return /^[/\\]{2}/.test(value);
}

function isSafeHref(href: string): boolean {
  const h = href.trim();
  if (!h) return false;
  if (isProtocolRelativeUrl(h)) return false;
  return /^(https?:|mailto:|tel:|#|\/)/i.test(h);
}

function isSafeImgSrc(src: string): boolean {
  const s = src.trim();
  if (!s) return false;
  if (isProtocolRelativeUrl(s)) return false;
  return /^(https?:|data:image\/|\/)/i.test(s);
}

/** Безопасный CSS-цвет для <font color> (имена, hex, rgb/rgba/hsl без url/expression). */
export function isSafeCssColor(value: string): boolean {
  const v = String(value || '').trim();
  if (!v || v.length > 64) return false;
  if (/[<>{}();]|url\s*\(|expression\s*\(|var\s*\(/i.test(v)) return false;
  if (/^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(v)) return true;
  if (/^(rgb|rgba|hsl|hsla)\(\s*[\d.%\s,/]+\s*\)$/i.test(v)) return true;
  // именованные цвета / currentColor
  if (/^[a-z][a-z0-9-]*$/i.test(v)) return true;
  return false;
}

function isSafeFontFace(face: string): boolean {
  const v = String(face || '').trim();
  if (!v || v.length > 120) return false;
  if (/[<>{}]|url\s*\(|@|\\|\/\*/i.test(v)) return false;
  // "Arial", Arial, sans-serif, "Courier New", monospace
  return /^[\w\s,"'-]+$/i.test(v);
}

function isSafeFontSize(size: string): boolean {
  const v = String(size || '').trim();
  if (!v) return false;
  // классический HTML size=1..7 или ±N
  if (/^[+-]?[1-7]$/.test(v)) return true;
  if (/^\d+(\.\d+)?(px|em|rem|%)$/i.test(v)) return true;
  return false;
}

function sanitizeAttrs(tag: ChatInlineTag, raw: Record<string, string>): Record<string, string> {
  const allowed = new Set<string>([
    ...(ATTR_ALLOW['*'] ? Array.from(ATTR_ALLOW['*']) : []),
    ...(ATTR_ALLOW[tag] ? Array.from(ATTR_ALLOW[tag]) : []),
  ]);
  const out: Record<string, string> = {};

  Object.keys(raw).forEach((key) => {
    const k = key.toLowerCase();
    if (k.startsWith('on') || k === 'style' || k === 'srcdoc' || k === 'formaction') return;
    if (!allowed.has(k)) return;
    let v = raw[key];

    if (k === 'href' && !isSafeHref(v)) return;
    if (k === 'src' && tag === 'img' && !isSafeImgSrc(v)) return;
    if (k === 'cite' && v && !isSafeHref(v) && !/^https?:/i.test(v)) return;
    if (k === 'color' && !isSafeCssColor(v)) return;
    if (k === 'face' && !isSafeFontFace(v)) return;
    if (k === 'size' && tag === 'font' && !isSafeFontSize(v)) return;
    if ((k === 'colspan' || k === 'rowspan' || k === 'span' || k === 'start' || k === 'value') && !/^\d+$/.test(v.trim())) {
      return;
    }
    if (k === 'width' || k === 'height') {
      if (!/^\d+%?$/.test(v.trim())) return;
    }
    if (k === 'target') {
      v = '_blank';
    }
    if (k === 'rel') {
      v = 'noopener noreferrer';
    }
    if (k === 'reversed') {
      v = 'reversed';
    }
    if (k === 'open') {
      v = 'open';
    }
    out[k] = v;
  });

  if (tag === 'a' && out.href) {
    out.target = '_blank';
    out.rel = 'noopener noreferrer';
  }

  return out;
}

/**
 * Вырезает целые HTML-блоки (списки, pre, цитаты, таблицы…) в плейсхолдеры,
 * чтобы построчный markdown-рендер их не разорвал.
 */
const PRESERVE_BLOCK_TAGS = 'ul|ol|pre|blockquote|table|dl|details';

export function extractPreservedHtmlBlocks(text: string): { text: string; blocks: string[] } {
  if (!text || !text.includes('<')) return { text, blocks: [] };
  const blocks: string[] = [];
  const re = new RegExp(`<(${PRESERVE_BLOCK_TAGS})\\b[^>]*>[\\s\\S]*?<\\/\\1>`, 'gi');
  const next = text.replace(re, (full) => {
    const token = `\n__ASTRA_HTML_BLOCK_${blocks.length}__\n`;
    blocks.push(full);
    return token;
  });
  return { text: next, blocks };
}

/**
 * Разбор строки с уже вставленными whitelist-тегами в AST.
 * Неизвестный / незакрытый HTML остаётся как текст.
 */
export function parseChatInlineHtml(text: string): ChatInlineNode[] {
  if (!text) return [];
  const str = stripOrphanInlineFormatTagsOnLine(text);
  return parseNested(str);
}

function parseNested(str: string): ChatInlineNode[] {
  const parts: ChatInlineNode[] = [];
  let lastIndex = 0;

  type Match =
    | {
        index: number;
        kind: 'pair';
        rawName: string;
        canon: Exclude<ChatInlineTag, ChatVoidTag>;
        endIndex: number;
        content: string;
        attrs: Record<string, string>;
      }
    | { index: number; kind: 'br' | 'hr' | 'wbr'; endIndex: number }
    | { index: number; kind: 'img'; endIndex: number; src: string; alt: string; title?: string }
    | { index: number; kind: 'col'; endIndex: number; attrs: Record<string, string> };

  const matches: Match[] = [];
  OPEN_TAG_PATTERN.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = OPEN_TAG_PATTERN.exec(str)) !== null) {
    const rawName = m[1].toLowerCase();
    const canon = canonicalizeInlineTag(rawName);
    if (!canon) continue;
    const full = m[0];
    const isVoid = VOID_TAGS.has(canon) || /\/\s*>$/.test(full);

    if (canon === 'br' || canon === 'hr' || canon === 'wbr') {
      matches.push({ index: m.index, kind: canon, endIndex: m.index + full.length });
      continue;
    }

    if (canon === 'img') {
      const attrs = sanitizeAttrs('img', parseRawAttrs(m[2]));
      const src = attrs.src || '';
      if (!src) continue;
      matches.push({
        index: m.index,
        kind: 'img',
        endIndex: m.index + full.length,
        src,
        alt: attrs.alt || '',
        title: attrs.title,
      });
      continue;
    }

    if (canon === 'col') {
      matches.push({
        index: m.index,
        kind: 'col',
        endIndex: m.index + full.length,
        attrs: sanitizeAttrs('col', parseRawAttrs(m[2])),
      });
      continue;
    }

    if (isVoid) continue;

    const closeTagIndex = findClosingTag(str, rawName, m.index);
    if (closeTagIndex <= 0) continue;

    const openTagEnd = m.index + full.length;
    const closeLen = `</${rawName}>`.length;
    const content = str.substring(openTagEnd, closeTagIndex - closeLen);
    const attrs = sanitizeAttrs(canon, parseRawAttrs(m[2]));

    if (canon === 'a' && !attrs.href) continue;

    matches.push({
      index: m.index,
      kind: 'pair',
      rawName,
      canon: canon as Exclude<ChatInlineTag, ChatVoidTag>,
      endIndex: closeTagIndex,
      content,
      attrs,
    });
  }

  matches.sort((a, b) => a.index - b.index);

  const filtered: Match[] = [];
  for (let i = 0; i < matches.length; i++) {
    const current = matches[i];
    let nested = false;
    for (let j = 0; j < filtered.length; j++) {
      const prev = filtered[j];
      if (prev.kind === 'pair' && current.index > prev.index && current.index < prev.endIndex) {
        nested = true;
        break;
      }
    }
    if (!nested) filtered.push(current);
  }

  for (const matchData of filtered) {
    if (matchData.index > lastIndex) {
      const before = str.substring(lastIndex, matchData.index);
      if (before) parts.push({ type: 'text', value: before });
    }

    if (matchData.kind === 'br' || matchData.kind === 'hr' || matchData.kind === 'wbr') {
      parts.push({ type: matchData.kind });
      lastIndex = matchData.endIndex;
    } else if (matchData.kind === 'img') {
      parts.push({
        type: 'img',
        src: matchData.src,
        alt: matchData.alt,
        title: matchData.title,
      });
      lastIndex = matchData.endIndex;
    } else if (matchData.kind === 'col') {
      parts.push({ type: 'col', attrs: matchData.attrs });
      lastIndex = matchData.endIndex;
    } else if (matchData.kind === 'pair') {
      parts.push({
        type: 'element',
        tag: matchData.canon,
        attrs: matchData.attrs,
        children: parseNested(matchData.content),
      });
      lastIndex = matchData.endIndex;
    }
  }

  if (lastIndex < str.length) {
    const remaining = str.substring(lastIndex);
    if (remaining) parts.push({ type: 'text', value: remaining });
  }

  return parts.length > 0 ? parts : [{ type: 'text', value: str }];
}
