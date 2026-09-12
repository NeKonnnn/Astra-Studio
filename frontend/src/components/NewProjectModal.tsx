import React, { useState, useRef, useEffect } from 'react';
import ProjectRagLibraryInline from './ProjectRagLibraryInline';
import RAGSettings from './settings/RAGSettings';
import { saveEntityRagSettings, type EntityRagDraft } from '../utils/entityRagSettings';
import { useRagReindexStatus } from '../contexts/RagReindexStatusContext';
import { syncProjectCreate } from '../utils/projectsApi';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Box,
  TextField,
  Button,
  IconButton,
  Typography,
  Collapse,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Avatar,
  Tabs,
  Tab,
  Paper,
  Tooltip,
  useTheme,
} from '@mui/material';
import {
  Close as CloseIcon,
  Add as AddIcon,
  Info as InfoIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
} from '@mui/icons-material';
import {
  PROJECT_DEFAULT_ICON as FolderIcon,
  PROJECT_ICON_OPTIONS as iconOptions,
} from '../constants/projectIcons';

export interface DraftProjectPayload {
  name: string;
  memory: 'default' | 'project-only';
  instructions: string;
  icon?: string;
  iconType?: 'icon' | 'emoji';
  iconColor?: string;
}

interface NewProjectModalProps {
  open: boolean;
  onClose: () => void;
  /** Обычное создание без предварительного черновика (файлы не загружались) */
  onCreateProject?: (projectData: ProjectData) => string | void;
  /** Создать проект в состоянии при первой загрузке файла — вернуть id */
  ensureDraftProjectForRag?: (draft: DraftProjectPayload) => string;
  /** Обновить черновик при нажатии «Создать проект» */
  finalizeDraftProject?: (projectId: string, updates: DraftProjectPayload) => void;
  /** Закрытие без подтверждения — удалить черновик и данные на сервере */
  cancelDraftProject?: (projectId: string) => void;
}

export interface ProjectData {
  name: string;
  icon?: string;
  iconType?: 'icon' | 'emoji';
  iconColor?: string;
  category?: string;
  memory: 'default' | 'project-only';
  instructions: string;
}

const colorOptions = [
  { name: 'white', value: '#ffffff' },
  { name: 'red', value: '#f44336' },
  { name: 'orange', value: '#ff9800' },
  { name: 'green', value: '#4caf50' },
  { name: 'blue', value: '#2196f3' },
  { name: 'purple', value: '#9c27b0' },
  { name: 'dark-purple', value: '#673ab7' },
];

const emojiOptions = [
  '📁', '💰', '📝', '❤️', '✈️', '🎯', '🚀', '💡', '📊', '🎨', '🏠', '🎓', '💼', '🏥', '🍕', '☕',
  '💻', '🌱', '🐱', '🐶', '🚗', '📚', '☂️', '📅', '🖥️', '🔊', '📈', '✉️', '🎮', '🎬', '🎵', '🎤',
  '🏀', '⚽', '🎾', '🏊', '🚴', '🎸', '🎹', '🎺', '🎻', '🎲', '🃏', '🎴', '🖼️', '🎭', '🎪', '🎡',
  '🌍', '🌎', '🌏', '🗺️', '🏔️', '⛰️', '🌋', '🏕️', '🏖️', '🏝️', '🏜️', '🌅', '🌄', '🌆', '🌇', '🌃',
];

