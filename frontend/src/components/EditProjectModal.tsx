import React, { useState, useRef, useEffect } from 'react';
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
  HelpOutline as HelpOutlineIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
} from '@mui/icons-material';
import type { Project } from '../contexts/AppContext';
import { getProjectIconGlyphSx } from '../constants/menuStyles';
import {
  PROJECT_DEFAULT_ICON as FolderIcon,
  PROJECT_ICON_OPTIONS as iconOptions,
} from '../constants/projectIcons';
import ProjectRagLibraryInline from './ProjectRagLibraryInline';
import RAGSettings from './settings/RAGSettings';
import { saveEntityRagSettings, type EntityRagDraft } from '../utils/entityRagSettings';
import { useRagReindexStatus } from '../contexts/RagReindexStatusContext';

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
];

export interface EditProjectModalProps {
  open: boolean;
  onClose: () => void;
  project: Project | null;
  onSave: (projectId: string, updates: Partial<Project>) => void;
}

export default function EditProjectModal({ open, onClose, project, onSave }: EditProjectModalProps) {
  const theme = useTheme();
  const { notifyReindexStarted } = useRagReindexStatus();
  const [projectName, setProjectName] = useState('');
  const [selectedIcon, setSelectedIcon] = useState<string | null>(null);
  const [selectedEmoji, setSelectedEmoji] = useState<string | null>(null);
  const [iconType, setIconType] = useState<'icon' | 'emoji'>('icon');
  const [selectedColor, setSelectedColor] = useState('#ffffff');
  const [memory, setMemory] = useState<'default' | 'project-only'>('default');
  const [instructions, setInstructions] = useState('');
  /** Черновик настроек РАГ: записывается по «Сохранить» проекта, не раньше. */
  const [ragDraft, setRagDraft] = useState<EntityRagDraft | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showRagSettingsPanel, setShowRagSettingsPanel] = useState(false);
  const [showIconPicker, setShowIconPicker] = useState(false);
  const [iconTab, setIconTab] = useState(0);
  const iconPickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open && project) {
      setProjectName(project.name);
      setIconType(project.iconType || 'icon');
      setSelectedIcon(project.iconType === 'icon' ? (project.icon || null) : null);
      setSelectedEmoji(project.iconType === 'emoji' ? (project.icon || null) : null);
      setSelectedColor(project.iconColor || '#ffffff');
      setMemory(project.memory || 'default');
      setInstructions(project.instructions || '');
      setShowAdvanced(false);
      setShowRagSettingsPanel(false);
      // Черновик принадлежит тому проекту, у которого его набрали: открыли
      // другой — сбрасываем, иначе его настройки уехали бы не туда.
      setRagDraft(null);
    }
  }, [open, project]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (iconPickerRef.current && !iconPickerRef.current.contains(event.target as Node)) {
        setShowIconPicker(false);
      }
    };
    if (showIconPicker) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showIconPicker]);

  const handleClose = () => {
    setShowIconPicker(false);
    setShowAdvanced(false);
    setShowRagSettingsPanel(false);
    onClose();
  };

  const handleSave = async () => {
    if (!project || !projectName.trim()) return;
    const trimmedName = projectName.trim();
    const trimmedInstructions = instructions.trim();
    onSave(project.id, {
      name: trimmedName,
      icon: iconType === 'icon' ? (selectedIcon || undefined) : (selectedEmoji || undefined),
      iconType,
      iconColor: selectedColor,
      memory,
      instructions: trimmedInstructions,
    });

    // Настройки РАГ пишутся здесь, по «Сохранить» проекта: панель их только
    // накапливает. Нужна ли перечанковка, решает backend.
    const ragApplied = await saveEntityRagSettings({
      scope: 'project',
      entityId: project.id,
      entityName: trimmedName,
      instructions: trimmedInstructions,
      draft: ragDraft,
    });
    if (ragApplied.ok) {
      setRagDraft(null);
      if (ragApplied.reindexed) {
        notifyReindexStarted();
      }
    } else {
      console.warn(`[RAG] Проект сохранён; настройки РАГ не применены: ${ragApplied.message}`);
    }

    handleClose();
  };

  const renderIcon = () => {
    const iconColor = selectedColor === '#ffffff' ? '#9ca3af' : selectedColor;
    const glyphSx = getProjectIconGlyphSx(26, iconColor);
    const iconWrapSx = {
      width: 48,
      height: 48,
      display: 'flex' as const,
      alignItems: 'center',
      justifyContent: 'center',
      color: iconColor,
    };
    if (iconType === 'emoji' && selectedEmoji) {
      return (
        <Box
          sx={{
            width: 48,
            height: 48,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 28,
            lineHeight: 1,
            transform: 'translateY(-0.25px)',
          }}
        >
          {selectedEmoji}
        </Box>
      );
    }
    if (iconType === 'icon' && selectedIcon) {
      const IconComponent = iconOptions.find((opt) => opt.name === selectedIcon)?.icon || FolderIcon;
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

  if (!project) return null;

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: {
          backgroundColor: theme.palette.mode === 'dark' ? '#1e1e1e' : '#ffffff',
          borderRadius: 2,
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
            entityId={project.id}
            entityName={project.name}
            entityInstructionsPrompt={instructions}
            draft
            draftValue={ragDraft}
            onDraftChange={setRagDraft}
            isDarkMode={theme.palette.mode === 'dark'}
            panelTitle={`Настройки РАГ: ${project.name}`}
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
          Редактировать проект
        </Typography>
        <IconButton onClick={handleClose} size="small" aria-label="Закрыть">
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent>
        <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
          <Box sx={{ position: 'relative' }}>
            <IconButton
              onClick={() => setShowIconPicker(!showIconPicker)}
              sx={{
                width: 56,
                height: 56,
                p: 0,
                '&:hover': { opacity: 0.8 },
              }}
            >
              {renderIcon()}
            </IconButton>

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
                            '&:hover': { bgcolor: 'action.hover' },
                          }}
                        >
                          <IconComponent sx={{ fontSize: 24, color: 'inherit' }} />
                        </IconButton>
                      );
                    })}
                  </Box>
                )}
                {iconTab === 1 && (
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mt: 2, mb: 2, maxHeight: 300, overflowY: 'auto' }}>
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
                          '&:hover': { bgcolor: 'action.hover' },
                        }}
                      >
                        {emoji}
                      </IconButton>
                    ))}
                  </Box>
                )}
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mt: 1 }}>
                  {colorOptions.map((c) => (
                    <IconButton
                      key={c.name}
                      onClick={() => setSelectedColor(c.value)}
                      sx={{
                        width: 28,
                        height: 28,
                        bgcolor: c.value,
                        border: selectedColor === c.value ? '2px solid' : '1px solid',
                        borderColor: selectedColor === c.value ? 'primary.main' : 'divider',
                        '&:hover': { opacity: 0.9 },
                      }}
                    />
                  ))}
                </Box>
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

        {/* Файлы RAG — сразу видны, как в настройках «База данных» памяти */}
        {project && (
          <Box sx={{ mb: 2, mt: 1 }}>
            <ProjectRagLibraryInline projectId={project.id} dense />
          </Box>
        )}

        {/* RAG-настройки проекта (scope=project) */}
        <Box sx={{ mb: 2 }}>
          <Button
            fullWidth
            onClick={() => setShowRagSettingsPanel(true)}
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
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <Typography variant="body2" fontWeight="500">
                      Память
                    </Typography>
                    <Tooltip
                      title="Выберите, имеет ли этот проект собственную изолированную память или использует общую память."
                      arrow
                    >
                      <IconButton
                        size="small"
                        sx={{
                          p: 0,
                          ml: 0.5,
                          opacity: 0.7,
                          '&:hover': {
                            opacity: 1,
                            '& .MuiSvgIcon-root': {
                              color: 'primary.main',
                            },
                          },
                        }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <HelpOutlineIcon fontSize="small" color="action" />
                      </IconButton>
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
              </Box>

              {/* Инструкции */}
              <Box sx={{ mb: 3 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
                  <Typography variant="body2" fontWeight="500">
                    Инструкции
                  </Typography>
                  <Tooltip
                    title="Определите конкретную роль, тон и формат ответа, которые вы ожидаете от AstraChat в рамках этого проекта."
                    arrow
                  >
                    <IconButton
                      size="small"
                      sx={{
                        p: 0,
                        ml: 0.5,
                        opacity: 0.7,
                        '&:hover': {
                          opacity: 1,
                          '& .MuiSvgIcon-root': {
                            color: 'primary.main',
                          },
                        },
                      }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <HelpOutlineIcon fontSize="small" color="action" />
                    </IconButton>
                  </Tooltip>
                </Box>
                <TextField
                  fullWidth
                  multiline
                  rows={4}
                  placeholder="Что ИИ должен знать об этом проекте? (например, конкретные правила, тон или форматирование)"
                  value={instructions}
                  onChange={(e) => {
                    if (e.target.value.length <= 1000) setInstructions(e.target.value);
                  }}
                  helperText={`${instructions.length} / 1000`}
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

      <DialogActions sx={{ p: 2, justifyContent: 'flex-end' }}>
        <Button
          onClick={handleSave}
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
          Сохранить
        </Button>
      </DialogActions>
        </>
      )}
    </Dialog>
  );
}
