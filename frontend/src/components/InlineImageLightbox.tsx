import React, { useCallback } from 'react';
import { Box, IconButton, Modal } from '@mui/material';
import {
  Close as CloseIcon,
  FileDownloadOutlined as DownloadIcon,
  EditOutlined as EditIcon,
} from '@mui/icons-material';
import { getInlineAttachmentExtension } from '../utils/inlineAttachmentRules';
import { authFetch } from '../config/api';
import AuthenticatedInlineImage from './AuthenticatedInlineImage';

export interface InlineImageLightboxProps {
  open: boolean;
  src: string;
  name: string;
  onClose: () => void;
  onEdit?: () => void;
  showEdit?: boolean;
}

const pillBtnSx = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 0.75,
  px: 2,
  py: 0.95,
  border: 'none',
  borderRadius: '999px',
  bgcolor: 'rgba(30, 30, 30, 0.88)',
  backdropFilter: 'blur(10px)',
  color: 'rgba(255,255,255,0.92)',
  cursor: 'pointer',
  fontSize: '0.875rem',
  fontWeight: 500,
  lineHeight: 1.2,
  whiteSpace: 'nowrap' as const,
  boxShadow: '0 8px 32px rgba(0,0,0,0.45)',
  transition: 'background-color 0.15s',
  '&:hover': { bgcolor: 'rgba(50, 50, 50, 0.95)' },
};

export default function InlineImageLightbox({
  open,
  src,
  name,
  onClose,
  onEdit,
  showEdit = true,
}: InlineImageLightboxProps) {
  const ext = getInlineAttachmentExtension(name);

  const handleDownload = useCallback(async () => {
    const fileName = name || `image${ext || '.png'}`;
    try {
      const res =
        src.startsWith('data:') || src.startsWith('blob:')
          ? await fetch(src)
          : await authFetch(src);
      if (!res.ok) throw new Error(String(res.status));
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = fileName;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    } catch {
      const anchor = document.createElement('a');
      anchor.href = src;
      anchor.download = fileName;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    }
  }, [ext, name, src]);

  const editVisible = showEdit && Boolean(onEdit);

  return (
    <Modal
      open={open}
      onClose={onClose}
      sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: (theme) => theme.zIndex.modal + 2,
      }}
      slotProps={{
        backdrop: { sx: { bgcolor: 'rgba(0, 0, 0, 0.92)' } },
      }}
    >
      <Box
        sx={{
          position: 'relative',
          width: '100vw',
          height: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          outline: 'none',
          p: { xs: 1, sm: 2 },
        }}
        onClick={onClose}
      >
        <IconButton
          aria-label="Закрыть просмотр изображения"
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          sx={{
            position: 'fixed',
            top: { xs: 12, sm: 20 },
            right: { xs: 12, sm: 20 },
            zIndex: 2,
            color: 'rgba(255,255,255,0.95)',
            bgcolor: 'rgba(0,0,0,0.45)',
            width: 40,
            height: 40,
            '&:hover': { bgcolor: 'rgba(0,0,0,0.65)' },
          }}
        >
          <CloseIcon sx={{ fontSize: '1.25rem' }} />
        </IconButton>

        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            maxWidth: 'min(98vw, 1800px)',
            maxHeight: '100%',
            gap: 2,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <AuthenticatedInlineImage
            src={src}
            alt={name}
            sx={{
              display: 'block',
              maxWidth: 'min(98vw, 1800px)',
              maxHeight: 'calc(100vh - 120px)',
              width: 'auto',
              height: 'auto',
              objectFit: 'contain',
              borderRadius: '4px',
              boxShadow: '0 12px 48px rgba(0,0,0,0.55)',
            }}
          />

          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 1.25,
              flexWrap: 'wrap',
            }}
          >
            {editVisible ? (
              <Box
                component="button"
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onEdit?.();
                }}
                sx={pillBtnSx}
              >
                <EditIcon sx={{ fontSize: '1.05rem' }} />
                Редактировать
              </Box>
            ) : null}
            <Box
              component="button"
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                void handleDownload();
              }}
              sx={pillBtnSx}
            >
              <DownloadIcon sx={{ fontSize: '1.05rem' }} />
              Скачать
            </Box>
          </Box>
        </Box>
      </Box>
    </Modal>
  );
}
