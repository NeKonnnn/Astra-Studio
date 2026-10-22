/** Открытие артефакта в отдельной вкладке — показываем уже отрисованную схему/диаграмму. */

import {
  isHtmlArtifactType,
  isMarkdownArtifactType,
  isMermaidArtifactType,
  isSvgArtifactType,
  prepareMermaidSourceForRender,
  repairMermaidSource,
  sanitizeMermaidSource,
  stripMermaidStyling,
} from './artifacts';
import { rewriteHtmlArtifactScriptsForOffline } from './htmlArtifactScripts';

export function sourceLabelForArtifactType(type: string): string {
  const t = (type || '').toLowerCase();
  if (t === 'application/vnd.mermaid') return 'Mermaid';
  if (t === 'text/markdown' || t === 'text/md') return 'Markdown';
  if (t === 'text/html' || t === 'application/vnd.code-html') return 'HTML';
  if (t === 'image/svg+xml') return 'SVG';
  if (t === 'application/vnd.react' || t === 'application/vnd.ant.react') return 'React';
  return 'код';
}

const UNSAFE_URL_RE = /^\s*(?:javascript|vbscript|data\s*:\s*text\/html)/i;
const UNSAFE_SVG_TAGS = ['script', 'iframe', 'object', 'embed', 'meta', 'base'];

/**
 * Очистка SVG перед вставкой в документ viewer-вкладки (BUG-FE-04):
 * удаляет `<script>`/`<iframe>`/`<object>`/`<embed>`, `on*`-обработчики и `javascript:`-ссылки.
 * `<foreignObject>` и стили сохраняются — на них держатся подписи mermaid.
 * HTML-артефакты намеренно НЕ очищаются: они интерактивны (скрипты, презентации), безопасность
 * для них обеспечивается изоляцией `artifact-viewer.html` / sandbox iframe.
 */
export function sanitizeSvgMarkup(svg: string): string {
  const raw = String(svg || '');
  if (!raw) return '';
  if (typeof DOMParser === 'undefined') {
    return raw
      .replace(/<\s*(script|iframe|object|embed)\b[\s\S]*?(?:<\/\s*\1\s*>|$)/gi, '')
      .replace(/\son[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '');
  }
  const doc = new DOMParser().parseFromString(raw, 'text/html');
  UNSAFE_SVG_TAGS.forEach((tag) => {
    doc.querySelectorAll(tag).forEach((el) => el.remove());
  });
  doc.body.querySelectorAll('*').forEach((el) => {
    Array.from(el.attributes).forEach((attr) => {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on')) {
        el.removeAttribute(attr.name);
      } else if (
        (name === 'href' || name === 'xlink:href' || name === 'src' || name === 'action') &&
        UNSAFE_URL_RE.test(attr.value)
      ) {
        el.removeAttribute(attr.name);
      }
    });
  });
  return doc.body.innerHTML;
}

function wrapCenteredSvg(rawSvg: string, title: string): string {
  const svg = sanitizeSvgMarkup(rawSvg);
  return `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>${escapeHtml(title)} — AstraChat</title>
  <style>
    html, body {
      margin: 0; min-height: 100%;
      background: #e8eaed;
      display: flex; align-items: center; justify-content: center;
      padding: 24px; font-family: 'Segoe UI', system-ui, sans-serif;
    }
    .card {
      background: #fff; border-radius: 10px;
      box-shadow: 0 2px 12px rgba(0,0,0,.08);
      padding: 24px; max-width: 96vw; overflow: auto;
    }
    .card h1 { margin: 0 0 16px; font-size: 1.15rem; font-weight: 650; color: #1f2937; }
    svg { max-width: 100%; height: auto; display: block; }
  </style>
</head>
<body>
  <div class="card">
    <h1>${escapeHtml(title)}</h1>
    ${svg}
  </div>
</body>
</html>`;
}

function wrapHtmlDocument(html: string, title: string): string {
  const body = rewriteHtmlArtifactScriptsForOffline(html || '');
  if (/<!doctype/i.test(body) || /<html[\s>]/i.test(body)) {
    return body;
  }
  return `<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>${escapeHtml(title)} — AstraChat</title>
<style>body{margin:0;padding:16px;font-family:system-ui,sans-serif}</style>
</head><body>${body}</body></html>`;
}

