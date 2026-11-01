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
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  TextField,
  Typography,
} from '@mui/material';
import {
  ArchiveOutlined as ArchiveIcon,
  CreateNewFolderOutlined as AddFolderIcon,
  FolderOutlined as FolderIcon,
  KeyboardArrowRight as ArrowRightIcon,
  MoreHoriz as MoreIcon,
} from '@mui/icons-material';
import {
  useAppContext,
  useAppActions,
  chatIsListedInAllChatsSection,
  type Chat,
  type Project,
} from '../../contexts/AppContext';
import NewProjectModal, {
  type DraftProjectPayload,
  type ProjectData,
} from '../NewProjectModal';
import {
  getDropdownItemSx,
  getDropdownPanelSx,
  getProjectIconGlyphSx,
  MENU_ICON_FONT_SIZE_PX,
  MENU_ICON_MIN_WIDTH,
  MENU_ICON_TO_TEXT_GAP_PX,
} from '../../constants/menuStyles';
import { PROJECT_ICON_MAP, resolveProjectIconColor } from '../../constants/projectIcons';

const MAX_VISIBLE_CHATS = 200;
const MENU_Z = 1500;

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

function readUseFoldersMode(): boolean {
  const saved = localStorage.getItem('use_folders_mode');
  return saved !== null ? saved === 'true' : false;
}

