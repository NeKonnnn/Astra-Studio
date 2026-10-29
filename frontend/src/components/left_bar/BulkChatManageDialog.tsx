import React, { useEffect, useMemo, useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  FormControlLabel,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Typography,
} from '@mui/material';
import { useAppContext, useAppActions, chatIsListedInAllChatsSection, type Chat } from '../../contexts/AppContext';

const MAX_VISIBLE_CHATS = 200;

interface BulkChatManageDialogProps {
  open: boolean;
  onClose: () => void;
  isDarkMode: boolean;
  /** Чат, из меню которого открыли диалог — сразу в выборке. */
  initialSelectedChatId?: string | null;
}

function manageableChats(chats: Chat[]): Chat[] {
  return chats
    .filter((c) => !c.isArchived && chatIsListedInAllChatsSection(c))
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
    .slice(0, MAX_VISIBLE_CHATS);
}

export default function BulkChatManageDialog({
  open,
  onClose,
  isDarkMode,
  initialSelectedChatId = null,
}: BulkChatManageDialogProps) {
  const { state } = useAppContext();
  const { deleteChat, setCurrentChat } = useAppActions();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const chats = useMemo(() => manageableChats(state.chats), [state.chats]);

  useEffect(() => {
    if (!open) {
      setSelectedIds(new Set());
      setConfirmOpen(false);
      setIsDeleting(false);
      return;
    }
    const next = new Set<string>();
    if (initialSelectedChatId) next.add(initialSelectedChatId);
    setSelectedIds(next);
    // Сбрасываем выбор только при открытии, не при обновлении списка чатов.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialSelectedChatId]);

  const selectedVisibleCount = useMemo(() => {
    if (selectedIds.size === 0 || chats.length === 0) return 0;
    const visible = new Set(chats.map((c) => c.id));
    // Array.from: без downlevelIteration `for..of` по Set падает на craco build.
    return Array.from(selectedIds).reduce((n, id) => (visible.has(id) ? n + 1 : n), 0);
  }, [selectedIds, chats]);

  const allSelected = chats.length > 0 && selectedVisibleCount === chats.length;
  const someSelected = selectedVisibleCount > 0 && !allSelected;

  const toggleAll = () => {
    if (allSelected) {
      setSelectedIds(new Set());
      return;
    }
    setSelectedIds(new Set(chats.map((c) => c.id)));
  };

  const toggleOne = (chatId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(chatId)) next.delete(chatId);
      else next.add(chatId);
      return next;
    });
  };

  const handleDeleteConfirmed = () => {
    if (selectedVisibleCount === 0 || isDeleting) return;
    setIsDeleting(true);
    const visible = new Set(chats.map((c) => c.id));
    const ids = Array.from(selectedIds).filter((id) => visible.has(id));
    const currentWasDeleted = state.currentChatId ? ids.includes(state.currentChatId) : false;
    for (const id of ids) {
      deleteChat(id);
    }
    if (currentWasDeleted) {
      const remaining = state.chats.filter(
        (c) => !ids.includes(c.id) && !c.isArchived && chatIsListedInAllChatsSection(c),
      );
      setCurrentChat(remaining[0]?.id ?? null);
    }
    setIsDeleting(false);
    setConfirmOpen(false);
    onClose();
  };

  const fg = isDarkMode ? 'rgba(255,255,255,0.92)' : 'rgba(0,0,0,0.87)';
  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const rowHover = isDarkMode ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.04)';
  const rowSelected = isDarkMode ? 'rgba(255,255,255,0.1)' : 'rgba(25, 118, 210, 0.08)';
  const border = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)';
  const paperBg = isDarkMode ? '#2d2d2d' : '#ffffff';

  return (
    <>
      <Dialog
        open={open}
        onClose={onClose}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            backgroundColor: paperBg,
            backgroundImage: 'none',
            color: fg,
            borderRadius: 2,
            overflow: 'hidden',
            maxHeight: 'min(720px, 85vh)',
            display: 'flex',
            flexDirection: 'column',
          },
        }}
      >
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 2,
            px: 2.5,
            pt: 2,
            pb: 1.25,
            flexShrink: 0,
          }}
        >
          <Typography sx={{ fontWeight: 600, fontSize: '1.05rem' }}>Управление чатами</Typography>
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={allSelected}
                indeterminate={someSelected}
                onChange={toggleAll}
                disabled={chats.length === 0}
              />
            }
            label="Выбрать все"
            labelPlacement="start"
            sx={{
              m: 0,
              gap: 0.5,
              '& .MuiFormControlLabel-label': {
                fontSize: '0.875rem',
                color: muted,
                whiteSpace: 'nowrap',
              },
            }}
          />
        </Box>

        <DialogContent
          sx={{
            px: 1,
            py: 0,
            flex: 1,
            minHeight: 280,
            overflowY: 'auto',
          }}
        >
          {chats.length === 0 ? (
            <Typography sx={{ px: 2, py: 4, color: muted, textAlign: 'center' }}>
              Нет чатов для управления
            </Typography>
          ) : (
            <List dense disablePadding>
              {chats.map((chat) => {
                const checked = selectedIds.has(chat.id);
                return (
                  <ListItem key={chat.id} disablePadding>
                    <ListItemButton
                      onClick={() => toggleOne(chat.id)}
                      sx={{
                        py: 1,
                        px: 2,
                        borderRadius: 1,
                        mx: 0.5,
                        bgcolor: checked ? rowSelected : 'transparent',
                        '&:hover': { bgcolor: checked ? rowSelected : rowHover },
                      }}
                    >
                      <ListItemText
                        primary={chat.title || 'Без названия'}
                        primaryTypographyProps={{
                          noWrap: true,
                          sx: { fontSize: '0.9rem', color: fg, pr: 1 },
                        }}
                      />
                      <Checkbox
                        edge="end"
                        size="small"
                        checked={checked}
                        tabIndex={-1}
                        disableRipple
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => toggleOne(chat.id)}
                      />
                    </ListItemButton>
                  </ListItem>
                );
              })}
            </List>
          )}
        </DialogContent>

        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 1.5,
            px: 2.5,
            py: 1.75,
            borderTop: `1px solid ${border}`,
            flexShrink: 0,
          }}
        >
          <Button
            variant="outlined"
            onClick={onClose}
            disabled={isDeleting}
            sx={{
              textTransform: 'none',
              whiteSpace: 'nowrap',
              minWidth: 'auto',
              px: 2,
              borderColor: border,
              color: fg,
            }}
          >
            Отменить
          </Button>
          <Typography sx={{ color: muted, fontSize: '0.875rem', whiteSpace: 'nowrap' }}>
            Выбрано {selectedVisibleCount}/{chats.length}
          </Typography>
          <Button
            variant="contained"
            onClick={() => setConfirmOpen(true)}
            disabled={selectedVisibleCount === 0 || isDeleting}
            sx={{
              textTransform: 'none',
              whiteSpace: 'nowrap',
              minWidth: 'auto',
              px: 2,
              bgcolor: '#d32f2f',
              color: '#fff',
              '&:hover': { bgcolor: '#b71c1c' },
              '&.Mui-disabled': {
                bgcolor: isDarkMode ? 'rgba(211,47,47,0.35)' : 'rgba(211,47,47,0.3)',
                color: 'rgba(255,255,255,0.55)',
              },
            }}
          >
            Удалить
          </Button>
        </Box>
      </Dialog>

      <Dialog
        open={confirmOpen}
        onClose={() => !isDeleting && setConfirmOpen(false)}
        maxWidth="xs"
        fullWidth
        PaperProps={{
          sx: {
            backgroundColor: paperBg,
            backgroundImage: 'none',
            color: fg,
            borderRadius: 2,
          },
        }}
      >
        <Box sx={{ px: 2.5, pt: 2, pb: 1 }}>
          <Typography sx={{ fontWeight: 600, fontSize: '1.05rem', mb: 1 }}>
            Удалить чаты
          </Typography>
          <Typography sx={{ color: muted, fontSize: '0.9rem' }}>
            Будет удалено чатов: <strong style={{ color: fg }}>{selectedVisibleCount}</strong>. Это
            действие нельзя отменить.
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1, px: 2.5, pb: 2, pt: 1 }}>
          <Button
            onClick={() => setConfirmOpen(false)}
            disabled={isDeleting}
            sx={{ textTransform: 'none', color: fg }}
          >
            Отменить
          </Button>
          <Button
            variant="contained"
            onClick={handleDeleteConfirmed}
            disabled={isDeleting}
            sx={{
              textTransform: 'none',
              bgcolor: '#d32f2f',
              '&:hover': { bgcolor: '#b71c1c' },
            }}
          >
            Удалить
          </Button>
        </Box>
      </Dialog>
    </>
  );
}
