import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Box,
  Typography,
  Popover,
  Tooltip,
  CircularProgress,
  FormControl,
  InputLabel,
  OutlinedInput,
  InputAdornment,
  IconButton,
} from '@mui/material';
import type { SxProps, Theme } from '@mui/material';
import {
  Search as SearchIcon,
  ExpandMore as ExpandMoreIcon,
  ChevronRight as ChevronRightIcon,
  Computer as ComputerIcon,
  Check as CheckIcon,
  HelpOutline as HelpOutlineIcon,
} from '@mui/icons-material';
import { useAppActions } from '../contexts/AppContext';
import { useRagReindexStatus } from '../contexts/RagReindexStatusContext';
import { getApiUrl, getAuthFetchHeaders } from '../config/api';
import {
  getDropdownItemSx,
  getDropdownChevronSx,
  getDropdownPanelSx,
  getMenuColors,
  MENU_ACTION_TEXT_SIZE,
  getCategoryFieldSx,
  flattenSx,
  AGENT_CONSTRUCTOR_FIELD_INPUT_PROPS,
  AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX,
} from '../constants/menuStyles';
import {
  MODEL_SETTINGS_HELP_ICON_BUTTON_SX,
} from '../constants/modelSettingsStyles';

export type RagModelKind = 'embedding' | 'reranker';

interface RagModelRow {
  path: string;
  name: string;
  display_name: string;
  source: string;
  kind: RagModelKind;
  description?: string;
  available?: boolean;
}

interface RagModelSelectorProps {
  kind: RagModelKind;
  isDarkMode: boolean;
  disabled?: boolean;
  triggerMaxWidth?: number | null;
  onModelSelect?: (modelPath: string) => void;
  /** Для каждого стора выбирается модель: проекты | агенты | БЗ. У них свои модели. */
  scope?: 'project' | 'agent' | 'kb';
  /** Конкретный проект/агент — подсветка выбранной модели из его настроек. */
  entityId?: string | number | null;
  onResolveEntityId?: () => string | number | Promise<string | number>;
  /** Плавающая подпись поля (как «Категория» в конструкторе агента). */
  label?: string;
  /** Подсказка у иконки «?» рядом с подписью. */
  helpTooltip?: string;
  /**
   * Не вызывать API сразу: только обновить UI и onModelSelect.
   * Применение (POST /api/rag/models/select) — снаружи по «Сохранить».
   */
  deferApply?: boolean;
  /** Стили поля-триггера (как «Категория» в конструкторе агента). */
  fieldSx?: SxProps<Theme>;
  /**
   * Путь из черновика родителя. Важнее cluster/current с сервера —
   * иначе после «Применить» и повторного открытия снова «Выбрать эмбеддинг».
   */
  preferredPath?: string | null;
}

const SOURCE_LABELS: Record<string, string> = {
  local: 'Локальные',
  corsur: 'CORSUR',
  corsur_rag: 'CORSUR',
  phoenix: 'PHOENIX',
  phoenix_embeddings: 'PHOENIX',
};

/** Порядок вкладок: локальные первыми, затем внешние шлюзы. */
const SOURCE_ORDER = ['Локальные', 'CORSUR', 'PHOENIX'];

const sourceLabel = (src: string) => SOURCE_LABELS[src] || (src ? src.toUpperCase() : '');

const sourceRank = (label: string) => {
  const i = SOURCE_ORDER.indexOf(label);
  return i < 0 ? SOURCE_ORDER.length : i;
};

const LEFT_PANEL_W = 185;
const RIGHT_PANEL_W = 260;

