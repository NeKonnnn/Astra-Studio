import React from 'react';
import { Box, Typography } from '@mui/material';
import { InlineAttachUploadSpinner } from '../InlineDocAttachmentChip';

export interface ChatTitleWithGeneratingProps {
  title: string;
  isGenerating: boolean;
  fontWeight?: number | string;
  color?: string;
}

/**
 * Название чата в сайдбаре + спиннер (как при загрузке файла), пока идёт генерация.
 */
export default function ChatTitleWithGenerating({
  title,
  isGenerating,
  fontWeight = 400,
  color = 'white',
}: ChatTitleWithGeneratingProps) {
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 0.75,
        minWidth: 0,
        width: '100%',
      }}
    >
      <Typography
        variant="body2"
        sx={{
          color,
          fontWeight,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          fontSize: '0.8rem',
          minWidth: 0,
          flex: 1,
        }}
      >
        {title}
      </Typography>
      {isGenerating ? (
        <Box
          component="span"
          aria-label="Идёт генерация"
          sx={{ display: 'inline-flex', flexShrink: 0, lineHeight: 0 }}
        >
          <InlineAttachUploadSpinner size={16} />
        </Box>
      ) : null}
    </Box>
  );
}