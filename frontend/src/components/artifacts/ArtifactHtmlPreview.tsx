import React, { useMemo } from 'react';
import { Box } from '@mui/material';
import {
  isGpbPresentationHtml,
} from '../../utils/presentationViewer';
import {
  escapeHtmlForSrcDoc,
  rewriteHtmlArtifactScriptsForOffline,
} from '../../utils/htmlArtifactScripts';
import { useCommittedContent } from '../../hooks/useCommittedContent';
import InlinePresentationViewer from '../InlinePresentationViewer';

function buildGenericHtmlSrcDoc(rawHtml: string): string {
  const rewritten = rewriteHtmlArtifactScriptsForOffline(rawHtml || '');
  const html = escapeHtmlForSrcDoc(rewritten);
  const looksComplete =
    /<!doctype/i.test(html) || /<html[\s>]/i.test(html);
  if (looksComplete) {
    return html;
  }
  return `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    html, body { margin: 0; padding: 12px; font-family: system-ui, sans-serif; }
  </style>
</head>
<body>
${html}
</body>
</html>`;
}

interface Props {
  content: string;
  isStreaming?: boolean;
}

export default function ArtifactHtmlPreview({ content, isStreaming = false }: Props) {
  const isPresentation = isGpbPresentationHtml(content);
  const committed = useCommittedContent(content || '', isStreaming, isStreaming ? 1200 : 500);
  const srcDoc = useMemo(
    () => (isPresentation || isStreaming ? '' : buildGenericHtmlSrcDoc(committed)),
    [committed, isStreaming, isPresentation],
  );

  if (isPresentation) {
    return (
      <Box sx={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
        <InlinePresentationViewer html={content} isStreaming={isStreaming} embedded />
      </Box>
    );
  }

  // На стриме iframe не монтируем (reload srcdoc → Virtuoso #185); статус — только в шапке карточки.
  if (isStreaming) {
    return <Box sx={{ width: '100%', height: '100%', minHeight: 240, bgcolor: '#e8eaed' }} />;
  }

  return (
    <Box
      component="iframe"
      title="HTML artifact preview"
      sandbox="allow-scripts allow-same-origin allow-forms"
      srcDoc={srcDoc}
      sx={{
        width: '100%',
        height: '100%',
        minHeight: 320,
        border: 0,
        borderRadius: 0,
        bgcolor: '#fff',
        display: 'block',
      }}
    />
  );
}
