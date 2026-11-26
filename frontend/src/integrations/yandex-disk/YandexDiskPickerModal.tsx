import React, { useCallback, useEffect, useState } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
} from '@mui/material';
import FolderIcon from '@mui/icons-material/Folder';
import InsertDriveFileOutlinedIcon from '@mui/icons-material/InsertDriveFileOutlined';
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import CloseIcon from '@mui/icons-material/Close';
import { downloadYandexDiskFile, listYandexDiskResources, parentDiskPath } from './api';
import type { YandexDiskPickedFile, YandexDiskResource } from './types';
import { formatFileSize } from '../../utils/inlineImage';

export interface YandexDiskPickerModalProps {
  open: boolean;
  accessToken: string | null;
  onClose: () => void;
  onPicked: (file: YandexDiskPickedFile) => void;
  onError?: (message: string) => void;
}

export default function YandexDiskPickerModal({
  open,
  accessToken,
  onClose,
  onPicked,
  onError,
}: YandexDiskPickerModalProps) {
  const [path, setPath] = useState('disk:/');
  const [items, setItems] = useState<YandexDiskResource[]>([]);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [selected, setSelected] = useState<YandexDiskResource | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (nextPath: string) => {
      if (!accessToken) return;
      setLoading(true);
      setError(null);
      setSelected(null);
      try {
        const result = await listYandexDiskResources(accessToken, nextPath);
        setPath(result.path || nextPath);
        setItems(result.items);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Не удалось загрузить содержимое Яндекс.Диска';
        setError(message);
        onError?.(message);
      } finally {
        setLoading(false);
      }
    },
    [accessToken, onError],
  );

  useEffect(() => {
    if (open && accessToken) {
      void load('disk:/');
    }
    if (!open) {
      setPath('disk:/');
      setItems([]);
      setSelected(null);
      setError(null);
      setDownloading(false);
    }
  }, [open, accessToken, load]);

  const parent = parentDiskPath(path);

  const handleOpenItem = (item: YandexDiskResource) => {
    if (item.type === 'dir') {
      void load(item.path);
      return;
    }
    setSelected(item);
  };

  const handleConfirm = async () => {
    if (!selected || !accessToken || selected.type === 'dir') return;
    setDownloading(true);
    setError(null);
    try {
      const file = await downloadYandexDiskFile(
        accessToken,
        selected.path,
        selected.name,
      );
      onPicked(file);
      onClose();
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Не удалось скачать файл с Яндекс.Диска';
      setError(message);
      onError?.(message);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Dialog open={open} onClose={downloading ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, pr: 1 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h6" component="div">
            Яндекс.Диск
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap title={path}>
            {path}
          </Typography>
        </Box>
        <IconButton
          aria-label="Закрыть"
          onClick={onClose}
          disabled={downloading}
          size="small"
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers sx={{ minHeight: 320, p: 0 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', px: 1, py: 0.5, gap: 0.5 }}>
          <IconButton
            size="small"
            disabled={!parent || loading || downloading}
            onClick={() => parent && void load(parent)}
            aria-label="На уровень вверх"
          >
            <ArrowUpwardIcon fontSize="small" />
          </IconButton>
          <Typography variant="body2" color="text.secondary">
            Выберите файл для прикрепления к сообщению
          </Typography>
        </Box>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
            <CircularProgress size={32} />
          </Box>
        ) : error ? (
          <Box sx={{ px: 2, py: 3 }}>
            <Typography color="error" variant="body2">
              {error}
            </Typography>
            <Button size="small" onClick={() => void load(path)} sx={{ mt: 1 }}>
              Повторить
            </Button>
          </Box>
        ) : (
          <List dense disablePadding>
            {items.length === 0 && (
              <Box sx={{ px: 2, py: 3 }}>
                <Typography variant="body2" color="text.secondary">
                  Папка пуста
                </Typography>
              </Box>
            )}
            {items.map((item) => {
              const isDir = item.type === 'dir';
              const isSelected = selected?.path === item.path;
              return (
                <ListItemButton
                  key={item.path}
                  selected={isSelected}
                  disabled={downloading}
                  onClick={() => handleOpenItem(item)}
                  onDoubleClick={() => {
                    if (isDir) void load(item.path);
                    else {
                      setSelected(item);
                      void (async () => {
                        setSelected(item);
                      })();
                    }
                  }}
                >
                  <ListItemIcon sx={{ minWidth: 36 }}>
                    {isDir ? (
                      <FolderIcon fontSize="small" color="primary" />
                    ) : (
                      <InsertDriveFileOutlinedIcon fontSize="small" />
                    )}
                  </ListItemIcon>
                  <ListItemText
                    primary={item.name}
                    secondary={
                      isDir
                        ? 'Папка'
                        : item.size != null
                          ? formatFileSize(item.size)
                          : item.mime_type || 'Файл'
                    }
                    primaryTypographyProps={{ noWrap: true }}
                  />
                </ListItemButton>
              );
            })}
          </List>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 2, py: 1.5 }}>
        <Button onClick={onClose} disabled={downloading}>
          Отмена
        </Button>
        <Button
          variant="contained"
          onClick={() => void handleConfirm()}
          disabled={!selected || selected.type === 'dir' || downloading || loading}
          startIcon={downloading ? <CircularProgress size={16} color="inherit" /> : undefined}
        >
          {downloading ? 'Скачивание…' : 'Прикрепить'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
