import React, { useState, useRef, useEffect, useMemo } from 'react';
import { useTheme } from '@mui/material/styles';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Alert,
  Divider,
  List,
  ListItem,
  ListItemText,
  Popover,
  Switch,
} from '@mui/material';
import {
  Delete as DeleteIcon,
  Warning as WarningIcon,
  Upload as UploadIcon,
  Download as DownloadIcon,
  Archive as ArchiveIcon,
  Link as LinkIcon,
  ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import {
  getDropdownPopoverPaperSx,
  getDropdownItemSx,
  getDropdownTriggerButtonSx,
  getDropdownTriggerTextSx,
  getDropdownChevronSx,
  getDropdownItemStateSx,
} from '../../constants/menuStyles';
import { useAppContext, useAppActions } from '../../contexts/AppContext';
import ManageSharesDialog from '../ManageSharesDialog';
import {
  getChatFontSize,
  setChatFontSize,
  type ChatFontSize,
} from '../../utils/chatFontSize';
import {
  loadFollowUpSettings,
  saveFollowUpAutoGenerate,
} from '../../chat/followUpSettings';
import {
  isKeepChatSettingsEnabled,
  setKeepChatSettingsEnabled,
} from '../../utils/newChatSettings';

interface ChatSettingsProps {
  isDarkMode?: boolean;
}

export default function ChatSettings({ isDarkMode: isDarkModeProp }: ChatSettingsProps = {}) {
  const theme = useTheme();
  const isDarkMode = isDarkModeProp ?? theme.palette.mode === 'dark';
  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const dropdownTriggerSx = useMemo(() => getDropdownTriggerButtonSx(isDarkMode), [isDarkMode]);
  const dropdownTriggerTextSx = useMemo(() => getDropdownTriggerTextSx(isDarkMode), [isDarkMode]);
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(isDarkMode), [isDarkMode]);
  const { state } = useAppContext();
  const { deleteAllChats, exportChats, importChats, archiveAllChats, showNotification } = useAppActions();
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [showArchiveDialog, setShowArchiveDialog] = useState(false);
  const [showManageSharesDialog, setShowManageSharesDialog] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [fontSize, setFontSize] = useState<ChatFontSize>(() => getChatFontSize());
  const [fontPopoverAnchor, setFontPopoverAnchor] = useState<HTMLElement | null>(null);
  const [chatSuggestionsEnabled, setChatSuggestionsEnabled] = useState(
    () => loadFollowUpSettings().followUpAutoGenerate,
  );

  useEffect(() => {
    const syncSuggestionsSetting = () => {
      setChatSuggestionsEnabled(loadFollowUpSettings().followUpAutoGenerate);
    };
    syncSuggestionsSetting();
    window.addEventListener('interfaceSettingsChanged', syncSuggestionsSetting);
    return () => window.removeEventListener('interfaceSettingsChanged', syncSuggestionsSetting);
  }, []);

  const [keepChatSettings, setKeepChatSettings] = useState(() => isKeepChatSettingsEnabled());

  const handleKeepChatSettingsToggle = (enabled: boolean) => {
    setKeepChatSettings(enabled);
    setKeepChatSettingsEnabled(enabled);
    showNotification(
      'success',
      enabled ? 'Новый чат получит настройки текущего' : 'Новый чат будет открываться чистым',
    );
  };

  const handleChatSuggestionsToggle = (enabled: boolean) => {
    setChatSuggestionsEnabled(enabled);
    saveFollowUpAutoGenerate(enabled);
    window.dispatchEvent(new Event('interfaceSettingsChanged'));
    showNotification('success', enabled ? 'Подсказки в чате включены' : 'Подсказки в чате отключены');
  };

  useEffect(() => {
    setFontSize(getChatFontSize());
  }, []);

  // Сохраняем размер шрифта (custom event → MessageRenderer без polling)
  const handleFontSizeChange = (event: any) => {
    const newFontSize = event.target.value as ChatFontSize;
    setFontSize(newFontSize);
    setChatFontSize(newFontSize);
    showNotification('success', 'Размер шрифта изменен');
  };

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const getFontSizeLabel = (size: ChatFontSize): string => {
    switch (size) {
      case 'small':
        return 'Мелкий';
      case 'large':
        return 'Большой';
      default:
        return 'Средний';
    }
  };

  const totalChats = state.chats.length;
  const totalFolders = state.folders.length;

  const handleDeleteAllChats = () => {
    deleteAllChats();
    setShowDeleteDialog(false);
    showNotification('success', 'Все чаты успешно удалены');
  };

  const handleExportChats = () => {
    try {
      exportChats();
      showNotification('success', 'Чаты успешно экспортированы');
    } catch (error) {
      showNotification('error', 'Ошибка при экспорте чатов');
    }
  };

  const handleImportChats = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith('.json')) {
      showNotification('error', 'Файл должен быть в формате JSON');
      return;
    }

    try {
      await importChats(file);
      showNotification('success', 'Чаты успешно импортированы');
      // Сбрасываем input для возможности повторного выбора того же файла
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    } catch (error: any) {
      showNotification('error', error.message || 'Ошибка при импорте чатов');
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleArchiveAllChats = () => {
    if (totalChats === 0) {
      showNotification('info', 'Нет чатов для архивирования');
      return;
    }
    archiveAllChats();
    setShowArchiveDialog(false);
    showNotification('success', 'Все чаты успешно архивированы');
  };

  const handleImportClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <Box sx={{ p: 3 }}>
      <Card>
        <CardContent>
          <List sx={{ p: 0 }}>
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Подсказки в чате"
                secondary="Динамические подсказки под ответом модели и «Предложено» в новом чате"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
                secondaryTypographyProps={{
                  variant: 'body2',
                  sx: { mt: 0.5 },
                }}
              />
              <Switch
                checked={chatSuggestionsEnabled}
                onChange={(e) => handleChatSuggestionsToggle(e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Что новый чат берёт из текущего */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Переносить настройки в новый чат"
                secondary="Агент, Skills, инструменты MCP, артефакты, общая библиотека, Coding и генерация из текущего чата включатся и в новом. Если выключено, новый чат открывается чистым"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
                secondaryTypographyProps={{
                  variant: 'body2',
                  sx: { mt: 0.5 },
                }}
              />
              <Switch
                checked={keepChatSettings}
                onChange={(e) => handleKeepChatSettingsToggle(e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Размер шрифта */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Размер шрифта"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Box sx={{ minWidth: 180 }}>
                <Box onClick={(e) => setFontPopoverAnchor(e.currentTarget)} sx={dropdownTriggerSx}>
                  <Typography sx={dropdownTriggerTextSx}>
                    {getFontSizeLabel(fontSize)}
                  </Typography>
                  <ExpandMoreIcon sx={{ ...dropdownChevronSx, transform: fontPopoverAnchor ? 'rotate(180deg)' : 'none' }} />
                </Box>
                <Popover
                  open={Boolean(fontPopoverAnchor)}
                  anchorEl={fontPopoverAnchor}
                  onClose={() => setFontPopoverAnchor(null)}
                  anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                  transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                  slotProps={{ paper: { sx: getDropdownPopoverPaperSx(fontPopoverAnchor, isDarkMode) } }}
                >
                  <Box sx={{ py: 0.5 }}>
                    {(['small', 'medium', 'large'] as const).map((size) => (
                      <Box
                        key={size}
                        onClick={() => {
                          setFontSize(size);
                          setChatFontSize(size);
                          showNotification('success', 'Размер шрифта изменен');
                          setFontPopoverAnchor(null);
                        }}
                        sx={{
                          ...dropdownItemSx,
                          ...getDropdownItemStateSx(isDarkMode, fontSize === size),
                        }}
                      >
                        {getFontSizeLabel(size)}
                      </Box>
                    ))}
                  </Box>
                </Popover>
              </Box>
            </ListItem>

            <Divider />

            {/* Общие ссылки */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Общие ссылки"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Button
                variant="outlined"
                startIcon={<LinkIcon />}
                onClick={() => setShowManageSharesDialog(true)}
                sx={{
                  textTransform: 'none',
                  minWidth: 180,
                }}
              >
                Управление
              </Button>
            </ListItem>

            <Divider />

            {/* Импорт чатов */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Импортировать чаты"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Button
                variant="outlined"
                startIcon={<UploadIcon />}
                onClick={handleImportClick}
                sx={{
                  textTransform: 'none',
                  minWidth: 180,
                }}
              >
                Импортировать чаты
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".json"
                onChange={handleImportChats}
                style={{ display: 'none' }}
              />
            </ListItem>

            <Divider />

            {/* Экспорт чатов */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Экспортировать чаты"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Button
                variant="outlined"
                startIcon={<DownloadIcon />}
                onClick={handleExportChats}
                disabled={totalChats === 0}
                sx={{
                  textTransform: 'none',
                  minWidth: 180,
                }}
              >
                Экспортировать чаты
              </Button>
            </ListItem>

            <Divider />

            {/* Архивирование чатов */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Архивировать все чаты"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Button
                variant="outlined"
                startIcon={<ArchiveIcon />}
                onClick={() => setShowArchiveDialog(true)}
                disabled={totalChats === 0}
                sx={{
                  textTransform: 'none',
                  minWidth: 180,
                }}
              >
                Архивировать все чаты
              </Button>
            </ListItem>

            <Divider />

            {/* Удаление всех чатов */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Удалить все чаты"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                  color: 'error.main',
                }}
              />
              <Button
                variant="outlined"
                color="error"
                startIcon={<DeleteIcon />}
                onClick={() => setShowDeleteDialog(true)}
                disabled={totalChats === 0}
                sx={{
                  textTransform: 'none',
                  minWidth: 180,
                  borderColor: 'error.main',
                  color: 'error.main',
                  '&:hover': {
                    borderColor: 'error.dark',
                    backgroundColor: 'error.light',
                    color: 'error.dark',
                  },
                }}
              >
                Удалить все чаты
              </Button>
            </ListItem>
          </List>
        </CardContent>
      </Card>

      {/* Диалог подтверждения архивирования */}
      <Dialog
        open={showArchiveDialog}
        onClose={() => setShowArchiveDialog(false)}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            backgroundColor: 'background.paper',
          }
        }}
      >
        <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <ArchiveIcon color="primary" />
          Архивирование чатов
        </DialogTitle>
        <DialogContent>
          <Alert severity="info" sx={{ mb: 2 }}>
            <Typography variant="body2">
              Все чаты будут архивированы. Вы сможете найти их в меню "Архив".
            </Typography>
          </Alert>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Будет архивировано:
          </Typography>
          <Box component="ul" sx={{ pl: 3, mb: 2 }}>
            <Typography component="li" variant="body2" color="text.secondary">
              Чатов: {totalChats}
            </Typography>
            <Typography component="li" variant="body2" color="text.secondary">
              Сообщений: {state.stats.totalMessages}
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions sx={{ p: 2, gap: 1 }}>
          <Button
            onClick={() => setShowArchiveDialog(false)}
            variant="outlined"
            sx={{
              textTransform: 'none',
              px: 3,
            }}
          >
            Отменить
          </Button>
          <Button
            onClick={handleArchiveAllChats}
            variant="contained"
            startIcon={<ArchiveIcon />}
            sx={{
              textTransform: 'none',
              px: 3,
            }}
          >
            Архивировать
          </Button>
        </DialogActions>
      </Dialog>

      {/* Диалог подтверждения удаления */}
      <Dialog
        open={showDeleteDialog}
        onClose={() => setShowDeleteDialog(false)}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            backgroundColor: 'background.paper',
          }
        }}
      >
        <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <WarningIcon color="error" />
          Подтверждение удаления
        </DialogTitle>
        <DialogContent>
          <Alert severity="error" sx={{ mb: 2 }}>
            <Typography variant="body2" fontWeight="bold">
              Вы уверены, что хотите удалить все чаты?
            </Typography>
          </Alert>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Это действие удалит:
          </Typography>
          <Box component="ul" sx={{ pl: 3, mb: 2 }}>
            <Typography component="li" variant="body2" color="text.secondary">
              Все чаты ({totalChats})
            </Typography>
            <Typography component="li" variant="body2" color="text.secondary">
              Все сообщения ({state.stats.totalMessages})
            </Typography>
            <Typography component="li" variant="body2" color="text.secondary">
              Все папки ({totalFolders})
            </Typography>
          </Box>
          <Alert severity="warning">
            <Typography variant="body2">
              <strong>Это действие нельзя отменить!</strong> Все данные будут безвозвратно удалены.
            </Typography>
          </Alert>
        </DialogContent>
        <DialogActions sx={{ p: 2, gap: 1 }}>
          <Button
            onClick={() => setShowDeleteDialog(false)}
            variant="outlined"
            sx={{
              textTransform: 'none',
              px: 3,
            }}
          >
            Отменить
          </Button>
          <Button
            onClick={handleDeleteAllChats}
            variant="contained"
            color="error"
            startIcon={<DeleteIcon />}
            sx={{
              textTransform: 'none',
              px: 3,
            }}
          >
            Удалить все чаты
          </Button>
        </DialogActions>
      </Dialog>

      {/* Диалог управления общими ссылками */}
      <ManageSharesDialog
        open={showManageSharesDialog}
        onClose={() => setShowManageSharesDialog(false)}
        isDarkMode={isDarkMode}
      />
    </Box>
  );
}
