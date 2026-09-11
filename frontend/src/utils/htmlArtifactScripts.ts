/**
 * HTML-артефакты часто тянут Chart.js с CDN — в корп. сети это падает (ERR_NAME_NOT_RESOLVED).
 * Подменяем на локальный /vendor/chart.umd.min.js (копируется prestart/prebuild).
 */

/**
 * Для iframe.srcdoc: экранирует `</script` только внутри тел <script>,
 * чтобы HTML-парсер не закрыл тег раньше времени (если в JS есть строка "</script>").
 *
 * Важно: НЕ трогать сами закрывающие теги `</script>` — иначе документ
 * ломается (скрипт не закрывается → SyntaxError → графики/JS не работают).
 */
export function escapeHtmlForSrcDoc(html: string): string {
  const src = html || '';
  return src.replace(/<script(\b[^>]*)>([\s\S]*?)<\/script>/gi, (_full, attrs: string, body: string) => {
    const escapedBody = body.replace(/<\/script/gi, '<\\/script');
    return `<script${attrs}>${escapedBody}</script>`;
  });
}


const CHART_CDN_SCRIPT_RE =
  /<script\b[^>]*\bsrc\s*=\s*["'][^"']*(?:cdnjs\.cloudflare\.com|cdn\.jsdelivr\.net|unpkg\.com|cdn\.bootcdn\.net)[^"']*chart[^"']*["'][^>]*>\s*<\/script>/gi;

const CHART_ANY_SCRIPT_RE =
  /<script\b[^>]*\bsrc\s*=\s*["'][^"']*chart(?:\.umd)?(?:\.min)?\.js[^"']*["'][^>]*>\s*<\/script>/gi;

const USES_CHART_API_RE = /\b(?:new\s+Chart\s*\(|Chart\.register\s*\(|window\.Chart\b)/i;

function localChartScriptSrc(): string {
  const base = (process.env.PUBLIC_URL || '').replace(/\/$/, '');
  return `${base}/vendor/chart.umd.min.js`;
}

function localChartScriptTag(): string {
  return `<script src="${localChartScriptSrc()}"><\/script>`;
}

/** Нужен ли Chart.js в этом HTML (CDN или вызов API). */
export function htmlNeedsChartJs(html: string): boolean {
  const s = html || '';
  CHART_CDN_SCRIPT_RE.lastIndex = 0;
  CHART_ANY_SCRIPT_RE.lastIndex = 0;
  return CHART_CDN_SCRIPT_RE.test(s) || CHART_ANY_SCRIPT_RE.test(s) || USES_CHART_API_RE.test(s);
}

/**
 * Убирает CDN/внешние script Chart.js и вставляет локальный vendor-файл.
 * Остальной HTML не трогаем.
 */
export function rewriteHtmlArtifactScriptsForOffline(html: string): string {
  let out = html || '';
  if (!out.trim()) return out;

  const needsChart = htmlNeedsChartJs(out);
  // Сброс lastIndex у global regex после test
  CHART_CDN_SCRIPT_RE.lastIndex = 0;
  CHART_ANY_SCRIPT_RE.lastIndex = 0;

  out = out.replace(CHART_CDN_SCRIPT_RE, '');
  out = out.replace(CHART_ANY_SCRIPT_RE, '');

  if (!needsChart) return out;

  const tag = localChartScriptTag();
  if (out.includes('/vendor/chart.umd.min.js')) {
    return out;
  }

  if (/<\/head>/i.test(out)) {
    return out.replace(/<\/head>/i, `${tag}\n</head>`);
  }
  if (/<body\b[^>]*>/i.test(out)) {
    return out.replace(/<body\b[^>]*>/i, (m) => `${m}\n${tag}`);
  }
  return `${tag}\n${out}`;
}
