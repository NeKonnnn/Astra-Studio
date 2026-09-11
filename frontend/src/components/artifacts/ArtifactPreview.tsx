import React from 'react';
import { Box, Typography } from '@mui/material';
import type { ChatArtifact } from '../../types/artifacts';
import {
  isHtmlArtifactType,
  isMarkdownArtifactType,
  isMermaidArtifactType,
  isReactArtifactType,
  isSvgArtifactType,
} from '../../utils/artifacts';
import ArtifactHtmlPreview from './ArtifactHtmlPreview';
import ArtifactMarkdownPreview from './ArtifactMarkdownPreview';
import ArtifactMermaidPreview from './ArtifactMermaidPreview';
import ArtifactSvgPreview from './ArtifactSvgPreview';
import ArtifactReactPreview from './ArtifactReactPreview';

interface Props {
  artifact: ChatArtifact;
  isStreaming?: boolean;
}

export default function ArtifactPreview({ artifact, isStreaming = false }: Props) {
  const { type, content, closed } = artifact;

  // Пустой стрим: статус только в шапке ArtifactCard.
  if (!closed && isStreaming && !(content || '').trim()) {
    return <Box sx={{ height: '100%', minHeight: 240, bgcolor: '#e8eaed' }} />;
  }

  // isStreaming приходит от ArtifactCard (= стрим сообщения), не смешиваем с artifact.closed.
  const streaming = Boolean(isStreaming);

  if (isHtmlArtifactType(type)) {
    return <ArtifactHtmlPreview content={content} isStreaming={streaming} />;
  }
  if (isSvgArtifactType(type)) {
    return <ArtifactSvgPreview content={content} isStreaming={streaming} />;
  }
  if (isMarkdownArtifactType(type)) {
    return <ArtifactMarkdownPreview content={content} />;
  }
  if (isMermaidArtifactType(type)) {
    return <ArtifactMermaidPreview content={content} isStreaming={streaming} />;
  }
  if (isReactArtifactType(type)) {
    return <ArtifactReactPreview content={content} isStreaming={streaming} />;
  }

  return (
    <Box sx={{ p: 2, color: '#1f2937' }}>
      <Typography variant="body2" sx={{ mb: 1, color: '#4b5563' }}>
        Предпросмотр для типа «{type}» недоступен. Смотрите вкладку «Код».
      </Typography>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 1.5,
          borderRadius: 1,
          bgcolor: 'rgba(255,255,255,0.85)',
          overflow: 'auto',
          fontSize: '0.8rem',
          whiteSpace: 'pre-wrap',
          color: '#1f2937',
        }}
      >
        {content}
      </Box>
    </Box>
  );
}
