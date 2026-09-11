import React, { useEffect, useId, useRef, useState } from 'react';
import { Box, Typography } from '@mui/material';
import {
  prepareMermaidSourceForRender,
  repairMermaidSource,
  sanitizeMermaidSource,
  stripMermaidStyling,
} from '../../utils/artifacts';

function diagramBodyWithoutInit(code: string): string {
  return code.replace(/^%%\{[\s\S]*?\}%%\s*/gm, '').trimStart();
}

function pieChartHasData(code: string): boolean {
  const body = diagramBodyWithoutInit(code);
  if (!/^pie\b/im.test(body)) return true;
  return body.split('\n').some((line) => /^"[^"]+"\s*:\s*-?[\d.]+/.test(line.trim()));
}

interface Props {
  content: string;
  isStreaming?: boolean;
}

const MERMAID_INIT = {
  startOnLoad: false,
  suppressErrorRendering: true,
  securityLevel: 'loose' as const,
  // Только theme base уважает themeVariables (pie1..N, plotColorPalette).
  // default/neutral подставляют свою палитру и игнорируют цвета пользователя.
  theme: 'base' as const,
};

let mermaidInitTheme: string | null = null;

async function getMermaid() {
  const mermaid = (await import('mermaid')).default;
  if (mermaidInitTheme !== MERMAID_INIT.theme) {
    mermaid.initialize(MERMAID_INIT);
    mermaidInitTheme = MERMAID_INIT.theme;
  }
  return mermaid;
}

function cleanupMermaidDomJunk(renderId: string) {
  try {
    document.getElementById(renderId)?.remove();
    document.getElementById(`d${renderId}`)?.remove();
    document.querySelectorAll('[id^="dmermaid-"]').forEach((n) => {
      const text = (n.textContent || '').toLowerCase();
      if (text.includes('error in text') || text.includes('syntax error')) {
        n.remove();
      }
    });
    document.querySelectorAll('body > svg').forEach((n) => {
      const text = (n.textContent || '').toLowerCase();
      if (text.includes('error in text') && text.includes('version')) {
        n.remove();
      }
    });
  } catch {
    /* ignore */
  }
}

function errorMessage(e: any): string {
  const msg = e?.str || e?.message || String(e) || 'Не удалось отрисовать диаграмму';
  return typeof msg === 'string' ? msg : 'Ошибка синтаксиса Mermaid';
}

async function tryRender(
  mermaid: any,
  renderId: string,
  code: string,
): Promise<string> {
  await mermaid.parse(code);
  const { svg } = await mermaid.render(renderId, code);
  return svg;
}

export default function ArtifactMermaidPreview({ content, isStreaming = false }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const reactId = useId().replace(/:/g, '');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadingRef = useRef(true);

  useEffect(() => {
    let cancelled = false;
    let renderId = `mermaid-${reactId}-pending`;
    // На стриме ждём дольше — иначе mermaid.render на каждый кусок раздувает DOM.
    const delay = isStreaming ? 900 : 280;
    const timer = window.setTimeout(() => {
      renderId = `mermaid-${reactId}-${Date.now()}`;

      const run = async () => {
        if (!loadingRef.current) {
          loadingRef.current = true;
          setLoading(true);
        }
        setError(null);
        const el = hostRef.current;
        if (!el) return;
        el.innerHTML = '';

        const variants = [
          prepareMermaidSourceForRender(content),
          sanitizeMermaidSource(content),
          repairMermaidSource(content),
          stripMermaidStyling(content),
        ].filter((v, i, arr) => v && arr.indexOf(v) === i);

        if (!variants.length) {
          loadingRef.current = false;
          setLoading(false);
          return;
        }

        const primary = variants[0];
        if (!pieChartHasData(primary)) {
          setError(
            'Круговая диаграмма без данных: в коде есть `pie title …`, но нет строк вида `"Название" : 123`.\n' +
              'Попросите модель добавить сегменты или допишите их во вкладке «Код».',
          );
          loadingRef.current = false;
          setLoading(false);
          return;
        }

        try {
          const mermaid = await getMermaid();
          let lastErr: any = null;
          let svg: string | null = null;

          for (let i = 0; i < variants.length; i++) {
            const id = `${renderId}-${i}`;
            try {
              svg = await tryRender(mermaid, id, variants[i]);
              cleanupMermaidDomJunk(id);
              break;
            } catch (e) {
              lastErr = e;
              cleanupMermaidDomJunk(id);
            }
          }

          if (cancelled) return;

          if (svg) {
            el.innerHTML = svg;
            setError(null);
          } else {
            setError(
              'Не удалось отрисовать диаграмму Mermaid — синтаксис исходника невалиден.\n' +
                'Откройте вкладку «Код» и проверьте исходник.\n' +
                'Частые причины: style с CSS (text-align/font-size), style с кириллическими id, ' +
                'незакрытые кавычки, style у pie/xychart, ' +
                'x-axis [Янв, …] без кавычек (нужно ["Янв", …]).\n\n' +
                errorMessage(lastErr),
            );
          }
        } catch (e: any) {
          cleanupMermaidDomJunk(renderId);
          if (!cancelled) setError(errorMessage(e));
        } finally {
          cleanupMermaidDomJunk(renderId);
          if (!cancelled) {
            loadingRef.current = false;
            setLoading(false);
          }
        }
      };

      void run();
    }, delay);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      cleanupMermaidDomJunk(renderId);
      if (hostRef.current) hostRef.current.innerHTML = '';
    };
  }, [content, reactId, isStreaming]);

  useEffect(() => {
    cleanupMermaidDomJunk(`mermaid-${reactId}-boot`);
  }, [reactId]);

  return (
    <Box
      sx={{
        p: 2,
        height: '100%',
        minHeight: '100%',
        overflow: 'auto',
        position: 'relative',
        boxSizing: 'border-box',
        bgcolor: '#e8eaed',
        color: '#1f2937',
      }}
    >
      {error ? (
        <Typography
          variant="body2"
          sx={{
            whiteSpace: 'pre-wrap',
            color: '#991b1b',
            bgcolor: 'rgba(255,255,255,0.95)',
            borderRadius: 1.5,
            p: 1.5,
            boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
          }}
        >
          {error}
        </Typography>
      ) : null}
      <Box
        ref={hostRef}
        sx={{
          display: loading || error ? 'none' : 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          minHeight: 240,
          height: '100%',
          '& svg': { maxWidth: '100%', maxHeight: '100%', height: 'auto' },
        }}
      />
    </Box>
  );
}
