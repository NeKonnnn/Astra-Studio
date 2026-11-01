/**
 * Диагностика React error #185 (Maximum update depth exceeded → белый экран).
 *
 * В консоли фильтруй по: ASTRA#185
 * Ручной дамп: в консоли выполнить __ASTRA_185_DUMP__()
 * Вкл/выкл: localStorage.setItem('astra_debug_185','0'|'1') — по умолчанию ВКЛ.
 */

const PREFIX = '[ASTRA#185]';
const MAX_BREADCRUMBS = 120;
const LOOP_WINDOW_MS = 1500;
const LOOP_RENDER_THRESHOLD = 40;

type Breadcrumb = {
  t: number;
  kind: string;
  detail?: string;
};

const breadcrumbs: Breadcrumb[] = [];
let renderCountWindow = 0;
let renderWindowStartedAt = 0;
let dumpPrinted = false;

function enabled(): boolean {
  try {
    const v = localStorage.getItem('astra_debug_185');
    if (v === '0' || v === 'false') return false;
  } catch {
    /* */
  }
  return true;
}

function push(kind: string, detail?: string): void {
  if (!enabled()) return;
  const entry: Breadcrumb = { t: Date.now(), kind, detail };
  breadcrumbs.push(entry);
  if (breadcrumbs.length > MAX_BREADCRUMBS) breadcrumbs.shift();
}

export function astra185Mark(kind: string, detail?: string): void {
  push(kind, detail);
  if (!enabled()) return;
  // Не спамим каждый токен — только важные события и петли.
    if (
      kind.startsWith('effect:') ||
      kind.startsWith('render-loop') ||
      kind.startsWith('error') ||
      kind.startsWith('chat:') ||
      kind.startsWith('stream:') ||
      kind.startsWith('patch:') ||
      kind.startsWith('grok:')
    ) {
    // eslint-disable-next-line no-console
    console.log(PREFIX, kind, detail ?? '', new Date().toISOString());
  }
}

export function astra185MarkRender(meta: Record<string, unknown>): void {
  if (!enabled()) return;
  const now = Date.now();
  if (!renderWindowStartedAt || now - renderWindowStartedAt > LOOP_WINDOW_MS) {
    renderWindowStartedAt = now;
    renderCountWindow = 0;
  }
  renderCountWindow += 1;
  push('render', JSON.stringify(meta));

  if (renderCountWindow === 10 || renderCountWindow === 25) {
    // eslint-disable-next-line no-console
    console.warn(PREFIX, `renders-in-window=${renderCountWindow}`, meta);
  }

  if (renderCountWindow >= LOOP_RENDER_THRESHOLD && !dumpPrinted) {
    dumpPrinted = true;
    // eslint-disable-next-line no-console
    console.error(
      PREFIX,
      `ПОДОЗРЕНИЕ НА ПЕТЛЮ: ${renderCountWindow} рендеров UnifiedChatPage за ${LOOP_WINDOW_MS}ms`,
    );
    astra185Dump('auto-render-loop');
  }
}

export function astra185Dump(reason = 'manual'): void {
  const lines = breadcrumbs.map((b) => {
    const iso = new Date(b.t).toISOString();
    return `${iso} | ${b.kind}${b.detail ? ` | ${b.detail}` : ''}`;
  });
  const payload = {
    reason,
    renderCountWindow,
    breadcrumbs: lines,
  };
  // eslint-disable-next-line no-console
  console.error(PREFIX, 'DUMP', payload);
  // eslint-disable-next-line no-console
  console.error(PREFIX, 'BREADCRUMBS (последние 30):');
  lines.slice(-30).forEach((line) => {
    // eslint-disable-next-line no-console
    console.error(PREFIX, line);
  });
  try {
    (window as unknown as { __ASTRA_185_LAST_DUMP__?: unknown }).__ASTRA_185_LAST_DUMP__ = payload;
  } catch {
    /* */
  }
  return;
}

export function installAstra185GlobalHandlers(): void {
  if (typeof window === 'undefined') return;
  const w = window as unknown as {
    __ASTRA_185_INSTALLED__?: boolean;
    __ASTRA_185_DUMP__?: () => void;
  };
  if (w.__ASTRA_185_INSTALLED__) return;
  w.__ASTRA_185_INSTALLED__ = true;
  w.__ASTRA_185_DUMP__ = () => astra185Dump('manual');

  window.addEventListener('error', (ev) => {
    const msg = String(ev?.error?.message || ev?.message || '');
    if (!msg.includes('#185') && !msg.toLowerCase().includes('maximum update depth')) return;
    astra185Mark('error:#185', msg.slice(0, 300));
    // eslint-disable-next-line no-console
    console.error(PREFIX, 'Поймана #185. Стек:', ev.error?.stack || ev.error);
    astra185Dump('caught-error-185');
  });

  window.addEventListener('unhandledrejection', (ev) => {
    const reason = ev?.reason;
    const msg = String(reason?.message || reason || '');
    if (!msg.includes('#185') && !msg.toLowerCase().includes('maximum update depth')) return;
    astra185Mark('error:unhandled-185', msg.slice(0, 300));
    astra185Dump('caught-unhandled-185');
  });

  // eslint-disable-next-line no-console
  console.info(
    PREFIX,
    'диагностика включена. Фильтр: ASTRA#185. Дамп: __ASTRA_185_DUMP__(). Выкл: localStorage.setItem("astra_debug_185","0")',
  );
}
