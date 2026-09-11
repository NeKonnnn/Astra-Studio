const STORAGE_PREFIX = 'astrachat:presentation:';

/**
 * class="…" / class='…' — значение целиком (нежадный, с учётом переносов).
 * Нужен, чтобы отличать токен `slide` от `slide-title` / `slide-title--xl`.
 */
const CLASS_ATTR_QUOTED_RE = /\bclass\s*=\s*(["'])([\s\S]*?)\1/gi;

/** <tag … class="…slide…"> — для подсчёта открывающих слайдов в стриме. */
const SLIDE_OPEN_QUOTED_RE =
  /<([a-zA-Z][\w-]*)\b[^>]*\bclass\s*=\s*(["'])([\s\S]*?)\2[^>]*>/gi;

const SLIDE_OPEN_UNQUOTED_RE =
  /<([a-zA-Z][\w-]*)\b[^>]*\bclass\s*=\s*slide\b[^>]*>/gi;

/** В значении class есть отдельный токен `slide` (не slide-title / slides). */
export function classAttrHasSlideToken(classValue: string): boolean {
  return classValue
    .trim()
    .split(/\s+/)
    .some((token) => token === 'slide');
}

/** В HTML есть элемент с class-токеном `slide`. */
export function hasGpbSlideClass(code: string): boolean {
  if (!code) return false;

  const quoted = new RegExp(CLASS_ATTR_QUOTED_RE.source, 'gi');
  let m: RegExpExecArray | null;
  while ((m = quoted.exec(code)) !== null) {
    if (classAttrHasSlideToken(m[2])) return true;
  }

  return /\bclass\s*=\s*slide\b/i.test(code);
}

/** Сколько открывающих тегов с class-токеном slide уже есть в потоке. */
export function countGpbSlideOpens(code: string): number {
  if (!code) return 0;
  let count = 0;

  const quoted = new RegExp(SLIDE_OPEN_QUOTED_RE.source, 'gi');
  let m: RegExpExecArray | null;
  while ((m = quoted.exec(code)) !== null) {
    if (classAttrHasSlideToken(m[3])) count += 1;
  }

  const unquoted = new RegExp(SLIDE_OPEN_UNQUOTED_RE.source, 'gi');
  while ((m = unquoted.exec(code)) !== null) {
    count += 1;
  }

  return count;
}

/** Fence-язык блока — HTML (без учёта регистра). */
export function isHtmlFenceLanguage(language: string | undefined | null): boolean {
  const lang = (language || '').trim().toLowerCase();
  return lang === 'html' || lang === 'htm' || lang === 'xhtml';
}

/** Открывающая строка markdown-fence — ```html / ```htm / ```xhtml. */
export function isHtmlFenceBlock(codeBlock: string): boolean {
  return /```(?:html|htm|xhtml)\b/i.test(codeBlock || '');
}

/** Языки fence, которые точно не GPB-презентация. */
const NON_PRESENTATION_FENCE_LANGS = new Set([
  'python', 'py', 'javascript', 'js', 'typescript', 'ts', 'json', 'sql', 'bash', 'sh', 'shell',
  'yaml', 'yml', 'csv', 'svg', 'mermaid', 'mmd', 'tsx', 'jsx', 'java', 'c', 'cpp', 'c++', 'go',
  'rust', 'rs', 'rb', 'ruby', 'php', 'swift', 'kotlin', 'scala', 'r', 'matlab', 'vba', 'powershell', 'ps1',
]);

/**
 * Стрим презентации: viewer сразу (спиннер), без Monaco / ArtifactCard.
 * Срабатывает при presentation skill/агенте + ```html — ещё до первого .slide / 297mm.
 */
export function shouldTreatHtmlFenceAsPresentationStream(
  code: string,
  codeBlock: string,
  language: string | undefined | null,
  opts: { isStreaming?: boolean; presentationExpected?: boolean } = {},
): boolean {
  if (!opts.isStreaming || !opts.presentationExpected) return false;
  const lang = (language || '').trim().toLowerCase();
  if (lang && NON_PRESENTATION_FENCE_LANGS.has(lang)) return false;
  // Уже открыт html-fence при skill презентации — сразу viewer, не сырой код.
  if (isHtmlFenceLanguage(lang) || isHtmlFenceBlock(codeBlock)) return true;
  const body = (code || '').trim();
  if (isGpbPresentationStreaming(body, lang)) return true;
  if (body && hasPresentationStreamHints(body)) return true;
  return false;
}

/**
 * HTML-блок презентации GPB: обязателен реальный class-токен `slide`.
 * Одних CSS-классов скилла (.slide-title / .content-zone) или путей иконок мало —
 * иначе viewer открывается и падает с «не найдены слайды с классом .slide».
 */
export function isGpbPresentationHtml(code: string): boolean {
  if (!code || code.length < 40) return false;
  if (!hasGpbSlideClass(code)) return false;

  const lower = code.toLowerCase();
  const looksLikeHtml =
    lower.includes('<div') ||
    lower.includes('<section') ||
    lower.includes('<html') ||
    lower.includes('<!doctype');

  return looksLikeHtml;
}

/**
 * Во время стрима: показать chrome презентации (спиннер), если уже видны
 * признаки GPB-скилла. В iframe попадают только готовые `.slide`.
 */
export function isGpbPresentationStreaming(code: string, _language?: string | null): boolean {
  if (isGpbPresentationHtml(code)) return true;
  if (!code || code.length < 8) return false;
  return hasPresentationStreamHints(code);
}

/** Показывать GPB viewer: готовые .slide или стрим/skill презентации с признаками GPB HTML. */
export function shouldOpenPresentationViewer(
  code: string,
  opts: {
    isStreaming?: boolean;
    presentationExpected?: boolean;
    language?: string | null;
  } = {},
): boolean {
  if (isGpbPresentationHtml(code)) return true;
  // Skill презентации + стрим: с первого ```html / <!DOCTYPE>/<style> — viewer со спиннером,
  // не ждём .slide (иначе мелькает Monaco/ArtifactCard с сырым HTML).
  if (opts.presentationExpected && opts.isStreaming) {
    if (isHtmlFenceLanguage(opts.language)) return true;
    if (isGpbPresentationStreaming(code, opts.language)) return true;
    const low = (code || '').toLowerCase();
    if (
      low.includes('<!doctype') ||
      low.includes('<html') ||
      low.includes('<head') ||
      low.includes('<style') ||
      low.includes('@font-face') ||
      low.includes('cera cy')
    ) {
      return true;
    }
  } else if (opts.presentationExpected && isGpbPresentationStreaming(code, opts.language)) {
    return true;
  }
  // Обычный ```html (Excel-дашборд и т.п.) — ArtifactCard, не presentation viewer.
  return false;
}

/**
 * Выделяет unfenced HTML презентации из текста ответа.
 * Модели часто отдают GPB HTML без ```html — иначе ChatInlineHtml рисует img/иконки в ленте.
 */
export function extractUnfencedPresentationHtml(
  text: string,
  opts?: { presentationExpected?: boolean; isStreaming?: boolean },
): {
  before: string;
  html: string | null;
  after: string;
} {
  if (!text || !text.includes('<')) {
    return { before: text || '', html: null, after: '' };
  }
  // Уже внутри markdown-fence — не трогаем (разбирает renderCodeBlock).
  if (/```/.test(text) && /```(?:html|htm|xhtml)?\b/i.test(text)) {
    return { before: text, html: null, after: '' };
  }

  const startCandidates = [
    text.search(/<!DOCTYPE\s+html\b/i),
    text.search(/<html\b/i),
    text.search(/<head\b/i),
    text.search(/<style\b/i),
    text.search(/<[a-z][\w-]*\b[^>]*\bclass\s*=\s*(["'])[^"'>\n]*\bslide\b/i),
  ].filter((i) => i >= 0);

  if (!startCandidates.length) {
    if (isGpbPresentationHtml(text) || hasGpbSlideClass(text)) {
      return { before: '', html: text, after: '' };
    }
    return { before: text, html: null, after: '' };
  }

  const start = Math.min(...startCandidates);
  const html = text.slice(start);
  const earlyPresentationStream =
    Boolean(opts?.presentationExpected && opts?.isStreaming) &&
    /<!doctype\s+html\b|<html\b|<head\b|<style\b|@font-face/i.test(html);

  if (
    !isGpbPresentationHtml(html) &&
    !hasGpbSlideClass(html) &&
    !isGpbPresentationStreaming(html) &&
    !earlyPresentationStream
  ) {
    return { before: text, html: null, after: '' };
  }
  return { before: text.slice(0, start), html, after: '' };
}

/** Куски HTML от каждого открывающего .slide до следующего (работает на обрезанном стриме). */
export function extractGpbSlideFragments(code: string): string[] {
  if (!code) return [];
  const starts: number[] = [];

  const quoted = new RegExp(SLIDE_OPEN_QUOTED_RE.source, 'gi');
  let m: RegExpExecArray | null;
  while ((m = quoted.exec(code)) !== null) {
    if (classAttrHasSlideToken(m[3])) starts.push(m.index);
  }
  const unquoted = new RegExp(SLIDE_OPEN_UNQUOTED_RE.source, 'gi');
  while ((m = unquoted.exec(code)) !== null) {
    starts.push(m.index);
  }
  starts.sort((a, b) => a - b);
  const unique: number[] = [];
  for (const idx of starts) {
    if (!unique.length || idx - unique[unique.length - 1] > 2) unique.push(idx);
  }
  return unique.map((start, i) => {
    const end = i + 1 < unique.length ? unique[i + 1] : code.length;
    return code.slice(start, end);
  });
}

function extractPresentationHeadChrome(code: string, firstSlideIndex: number): string {
  const head = firstSlideIndex > 0 ? code.slice(0, firstSlideIndex) : code;
  const links = head.match(/<link\b[^>]*rel\s*=\s*['"]stylesheet['"][^>]*>/gi) || [];
  const styles = head.match(/<style\b[^>]*>[\s\S]*?<\/style>/gi) || [];
  return [...links, ...styles].join('\n');
}

/** Минимальный GPB chrome, если модель отдала пустой <head> (типично после auto-continue). */
const GPB_FALLBACK_STYLE = `
@font-face{font-family:'Cera CY';src:url('/static/fonts/Cera-Regular-App.ttf') format('truetype');font-weight:400;font-style:normal}
@font-face{font-family:'Cera CY';src:url('/static/fonts/Cera-Bold-App.ttf') format('truetype');font-weight:700;font-style:normal}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Cera CY',Calibri,sans-serif;background:#e8e8e8}
.slide{width:297mm;height:167mm;background:#fff;position:relative;overflow:hidden}
.slide-title{position:absolute;left:13.3mm;top:7.2mm;color:#2355D7;font-family:'Cera CY',Calibri,sans-serif;font-weight:700;line-height:1.2;z-index:3;max-width:220mm;word-wrap:break-word;overflow-wrap:break-word}
.slide-title--xl{font-size:43px;max-width:220mm}.slide-title--lg{font-size:37px;max-width:220mm}
.slide-title--md{font-size:32px;max-width:220mm}.slide-title--sm{font-size:27px;max-width:220mm}
.slide-title--xs{font-size:24px;max-width:220mm}.slide-title--mini{font-size:21px;max-width:220mm;line-height:1.25}
.content-zone{position:absolute;left:13.3mm;right:13.3mm;top:var(--content-top,22mm);width:calc(297mm - 26.6mm);z-index:3}
.gpb-small{position:absolute;right:13.3mm;top:8.9mm;width:43.6mm;height:auto;z-index:4}
.page-num,.page-number{position:absolute;right:13.3mm;bottom:7.3mm;z-index:5;font-size:8px;color:#696E82}
.card{border-radius:2.5mm;padding:5mm}
.title-slide .main-title{position:absolute;left:13.6mm;top:66.8mm;font-size:48px;font-weight:700;color:#2355D7;z-index:3;max-width:200mm}
.title-slide .subtitle{position:absolute;left:13.6mm;top:95mm;font-size:20px;color:#333;z-index:3}
.title-slide .website{position:absolute;left:13.6mm;bottom:12mm;font-size:14px;color:#2355D7;z-index:3}
`.trim();

function presentationFallbackChrome(): string {
  return `<style>\n${GPB_FALLBACK_STYLE}\n</style>`;
}

/** Убирает fence/DOCTYPE-шум auto-continue из фрагмента слайда. */
function cleanSlideFragment(frag: string): string {
  let s = frag || '';
  s = s.replace(/```(?:html|htm|xhtml)?\b[^\n]*\n?/gi, '\n');
  s = s.replace(/```+/g, '\n');
  s = s.replace(/^\s*html\s*$/gim, '');
  s = s.replace(/<!DOCTYPE\s+html[^>]*>/gi, '');
  s = s.replace(/<head\b[^>]*>[\s\S]*?<\/head>/gi, '');
  s = s.replace(/<\/?(?:html|body)\b[^>]*>/gi, '');
  const lastOpen = s.lastIndexOf('<!--');
  if (lastOpen >= 0 && !s.slice(lastOpen).includes('-->')) {
    s = s.slice(0, lastOpen).replace(/\s+$/, '');
  }
  return s.trim();
}

/** Срезает markdown-fence и незакрытый хвост <!-- ... без -->. */
export function sanitizePresentationHtmlSource(code: string): string {
  let s = (code || '').replace(/^\uFEFF/, '').trim();
  if (!s) return s;
  const fenced = s.match(/^```(?:html|htm|xhtml)?\b[^\n]*\n([\s\S]*?)(?:```\s*)?$/i);
  if (fenced) s = fenced[1].trim();
  else {
    s = s.replace(/^```(?:html|htm|xhtml)?\b[^\n]*\n/i, '');
    if (s.trimEnd().endsWith('```')) s = s.trimEnd().slice(0, -3).trimEnd();
  }
  const lastOpen = s.lastIndexOf('<!--');
  if (lastOpen >= 0 && !s.slice(lastOpen).includes('-->')) {
    s = s.slice(0, lastOpen).replace(/\s+$/, '');
  }
  return s;
}

function wrapPresentationSlidesHtml(chrome: string, slidesHtml: string): string {
  return `<!DOCTYPE html><html><head>${chrome}</head><body>${slidesHtml}</body></html>`;
}

function ensureHtmlDocumentClosed(html: string): string {
  const body = (html || '').replace(/\s+$/, '');
  if (!body) return body;
  if (/<\/html\s*>\s*$/i.test(body)) return body;
  if (/<\/body\s*>\s*$/i.test(body)) return `${body}\n</html>`;
  return `${body}\n</body>\n</html>`;
}

/**
 * Все слайды из сообщения (в любом виде: fenced ```html, unfenced <!DOCTYPE>,
 * несколько блоков подряд) → один ```html с одним документом = один viewer.
 * Также убирает хвост ``</body></html>`` и пустые ```text после презы.
 *
 * Ключевой инвариант: НЕ зависим от того, fenced блок или нет — собираем все
 * фрагменты `.slide` из всего текста. Это чинит смешанный кейс
 * (первый блок в ```html, второй — сырой <!DOCTYPE html>).
 */
export function coalescePresentationHtmlInMessage(text: string): string {
  if (!text || !hasGpbSlideClass(text)) return text;

  // Начало презентационной части: первый fence ```html ИЛИ первый <!DOCTYPE>/<html>
  // ИЛИ первый элемент с class="slide" (если модель дала слайды без обёртки).
  // Также ```\nhtml (язык на следующей строке) — иначе утекает слово «html» в чат.
  const firstFence = text.search(/```(?:html|htm|xhtml)\b/i);
  const firstFenceBare = text.search(/```\s*\n\s*html\b/i);
  const firstDoc = text.search(/<!DOCTYPE\s+html\b|<html\b/i);
  const firstSlide = text.search(/<[a-zA-Z][\w-]*\b[^>]*\bclass\s*=\s*(["'])[^"'>]*\bslide\b/i);

  const candidates = [firstFence, firstFenceBare, firstDoc, firstSlide].filter((i) => i >= 0);
  if (!candidates.length) return text;
  const presStart = Math.min(...candidates);
  const prefix = presStart > 0 ? text.slice(0, presStart).trimEnd() : '';

  // Все слайды из всего хвоста сообщения, независимо от fence/doctype-границ.
  const presPart = text.slice(presStart);
  const allSlides: string[] = [];
  const seen = new Set<string>();
  for (const frag of extractGpbSlideFragments(presPart)) {
    let cleaned = cleanSlideFragment(frag)
      .replace(/```[\w.+-]*\s*$/i, '')
      .replace(/<\/body>\s*<\/html>\s*$/i, '')
      .trimEnd();
    if (!classAttrHasSlideTokenInFragment(cleaned)) continue;
    // Полный фрагмент — не схлопывать титул и финальный title-slide по префиксу.
    const key = cleaned;
    if (seen.has(key)) continue;
    seen.add(key);
    allSlides.push(cleaned);
  }
  if (!allSlides.length) return text;

  // Chrome (link/style) берём из первого HTML-блока презентации.
  let chrome = extractPresentationHeadChrome(
    presPart,
    presPart.indexOf(allSlides[0]) >= 0 ? presPart.indexOf(allSlides[0]) : presPart.length,
  ).trim();
  if (!chrome || !/<style\b/i.test(chrome)) {
    chrome = !chrome ? presentationFallbackChrome() : `${chrome}\n${presentationFallbackChrome()}`;
  }
  const merged = ensureHtmlDocumentClosed(wrapPresentationSlidesHtml(chrome, allSlides.join('\n')));
  const body = `\`\`\`html\n${merged}\n\`\`\``;

  return prefix ? `${prefix}\n\n${body}` : body;
}

/** Внутри фрагмента есть хотя бы один тег с class-токеном slide. */
function classAttrHasSlideTokenInFragment(fragment: string): boolean {
  const quoted = /class\s*=\s*(["'])([^"']*)\1/gi;
  let m: RegExpExecArray | null;
  while ((m = quoted.exec(fragment)) !== null) {
    if (classAttrHasSlideToken(m[2])) return true;
  }
  return /class\s*=\s*slide(?![\w-])/i.test(fragment);
}

/** Мягкие признаки скилла — только для спиннера «генерация…» во время стрима. */
export function hasPresentationStreamHints(code: string): boolean {
  const lower = code.toLowerCase();
  return (
    hasGpbSlideClass(code) ||
    lower.includes('/static/icons/gpb_') ||
    lower.includes('/static/icons_new/') ||
    lower.includes('content-zone') ||
    lower.includes('slide-title') ||
    lower.includes('slide-header') ||
    lower.includes('gpb-slide') ||
    lower.includes('--gpb-') ||
    // GPB mm-формат (не `.slide` в CSS — ложное срабатывание на обычных HTML-дашбордах)
    (lower.includes('<style') && (lower.includes('297mm') || lower.includes('167mm')))
  );
}

/**
 * Заголовок/identifier артефакта намекает на презентацию (до появления .slide в HTML).
 */
export function artifactMetaLooksLikePresentation(meta?: {
  title?: string | null;
  identifier?: string | null;
  type?: string | null;
}): boolean {
  const blob = `${meta?.title || ''} ${meta?.identifier || ''} ${meta?.type || ''}`.toLowerCase();
  return /present|презента|слайд|slide|gpb.?html|deck/.test(blob);
}

export interface StablePresentationSnapshot {
  /** HTML только с «зафиксированными» слайдами (без текущего недописанного). */
  html: string | null;
  /** Сколько слайдов уже можно показывать. */
  readyCount: number;
  /** Сколько открывающих .slide уже встретилось в потоке. */
  startedCount: number;
  /** Ещё идёт генерация текущего слайда. */
  pending: boolean;
}

/**
 * Для стрима: последний .slide почти всегда обрезан — его не показываем,
 * чтобы iframe не мерцал на каждый токен. Обновляем snapshot только когда
 * появляется новый слайд (предыдущий считается готовым).
 * @param opts.maxReadyCount — ограничить число готовых слайдов (пошаговый reveal).
 */
export function getStablePresentationSnapshot(
  code: string,
  isStreaming: boolean,
  opts?: { maxReadyCount?: number },
): StablePresentationSnapshot {
  const cleaned = sanitizePresentationHtmlSource(code);
  const fragments = extractGpbSlideFragments(cleaned);
  const startedCount = Math.max(countGpbSlideOpens(cleaned), fragments.length);
  const maxReady = opts?.maxReadyCount;

  if (!cleaned.trim()) {
    return { html: null, readyCount: 0, startedCount: 0, pending: isStreaming };
  }

  if (!isStreaming) {
    try {
      let htmlOut = cleaned;
      const chromeFromDoc = extractPresentationHeadChrome(
        cleaned,
        fragments.length ? cleaned.indexOf(fragments[0]) : 0,
      ).trim();
      const chrome =
        chromeFromDoc && /<style\b/i.test(chromeFromDoc)
          ? chromeFromDoc
          : chromeFromDoc
            ? `${chromeFromDoc}\n${presentationFallbackChrome()}`
            : presentationFallbackChrome();

      // Пустой <head> без <style> — собираем из фрагментов + fallback CSS.
      if (!/<style\b/i.test(htmlOut) && fragments.length) {
        htmlOut = wrapPresentationSlidesHtml(chrome, fragments.map(cleanSlideFragment).join('\n'));
      }

      const doc = new DOMParser().parseFromString(htmlOut, 'text/html');
      const n = doc.querySelectorAll('.slide').length;
      // DOMParser на кривом хвосте иногда «съедает» последний .slide (title-slide),
      // хотя в исходнике он есть — тогда пересобираем из regex-фрагментов.
      if (fragments.length > n && fragments.length > 0) {
        const rebuilt = wrapPresentationSlidesHtml(
          chrome,
          fragments.map(cleanSlideFragment).join('\n'),
        );
        return {
          html: rebuilt,
          readyCount: fragments.length,
          startedCount: Math.max(startedCount, fragments.length),
          pending: false,
        };
      }
      const readyCount = Math.max(n, fragments.length);
      if (readyCount === 0) {
        return { html: null, readyCount: 0, startedCount: 0, pending: false };
      }
      if (n === 0 && fragments.length > 0) {
        return {
          html: wrapPresentationSlidesHtml(chrome, fragments.map(cleanSlideFragment).join('\n')),
          readyCount: fragments.length,
          startedCount: fragments.length,
          pending: false,
        };
      }
      return {
        html: htmlOut,
        readyCount,
        startedCount: Math.max(startedCount, readyCount),
        pending: false,
      };
    } catch {
      if (!fragments.length) {
        return { html: null, readyCount: 0, startedCount: 0, pending: false };
      }
      const chrome = extractPresentationHeadChrome(cleaned, cleaned.indexOf(fragments[0]));
      return {
        html: wrapPresentationSlidesHtml(chrome, fragments.join('\n')),
        readyCount: fragments.length,
        startedCount: fragments.length,
        pending: false,
      };
    }
  }

  // Пока стрим: последний .slide почти всегда обрезан — его не показываем.
  let readyCount = Math.max(0, startedCount - 1);
  if (typeof maxReady === 'number' && Number.isFinite(maxReady)) {
    readyCount = Math.min(readyCount, Math.max(0, Math.floor(maxReady)));
  }
  if (readyCount === 0 || !fragments.length) {
    return { html: null, readyCount: 0, startedCount, pending: true };
  }

  const readyFragments = fragments.slice(0, Math.min(readyCount, fragments.length));
  if (!readyFragments.length) {
    return { html: null, readyCount: 0, startedCount, pending: true };
  }
  const chrome = extractPresentationHeadChrome(cleaned, cleaned.indexOf(readyFragments[0]));
  return {
    html: wrapPresentationSlidesHtml(chrome, readyFragments.join('\n')),
    readyCount: readyFragments.length,
    startedCount,
    pending: true,
  };
}

/**
 * Открывает viewer с HTML презентации в новой вкладке.
 * Используем localStorage (не sessionStorage): при window.open с noopener
 * новая вкладка получает пустой sessionStorage и HTML «теряется».
 */
export function openPresentationViewer(html: string): void {
  const key = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  const payload = sanitizePresentationHtmlSource(html);
  try {
    localStorage.setItem(STORAGE_PREFIX + key, payload);
  } catch {
    throw new Error('Не удалось сохранить HTML презентации (слишком большой объём?)');
  }
  const base = (process.env.PUBLIC_URL || '').replace(/\/$/, '');
  // Без noopener/noreferrer — иначе в части браузеров storage не шарится вовремя.
  const win = window.open(
    `${base}/presentation-viewer.html?key=${encodeURIComponent(key)}`,
    '_blank'
  );
  if (!win) {
    localStorage.removeItem(STORAGE_PREFIX + key);
    throw new Error('Всплывающее окно заблокировано браузером');
  }
}