export default function RagModelSelector({
  kind,
  isDarkMode,
  disabled = false,
  triggerMaxWidth = 220,
  onModelSelect,
  scope = 'project',
  entityId,
  onResolveEntityId,
  label,
  helpTooltip,
  deferApply = false,
  fieldSx: fieldSxProp,
  preferredPath = null,
}: RagModelSelectorProps) {
  const [models, setModels] = useState<RagModelRow[]>([]);
  const [selectedPath, setSelectedPath] = useState('');
  const [loadingModels, setLoadingModels] = useState(false);
  const [isSelecting, setIsSelecting] = useState(false);
  const [loadingModelPath, setLoadingModelPath] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const [modelSearch, setModelSearch] = useState('');
  const [sourcesSubmenuOpen, setSourcesSubmenuOpen] = useState(false);
  const [activeSource, setActiveSource] = useState<string | null>(null);
  const outlinedRef = useRef<HTMLDivElement>(null);
  const { showNotification } = useAppActions();
  const { notifyReindexStarted } = useRagReindexStatus();

  const { menuItemColor, menuItemHover, menuDividerBorder } = getMenuColors(isDarkMode);
  const windowSx = { ...getDropdownPanelSx(isDarkMode) } as Record<string, unknown>;
  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(isDarkMode), [isDarkMode]);
  const fieldSx = useMemo(
    () =>
      flattenSx(fieldSxProp ?? getCategoryFieldSx(isDarkMode), {
        flex: 1,
        minWidth: 0,
        maxWidth: triggerMaxWidth ?? '100%',
        opacity: disabled || isSelecting ? 0.9 : 1,
        pointerEvents: disabled ? 'none' : 'auto',
        '& .MuiOutlinedInput-root': {
          cursor: disabled || isSelecting ? 'default' : 'pointer',
        },
      }),
    [fieldSxProp, isDarkMode, triggerMaxWidth, disabled, isSelecting],
  );
  const iconColor = isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)';
  const mutedTextColor = isDarkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.85)';
  const placeholderColor = isDarkMode ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.35)';
  const subtleColor = isDarkMode ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.5)';

  const loadModels = useCallback(async () => {
    try {
      setLoadingModels(true);
      let resolvedEntityId = entityId;
      if ((resolvedEntityId == null || resolvedEntityId === '') && onResolveEntityId) {
        resolvedEntityId = await Promise.resolve(onResolveEntityId());
      }
      const entityQuery =
        resolvedEntityId == null || resolvedEntityId === ''
          ? ''
          : scope === 'project'
            ? `&project_id=${encodeURIComponent(String(resolvedEntityId))}`
            : scope === 'kb'
              ? `&knowledge_base_id=${encodeURIComponent(String(resolvedEntityId))}`
              : `&agent_id=${encodeURIComponent(String(resolvedEntityId))}`;
      const response = await fetch(
        getApiUrl(`/api/rag/models?type=${kind}&scope=${scope}${entityQuery}`),
        { headers: getAuthFetchHeaders() },
      );
      if (!response.ok) return;
      const data = await response.json();
      const rows: RagModelRow[] = (data?.models?.[kind] ?? []).filter(
        (m: RagModelRow) => Boolean(m.path),
      );
      setModels(rows);
      // offline=true в ответе rag-models = только локальные веса; для UI — подсказка.
      setOffline(
        Boolean(data?.offline) ||
          (rows.length > 0 &&
            rows.every((m) => String(m.source || '').toLowerCase() === 'local')),
      );
      const current = data?.current?.[kind];
      const clusterDefault = data?.cluster_default?.[kind];
      const preferred = String(preferredPath || '').trim();
      if (preferred) {
        setSelectedPath(preferred);
      } else if (current?.path) {
        setSelectedPath(current.path);
      } else if (clusterDefault?.path) {
        setSelectedPath(String(clusterDefault.path));
      }
    } catch {
      // сервис может быть недоступен
    } finally {
      setLoadingModels(false);
    }
    // scope / entity в зависимостях: при смене подсветка выбранной модели перечитывается.
  }, [kind, scope, entityId, onResolveEntityId, preferredPath]);

  useEffect(() => {
    loadModels();
  }, [loadModels]);

  useEffect(() => {
    const preferred = String(preferredPath || '').trim();
    if (preferred) setSelectedPath(preferred);
  }, [preferredPath]);

  const getDisplayName = useCallback(
    (path: string) => {
      const model = models.find((m) => m.path === path);
      if (model?.display_name) return model.display_name;
      return path.split('/').pop() || path;
    },
    [models],
  );

  const sources = useMemo(() => {
    const map = new Map<string, RagModelRow[]>();
    for (const m of models) {
      const src = String(m.source || '').toLowerCase();
      const label = sourceLabel(src);
      const bucket = map.get(label);
      if (bucket) bucket.push(m);
      else map.set(label, [m]);
    }
    return Array.from(map.entries()).map(([label, items]) => ({ label, items })).sort(
      (a, b) => sourceRank(a.label) - sourceRank(b.label),
    );
  }, [models]);

  const filteredModels = useMemo(() => {
    const base = sources.find((s) => s.label === activeSource)?.items ?? [];
    if (!modelSearch.trim()) return base;
    const q = modelSearch.toLowerCase();
    return base.filter(
      (m) =>
        m.name.toLowerCase().includes(q) ||
        m.display_name.toLowerCase().includes(q) ||
        m.path.toLowerCase().includes(q),
    );
  }, [sources, activeSource, modelSearch]);

  const handleOpen = () => {
    if (disabled || isSelecting) return;
    setAnchorEl(outlinedRef.current);
    setModelSearch('');
    if (sources.length > 0) {
      const firstWithSelection = sources.find((s) => s.items.some((m) => m.path === selectedPath));
      setActiveSource(firstWithSelection?.label ?? sources[0].label);
      setSourcesSubmenuOpen(true);
    }
  };

  const handleClose = () => {
    setAnchorEl(null);
    setSourcesSubmenuOpen(false);
    setModelSearch('');
  };

  const handleSelect = async (modelPath: string) => {
    if (modelPath === selectedPath) {
      handleClose();
      return;
    }

    // Черновик: меняем только UI, API вызовется при «Сохранить настройки».
    if (deferApply) {
      setSelectedPath(modelPath);
      handleClose();
      onModelSelect?.(modelPath);
      return;
    }

    const prevPath = selectedPath;
    if (kind === 'embedding') {
      const ok = window.confirm(
        'Смена embedding-модели загрузит её для поиска/индексации и переиндексирует только ваши документы в проектах и KB агентов (где вы владелец). В настройках других пользователей выбор не меняется. Memory RAG не затрагивается. Модели с другой размерностью (dim) — только через ConfigMap. Продолжить?',
      );
      if (!ok) return;
    }
    try {
      setIsSelecting(true);
      setLoadingModelPath(modelPath);
      const response = await fetch(getApiUrl('/api/rag/models/select'), {
        method: 'POST',
        headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ model_type: kind, model_path: modelPath, scope }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof data?.detail === 'string'
            ? data.detail
            : data?.message || 'Не удалось загрузить модель',
        );
      }
      setSelectedPath(modelPath);
      const reindexed = Boolean(data?.reindexed);
      if (reindexed) {
        notifyReindexStarted();
      }
      showNotification(
        'success',
        reindexed
          ? 'Модель загружена. Запущена переиндексация ваших документов (без Memory RAG).'
          : 'Модель RAG успешно загружена',
      );
      handleClose();
      onModelSelect?.(modelPath);
      await loadModels();
    } catch (err: unknown) {
      setSelectedPath(prevPath);
      const message = err instanceof Error ? err.message : String(err);
      showNotification('error', `Ошибка загрузки модели: ${message}`);
    } finally {
      setIsSelecting(false);
      setLoadingModelPath(null);
    }
  };

  const fieldLabel =
    label ?? (kind === 'embedding' ? 'Модель эмбеддингов' : 'Модель реранкера');

  const triggerLabel = loadingModelPath
    ? getDisplayName(loadingModelPath)
    : selectedPath
      ? getDisplayName(selectedPath)
      : kind === 'embedding'
        ? 'Выбрать эмбеддинг'
        : 'Выбрать реранкер';

  const leftEntrySx = (active: boolean) => ({
    ...dropdownItemSx,
    display: 'flex',
    alignItems: 'center',
    gap: 1,
    color: active ? menuItemColor : mutedTextColor,
    fontWeight: active ? 600 : 400,
    bgcolor: active ? menuItemHover : 'transparent',
  });

  const paperSx = {
    mt: 0.75,
    p: 0,
    overflow: 'visible',
    background: 'transparent !important',
    backgroundColor: 'transparent !important',
    boxShadow: 'none !important',
    backdropFilter: 'none',
    border: 'none',
    maxWidth: '96vw',
  };

  if (loadingModels && models.length === 0) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.5, maxWidth: '100%', width: '100%' }}>
        <CircularProgress size={16} sx={{ color: subtleColor }} />
        <Typography sx={{ fontSize: MENU_ACTION_TEXT_SIZE, color: subtleColor }}>
          Загрузка моделей…
        </Typography>
      </Box>
    );
  }

  if (models.length === 0) {
    return (
      <Typography sx={{ fontSize: MENU_ACTION_TEXT_SIZE, color: subtleColor, maxWidth: '100%', width: '100%' }}>
        Модели {kind === 'embedding' ? 'эмбеддингов' : 'реранкера'} недоступны
        {offline ? ' (только локальные модели)' : ''}
      </Typography>
    );
  }

  const inputLabelId = `rag-model-${kind}-${scope}`;

  return (
    <Box sx={{ maxWidth: '100%', width: '100%', display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <FormControl variant="outlined" fullWidth size="small" sx={fieldSx}>
        <InputLabel htmlFor={inputLabelId}>{fieldLabel}</InputLabel>
        <OutlinedInput
          ref={outlinedRef}
          id={inputLabelId}
          label={fieldLabel}
          value={triggerLabel}
          readOnly
          inputProps={AGENT_CONSTRUCTOR_FIELD_INPUT_PROPS}
          sx={AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX}
          onClick={isSelecting ? undefined : handleOpen}
          endAdornment={
            <InputAdornment position="end">
              {isSelecting ? (
                <CircularProgress size={16} sx={{ color: mutedTextColor }} />
              ) : (
                <ExpandMoreIcon
                  sx={{
                    ...dropdownChevronSx,
                    transform: anchorEl ? 'rotate(180deg)' : 'none',
                  }}
                />
              )}
            </InputAdornment>
          }
        />
      </FormControl>
      {helpTooltip ? (
        <Tooltip title={helpTooltip} arrow>
          <IconButton
            size="small"
            sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX}
            aria-label={`Справка: ${fieldLabel}`}
          >
            <HelpOutlineIcon fontSize="small" color="action" />
          </IconButton>
        </Tooltip>
      ) : null}

      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={handleClose}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        slotProps={{ paper: { sx: paperSx } }}
      >
        <Box
          onMouseLeave={() => setSourcesSubmenuOpen(false)}
          sx={{ display: 'flex', flexDirection: 'row', alignItems: 'flex-start', gap: '6px' }}
        >
          <Box
            sx={{
              ...windowSx,
              width: LEFT_PANEL_W,
              flexShrink: 0,
              display: 'flex',
              flexDirection: 'column',
              maxHeight: 300,
              overflowY: 'auto',
              scrollbarWidth: 'none',
              msOverflowStyle: 'none',
              '&::-webkit-scrollbar': { display: 'none' },
            }}
          >
            <Box sx={{ py: 0.5, px: 0.5 }}>
              {sources.map((src) => {
                const isActive = sourcesSubmenuOpen && src.label === activeSource;
                const hasSelectedModel = src.items.some((m) => m.path === selectedPath);
                return (
                  <Box
                    key={src.label}
                    onMouseEnter={() => {
                      setActiveSource(src.label);
                      setSourcesSubmenuOpen(true);
                      setModelSearch('');
                    }}
                    sx={leftEntrySx(isActive || hasSelectedModel)}
                  >
                    <ComputerIcon sx={{ fontSize: 18, color: iconColor, flexShrink: 0 }} />
                    <Typography
                      sx={{
                        flex: 1,
                        minWidth: 0,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        fontSize: MENU_ACTION_TEXT_SIZE,
                      }}
                    >
                      {src.label}
                    </Typography>
                    <ChevronRightIcon
                      sx={{
                        fontSize: 18,
                        color: subtleColor,
                        flexShrink: 0,
                        transform: isActive ? 'rotate(90deg)' : 'none',
                        transition: 'transform 0.15s',
                      }}
                    />
                  </Box>
                );
              })}
            </Box>
          </Box>

          {sourcesSubmenuOpen ? (
            <Box
              sx={{
                ...windowSx,
                width: RIGHT_PANEL_W,
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  px: 1.5,
                  py: 0.9,
                  gap: 1,
                  borderBottom: `1px solid ${menuDividerBorder}`,
                }}
              >
                <SearchIcon sx={{ color: subtleColor, fontSize: 16, flexShrink: 0 }} />
                <Box
                  component="input"
                  placeholder="Поиск моделей..."
                  value={modelSearch}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelSearch(e.target.value)}
                  disabled={isSelecting}
                  sx={{
                    flex: 1,
                    bgcolor: 'transparent',
                    border: 'none',
                    outline: 'none',
                    color: menuItemColor,
                    fontSize: MENU_ACTION_TEXT_SIZE,
                    '&::placeholder': { color: placeholderColor },
                  }}
                />
              </Box>

              <Box
                sx={{
                  maxHeight: 260,
                  overflowY: 'auto',
                  py: 0.5,
                  pointerEvents: isSelecting ? 'none' : 'auto',
                  scrollbarWidth: 'none',
                  msOverflowStyle: 'none',
                  '&::-webkit-scrollbar': { display: 'none' },
                }}
              >
                {filteredModels.map((model) => {
                  const isSelected = selectedPath === model.path && !loadingModelPath;
                  const isLoading = loadingModelPath === model.path;
                  const unavailable = model.available === false;
                  return (
                    <Box
                      key={model.path}
                      onClick={unavailable || isLoading ? undefined : () => handleSelect(model.path)}
                      sx={{
                        ...dropdownItemSx,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1,
                        opacity: unavailable ? 0.45 : 1,
                        color: isSelected || isLoading ? menuItemColor : mutedTextColor,
                        fontWeight: isSelected || isLoading ? 600 : 400,
                        bgcolor: isSelected || isLoading ? menuItemHover : 'transparent',
                        cursor: unavailable ? 'not-allowed' : 'pointer',
                      }}
                    >
                      <ComputerIcon sx={{ fontSize: 18, color: iconColor, flexShrink: 0 }} />
                      <Typography
                        sx={{
                          flex: 1,
                          minWidth: 0,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          fontSize: MENU_ACTION_TEXT_SIZE,
                        }}
                      >
                        {model.display_name}
                      </Typography>
                      {isLoading ? (
                        <CircularProgress size={16} sx={{ color: mutedTextColor, flexShrink: 0 }} />
                      ) : isSelected ? (
                        <CheckIcon sx={{ fontSize: 16, color: 'primary.main', flexShrink: 0 }} />
                      ) : null}
                    </Box>
                  );
                })}
                {filteredModels.length === 0 && (
                  <Box sx={{ px: 1.5, py: 2, fontSize: MENU_ACTION_TEXT_SIZE, color: subtleColor, textAlign: 'center' }}>
                    {modelSearch.trim() ? 'Ничего не найдено' : 'Нет доступных моделей'}
                  </Box>
                )}
              </Box>
            </Box>
          ) : null}
        </Box>
      </Popover>
    </Box>
  );
}
