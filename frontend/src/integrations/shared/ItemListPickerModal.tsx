import React, { useEffect, useState } from 'react';
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
  ListItemText,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import type { ListItem } from '../types';

export interface ItemListPickerModalProps {
  open: boolean;
  title: string;
  items: ListItem[];
  loading?: boolean;
  error?: string | null;
  onClose: () => void;
  onRefresh?: () => void;
  onPick: (item: ListItem) => void;
  confirmLabel?: string;
}

export default function ItemListPickerModal({
  open,
  title,
  items,
  loading,
  error,
  onClose,
  onRefresh,
  onPick,
  confirmLabel = 'Прикрепить',
}: ItemListPickerModalProps) {
  const [selected, setSelected] = useState<ListItem | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) {
      setSelected(null);
      setBusy(false);
    }
  }, [open]);

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, pr: 1 }}>
        <Typography variant="h6" sx={{ flex: 1 }}>
          {title}
        </Typography>
        <IconButton size="small" onClick={onClose} disabled={busy}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers sx={{ minHeight: 280, p: 0 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
            <CircularProgress size={28} />
          </Box>
        ) : error ? (
          <Box sx={{ p: 2 }}>
            <Typography color="error" variant="body2">
              {error}
            </Typography>
            {onRefresh && (
              <Button size="small" onClick={onRefresh} sx={{ mt: 1 }}>
                Повторить
              </Button>
            )}
          </Box>
        ) : (
          <List dense>
            {items.length === 0 && (
              <Box sx={{ p: 2 }}>
                <Typography variant="body2" color="text.secondary">
                  Пусто
                </Typography>
              </Box>
            )}
            {items.map((item) => (
              <ListItemButton
                key={item.id}
                selected={selected?.id === item.id}
                onClick={() => setSelected(item)}
                onDoubleClick={() => {
                  setSelected(item);
                  setBusy(true);
                  onPick(item);
                }}
              >
                <ListItemText
                  primary={item.title}
                  secondary={item.subtitle}
                  primaryTypographyProps={{ noWrap: true }}
                  secondaryTypographyProps={{ noWrap: true }}
                />
              </ListItemButton>
            ))}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Отмена
        </Button>
        <Button
          variant="contained"
          disabled={!selected || busy || loading}
          startIcon={busy ? <CircularProgress size={14} color="inherit" /> : undefined}
          onClick={() => {
            if (!selected) return;
            setBusy(true);
            onPick(selected);
          }}
        >
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