export default function NewProjectModal({
  open,
  onClose,
  onCreateProject,
  ensureDraftProjectForRag,
  finalizeDraftProject,
  cancelDraftProject,
}: NewProjectModalProps) {
  const theme = useTheme();
  const { notifyReindexStarted } = useRagReindexStatus();
  const [projectName, setProjectName] = useState('');
  const [selectedIcon, setSelectedIcon] = useState<string | null>(null);
  const [selectedEmoji, setSelectedEmoji] = useState<string | null>(null);
  const [iconType, setIconType] = useState<'icon' | 'emoji'>('icon');
  const [selectedColor, setSelectedColor] = useState('#ffffff');
  const [memory, setMemory] = useState<'default' | 'project-only'>('default');
  const [instructions, setInstructions] = useState('');
  /** Черновик настроек РАГ: уезжает в БД вместе с созданием проекта. */
  const [ragDraft, setRagDraft] = useState<EntityRagDraft | null>(null);
  const [showIconPicker, setShowIconPicker] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showRagSettingsPanel, setShowRagSettingsPanel] = useState(false);
  const [iconTab, setIconTab] = useState(0);
  const [ragDraftProjectId, setRagDraftProjectId] = useState<string | null>(null);
  const createCompletedRef = useRef(false);
  const iconPickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      createCompletedRef.current = false;
      setRagDraftProjectId(null);
    }
  }, [open]);

  // Закрываем попап при клике вне его
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (iconPickerRef.current && !iconPickerRef.current.contains(event.target as Node)) {
        setShowIconPicker(false);
      }
    };

    if (showIconPicker) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [showIconPicker]);

  const resetForm = () => {
    setProjectName('');
    setSelectedIcon(null);
    setSelectedEmoji(null);
    setIconType('icon');
    setSelectedColor('#ffffff');
    setMemory('default');
    setInstructions('');
    setShowIconPicker(false);
    setShowAdvanced(false);
    setShowRagSettingsPanel(false);
    setIconTab(0);
    setRagDraftProjectId(null);
    setRagDraft(null);
  };

  const handleClose = () => {
    if (ragDraftProjectId && !createCompletedRef.current && cancelDraftProject) {
      cancelDraftProject(ragDraftProjectId);
    }
    resetForm();
    onClose();
  };

  const buildDraftPayload = (): DraftProjectPayload => ({
    name: projectName.trim(),
    memory,
    instructions: instructions.trim(),
    icon: iconType === 'icon' ? selectedIcon || undefined : selectedEmoji || undefined,
    iconType,
    iconColor: selectedColor,
  });

  const handleCreate = async () => {
    if (!projectName.trim()) return;
    createCompletedRef.current = true;

    const payload = buildDraftPayload();
    let projectId: string | null = null;

    if (ragDraftProjectId && finalizeDraftProject) {
      finalizeDraftProject(ragDraftProjectId, payload);
      projectId = ragDraftProjectId;
    } else {
      const projectData: ProjectData = {
        name: payload.name,
        icon: payload.icon,
        iconType: payload.iconType,
        iconColor: payload.iconColor,
        category: undefined,
        memory: payload.memory,
        instructions: payload.instructions,
      };
      const createdId = onCreateProject?.(projectData);
      if (typeof createdId === 'string') {
        projectId = createdId;
      }
    }

    if (projectId) {
      // Настройки уходят ПОСЛЕ создания: до него id проекта ещё нет. Нужна ли
      // перечанковка, решает backend — сравнивает индексные поля «до/после».
      const ragApplied = await saveEntityRagSettings({
        scope: 'project',
        entityId: projectId,
        entityName: payload.name,
        instructions: payload.instructions,
        draft: ragDraft,
      });
      if (!ragApplied.ok) {
        console.warn(`[RAG] Проект создан; настройки РАГ не применены: ${ragApplied.message}`);
      } else if (ragApplied.reindexed) {
        notifyReindexStarted();
      }
    }

    resetForm();
    onClose();
  };

  const resolveProjectIdForRag = async (): Promise<string> => {
    if (ragDraftProjectId) return ragDraftProjectId;
    if (!projectName.trim()) {
      throw new Error('Сначала введите название проекта');
    }
    if (!ensureDraftProjectForRag) {
      throw new Error('Загрузка файлов для нового проекта недоступна');
    }
    const payload = buildDraftPayload();
    const id = ensureDraftProjectForRag(payload);
    setRagDraftProjectId(id);
    // createProject пишет в Postgres асинхронно; без ожидания PUT RAG settings
    // получает 403 («не владелец») — настройки «Применить» выглядят потерянными.
    try {
      await syncProjectCreate({
        id,
        name: payload.name,
        instructions: payload.instructions,
        memory: payload.memory,
        icon: payload.icon,
        iconType: payload.iconType,
        iconColor: payload.iconColor,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      });
    } catch (err) {
      console.warn('[RAG] черновик проекта ещё не в БД:', err);
    }
    return id;
  };

  const renderIcon = () => {
    if (iconType === 'emoji' && selectedEmoji) {
      return (
        <Avatar
          sx={{
            width: 48,
            height: 48,
            bgcolor: selectedColor === '#ffffff' ? 'rgba(255,255,255,0.1)' : selectedColor,
            fontSize: 24,
          }}
        >
          {selectedEmoji}
        </Avatar>
      );
    }
    if (iconType === 'icon' && selectedIcon) {
      const IconComponent = iconOptions.find(opt => opt.name === selectedIcon)?.icon || FolderIcon;
      return (
        <Avatar
          sx={{
            width: 48,
            height: 48,
            bgcolor: selectedColor === '#ffffff' ? 'rgba(255,255,255,0.1)' : selectedColor,
            color: selectedColor === '#ffffff' ? 'white' : 'white',
          }}
        >
          <IconComponent />
        </Avatar>
      );
    }
    return (
      <Avatar
        sx={{
          width: 48,
          height: 48,
          bgcolor: 'rgba(255,255,255,0.1)',
          color: 'white',
        }}
      >
        <AddIcon />
      </Avatar>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          backgroundColor: theme.palette.mode === 'dark' ? '#1e1e1e' : '#ffffff',
          borderRadius: 2,
          minHeight: '500px',
          ...(showRagSettingsPanel
            ? { height: 'min(90vh, 820px)', display: 'flex', flexDirection: 'column' }
            : {}),
        },
      }}
    >
      {showRagSettingsPanel ? (
        <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <RAGSettings
            variant="panel"
            lockedScope="project"
            entityId={ragDraftProjectId}
            entityName={projectName.trim() || 'новый проект'}
            entityInstructionsPrompt={instructions}
            draft
            draftValue={ragDraft}
            onDraftChange={setRagDraft}
            onResolveEntityId={ensureDraftProjectForRag ? resolveProjectIdForRag : undefined}
            isDarkMode={theme.palette.mode === 'dark'}
            panelTitle={
              projectName.trim()
                ? `Настройки РАГ: ${projectName.trim()}`
                : 'Настройки РАГ для проекта'
            }
            onClose={() => setShowRagSettingsPanel(false)}
          />
        </Box>
      ) : (
        <>
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          pb: 2,
        }}
      >
        <Typography variant="h6" fontWeight="600">
          Новый проект
        </Typography>
        <IconButton onClick={handleClose} size="small">
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent>
        <Box sx={{ display: 'flex', gap: 2, mb: 3 }}>
          <Box
            sx={{
              position: 'relative',
            }}
          >
            <IconButton
              onClick={() => setShowIconPicker(!showIconPicker)}
              sx={{
                width: 56,
                height: 56,
                p: 0,
                '&:hover': {
                  opacity: 0.8,
                },
              }}
            >
              {renderIcon()}
            </IconButton>

            {/* Попап выбора иконки/эмодзи */}
            {showIconPicker && (
              <Paper
                ref={iconPickerRef}
                sx={{
                  position: 'absolute',
                  top: 64,
                  left: 0,
                  zIndex: 1000,
                  p: 2,
                  minWidth: 400,
                  bgcolor: theme.palette.mode === 'dark' ? '#2d2d2d' : '#ffffff',
                  boxShadow: 4,
                  borderRadius: 2,
                }}
              >
                <Tabs value={iconTab} onChange={(_, v) => setIconTab(v)}>
                  <Tab label="Икона" />
                  <Tab label="Эмодзи" />
                </Tabs>

                {iconTab === 0 && (
                  <Box>
                    <Box
                      sx={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(6, 1fr)',
                        gap: 1,
                        mt: 2,
                        mb: 2,
                        maxHeight: 300,
                        overflowY: 'auto',
                      }}
                    >
                      {iconOptions.map((option) => {
                        const IconComponent = option.icon;
                        const previewColor =
                          selectedColor === '#ffffff' ? '#9ca3af' : selectedColor;
                        return (
                          <IconButton
                            key={option.name}
                            onClick={() => {
                              setSelectedIcon(option.name);
                              setSelectedEmoji(null);
                              setIconType('icon');
                              setShowIconPicker(false);
                            }}
                            sx={{
                              width: 48,
                              height: 48,
                              border: 'none',
                              borderRadius: 1,
                              color: previewColor,
                              bgcolor: 'transparent',
                              '&:hover': {
                                bgcolor: 'action.hover',
                              },
                            }}
                          >
                            <IconComponent sx={{ fontSize: 24, color: 'inherit' }} />
                          </IconButton>
                        );
                      })}
                    </Box>
                    <Box sx={{ display: 'flex', gap: 1, justifyContent: 'center', pt: 1, borderTop: '1px solid', borderColor: 'divider' }}>
                      {colorOptions.map((color) => (
                        <Box
                          key={color.name}
                          onClick={() => setSelectedColor(color.value)}
                          sx={{
                            width: 32,
                            height: 32,
                            borderRadius: '50%',
                            bgcolor: color.value,
                            border: selectedColor === color.value ? '2px solid' : '1px solid',
                            borderColor: selectedColor === color.value ? 'primary.main' : 'divider',
                            cursor: 'pointer',
                            '&:hover': {
                              transform: 'scale(1.1)',
                            },
                            transition: 'transform 0.2s',
                          }}
                        />
                      ))}
                    </Box>
                  </Box>
                )}

                {iconTab === 1 && (
                  <Box
                    sx={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(6, 1fr)',
                      gap: 1,
                      mt: 2,
                      mb: 2,
                      maxHeight: 300,
                      overflowY: 'auto',
                    }}
                  >
                    {emojiOptions.map((emoji) => (
                      <IconButton
                        key={emoji}
                        onClick={() => {
                          setSelectedEmoji(emoji);
                          setSelectedIcon(null);
                          setIconType('emoji');
                          setShowIconPicker(false);
                        }}
                        sx={{
                          width: 48,
                          height: 48,
                          border: 'none',
                          borderRadius: 1,
                          fontSize: 24,
                          bgcolor: 'transparent',
                          '&:hover': {
                            bgcolor: 'action.hover',
                          },
                        }}
                      >
                        {emoji}
                      </IconButton>
                    ))}
                  </Box>
                )}
              </Paper>
            )}
          </Box>

          <TextField
            fullWidth
            placeholder="Название проекта"
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            sx={{
              '& .MuiOutlinedInput-root': {
                color: theme.palette.mode === 'dark' ? 'white' : 'text.primary',
              },
            }}
          />
        </Box>

        {/* Файлы RAG — немедленная загрузка; при первом файле создаётся черновик проекта */}
        <Box sx={{ mb: 2, mt: 1 }}>
          <ProjectRagLibraryInline
            projectId={ragDraftProjectId}
            onResolveProjectId={ensureDraftProjectForRag ? resolveProjectIdForRag : undefined}
            dense
            subtitle="После выбора файлов проект создаётся как черновик с текущим названием; при «Отменить» черновик удаляется."
          />
        </Box>

        {/* RAG-настройки проекта (scope=project) */}
        <Box sx={{ mb: 2 }}>
          <Button
            fullWidth
            onClick={() => {
              if (!projectName.trim()) {
                window.alert('Сначала введите название проекта');
                return;
              }
              void (async () => {
                if (ensureDraftProjectForRag && !ragDraftProjectId) {
                  try {
                    await resolveProjectIdForRag();
                  } catch {
                    return;
                  }
                }
                setShowRagSettingsPanel(true);
              })();
            }}
            endIcon={<ExpandMoreIcon />}
            sx={{
              justifyContent: 'space-between',
              textTransform: 'none',
              color: 'text.primary',
            }}
          >
            Настройки РАГ для этого проекта
          </Button>
        </Box>

        {/* Память / инструкции */}
        <Box sx={{ mb: 2 }}>
          <Button
            fullWidth
            onClick={() => setShowAdvanced(!showAdvanced)}
            endIcon={showAdvanced ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            sx={{
              justifyContent: 'space-between',
              textTransform: 'none',
              color: 'text.primary',
            }}
          >
            Память и инструкции
          </Button>

          <Collapse in={showAdvanced}>
            <Box sx={{ mt: 2, pl: 2 }}>
              {/* Память */}
              <Box sx={{ mb: 3 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Typography variant="body2" fontWeight="500">
                      Память
                    </Typography>
                    <Tooltip title="Выберите, имеет ли этот проект собственную изолированную память или использует общую память.">
                      <InfoIcon sx={{ fontSize: 16, opacity: 0.7 }} />
                    </Tooltip>
                  </Box>
                  <FormControl size="small" sx={{ minWidth: 200 }}>
                    <Select
                      value={memory}
                      onChange={(e) => setMemory(e.target.value as 'default' | 'project-only')}
                      sx={{
                        '& .MuiSelect-select': {
                          color: theme.palette.mode === 'dark' ? 'white' : 'text.primary',
                        },
                      }}
                    >
                      <MenuItem value="default">По умолчанию</MenuItem>
                      <MenuItem value="project-only">Только для проекта</MenuItem>
                    </Select>
                  </FormControl>
                </Box>
                <Typography variant="caption" sx={{ mt: 0.5, display: 'block', opacity: 0.7 }}>
                  {memory === 'default'
                    ? 'Чаты будут получать доступ к вашим общим воспоминаниям'
                    : 'Воспоминания изолированы в рамках этого проекта'}
                </Typography>
              </Box>

              {/* Инструкции */}
              <Box sx={{ mb: 3 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                  <Typography variant="body2" fontWeight="500">
                    Инструкции
                  </Typography>
                  <Tooltip title="Определите конкретную роль, тон и формат ответа, которые вы ожидаете от AstraChat в рамках этого проекта.">
                    <InfoIcon sx={{ fontSize: 16, opacity: 0.7 }} />
                  </Tooltip>
                </Box>
                <TextField
                  fullWidth
                  multiline
                  rows={4}
                  placeholder="Что ИИ должен знать об этом проекте? (например, конкретные правила, тон или форматирование)"
                  value={instructions}
                  onChange={(e) => {
                    if (e.target.value.length <= 1000) {
                      setInstructions(e.target.value);
                    }
                  }}
                  helperText={`${instructions.length}/1000`}
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      color: theme.palette.mode === 'dark' ? 'white' : 'text.primary',
                    },
                  }}
                />
              </Box>
            </Box>
          </Collapse>
        </Box>
      </DialogContent>

      <DialogActions sx={{ p: 2 }}>
        <Button onClick={handleClose} sx={{ textTransform: 'none' }}>
          Отменить
        </Button>
        <Button
          onClick={handleCreate}
          variant="contained"
          disabled={!projectName.trim()}
          sx={{
            textTransform: 'none',
            bgcolor: !projectName.trim() ? 'rgba(255,255,255,0.1)' : 'primary.main',
            color: !projectName.trim() ? 'rgba(255,255,255,0.5)' : 'white',
            '&:hover': {
              bgcolor: !projectName.trim() ? 'rgba(255,255,255,0.1)' : 'primary.dark',
            },
          }}
        >
          Создать проект
        </Button>
      </DialogActions>
        </>
      )}
    </Dialog>
  );
}

