import type { ArtifactContentSegment, ChatArtifact } from '../types/artifacts';

const CYRILLIC_RE = /[\u0400-\u04FF]/;
const HEX_COLOR_RE = /#[0-9a-fA-F]{3,8}\b/g;

/** Строка данных pie: `"Отварной" : 226` — не prose, даже с кириллицей. */
function isMermaidPieDataLine(line: string): boolean {
  const t = line.trim();
  if (!t || t.startsWith('%%')) return false;
  return /^"[^"]+"\s*:\s*-?[\d.]+(?:\s*%%.*)?$/.test(t) || /^[\w-]+\s*:\s*-?[\d.]+(?:\s*%%.*)?$/.test(t);
}

/** Строка — часть mermaid-диаграммы (не пояснение модели после блока). */
function isMermaidDiagramLine(line: string): boolean {
  const t = line.trim();
  if (!t) return false;
  if (/^%%/.test(t)) return true;
  if (isMermaidPieDataLine(t)) return true;
  if (
    /^(%%\{|graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|mindmap|timeline|gitGraph|xychart|quadrantChart|sankey|block-beta|title|x-axis|y-axis|bar|line|section|subgraph|end|style|classDef|class|linkStyle|click|acc_title|acc_descr|direction)\b/i.test(
      t,
    )
  ) {
    return true;
  }
  if (/^pie\s+title\b/i.test(t)) return true;
  if (/^pie\s+showData\b/i.test(t)) return true;
  if (/^(title|x-axis|y-axis|bar|line)\b/i.test(t)) return true;
  // Связи / рёбра flowchart (самый частый контент после graph TD).
  if (/(-->|---|-\.?->|==>|<\-->|<--|o--|--o|x--|--x)/.test(t)) return true;
  // Узел: A[...], A(...), A{...}, A((...)), A:::class, A -->|label| B
  if (/^[A-Za-z_][\w-]*\s*(?:\[|\(|\{|>|{{)/.test(t)) return true;
  if (/^[A-Za-z_][\w-]*\s*:::\w+/.test(t)) return true;
  // sequenceDiagram: Alice->>Bob: text
  if (/^[A-Za-z_][\w-]*\s*(-{1,2}>{1,2}|-{1,2}>>)\s*[A-Za-z_]/.test(t)) return true;
  // participant / actor
  if (/^(participant|actor|autonumber)\b/i.test(t)) return true;
  return false;
}

/**
 * LLM часто вставляет эмодзи в plotColorPalette / pieN (`🟦#3b82f6`) — Mermaid
 * не парсит такую палитру и рисует все серии одним дефолтным цветом.
 */
export function normalizeMermaidInitColors(code: string): string {
  if (!code || !/%%\{[\s\S]*?init/i.test(code)) return code;
  return code.replace(/%%\{[\s\S]*?\}%%/g, (block) => {
    if (!/init\s*:/i.test(block)) return block;
    let out = block;
    out = out.replace(/plotColorPalette['"]\s*:\s*['"]([^'"]*)['"]/gi, (_m, palette: string) => {
      const hexes = palette.match(HEX_COLOR_RE) || [];
      if (!hexes.length) return _m;
      return `plotColorPalette': '${hexes.join(', ')}'`;
    });
    out = out.replace(
      /(['"]pie(\d+)['"]\s*:\s*['"])([^'"]*)(['"])/gi,
      (_m, pre: string, _n: string, color: string, post: string) => {
        const hex = color.match(HEX_COLOR_RE);
        const clean = hex ? hex[0] : color.replace(/[^\#0-9a-fA-F]/g, '');
        return `${pre}${clean || color.trim()}${post}`;
      },
    );
    return out;
  });
}

const ATTR_RE = /(\w+)=["']([^"']*)["']/g;

/** Открывающий блок артефакта + fence ``` / ~~~ (закрытый или стримящийся). */
const ARTIFACT_FENCED_RE =
  /:::artifact\{([^}]*)\}\s*\r?\n([`~]{3,})([^\r\n]*)\r?\n([\s\S]*?)(?:\2\s*\r?\n:::|$)/g;

/** Без fence: :::artifact{…}\ncontent\n::: (формат LibreChat / часть моделей). */
const ARTIFACT_UNFENCED_RE =
  /:::artifact\{([^}]*)\}\s*\r?\n([\s\S]*?)(?:\r?\n:::\s*(?:\r?\n|$)|$)/g;

function normalizeArtifactInput(text: string): string {
  return (text || '').replace(/^\uFEFF/, '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}

function inferArtifactType(content: string, fenceLang: string, declaredType: string): string {
  const declared = (declaredType || '').trim().toLowerCase();
  if (declared && declared !== 'text/plain') return declaredType;

  const lang = (fenceLang || '').trim().toLowerCase();
  if (lang === 'mermaid') return 'application/vnd.mermaid';
  if (lang === 'html' || lang === 'htm') return 'text/html';
  if (lang === 'svg') return 'image/svg+xml';
  if (lang === 'markdown' || lang === 'md') return 'text/markdown';
  if (lang === 'tsx' || lang === 'jsx' || lang === 'typescript' || lang === 'javascript') {
    return 'application/vnd.react';
  }

  const body = (content || '').trim();
  if (
    /^\s*(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|mindmap|timeline|gitGraph|xychart-beta|xychart)\b/m.test(
      body,
    )
  ) {
    return 'application/vnd.mermaid';
  }
  if (/^<svg[\s>]/i.test(body)) return 'image/svg+xml';
  if (/<!doctype/i.test(body) || /<html[\s>]/i.test(body)) return 'text/html';
  if (/^import\s+React|^export\s+default\s+function|^export\s+default\s+/m.test(body)) {
    return 'application/vnd.react';
  }

  return declaredType || 'text/plain';
}

function pushArtifactSegment(
  segments: ArtifactContentSegment[],
  opts: {
    attrsRaw: string;
    body: string;
    fenceLang: string;
    closed: boolean;
    messageId?: string;
  },
): void {
  const attrs = parseAttrs(opts.attrsRaw);
  const identifier = (attrs.identifier || 'untitled').trim() || 'untitled';
  const declaredType = (attrs.type || 'text/plain').trim() || 'text/plain';
  const title = (attrs.title || identifier).trim() || identifier;
  let content = sanitizeArtifactBody(opts.body, opts.fenceLang);
  const type = inferArtifactType(content, opts.fenceLang, declaredType);
  if (type === 'application/vnd.mermaid') {
    content = sanitizeMermaidSource(content);
  }

  segments.push({
    kind: 'artifact',
    artifact: {
      id: makeId(identifier, type, title, opts.messageId),
      identifier,
      type,
      title,
      content,
      closed: opts.closed,
      messageId: opts.messageId,
    },
  });
}

function splitTextSegmentForUnfencedArtifacts(
  text: string,
  messageId?: string,
): ArtifactContentSegment[] {
  if (!text || !text.includes(':::artifact{')) {
    return [{ kind: 'text', text }];
  }

  const out: ArtifactContentSegment[] = [];
  let lastIndex = 0;
  ARTIFACT_UNFENCED_RE.lastIndex = 0;

  let match: RegExpExecArray | null;
  while ((match = ARTIFACT_UNFENCED_RE.exec(text)) !== null) {
    const full = match[0];
    const start = match.index;

    // Уже разобран fenced-парсером (есть ```/~~~ сразу после заголовка) — пропускаем.
    const headerEnd = text.indexOf('}', start) + 1;
    if (/^\s*\r?\n[`~]{3,}/.test(text.slice(headerEnd, headerEnd + 24))) {
      continue;
    }

    if (start > lastIndex) {
      out.push({ kind: 'text', text: text.slice(lastIndex, start) });
    }

    const closed = /\r?\n:::\s*(?:\r?\n|$)/.test(full);
    pushArtifactSegment(out, {
      attrsRaw: match[1] || '',
      body: match[2] ?? '',
      fenceLang: '',
      closed,
      messageId,
    });
    lastIndex = start + full.length;
  }

  if (lastIndex < text.length) {
    out.push({ kind: 'text', text: text.slice(lastIndex) });
  }

  return out.length ? out : [{ kind: 'text', text }];
}

function parseAttrs(raw: string): Record<string, string> {
  const out: Record<string, string> = {};
  ATTR_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = ATTR_RE.exec(raw)) !== null) {
    out[m[1]] = m[2];
  }
  return out;
}

function makeId(identifier: string, type: string, title: string, messageId?: string): string {
  const base = `${identifier}_${type}_${title}_${messageId || 'msg'}`
    .replace(/\s+/g, '_')
    .toLowerCase();
  return base || 'artifact';
}

function trimArtifactTail(code: string): string {
  let out = (code || '').replace(/\r\n/g, '\n');

  // Закрытие блока :::artifact (часто без закрывающего ``` при стриме).
  const blockClose = out.search(/\n:::\s*(?:\n|$)/);
  if (blockClose >= 0) {
    out = out.slice(0, blockClose);
  }

  // НЕ режем по любому `:::` внутри тела: в Mermaid это class shorthand
  // (`A:::myClass`). Иначе диаграмма обрывалась посередине.

  // Закрывающий markdown-fence внутри тела (после него — пояснения).
  const fenceClose = out.search(/\n`{3,}\s*\w*\s*(?:\n|$)/);
  if (fenceClose >= 0) {
    out = out.slice(0, fenceClose);
  }

  return out;
}

/** Строка пояснения модели после диаграммы — не часть Mermaid. */
function isLikelyMermaidProseLine(line: string): boolean {
  const t = line.trim();
  if (!t) return false;
  if (isMermaidDiagramLine(t)) return false;
  // Любая строка с рёбрами/узловым синтаксисом — не prose.
  if (/(-->|---|-\.?->|==>|[\[\](){}])/.test(t) && /^[A-Za-z_`%]/.test(t)) return false;
  if (/^:::\s*$/.test(t) || /^`{3,}\s*\w*\s*$/.test(t)) return true;
  if (/^(готово|готова|итог|вывод|note:|примечание)/i.test(t)) return true;
  if (/^(\*\*|•|\*\s|-\s+\*\*)/.test(t)) return true;
  if (/^[•\-*]\s+\S/.test(t) && /[а-яё]/i.test(t)) return true;
  // Свободный русский текст без синтаксиса mermaid
  if (CYRILLIC_RE.test(t) && !/^"[^"]+"\s*:/.test(t)) return true;
  return false;
}

/** Обрезает пояснения модели после последней строки диаграммы. */
function trimMermaidTrailingProse(code: string): string {
  const lines = code.split('\n');
  const out: string[] = [];
  let sawDiagram = false;

  for (const line of lines) {
    const trimmed = line.trim();
    // Только отдельная строка `:::` — закрытие артефакта. Не трогаем A:::class.
    if (/^:::\s*$/.test(trimmed)) break;
    if (/^`{3,}/.test(trimmed)) break;
    if (/^`{1,2}\s*$/.test(trimmed)) break;

    const looksLikeDiagram = isMermaidDiagramLine(trimmed);

    if (looksLikeDiagram) {
      sawDiagram = true;
      out.push(line);
      continue;
    }

    if (sawDiagram && isLikelyMermaidProseLine(line)) break;
    if (!sawDiagram) out.push(line);
    else if (!trimmed) out.push(line);
    else if (isLikelyMermaidProseLine(line)) break;
    else out.push(line);
  }

  return out.join('\n');
}

/**
 * Убирает из тела артефакта вложенные markdown-fence (``` / ````),
 * хвосты ::: и прочий мусор, который модели часто оставляют внутри.
 */
export function sanitizeArtifactBody(raw: string, fenceLang?: string): string {
  let code = (raw || '').replace(/\r\n/g, '\n');

  // Срезаем обёртки, если просочились в content
  code = code.replace(/^:::artifact\{[^}]*\}\s*\n?/i, '');
  code = trimArtifactTail(code);
  code = code.replace(/\n?:::\s*$/g, '');

  // Выкидываем строки, которые целиком — fence (``` / ````mermaid / …)
  code = code
    .split('\n')
    .filter((line) => !/^`{3,}\s*\w*\s*$/.test(line.trim()))
    .join('\n');

  // Хвосты/головы из backticks на всякий случай
  code = code.replace(/^`{3,}(?:[\w-]*)?\s*/i, '').replace(/\s*`{3,}\s*$/g, '');

  // Иногда язык fence дублируется первой строкой: "mermaid"
  const lang = (fenceLang || '').trim().toLowerCase();
  if (lang && code.toLowerCase().startsWith(lang + '\n')) {
    code = code.slice(lang.length + 1);
  } else if (/^(mermaid|tsx|jsx|html|svg|markdown|md)\s*\n/i.test(code)) {
    code = code.replace(/^(mermaid|tsx|jsx|html|svg|markdown|md)\s*\n/i, '');
  }

  // Обрезаем всё после первого оставшегося fence внутри
  const innerFence = code.search(/\n`{3,}/);
  if (innerFence >= 0) {
    code = code.slice(0, innerFence);
  }

  return code.replace(/^\n+/, '').replace(/\n+$/, '');
}

/** Чистый исходник Mermaid для рендера (без markdown-обёрток). */
export function sanitizeMermaidSource(raw: string): string {
  let code = sanitizeArtifactBody(raw, 'mermaid');

  // Стартуем с ключевого слова диаграммы, если перед ним мусор —
  // но сохраняем ведущие %%{init:…}%% (там themeVariables / цвета pie).
  const initBlocks: string[] = [];
  code = code.replace(/^\s*(%%\{[\s\S]*?\}%%)\s*/gm, (full, block) => {
    if (/init\s*:/i.test(block)) {
      initBlocks.push(normalizeMermaidInitColors(block.trim()));
      return '';
    }
    return full;
  });

  const start = code.search(
    /^\s*(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|gantt|pie|mindmap|timeline|gitGraph|xychart-beta|xychart|quadrantChart|sankey-beta|block-beta)\b/im,
  );
  if (start > 0) {
    code = code.slice(start);
  }

  // Убрать одиночные/двойные backticks, если вдруг остались в тексте
  // (ломают lexer: "Expecting EOF but found '``'")
  if (/^`+$/m.test(code)) {
    code = code
      .split('\n')
      .filter((line) => !/^`+$/.test(line.trim()))
      .join('\n');
  }

  const prefix = initBlocks.length ? `${initBlocks.join('\n')}\n` : '';
  code = trimMermaidTrailingProse(`${prefix}${code}`.trim());
  return normalizeMermaidInitColors(code.trim());
}

const INVALID_STYLE_CSS_RE =
  /\b(text-align|font-weight|font-size|font-family|line-height|padding|margin|display|width|height|border-radius)\b/i;

const FILL_HEX_RE = /fill\s*:\s*(#[0-9a-fA-F]{3,8})\b/i;

function diagramBodyWithoutInit(code: string): string {
  return code.replace(/^%%\{[\s\S]*?\}%%\s*/gm, '').trimStart();
}

function collectStyleFillColors(code: string): string[] {
  const colors: string[] = [];
  const seen = new Set<string>();
  const push = (c: string) => {
    const hex = c.trim();
    if (!hex || seen.has(hex.toLowerCase())) return;
    seen.add(hex.toLowerCase());
    colors.push(hex);
  };
  for (const line of code.split('\n')) {
    const trimmed = line.trim();
    // style X fill:#…  / classDef X fill:#…
    if (/^(style|classDef)\s+/i.test(trimmed)) {
      const m = trimmed.match(FILL_HEX_RE);
      if (m?.[1]) push(m[1]);
      continue;
    }
    // %% color: #… / %% pie1=#…
    if (trimmed.startsWith('%%')) {
      const hexRe = /#[0-9a-fA-F]{3,8}\b/g;
      let m: RegExpExecArray | null;
      while ((m = hexRe.exec(trimmed)) !== null) {
        push(m[0]);
      }
    }
  }
  return colors;
}

/**
 * У pie директива `style` не красит сегменты — цвета через themeVariables pie1..pieN.
 * У xychart — через themeVariables.xyChart.plotColorPalette.
 * LLM часто пишет `style … fill:#…`; переносим fill в %%{init}%%.
 */
export function injectChartThemeColorsFromStyles(raw: string): string {
  const code = sanitizeMermaidSource(raw);
  if (!code) return code;
  if (/%%\s*\{\s*init\s*:/i.test(code) || /^---[\s\S]*?---/m.test(code)) return code;

  const body = diagramBodyWithoutInit(code);
  const colors = collectStyleFillColors(code);
  if (!colors.length) return code;

  if (/^pie\b/im.test(body)) {
    if (/['"]?pie\d+['"]?\s*:/i.test(code)) return code;
    const themeVars = colors.map((c, i) => `'pie${i + 1}':'${c}'`).join(', ');
    return `%%{init: {'theme':'base', 'themeVariables': {${themeVars}}}}%%\n${code}`;
  }

  if (/^xychart(-beta)?\b/im.test(body)) {
    if (/plotColorPalette\s*:/i.test(code)) return code;
    const palette = colors.join(', ');
    return (
      `%%{init: {'theme':'base', 'themeVariables': {'xyChart': {'plotColorPalette': '${palette}'}}}}%%\n` +
      code
    );
  }

  return code;
}

/** @deprecated используйте injectChartThemeColorsFromStyles */
export function injectPieThemeColorsFromStyles(raw: string): string {
  return injectChartThemeColorsFromStyles(raw);
}

/** Исходник для первого прохода рендера: цвета диаграмм из style + мягкий repair. */
export function prepareMermaidSourceForRender(raw: string): string {
  return repairMermaidSource(normalizeMermaidInitColors(injectChartThemeColorsFromStyles(raw)));
}

/**
 * Разбивает список категорий x-axis `[A, "B", C]` с учётом кавычек.
 */
function splitXyAxisCategories(inner: string): string[] {
  const parts: string[] = [];
  const s = inner.trim();
  let i = 0;
  while (i < s.length) {
    while (i < s.length && /[\s,]/.test(s[i]!)) i += 1;
    if (i >= s.length) break;
    const ch = s[i]!;
    if (ch === '"' || ch === "'") {
      const q = ch;
      let j = i + 1;
      let token = q;
      while (j < s.length) {
        const c = s[j]!;
        if (c === '\\' && j + 1 < s.length) {
          token += c + s[j + 1];
          j += 2;
          continue;
        }
        token += c;
        j += 1;
        if (c === q) break;
      }
      parts.push(token);
      i = j;
      continue;
    }
    let j = i;
    while (j < s.length && s[j] !== ',') j += 1;
    const token = s.slice(i, j).trim();
    if (token) parts.push(token);
    i = j;
  }
  return parts;
}

/** Категория xychart: ASCII-идентификатор можно без кавычек, остальное — в "". */
function quoteXyCategoryIfNeeded(token: string): string {
  const t = token.trim();
  if (!t) return t;
  if (
    (t.startsWith('"') && t.endsWith('"') && t.length >= 2) ||
    (t.startsWith("'") && t.endsWith("'") && t.length >= 2)
  ) {
    return t;
  }
  if (/^[A-Za-z0-9_]+$/.test(t)) return t;
  return `"${t.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

/**
 * Лексер xychart не принимает кириллицу/пробелы в unquoted x-axis `[Янв, Фев]`.
 * Чиним в `["Янв", "Фев"]`. Числовые диапазоны `0 --> 100` не трогаем.
 */
export function quoteXyChartCategoryLabels(raw: string): string {
  const code = raw || '';
  const body = diagramBodyWithoutInit(code);
  if (!/^xychart(-beta)?\b/im.test(body)) return code;

  return code.replace(/^([ \t]*x-axis[ \t]*)\[([^\]]*)\]/gim, (full, prefix: string, inner: string) => {
    if (/-->/.test(inner)) return full;
    const parts = splitXyAxisCategories(inner);
    if (!parts.length) return full;
    return `${prefix}[${parts.map(quoteXyCategoryIfNeeded).join(', ')}]`;
  });
}

/**
 * Чинит типичный битый Mermaid от LLM:
 * - style с CSS вроде text-align:center (ломает lexer на ':')
 * - style/class с кириллическими id узлов
 * - кривой `Node :: class` вместо `:::class`
 * - classDef / linkStyle / click, если мешают
 * - xychart x-axis с кириллицей без кавычек
 */
export function repairMermaidSource(raw: string): string {
  let code = sanitizeMermaidSource(raw);
  if (!code) return code;

  const lines = code.split('\n');
  const out: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();

    // style NodeId fill:#fff,...  — выкидываем заведомо битые
    if (/^style\s+/i.test(trimmed)) {
      if (INVALID_STYLE_CSS_RE.test(trimmed) || CYRILLIC_RE.test(trimmed)) {
        continue;
      }
      // style у pie/xychart не красит серии — цвета через themeVariables (см. injectChart…)
      const diagramKind = diagramBodyWithoutInit(code);
      if (/^(pie|xychart)/i.test(diagramKind)) {
        continue;
      }
      out.push(line);
      continue;
    }

    // classDef / class / linkStyle / click — частый источник поломок
    if (/^(classDef|class|linkStyle|click)\s+/i.test(trimmed)) {
      // classDef с кириллицей в имени — дропаем; чистый ASCII оставляем
      if (CYRILLIC_RE.test(trimmed) || INVALID_STYLE_CSS_RE.test(trimmed)) {
        continue;
      }
      out.push(line);
      continue;
    }

    // `Start(Label) :: step` / `Start :: step` — модель путает с :::class
    // Убираем хвост ` :: word` / ` ::: word` после узла, чтобы граф хотя бы собрался
    let fixed = line.replace(/(\))\s*:{2,3}\s*[A-Za-z_][\w-]*/g, '$1');
    fixed = fixed.replace(/(\b[A-Za-z][\w]*)\s*:{2,3}\s*[A-Za-z_][\w-]*(?=\s|$)/g, '$1');

    out.push(fixed);
  }

  return quoteXyChartCategoryLabels(out.join('\n').trim());
}

/**
 * Агрессивный fallback: выкинуть ВСЕ style/classDef/class/linkStyle/click.
 * Часто после этого flowchart/pie уже рендерится.
 */
export function stripMermaidStyling(raw: string): string {
  const code = repairMermaidSource(raw);
  return code
    .split('\n')
    .filter((line) => {
      const t = line.trim();
      return !/^(style|classDef|class|linkStyle|click)\s+/i.test(t);
    })
    .join('\n')
    .trim();
}

/**
 * Разбивает текст сообщения на обычный markdown и артефакты.
 * Незавершённый (стриминг) блок без закрывающего ::: тоже попадает в результат.
 */
export function splitContentWithArtifacts(
  text: string,
  options?: { messageId?: string; isStreaming?: boolean },
): ArtifactContentSegment[] {
  const normalized = normalizeArtifactInput(text);
  if (!normalized) return [{ kind: 'text', text: '' }];

  const messageId = options?.messageId;
  const segments: ArtifactContentSegment[] = [];
  let lastIndex = 0;
  ARTIFACT_FENCED_RE.lastIndex = 0;

  let match: RegExpExecArray | null;
  while ((match = ARTIFACT_FENCED_RE.exec(normalized)) !== null) {
    const full = match[0];
    const attrsRaw = match[1] || '';
    const fenceLang = match[3] || '';
    const body = match[4] ?? '';

    const start = match.index;
    if (start > lastIndex) {
      segments.push({ kind: 'text', text: normalized.slice(lastIndex, start) });
    }

    const closed = /\r?\n:::\s*$/.test(full.trimEnd()) || full.trimEnd().endsWith(':::');
    pushArtifactSegment(segments, {
      attrsRaw,
      body,
      fenceLang,
      closed,
      messageId,
    });
    lastIndex = start + full.length;
  }

  if (lastIndex < normalized.length) {
    segments.push({ kind: 'text', text: normalized.slice(lastIndex) });
  }

  if (segments.length === 0) {
    return splitTextSegmentForUnfencedArtifacts(normalized, messageId);
  }

  // Второй проход: unfenced-блоки внутри текстовых сегментов.
  const merged: ArtifactContentSegment[] = [];
  for (const seg of segments) {
    if (seg.kind === 'artifact') {
      merged.push(seg);
      continue;
    }
    merged.push(...splitTextSegmentForUnfencedArtifacts(seg.text, messageId));
  }

  return merged.length ? merged : [{ kind: 'text', text: normalized }];
}

export function extractArtifactsFromText(
  text: string,
  options?: { messageId?: string },
): ChatArtifact[] {
  return splitContentWithArtifacts(text, options)
    .filter((s): s is { kind: 'artifact'; artifact: ChatArtifact } => s.kind === 'artifact')
    .map((s) => s.artifact);
}

/**
 * Внутри непрерывных серий артефактов ставит GPB-презентации первыми,
 * остальные артефакты — следом. Текстовые сегменты не сдвигает.
 */
export function hoistPresentationArtifacts(
  segments: ArtifactContentSegment[],
  isPresentation: (content: string) => boolean,
): ArtifactContentSegment[] {
  if (segments.length < 2) return segments;

  const out: ArtifactContentSegment[] = [];
  let i = 0;
  while (i < segments.length) {
    const seg = segments[i];
    if (seg.kind === 'text') {
      out.push(seg);
      i += 1;
      continue;
    }

    const run: ArtifactContentSegment[] = [];
    while (i < segments.length && segments[i].kind === 'artifact') {
      run.push(segments[i]);
      i += 1;
    }

    const presentations = run.filter(
      (s) => s.kind === 'artifact' && isPresentation(s.artifact.content || ''),
    );
    const others = run.filter(
      (s) => !(s.kind === 'artifact' && isPresentation(s.artifact.content || '')),
    );
    out.push(...presentations, ...others);
  }
  return out;
}

export function artifactTypeLabel(type: string): string {
  const t = (type || '').toLowerCase();
  if (t === 'text/html' || t === 'application/vnd.code-html') return 'HTML';
  if (t === 'image/svg+xml') return 'SVG';
  if (t === 'text/markdown' || t === 'text/md') return 'Markdown';
  if (t === 'application/vnd.mermaid') return 'Mermaid';
  if (t === 'application/vnd.react' || t === 'application/vnd.ant.react') return 'React';
  return type || 'Artifact';
}

export function isHtmlArtifactType(type: string): boolean {
  const t = (type || '').toLowerCase();
  return t === 'text/html' || t === 'application/vnd.code-html';
}

export function isReactArtifactType(type: string): boolean {
  const t = (type || '').toLowerCase();
  return t === 'application/vnd.react' || t === 'application/vnd.ant.react';
}

export function isMarkdownArtifactType(type: string): boolean {
  const t = (type || '').toLowerCase();
  return t === 'text/markdown' || t === 'text/md';
}

export function isMermaidArtifactType(type: string): boolean {
  return (type || '').toLowerCase() === 'application/vnd.mermaid';
}

export function isSvgArtifactType(type: string): boolean {
  return (type || '').toLowerCase() === 'image/svg+xml';
}

export function guessCodeLanguage(type: string): string {
  if (isHtmlArtifactType(type)) return 'html';
  if (isSvgArtifactType(type)) return 'xml';
  if (isMarkdownArtifactType(type)) return 'markdown';
  if (isMermaidArtifactType(type)) return 'mermaid';
  if (isReactArtifactType(type)) return 'tsx';
  return 'plaintext';
}