export default function BulkChatManageDialog({
  open,
  onClose,
  isDarkMode,
  initialSelectedChatId = null,
}: BulkChatManageDialogProps) {
  const { state } = useAppContext();
  const {
    deleteChat,
    setCurrentChat,
    archiveChat,
    moveChatToFolder,
    moveChatToProject,
    createFolder,
    createProject,
    updateProject,
    deleteProject,
  } = useAppActions();

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [useFoldersMode, setUseFoldersMode] = useState(readUseFoldersMode);

  const [moreAnchor, setMoreAnchor] = useState<HTMLElement | null>(null);
  const [targetAnchor, setTargetAnchor] = useState<HTMLElement | null>(null);
  const [targetKind, setTargetKind] = useState<'folder' | 'project' | null>(null);

  const [createFolderOpen, setCreateFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  /** Ids, которые переносим после создания папки/проекта. */
  const [pendingMoveIds, setPendingMoveIds] = useState<string[]>([]);

  const chats = useMemo(() => manageableChats(state.chats), [state.chats]);
  const folders = state.folders;
  const projects = state.projects;

  useEffect(() => {
    if (!open) {
      setSelectedIds(new Set());
      setConfirmOpen(false);
      setIsBusy(false);
      setMoreAnchor(null);
      setTargetAnchor(null);
      setTargetKind(null);
      setCreateFolderOpen(false);
      setNewFolderName('');
      setNewProjectOpen(false);
      setPendingMoveIds([]);
      return;
    }
    setUseFoldersMode(readUseFoldersMode());
    const next = new Set<string>();
    if (initialSelectedChatId) next.add(initialSelectedChatId);
    setSelectedIds(next);
    // Сбрасываем выбор только при открытии, не при обновлении списка чатов.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialSelectedChatId]);

  useEffect(() => {
    const onSettings = () => setUseFoldersMode(readUseFoldersMode());
    window.addEventListener('interfaceSettingsChanged', onSettings);
    return () => window.removeEventListener('interfaceSettingsChanged', onSettings);
  }, []);

  const selectedVisibleCount = useMemo(() => {
    if (selectedIds.size === 0 || chats.length === 0) return 0;
    const visible = new Set(chats.map((c) => c.id));
    return Array.from(selectedIds).reduce((n, id) => (visible.has(id) ? n + 1 : n), 0);
  }, [selectedIds, chats]);

  const allSelected = chats.length > 0 && selectedVisibleCount === chats.length;
  const someSelected = selectedVisibleCount > 0 && !allSelected;

  const getSelectedVisibleIds = () => {
    const visible = new Set(chats.map((c) => c.id));
    return Array.from(selectedIds).filter((id) => visible.has(id));
  };

  const closeMenus = () => {
    setMoreAnchor(null);
    setTargetAnchor(null);
    setTargetKind(null);
  };

  const switchCurrentIfNeeded = (ids: string[]) => {
    if (!state.currentChatId || !ids.includes(state.currentChatId)) return;
    const remaining = state.chats.filter(
      (c) => !ids.includes(c.id) && !c.isArchived && chatIsListedInAllChatsSection(c),
    );
    setCurrentChat(remaining[0]?.id ?? null);
  };

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
    if (selectedVisibleCount === 0 || isBusy) return;
    setIsBusy(true);
    const ids = getSelectedVisibleIds();
    for (const id of ids) {
      deleteChat(id);
    }
    switchCurrentIfNeeded(ids);
    setIsBusy(false);
    setConfirmOpen(false);
    onClose();
  };

  const handleArchiveSelected = () => {
    if (selectedVisibleCount === 0 || isBusy) return;
    setIsBusy(true);
    const ids = getSelectedVisibleIds();
    for (const id of ids) {
      archiveChat(id);
    }
    switchCurrentIfNeeded(ids);
    setIsBusy(false);
    closeMenus();
    onClose();
  };

  const handleMoveToFolder = (folderId: string) => {
    if (selectedVisibleCount === 0 || isBusy) return;
    setIsBusy(true);
    const ids = getSelectedVisibleIds();
    for (const id of ids) {
      moveChatToFolder(id, folderId);
    }
    setIsBusy(false);
    closeMenus();
    onClose();
  };

  const handleMoveToProject = (projectId: string) => {
    if (selectedVisibleCount === 0 || isBusy) return;
    setIsBusy(true);
    const ids = getSelectedVisibleIds();
    for (const id of ids) {
      moveChatToProject(id, projectId);
    }
    setIsBusy(false);
    closeMenus();
    onClose();
  };

  const openCreateFolder = () => {
    const ids = getSelectedVisibleIds();
    if (ids.length === 0) return;
    setPendingMoveIds(ids);
    setNewFolderName('');
    closeMenus();
    setCreateFolderOpen(true);
  };

  const handleCreateFolderConfirm = () => {
    const name = newFolderName.trim();
    if (!name || pendingMoveIds.length === 0 || isBusy) return;
    setIsBusy(true);
    const folderId = createFolder(name);
    for (const id of pendingMoveIds) {
      moveChatToFolder(id, folderId);
    }
    setIsBusy(false);
    setCreateFolderOpen(false);
    setNewFolderName('');
    setPendingMoveIds([]);
    onClose();
  };

  const openCreateProject = () => {
    const ids = getSelectedVisibleIds();
    if (ids.length === 0) return;
    setPendingMoveIds(ids);
    closeMenus();
    setNewProjectOpen(true);
  };

  const movePendingToProject = (projectId: string) => {
    if (!projectId || pendingMoveIds.length === 0) return;
    for (const id of pendingMoveIds) {
      moveChatToProject(id, projectId);
    }
    setPendingMoveIds([]);
  };

  const handleCreateProject = (projectData: ProjectData) => {
    const projectId = createProject({
      name: projectData.name,
      icon: projectData.icon,
      iconType: projectData.iconType,
      iconColor: projectData.iconColor,
      memory: projectData.memory,
      instructions: projectData.instructions,
      knowledgeBasesEnabled: projectData.knowledgeBasesEnabled,
      knowledgeBaseIds: projectData.knowledgeBaseIds,
    });
    movePendingToProject(projectId);
    setNewProjectOpen(false);
    onClose();
    return projectId;
  };

  const ensureDraftProjectForRag = (draft: DraftProjectPayload) => {
    const projectId = createProject({
      name: draft.name,
      icon: draft.icon,
      iconType: draft.iconType,
      iconColor: draft.iconColor,
      memory: draft.memory,
      instructions: draft.instructions,
      knowledgeBasesEnabled: draft.knowledgeBasesEnabled,
      knowledgeBaseIds: draft.knowledgeBaseIds,
    });
    movePendingToProject(projectId);
    return projectId;
  };

  const finalizeDraftProject = (projectId: string, updates: DraftProjectPayload) => {
    updateProject(projectId, {
      name: updates.name,
      icon: updates.icon,
      iconType: updates.iconType,
      iconColor: updates.iconColor,
      memory: updates.memory,
      instructions: updates.instructions,
      knowledgeBasesEnabled: updates.knowledgeBasesEnabled,
      knowledgeBaseIds: updates.knowledgeBaseIds,
    });
    // Чаты уже перенесены на ensureDraft; если ensure не вызывали — переносим сейчас.
    if (pendingMoveIds.length > 0) {
      movePendingToProject(projectId);
    }
    setNewProjectOpen(false);
    onClose();
  };

  const renderProjectIcon = (project: Project) => {
    const iconColor = resolveProjectIconColor(project.iconColor, isDarkMode);
    const glyphSx = getProjectIconGlyphSx(11, iconColor);
    const iconWrapSx = {
      width: 20,
      height: 20,
      display: 'flex' as const,
      alignItems: 'center' as const,
      justifyContent: 'center' as const,
      color: iconColor,
    };
    if (project.iconType === 'emoji' && project.icon) {
      return (
        <Box
          sx={{
            width: 20,
            height: 20,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 12,
            lineHeight: 1,
          }}
        >
          {project.icon}
        </Box>
      );
    }
    if (project.iconType === 'icon' && project.icon) {
      const IconComponent = PROJECT_ICON_MAP[project.icon] || FolderIcon;
      return (
        <Box sx={iconWrapSx}>
          <IconComponent sx={{ ...glyphSx, color: 'currentColor' }} />
        </Box>
      );
    }
    return (
      <Box sx={iconWrapSx}>
        <FolderIcon sx={{ ...glyphSx, color: 'currentColor' }} />
      </Box>
    );
  };

  const fg = isDarkMode ? 'rgba(255,255,255,0.92)' : 'rgba(0,0,0,0.87)';
  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const rowHover = isDarkMode ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.04)';
  const rowSelected = isDarkMode ? 'rgba(255,255,255,0.1)' : 'rgba(25, 118, 210, 0.08)';
  const border = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)';
  const paperBg = isDarkMode ? '#2d2d2d' : '#ffffff';
  const menuItemColor = fg;
  const menuItemHover = isDarkMode ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.06)';
  const dropdownPanelSx = getDropdownPanelSx(isDarkMode);
  const dropdownItemSx = getDropdownItemSx(isDarkMode);
  const moreOpen = Boolean(moreAnchor);
  const targetOpen = Boolean(targetAnchor) && targetKind !== null;
  const canAct = selectedVisibleCount > 0 && !isBusy;

  const outlinedBtnSx = {
    textTransform: 'none' as const,
    whiteSpace: 'nowrap' as const,
    minWidth: 'auto',
    px: 2,
    borderColor: border,
    color: fg,
  };

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
          <Button variant="outlined" onClick={onClose} disabled={isBusy} sx={outlinedBtnSx}>
            Отменить
          </Button>
          <Typography sx={{ color: muted, fontSize: '0.875rem', whiteSpace: 'nowrap' }}>
            Выбрано {selectedVisibleCount}/{chats.length}
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Button
              variant="outlined"
              onClick={(e) => setMoreAnchor(e.currentTarget)}
              disabled={!canAct}
              endIcon={<MoreIcon sx={{ fontSize: 18 }} />}
              sx={outlinedBtnSx}
            >
              Еще
            </Button>
            <Button
              variant="contained"
              onClick={() => setConfirmOpen(true)}
              disabled={!canAct}
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
        </Box>
      </Dialog>

      <Menu
        anchorEl={moreAnchor}
        open={moreOpen}
        onClose={closeMenus}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        transformOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        slotProps={{ root: { sx: { zIndex: MENU_Z } } }}
        PaperProps={{
          sx: {
            ...dropdownPanelSx,
            minWidth: 200,
            zIndex: MENU_Z,
          },
        }}
        MenuListProps={{ dense: true }}
      >
        <MenuItem
          onClick={handleArchiveSelected}
          sx={{ ...dropdownItemSx, color: menuItemColor, '&:hover': { backgroundColor: menuItemHover } }}
        >
          <ListItemIcon
            sx={{
              color: menuItemColor,
              minWidth: `${MENU_ICON_MIN_WIDTH}px`,
              marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`,
              '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` },
            }}
          >
            <ArchiveIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText primary="В архив" />
        </MenuItem>

        {useFoldersMode ? (
          <MenuItem
            onClick={(e) => {
              setTargetKind('folder');
              setTargetAnchor(e.currentTarget);
            }}
            sx={{ ...dropdownItemSx, color: menuItemColor, '&:hover': { backgroundColor: menuItemHover } }}
          >
            <ListItemIcon
              sx={{
                color: menuItemColor,
                minWidth: `${MENU_ICON_MIN_WIDTH}px`,
                marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`,
                '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` },
              }}
            >
              <FolderIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText primary="В папку" />
            <ArrowRightIcon sx={{ fontSize: 18, color: muted, ml: 1 }} />
          </MenuItem>
        ) : (
          <MenuItem
            onClick={(e) => {
              setTargetKind('project');
              setTargetAnchor(e.currentTarget);
            }}
            sx={{ ...dropdownItemSx, color: menuItemColor, '&:hover': { backgroundColor: menuItemHover } }}
          >
            <ListItemIcon
              sx={{
                color: menuItemColor,
                minWidth: `${MENU_ICON_MIN_WIDTH}px`,
                marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`,
                '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` },
              }}
            >
              <FolderIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText primary="В проект" />
            <ArrowRightIcon sx={{ fontSize: 18, color: muted, ml: 1 }} />
          </MenuItem>
        )}
      </Menu>

      <Menu
        anchorEl={targetAnchor}
        open={targetOpen}
        onClose={() => {
          setTargetAnchor(null);
          setTargetKind(null);
        }}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        slotProps={{ root: { sx: { zIndex: MENU_Z + 1 } } }}
        PaperProps={{
          sx: {
            ...dropdownPanelSx,
            minWidth: 220,
            maxHeight: 360,
            zIndex: MENU_Z + 1,
          },
        }}
        MenuListProps={{ dense: true }}
      >
        {targetKind === 'folder' && (
          <>
            <MenuItem
              onClick={openCreateFolder}
              sx={{ ...dropdownItemSx, color: menuItemColor, '&:hover': { backgroundColor: menuItemHover } }}
            >
              <ListItemIcon
                sx={{
                  color: menuItemColor,
                  minWidth: `${MENU_ICON_MIN_WIDTH}px`,
                  marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`,
                  '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` },
                }}
              >
                <AddFolderIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText primary="Создать папку" />
            </MenuItem>
            {folders.length === 0 ? (
              <MenuItem disabled sx={{ ...dropdownItemSx, color: muted }}>
                <ListItemText primary="Нет папок" />
              </MenuItem>
            ) : (
              folders.map((folder) => (
                <MenuItem
                  key={folder.id}
                  onClick={() => handleMoveToFolder(folder.id)}
                  sx={{
                    ...dropdownItemSx,
                    color: menuItemColor,
                    '&:hover': { backgroundColor: menuItemHover },
                  }}
                >
                  <ListItemIcon
                    sx={{
                      color: menuItemColor,
                      minWidth: `${MENU_ICON_MIN_WIDTH}px`,
                      marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`,
                      '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` },
                    }}
                  >
                    <FolderIcon fontSize="small" />
                  </ListItemIcon>
                  <ListItemText primary={folder.name} />
                </MenuItem>
              ))
            )}
          </>
        )}

        {targetKind === 'project' && (
          <>
            <MenuItem
              onClick={openCreateProject}
              sx={{ ...dropdownItemSx, color: menuItemColor, '&:hover': { backgroundColor: menuItemHover } }}
            >
              <ListItemIcon
                sx={{
                  color: menuItemColor,
                  minWidth: `${MENU_ICON_MIN_WIDTH}px`,
                  marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`,
                  '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` },
                }}
              >
                <AddFolderIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText primary="Новый проект" />
            </MenuItem>
            {projects.length === 0 ? (
              <MenuItem disabled sx={{ ...dropdownItemSx, color: muted }}>
                <ListItemText primary="Нет проектов" />
              </MenuItem>
            ) : (
              projects.map((project) => (
                <MenuItem
                  key={project.id}
                  onClick={() => handleMoveToProject(project.id)}
                  sx={{
                    ...dropdownItemSx,
                    color: menuItemColor,
                    '&:hover': { backgroundColor: menuItemHover },
                  }}
                >
                  <ListItemIcon
                    sx={{
                      color: menuItemColor,
                      minWidth: `${MENU_ICON_MIN_WIDTH}px`,
                      marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`,
                      '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` },
                    }}
                  >
                    {renderProjectIcon(project)}
                  </ListItemIcon>
                  <ListItemText primary={project.name} />
                </MenuItem>
              ))
            )}
          </>
        )}
      </Menu>

      <Dialog
        open={confirmOpen}
        onClose={() => !isBusy && setConfirmOpen(false)}
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
            disabled={isBusy}
            sx={{ textTransform: 'none', color: fg }}
          >
            Отменить
          </Button>
          <Button
            variant="contained"
            onClick={handleDeleteConfirmed}
            disabled={isBusy}
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

      <Dialog
        open={createFolderOpen}
        onClose={() => {
          if (isBusy) return;
          setCreateFolderOpen(false);
          setPendingMoveIds([]);
          setNewFolderName('');
        }}
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
          <Typography sx={{ fontWeight: 600, fontSize: '1.05rem', mb: 1.5 }}>
            Создать папку
          </Typography>
          <TextField
            autoFocus
            fullWidth
            placeholder="Название папки"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreateFolderConfirm();
            }}
            sx={{
              '& .MuiOutlinedInput-root': {
                color: fg,
                '& fieldset': { borderColor: border },
                '&:hover fieldset': { borderColor: muted },
                '&.Mui-focused fieldset': { borderColor: fg },
              },
            }}
          />
          <Typography sx={{ color: muted, fontSize: '0.8rem', mt: 1 }}>
            Выбранные чаты ({pendingMoveIds.length}) будут перенесены в новую папку.
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1, px: 2.5, pb: 2, pt: 1 }}>
          <Button
            onClick={() => {
              setCreateFolderOpen(false);
              setPendingMoveIds([]);
              setNewFolderName('');
            }}
            disabled={isBusy}
            sx={{ textTransform: 'none', color: fg }}
          >
            Отменить
          </Button>
          <Button
            variant="contained"
            onClick={handleCreateFolderConfirm}
            disabled={!newFolderName.trim() || isBusy}
            sx={{ textTransform: 'none' }}
          >
            Создать
          </Button>
        </Box>
      </Dialog>

      <NewProjectModal
        open={newProjectOpen}
        onClose={() => {
          setNewProjectOpen(false);
          setPendingMoveIds([]);
        }}
        onCreateProject={handleCreateProject}
        ensureDraftProjectForRag={ensureDraftProjectForRag}
        finalizeDraftProject={finalizeDraftProject}
        cancelDraftProject={(projectId) => {
          deleteProject(projectId);
          setPendingMoveIds([]);
        }}
      />
    </>
  );
}
