import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, CircularProgress, IconButton, Tooltip, Typography, Collapse, useTheme } from '@mui/material';
import {
  OpenInNew as OpenInNewIcon,
  Code as CodeIcon,
  ExpandLess as ExpandLessIcon,
} from '@mui/icons-material';
import {
  getStablePresentationSnapshot,
  openPresentationViewer,
} from '../utils/presentationViewer';
import { useInViewport } from '../hooks/useInViewport';
import ThinkingShimmerText from './chat/ThinkingShimmerText';

/** Номинальный размер слайда GPB (как в presentation-viewer.html). */
const SLIDE_W_MM = 297;
const SLIDE_H_MM = 167;
const SLIDE_ASPECT = SLIDE_W_MM / SLIDE_H_MM;

function stripEmbeddedScripts(html: string): string {
  return html.replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '');
}

function escapeForScriptPlain(html: string): string {
  return html.replace(/<\/script/gi, '<\\/script');
}

/**
 * Self-contained srcdoc.
 * Слайд всегда в номинальном размере 297×167mm, в контейнер вписывается
 * через transform: scale — без растягивания width/height (иначе ломается вёрстка GPB).
 */
export function buildInlinePresentationViewerSrcDoc(rawHtml: string): string {
  const html = escapeForScriptPlain(stripEmbeddedScripts(rawHtml));
  const publicBase = (process.env.PUBLIC_URL || '').replace(/\/$/, '');
  const pptxScript = `${publicBase}/static/dom-to-pptx.bundle.js`;

  return `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="${pptxScript}"></script>
  <style>
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      padding: 0;
      width: 100%;
      height: 100%;
      background: #e8eaed;
      font-family: 'Segoe UI', sans-serif;
      overflow: hidden;
    }
    .root {
      display: flex;
      flex-direction: column;
      width: 100%;
      height: 100%;
      padding: 8px;
      gap: 6px;
    }
    #stageWrap {
      flex: 1 1 auto;
      min-height: 0;
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      position: relative;
    }
    #stageScaler {
      position: relative;
      flex-shrink: 0;
    }
    #viewer {
      width: ${SLIDE_W_MM}mm;
      height: ${SLIDE_H_MM}mm;
      background: white;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.12);
      position: relative;
      overflow: hidden;
      border-radius: 4px;
      transform-origin: top left;
    }
    #viewer:empty::after {
      content: 'Загрузка...';
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      color: #999;
      font-size: 13px;
    }
    .toolbar {
      display: flex;
      gap: 6px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: center;
      flex-shrink: 0;
    }
    button {
      padding: 7px 14px;
      background: #2355D7;
      color: white;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12px;
      line-height: 1.2;
    }
    button:disabled {
      background: #9ca3af;
      cursor: not-allowed;
    }
    button.secondary {
      background: #fff;
      color: #2355D7;
      border: 1px solid #2355D7;
    }
    #counter { font-size: 11px; color: #666; flex-shrink: 0; }
    #error {
      display: none;
      width: 100%;
      padding: 8px 10px;
      background: #fee2e2;
      color: #991b1b;
      border-radius: 6px;
      font-size: 12px;
      flex-shrink: 0;
    }
    iframe#frame { display: none; }
  </style>
</head>
<body>
  <div class="root">
    <div id="error"></div>
    <div id="stageWrap">
      <div id="stageScaler">
        <div id="viewer"></div>
      </div>
    </div>
    <div class="toolbar">
      <button type="button" class="secondary" id="prevBtn" disabled>← Назад</button>
      <button type="button" class="secondary" id="nextBtn" disabled>Вперёд →</button>
      <button type="button" id="exportBtn" disabled>Скачать PPTX</button>
      <span id="counter">—</span>
    </div>
  </div>
  <iframe id="frame" title="presentation-source"></iframe>
  <script id="src" type="text/plain">${html}</script>
  <script>
    var slides = [];
    var current = 0;
    var exporting = false;
    var applyGen = 0;
    var stickToLatest = true;
    var lastSlideCount = 0;

    function showError(msg) {
      var el = document.getElementById('error');
      el.style.display = 'block';
      el.textContent = msg;
    }

    function hideError() {
      var el = document.getElementById('error');
      el.style.display = 'none';
      el.textContent = '';
    }

    function stripEmbeddedScripts(h) {
      return h.replace(/<script[^>]*>[\\s\\S]*?<\\/script>/gi, '');
    }

    // Fence-sanitize снаружи (sanitizePresentationHtmlSource).
    // Здесь только BOM + незакрытый <!-- — без backticks в regex (ломают Babel).
    function sanitizePresentationHtml(h) {
      var s = String(h || '').replace(/^\\uFEFF/, '').trim();
      if (!s) return s;
      var lastOpen = s.lastIndexOf('<!--');
      if (lastOpen >= 0 && s.slice(lastOpen).indexOf('-->') < 0) {
        s = s.slice(0, lastOpen).replace(/\\s+$/, '');
      }
      return s;
    }

    function fitStage() {
      var wrap = document.getElementById('stageWrap');
      var scaler = document.getElementById('stageScaler');
      var viewer = document.getElementById('viewer');
      if (!wrap || !scaler || !viewer) return;
      var natW = viewer.offsetWidth;
      var natH = viewer.offsetHeight;
      if (!natW || !natH) return;
      var availW = wrap.clientWidth;
      var availH = wrap.clientHeight;
      if (!availW || !availH) return;
      var s = Math.min(availW / natW, availH / natH);
      if (s > 1) s = 1;
      viewer.style.transform = 'scale(' + s + ')';
      scaler.style.width = (natW * s) + 'px';
      scaler.style.height = (natH * s) + 'px';
    }

    function applyHtml(raw) {
      var html = sanitizePresentationHtml(stripEmbeddedScripts((raw || '').trim()));
      if (!html) return;
      var gen = ++applyGen;
      hideError();
      var frame = document.getElementById('frame');
      var keep = current;
      frame.onload = function () {
        if (gen !== applyGen) return;
        try {
          var doc = frame.contentDocument;
          // Все контейнеры с токеном class=slide (включая slide title-slide).
          slides = Array.from(doc.querySelectorAll('.slide'));
          if (!slides.length) {
            slides = Array.from(doc.querySelectorAll('[class~="slide"]'));
          }
          if (!slides.length) {
            showError('В HTML не найдены слайды с классом .slide');
            return;
          }
          document.querySelectorAll('style[data-astra-slide-css]').forEach(function (s) { s.remove(); });
          doc.querySelectorAll('style').forEach(function (s) {
            var ns = document.createElement('style');
            ns.setAttribute('data-astra-slide-css', '1');
            ns.textContent = s.textContent;
            document.head.appendChild(ns);
          });
          var grew = slides.length > lastSlideCount;
          lastSlideCount = slides.length;
          var idx;
          if (stickToLatest && grew) {
            idx = slides.length - 1;
          } else {
            idx = Math.min(Math.max(keep, 0), slides.length - 1);
          }
          show(idx);
          document.getElementById('exportBtn').disabled = false;
          document.getElementById('prevBtn').disabled = false;
          document.getElementById('nextBtn').disabled = false;
          fitStage();
          requestAnimationFrame(fitStage);
          try {
            window.parent.postMessage(
              { type: 'astra-pptx-applied', count: slides.length },
              '*'
            );
          } catch (ackErr) {}
        } catch (e) {
          showError('Ошибка загрузки HTML презентации');
          console.error(e);
        }
      };
      frame.srcdoc = html;
    }

    function init() {
      window.addEventListener('message', function (e) {
        if (e.source !== window.parent) return;
        if (!e.data || e.data.type !== 'astra-pptx-set-html') return;
        applyHtml(String(e.data.html || ''));
      });
      var initial = document.getElementById('src').textContent || '';
      if (initial.trim()) applyHtml(initial);
      if (typeof ResizeObserver !== 'undefined') {
        new ResizeObserver(fitStage).observe(document.getElementById('stageWrap'));
      }
      window.addEventListener('resize', fitStage);
    }

    function show(i) {
      if (i < 0 || i >= slides.length) return;
      current = i;
      stickToLatest = i >= slides.length - 1;
      var viewer = document.getElementById('viewer');
      var clone = slides[i].cloneNode(true);
      clone.style.position = 'absolute';
      clone.style.top = '0';
      clone.style.left = '0';
      clone.style.margin = '0';
      clone.style.width = '${SLIDE_W_MM}mm';
      clone.style.height = '${SLIDE_H_MM}mm';
      clone.style.maxWidth = 'none';
      clone.style.maxHeight = 'none';
      clone.style.boxSizing = 'border-box';
      clone.style.overflow = 'hidden';
      viewer.innerHTML = '';
      viewer.appendChild(clone);
      document.getElementById('counter').textContent = (i + 1) + ' / ' + slides.length;
      document.getElementById('prevBtn').disabled = i <= 0;
      document.getElementById('nextBtn').disabled = i >= slides.length - 1;
      fitStage();
    }

    document.getElementById('prevBtn').addEventListener('click', function () { show(current - 1); });
    document.getElementById('nextBtn').addEventListener('click', function () { show(current + 1); });
    document.getElementById('stageWrap').addEventListener('wheel', function (e) {
      e.preventDefault();
      show(current + (e.deltaY > 0 ? 1 : -1));
    }, { passive: false });

    async function exportPptx() {
      if (exporting || !slides.length) return;
      if (typeof domToPptx === 'undefined' || !domToPptx.exportToPptx) {
        showError('Библиотека dom-to-pptx не загружена.');
        return;
      }
      exporting = true;
      var btn = document.getElementById('exportBtn');
      btn.disabled = true;
      btn.textContent = 'Экспорт...';
      var wrap = document.createElement('div');
      wrap.style.cssText = 'position:fixed;left:-9999px;top:0;';
      document.body.appendChild(wrap);
      var elems = slides.map(function (s) {
        var d = document.createElement('div');
        d.style.cssText = 'width:${SLIDE_W_MM}mm;height:${SLIDE_H_MM}mm;overflow:hidden;position:relative;';
        var c = s.cloneNode(true);
        c.style.cssText = 'width:100%;height:100%;margin:0;font-family:"Cera CY",sans-serif;';
        d.appendChild(c);
        wrap.appendChild(d);
        return d;
      });
      try {
        await domToPptx.exportToPptx(elems, {
          fileName: 'presentation.pptx',
          svgAsVector: true,
          autoEmbedFonts: false,
        });
      } catch (e) {
        showError('Ошибка экспорта в PPTX: ' + (e && e.message ? e.message : String(e)));
        console.error(e);
      } finally {
        wrap.remove();
        exporting = false;
        btn.disabled = false;
        btn.textContent = 'Скачать PPTX';
      }
    }

    document.getElementById('exportBtn').addEventListener('click', exportPptx);
    init();
  </script>
</body>
</html>`;
}