function escapeHtml(s: string): string {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function pickMermaidSource(raw: string): string {
  // Сначала исходник со стилями/цветами; strip — только fallback при битом синтаксисе.
  return (
    prepareMermaidSourceForRender(raw) ||
    sanitizeMermaidSource(raw) ||
    repairMermaidSource(raw) ||
    stripMermaidStyling(raw) ||
    (raw || '').trim()
  );
}

/** SVG из DOM inline-viewer, если уже отрисован. */
export function captureSvgFromPreviewRoot(root: HTMLElement | null): string | null {
  if (!root) return null;
  const svg = root.querySelector('svg');
  return svg ? svg.outerHTML : null;
}

export function captureHtmlFromPreviewRoot(root: HTMLElement | null): string | null {
  if (!root) return null;
  const iframe = root.querySelector('iframe') as HTMLIFrameElement | null;
  if (iframe) {
    const srcdoc = iframe.getAttribute('srcdoc') || (iframe as any).srcdoc;
    if (typeof srcdoc === 'string' && srcdoc.trim()) return srcdoc;
    try {
      const doc = iframe.contentDocument;
      if (doc?.documentElement) return '<!DOCTYPE html>\n' + doc.documentElement.outerHTML;
    } catch {
      /* ignore */
    }
  }
  return null;
}

async function renderMermaidToSvg(source: string): Promise<string> {
  const code = pickMermaidSource(source);
  const mermaid = (await import('mermaid')).default;
  mermaid.initialize({
    startOnLoad: false,
    // strict: нет HTML-инъекций из LLM-диаграмм и click-callbacks в SVG, который попадёт в новую вкладку.
    securityLevel: 'strict',
    // theme base — чтобы themeVariables/цвета пользователя работали.
    theme: 'base',
    suppressErrorRendering: true,
  });
  const id = `artifact-ext-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  const variants = [
    code,
    prepareMermaidSourceForRender(source),
    sanitizeMermaidSource(source),
    repairMermaidSource(source),
    stripMermaidStyling(source),
  ].filter((v, i, a) => v && a.indexOf(v) === i);
  let lastErr: unknown = null;
  for (const variant of variants) {
    try {
      const { svg } = await mermaid.render(`${id}-${variants.indexOf(variant)}`, variant);
      return svg;
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error('Не удалось отрисовать Mermaid');
}

function simpleMarkdownToHtml(md: string): string {
  return escapeHtml(md)
    .replace(/^### (.*)$/gm, '<h3>$1</h3>')
    .replace(/^## (.*)$/gm, '<h2>$1</h2>')
    .replace(/^# (.*)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br/>');
}

/**
 * Собирает полный HTML-документ для новой вкладки (схема/диаграмма, не сырой код).
 */
export async function buildArtifactViewerDocument(opts: {
  type: string;
  content: string;
  title?: string;
  previewRoot?: HTMLElement | null;
}): Promise<string> {
  const title = opts.title || sourceLabelForArtifactType(opts.type) || 'Артефакт';
  const type = opts.type || '';

  if (isMermaidArtifactType(type)) {
    const fromDom = captureSvgFromPreviewRoot(opts.previewRoot || null);
    const svg = fromDom || (await renderMermaidToSvg(opts.content));
    return wrapCenteredSvg(svg, title);
  }

  if (isHtmlArtifactType(type)) {
    const fromDom = captureHtmlFromPreviewRoot(opts.previewRoot || null);
    return wrapHtmlDocument(fromDom || opts.content, title);
  }

  if (isSvgArtifactType(type)) {
    const fromDom = captureSvgFromPreviewRoot(opts.previewRoot || null);
    const svg = fromDom || opts.content;
    return wrapCenteredSvg(svg.includes('<svg') ? svg : `<svg xmlns="http://www.w3.org/2000/svg">${svg}</svg>`, title);
  }

  if (isMarkdownArtifactType(type)) {
    return wrapHtmlDocument(
      `<div style="max-width:900px;margin:24px auto;padding:24px;background:#fff;border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.08)">${simpleMarkdownToHtml(opts.content)}</div>`,
      title,
    );
  }

  // React и прочее — хотя бы не пустая страница; но пользователь просил схемы
  return wrapHtmlDocument(
    `<div style="max-width:900px;margin:24px auto;padding:24px;background:#fff;border-radius:10px">
      <h1>${escapeHtml(title)}</h1>
      <p style="color:#6b7280">Интерактивный preview этого типа в новой вкладке ограничен.</p>
      <pre style="white-space:pre-wrap;font-size:13px">${escapeHtml(opts.content)}</pre>
    </div>`,
    title,
  );
}

const ORPHAN_CLEANUP_MS = 60_000;

/** Открывает новую вкладку с готовым preview (localStorage + artifact-viewer.html). */
export async function openArtifactViewer(opts: {
  type: string;
  content: string;
  title?: string;
  previewRoot?: HTMLElement | null;
}): Promise<void> {
  const STORAGE_PREFIX = 'astrachat:artifact:';
  const key = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;

  let previewHtml = '';
  try {
    previewHtml = await buildArtifactViewerDocument(opts);
  } catch (e) {
    console.warn('artifact preview build failed, opening raw content fallback', e);
  }

  const payload = {
    type: opts.type,
    content: opts.content,
    title: opts.title || sourceLabelForArtifactType(opts.type) || 'Артефакт',
    previewHtml,
  };

  try {
    localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(payload));
  } catch {
    throw new Error('Не удалось сохранить артефакт (слишком большой объём?)');
  }

  const base = (process.env.PUBLIC_URL || '').replace(/\/$/, '');
  // noopener,noreferrer: новая вкладка не получает window.opener. Данные передаются через
  // localStorage (same-origin), поэтому ссылка на окно не нужна; при noopener window.open
  // всегда возвращает null, так что блокировку popup по результату определить нельзя.
  // artifact-viewer.html сам удаляет ключ после чтения.
  window.open(
    `${base}/artifact-viewer.html?key=${encodeURIComponent(key)}`,
    '_blank',
    'noopener,noreferrer',
  );
  // Страховка: если вкладка не открылась (popup заблокирован), не оставляем артефакт в localStorage.
  setTimeout(() => {
    try {
      localStorage.removeItem(STORAGE_PREFIX + key);
    } catch {
      /* ignore */
    }
  }, ORPHAN_CLEANUP_MS);
}