interface InlinePresentationViewerProps {
  html: string;
  sourceSlot?: React.ReactNode;
  /** Пока модель стримит HTML — не пересоздаём iframe на каждый токен. */
  isStreaming?: boolean;
  /**
   * Внутри ArtifactCard: без своей рамки/шапки («Презентация»),
   * иначе двойной chrome поверх заголовка артефакта.
   */
  embedded?: boolean;
}

/**
 * Встроенный просмотр GPB-презентации в ответе чата.
 * При стриме: спиннер + послайдовый показ без мерцания.
 */
const PPTX_PUSH_MIN_MS = 700;
/** Пауза без роста HTML: только дожимаем iframe, спиннер не гасим (стрим ещё идёт). */
const PPTX_CATCHUP_MS = 1200;

export default function InlinePresentationViewer({
  html,
  sourceSlot,
  isStreaming = false,
  embedded = false,
}: InlinePresentationViewerProps) {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const [showSource, setShowSource] = useState(false);
  const [committedHtml, setCommittedHtml] = useState<string | null>(null);
  // readyCount = слайды, ФАКТИЧЕСКИ отрисованные в iframe (по ack от него),
  // чтобы плашка «готово N» не убегала вперёд реального кадра.
  const [readyCount, setReadyCount] = useState(0);
  const [startedCount, setStartedCount] = useState(0);
  const [pending, setPending] = useState(isStreaming);
  const lastReadyRef = useRef(-1);
  const lastPushAtRef = useRef(0);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const iframeReadyRef = useRef(false);
  const { ref: viewportRef, inView } = useInViewport<HTMLDivElement>(
    '280px 0px',
    // Только текущая генерация сразу с iframe; старые презентации — после IO,
    // иначе при Virtuoso→map все iframe встают разом → белый экран.
    Boolean(isStreaming),
  );
  const keepIframe = isStreaming || inView;

  const shellSrcDoc = useMemo(() => buildInlinePresentationViewerSrcDoc(''), []);

  const pushHtmlToIframe = (nextHtml: string) => {
    const win = iframeRef.current?.contentWindow;
    if (!win || !iframeReadyRef.current) return;
    win.postMessage({ type: 'astra-pptx-set-html', html: nextHtml }, '*');
  };

  useEffect(() => {
    const base = getStablePresentationSnapshot(html, isStreaming);

    if (!isStreaming) {
      if (base.html) {
        setCommittedHtml(base.html);
        setReadyCount(base.readyCount);
        setStartedCount(base.startedCount);
        lastReadyRef.current = base.readyCount;
      }
      setPending(false);
      return;
    }

    setStartedCount(base.startedCount);
    setPending(true);

    // По одному слайду: не прыгаем 1→4 через jumped/throttle.
    if (base.readyCount <= 0) {
      setReadyCount(0);
      return;
    }

    const now = Date.now();
    const first = lastReadyRef.current < 0;
    const elapsed = first || now - lastPushAtRef.current >= PPTX_PUSH_MIN_MS;
    if (!elapsed) {
      // Не двигаем плашку вперёд iframe: readyCount = уже показанное.
      return;
    }

    const target = first
      ? 1
      : Math.min(lastReadyRef.current + 1, base.readyCount);
    if (target <= lastReadyRef.current) {
      return;
    }

    const snap = getStablePresentationSnapshot(html, true, { maxReadyCount: target });
    if (!snap.html) {
      return;
    }
    lastReadyRef.current = snap.readyCount;
    lastPushAtRef.current = now;
    // readyCount НЕ трогаем здесь — обновит ack от iframe (astra-pptx-applied),
    // чтобы «готово N» совпадало с реально показанным кадром.
    setCommittedHtml(snap.html);
  }, [html, isStreaming]);

  // Если HTML уже готов дальше iframe — догоняем по одному слайду.
  useEffect(() => {
    if (!isStreaming) return;
    const id = window.setInterval(() => {
      const base = getStablePresentationSnapshot(html, true);
      if (base.readyCount <= lastReadyRef.current) return;
      if (Date.now() - lastPushAtRef.current < PPTX_PUSH_MIN_MS) return;
      const target = Math.min(lastReadyRef.current + 1, base.readyCount);
      const snap = getStablePresentationSnapshot(html, true, { maxReadyCount: target });
      if (!snap.html) return;
      lastReadyRef.current = snap.readyCount;
      lastPushAtRef.current = Date.now();
      setStartedCount(base.startedCount);
      setCommittedHtml(snap.html);
      setPending(true);
    }, PPTX_CATCHUP_MS);
    return () => window.clearInterval(id);
  }, [html, isStreaming]);

  // Ack от iframe: слайды реально отрисованы — только теперь двигаем «готово N».
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source !== iframeRef.current?.contentWindow) return;
      const data = e.data as { type?: string; count?: number } | null;
      if (!data || data.type !== 'astra-pptx-applied') return;
      const n = typeof data.count === 'number' ? data.count : 0;
      setReadyCount((prev: number) => (n > prev ? n : !isStreaming ? n : prev));
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [isStreaming]);

  useEffect(() => {
    if (!committedHtml) return;
    pushHtmlToIframe(committedHtml);
  }, [committedHtml]);

  useEffect(() => {
    if (!keepIframe) iframeReadyRef.current = false;
  }, [keepIframe]);

  const showMissingSlides = !pending && !committedHtml;

  const statusLabel = (() => {
    if (showMissingSlides) return 'Презентация · нет слайдов';
    if (!pending) return 'Презентация';
    // readyCount = слайды в iframe; startedCount = открыто в HTML (включая текущий недописанный).
    if (readyCount > 0) {
      const generating =
        startedCount > readyCount ? ` · слайд ${readyCount + 1}…` : '…';
      return `Презентация · готово ${readyCount}${generating}`;
    }
    if (startedCount > 0) return `Презентация · слайд ${startedCount}…`;
    return 'Презентация · генерация…';
  })();

  const handleOpenExternal = () => {
    try {
      openPresentationViewer(html);
    } catch (e) {
      console.error('Failed to open presentation viewer:', e);
    }
  };

  const stage = (
    <Box
      ref={viewportRef}
      sx={
        embedded
          ? {
              position: 'absolute',
              inset: 0,
              width: '100%',
              height: '100%',
              bgcolor: '#e8eaed',
              overflow: 'hidden',
            }
          : {
              width: '100%',
              maxHeight: 'min(88vh, 880px)',
              minHeight: 320,
              position: 'relative',
              bgcolor: '#e8eaed',
              pt: `calc(100% / ${SLIDE_ASPECT})`,
              pb: '48px',
              overflow: 'hidden',
            }
      }
    >
      {committedHtml && keepIframe ? (
        <iframe
          ref={iframeRef}
          title="Просмотр презентации"
          srcDoc={shellSrcDoc}
          onLoad={() => {
            iframeReadyRef.current = true;
            if (committedHtml) pushHtmlToIframe(committedHtml);
          }}
          sandbox="allow-scripts allow-same-origin allow-downloads"
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            border: 0,
            display: 'block',
          }}
        />
      ) : null}

      {showMissingSlides ? (
        <Box
          sx={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 1,
            px: 2,
            bgcolor: '#e8eaed',
          }}
        >
          <Typography variant="body2" sx={{ color: '#991b1b', fontSize: 13, textAlign: 'center' }}>
            В HTML нет элементов с классом <code>.slide</code>
          </Typography>
          <Typography variant="caption" sx={{ color: 'text.secondary', textAlign: 'center', maxWidth: 420 }}>
            Каждый слайд должен быть обёрнут в <code>{'<div class="slide">'}</code> — не путать с{' '}
            <code>slide-title</code> / <code>content-zone</code>.
          </Typography>
        </Box>
      ) : null}
    </Box>
  );

  if (embedded) {
    return (
      <Box sx={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
        {stage}
      </Box>
    );
  }

  return (
    <Box
      sx={{
        my: 2,
        borderRadius: 2,
        overflow: 'hidden',
        border: '1px solid',
        borderColor: 'divider',
        bgcolor: 'background.paper',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 1,
          px: 1.5,
          py: 0.75,
          borderBottom: '1px solid',
          borderColor: 'divider',
          bgcolor: (t) => (t.palette.mode === 'dark' ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.02)'),
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
          {pending ? (
            <Box
              sx={{
                display: 'inline-flex',
                flexShrink: 0,
                animation: 'thinking 2s ease-in-out infinite',
              }}
            >
              <CircularProgress size={14} thickness={5} sx={{ color: 'primary.main' }} />
            </Box>
          ) : null}
          {pending ? (
            <ThinkingShimmerText
              isDarkMode={isDarkMode}
              fontSize="0.75rem"
              fontWeight={600}
              sx={{
                letterSpacing: 0.02,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                minWidth: 0,
              }}
            >
              {statusLabel}
            </ThinkingShimmerText>
          ) : (
            <Typography
              variant="caption"
              sx={{ fontWeight: 600, letterSpacing: 0.02, overflow: 'hidden', textOverflow: 'ellipsis' }}
            >
              {statusLabel}
            </Typography>
          )}
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
          {sourceSlot && !pending ? (
            <Tooltip title={showSource ? 'Скрыть HTML' : 'Показать HTML'}>
              <IconButton size="small" onClick={() => setShowSource((v) => !v)}>
                {showSource ? <ExpandLessIcon fontSize="small" /> : <CodeIcon fontSize="small" />}
              </IconButton>
            </Tooltip>
          ) : null}
          {!pending ? (
            <Tooltip title="Открыть в новой вкладке">
              <IconButton size="small" onClick={handleOpenExternal}>
                <OpenInNewIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          ) : null}
        </Box>
      </Box>

      {stage}

      {sourceSlot && !pending ? (
        <Collapse in={showSource} unmountOnExit timeout={180}>
          <Box sx={{ borderTop: '1px solid', borderColor: 'divider' }}>{sourceSlot}</Box>
        </Collapse>
      ) : null}
    </Box>
  );
}