import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Dialog,
  DialogContent,
  Box,
  Typography,
  Button,
  IconButton,
  Slider,
  FormControl,
  InputLabel,
  OutlinedInput,
  InputAdornment,
  Popover,
} from '@mui/material';
import {
  ArrowBack as ArrowBackIcon,
  Restore as RestoreIcon,
  Save as SaveIcon,
  ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import {
  getCategoryFieldSx,
  getDropdownChevronSx,
  getDropdownItemStateSx,
  getDropdownItemSx,
  getDropdownPopoverPaperSx,
  getFormFieldInputSx,
  getAgentConstructorSaveButtonSx,
  getAgentConstructorRestoreButtonSx,
  AGENT_CONSTRUCTOR_SAVE_ICON_SX,
} from '../constants/menuStyles';
import {
  getSidebarPanelBackground,
  getSidebarPanelChrome,
} from '../constants/sidebarPanelColor';
import ModelSettingsFields from './ModelSettingsFields';
import { MODEL_SETTINGS_DEFAULT, type ModelSettingsState } from '../constants/modelSettingsStyles';

export interface ModelParamsState {
  provider: string;
  model: string;
  contextTokens: string;
  outputTokens: string;
  temperature: number;
  topP: number;
  frequencyPenalty: number;
  presencePenalty: number;
}

const defaultParams: ModelParamsState = {
  provider: '',
  model: '',
  contextTokens: 'Системная',
  outputTokens: 'Системная',
  temperature: 1,
  topP: 1,
  frequencyPenalty: 0,
  presencePenalty: 0,
};

export interface ProviderModelOption {
  name: string;
  path: string;
  display_name?: string;
  provider_id?: string;
  llm_host_id?: string;
}

interface ModelParametersModalProps {
  open: boolean;
  onClose: () => void;
  currentModel: string;
  availableModels: string[];
  initialParams?: Partial<ModelParamsState>;
  onSave: (model: string, params: Partial<ModelParamsState>) => void;
  /** 'modal' — диалог поверх контента; 'panel' — панель вместо формы агента с кнопкой «Назад» */
  variant?: 'modal' | 'panel';
  /** Тонкая настройка модели (из конструктора агента): при задании показывается блок внутри меню и сохраняется через onSaveModelSettings */
  initialModelSettings?: ModelSettingsState;
  onSaveModelSettings?: (s: ModelSettingsState) => void;
  /** Каталог моделей с провайдерами (CORSUR/Phoenix …). Если задан — провайдер выбирается из реальных подключений, модели фильтруются по провайдеру. */
  providerModels?: ProviderModelOption[];
  /** Порядок/список id провайдеров для выпадающего списка. */
  providerIds?: string[];
  /** Только просмотр (роль «Зритель»): значения видны, сохранить/сменить нельзя. */
  readOnly?: boolean;
}

/** Провайдер (первый сегмент пути ``<provider>/<model_id>``). */
function providerOfPath(path: string): string {
  const p = (path || '').replace(/^llm-svc:\/\//i, '');
  if (!p) return '';
  return p.includes('/') ? p.split('/')[0] : '';
}

const FALLBACK_PROVIDER_OPTIONS = [
  { value: 'OpenAI', label: 'OpenAI' },
  { value: 'Local', label: 'Local' },
];

/** Список провайдеров для селектора: ids из конфига важнее каталога моделей. */
export function resolveProviderOptions(
  providerIds?: string[] | null,
  providerModels?: Array<{ provider_id?: string; path?: string }> | null,
): Array<{ value: string; label: string }> {
  const fromIds = (providerIds || []).map((id) => String(id || '').trim()).filter(Boolean);
  const fromModels = Array.from(
    new Set(
      (providerModels || [])
        .map((m) => (m.provider_id || providerOfPath(m.path || '')).trim())
        .filter(Boolean),
    ),
  );
  const ids = fromIds.length ? fromIds : fromModels;
  if (!ids.length) return FALLBACK_PROVIDER_OPTIONS;
  return ids
    .filter((id) => String(id).toUpperCase() !== 'SC')
    .map((id) => ({ value: id, label: id }));
}

function formatModelLabel(path: string) {
  return path.replace('llm-svc://', '').split('/').pop() || path || '—';
}

export default function ModelParametersModal({
  open,
  onClose,
  currentModel,
  availableModels,
  initialParams,
  onSave,
  variant = 'modal',
  initialModelSettings,
  onSaveModelSettings,
  providerModels,
  providerIds,
  readOnly = false,
}: ModelParametersModalProps) {
  const [panelBg, setPanelBg] = useState(() => getSidebarPanelBackground());
  useEffect(() => {
    const onColorChanged = () => setPanelBg(getSidebarPanelBackground());
    window.addEventListener('sidebarColorChanged', onColorChanged);
    return () => window.removeEventListener('sidebarColorChanged', onColorChanged);
  }, []);
  const panelChrome = useMemo(() => getSidebarPanelChrome(panelBg), [panelBg]);
  const darkFields = !panelChrome.isLight;

  const formFieldInputSx = useMemo(() => getFormFieldInputSx(darkFields), [darkFields]);
  const outlinedSelectSx = useMemo(() => getCategoryFieldSx(darkFields), [darkFields]);
  const dropdownItemSx = useMemo(() => getDropdownItemSx(darkFields), [darkFields]);
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(darkFields), [darkFields]);
  const saveButtonSx = useMemo(() => getAgentConstructorSaveButtonSx(darkFields), [darkFields]);
  const restoreButtonSx = useMemo(() => getAgentConstructorRestoreButtonSx(darkFields), [darkFields]);

  const [params, setParams] = useState<ModelParamsState>({ ...defaultParams, model: currentModel });
  const [providerAnchor, setProviderAnchor] = useState<HTMLElement | null>(null);
  const [modelAnchor, setModelAnchor] = useState<HTMLElement | null>(null);
  const hasModelSettings =
    initialModelSettings != null && (onSaveModelSettings != null || readOnly);
  const [modelSettings, setModelSettings] = useState<ModelSettingsState>(() => ({
    ...MODEL_SETTINGS_DEFAULT,
    ...initialModelSettings,
  }));

  // Провайдеры из /api/llm-providers (CORSUR/PHOENIX) важнее плоского каталога моделей.
  // Раньше при пустом providerModels UI откатывался на захардкоженные OpenAI/Local.
  const providerMode =
    (Array.isArray(providerIds) && providerIds.length > 0)
    || (Array.isArray(providerModels) && providerModels.length > 0);
  const providerOptions = useMemo(
    () => resolveProviderOptions(providerIds, providerModels),
    [providerIds, providerModels],
  );

  const selectedProvider = params.provider || '';

  const providerFilteredModels = useMemo(() => {
    if (!providerMode) return availableModels;
    return (providerModels || [])
      .filter((m) => (m.provider_id || providerOfPath(m.path)) === selectedProvider)
      .map((m) => m.path);
  }, [providerMode, providerModels, availableModels, selectedProvider]);

  const modelLabelOf = useCallback(
    (path: string) => {
      if (providerMode) {
        const found = (providerModels || []).find((m) => m.path === path);
        if (found) return (found.display_name || found.name || formatModelLabel(path)).replace(/\.gguf$/i, '');
      }
      return formatModelLabel(path);
    },
    [providerMode, providerModels],
  );

  useEffect(() => {
    if (open) {
      const nextModel = currentModel || (initialParams?.model as string) || '';
      const derivedProvider = providerMode
        ? providerOfPath(nextModel || '') || (initialParams?.provider as string) || ''
        : (initialParams?.provider as string) || '';
      const provider = String(derivedProvider).toUpperCase() === 'SC' ? '' : derivedProvider;
      setParams((prev) => ({
        ...defaultParams,
        ...prev,
        ...initialParams,
        model: nextModel || prev.model,
        provider,
      }));
      if (hasModelSettings && initialModelSettings) {
        setModelSettings({ ...MODEL_SETTINGS_DEFAULT, ...initialModelSettings });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, currentModel, initialParams, hasModelSettings, initialModelSettings, providerMode]);

  const handleReset = () => {
    if (readOnly) return;
    setParams({ ...defaultParams, model: params.model });
    if (hasModelSettings) setModelSettings({ ...MODEL_SETTINGS_DEFAULT });
  };

  const handleSave = () => {
    if (readOnly) {
      onClose();
      return;
    }
    onSave(params.model, params);
    if (hasModelSettings && onSaveModelSettings) onSaveModelSettings(modelSettings);
    onClose();
  };

  const labelSx = {
    color: darkFields ? 'rgba(255,255,255,0.85)' : 'rgba(0,0,0,0.87)',
    fontSize: '0.8rem',
    mb: 0.5,
    display: 'block',
  };
  const headerBorder = darkFields ? '1px solid rgba(255,255,255,0.08)' : '1px solid rgba(0,0,0,0.08)';
  const headerTitleColor = darkFields ? 'white' : 'rgba(0,0,0,0.87)';
  const backBtnColor = darkFields ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.65)';
  const backBtnHover = darkFields ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)';
  const mutedCaption = darkFields ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.55)';
  const emptyMenuColor = darkFields ? 'rgba(255,255,255,0.4)' : 'rgba(0,0,0,0.45)';

  const content = (
    <>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          px: 2,
          py: 1.5,
          borderBottom: headerBorder,
          flexShrink: 0,
        }}
      >
        <IconButton size="small" onClick={onClose} sx={{ color: backBtnColor, '&:hover': { bgcolor: backBtnHover } }}>
          <ArrowBackIcon />
        </IconButton>
        <Typography variant="h6" sx={{ color: headerTitleColor, fontSize: '1rem', fontWeight: 600 }}>
          Параметры модели
        </Typography>
      </Box>

      <Box sx={{ px: 2, py: 2, display: 'flex', flexDirection: 'column', gap: 2, flex: 1, minHeight: 0, overflow: 'auto' }}>
        <Box>
          <FormControl variant="outlined" fullWidth size="small" required sx={outlinedSelectSx}>
            <InputLabel htmlFor="model-params-provider">Провайдер</InputLabel>
            <OutlinedInput
              id="model-params-provider"
              label="Провайдер"
              value={providerOptions.find((o) => o.value === params.provider)?.label ?? params.provider}
              readOnly
              placeholder="Выберите провайдера"
              onClick={(e) => {
                if (readOnly) return;
                setProviderAnchor(e.currentTarget);
              }}
              endAdornment={
                <InputAdornment position="end">
                  <ExpandMoreIcon
                    sx={{ ...dropdownChevronSx, transform: Boolean(providerAnchor) ? 'rotate(180deg)' : 'none' }}
                  />
                </InputAdornment>
              }
            />
          </FormControl>
          <Popover
            open={!readOnly && Boolean(providerAnchor)}
            anchorEl={providerAnchor}
            onClose={() => setProviderAnchor(null)}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
            transformOrigin={{ vertical: 'top', horizontal: 'left' }}
            slotProps={{ paper: { sx: getDropdownPopoverPaperSx(providerAnchor, darkFields) } }}
          >
            <Box sx={{ py: 0.5 }}>
              {providerOptions.map((o) => (
                <Box
                  key={o.value}
                  onClick={() => {
                    setParams((p) => {
                      if (!providerMode) return { ...p, provider: o.value };
                      const keepModel = providerOfPath(p.model) === o.value ? p.model : '';
                      return { ...p, provider: o.value, model: keepModel };
                    });
                    setProviderAnchor(null);
                  }}
                  sx={{
                    ...dropdownItemSx,
                    ...getDropdownItemStateSx(darkFields, params.provider === o.value),
                  }}
                >
                  {o.label}
                </Box>
              ))}
              {providerOptions.length === 0 && (
                <Box sx={{ px: 1.5, py: 1, fontSize: '0.78rem', color: emptyMenuColor }}>Нет доступных провайдеров</Box>
              )}
            </Box>
          </Popover>
        </Box>

        <Box>
          <FormControl variant="outlined" fullWidth size="small" required sx={outlinedSelectSx}>
            <InputLabel htmlFor="model-params-model">Модель</InputLabel>
            <OutlinedInput
              id="model-params-model"
              label="Модель"
              value={params.model ? modelLabelOf(params.model) : ''}
              readOnly
              placeholder="Выберите модель"
              onClick={(e) => {
                if (readOnly) return;
                setModelAnchor(e.currentTarget);
              }}
              endAdornment={
                <InputAdornment position="end">
                  <ExpandMoreIcon
                    sx={{ ...dropdownChevronSx, transform: Boolean(modelAnchor) ? 'rotate(180deg)' : 'none' }}
                  />
                </InputAdornment>
              }
            />
          </FormControl>
          <Popover
            open={!readOnly && Boolean(modelAnchor)}
            anchorEl={modelAnchor}
            onClose={() => setModelAnchor(null)}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
            transformOrigin={{ vertical: 'top', horizontal: 'left' }}
            slotProps={{ paper: { sx: getDropdownPopoverPaperSx(modelAnchor, darkFields) } }}
          >
            <Box sx={{ py: 0.5, maxHeight: 260, overflowY: 'auto' }}>
              {providerFilteredModels.map((m) => (
                <Box
                  key={m}
                  onClick={() => {
                    setParams((p) => ({ ...p, model: m }));
                    setModelAnchor(null);
                  }}
                  sx={{
                    ...dropdownItemSx,
                    ...getDropdownItemStateSx(darkFields, params.model === m),
                  }}
                >
                  {modelLabelOf(m)}
                </Box>
              ))}
              {providerFilteredModels.length === 0 && (
                <Box sx={{ px: 1.5, py: 1, fontSize: '0.78rem', color: emptyMenuColor }}>Нет доступных моделей</Box>
              )}
            </Box>
          </Popover>
        </Box>

        {hasModelSettings && (
          <Box>
            <ModelSettingsFields
              value={modelSettings}
              onChange={setModelSettings}
              darkPanel={darkFields}
              readOnly={readOnly}
              compact
            />
          </Box>
        )}

        {/* Legacy token/temperature block — hidden when fine-tune settings present */}
        {!hasModelSettings && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            <Box>
              <Typography sx={labelSx}>Контекст (токены)</Typography>
              <OutlinedInput
                fullWidth
                size="small"
                value={params.contextTokens}
                onChange={(e) => setParams((p) => ({ ...p, contextTokens: e.target.value }))}
                sx={formFieldInputSx}
                disabled={readOnly}
              />
            </Box>
            <Box>
              <Typography sx={labelSx}>Выход (токены)</Typography>
              <OutlinedInput
                fullWidth
                size="small"
                value={params.outputTokens}
                onChange={(e) => setParams((p) => ({ ...p, outputTokens: e.target.value }))}
                sx={formFieldInputSx}
                disabled={readOnly}
              />
            </Box>
            <Box>
              <Typography sx={{ ...labelSx, minHeight: 28, display: 'flex', alignItems: 'center' }}>
                Температура — {params.temperature.toFixed(2)}
              </Typography>
              <Slider
                size="small"
                value={params.temperature}
                min={0}
                max={2}
                step={0.01}
                onChange={(_, v) => setParams((p) => ({ ...p, temperature: v as number }))}
                sx={{ color: '#2196f3' }}
                disabled={readOnly}
              />
            </Box>
            <Box>
              <Typography sx={{ ...labelSx, minHeight: 28, display: 'flex', alignItems: 'center' }}>
                Top P — {params.topP.toFixed(2)}
              </Typography>
              <Slider
                size="small"
                value={params.topP}
                min={0}
                max={1}
                step={0.01}
                onChange={(_, v) => setParams((p) => ({ ...p, topP: v as number }))}
                sx={{ color: '#2196f3' }}
                disabled={readOnly}
              />
            </Box>
            <Box>
              <Typography sx={{ ...labelSx, minHeight: 28, display: 'flex', alignItems: 'center' }}>
                Штраф за частоту — {params.frequencyPenalty.toFixed(2)}
              </Typography>
              <Slider
                size="small"
                value={params.frequencyPenalty}
                min={0}
                max={2}
                step={0.01}
                onChange={(_, v) => setParams((p) => ({ ...p, frequencyPenalty: v as number }))}
                sx={{ color: '#2196f3' }}
                disabled={readOnly}
              />
            </Box>
            <Box>
              <Typography sx={{ ...labelSx, minHeight: 28, display: 'flex', alignItems: 'center' }}>
                Штраф за присутствие — {params.presencePenalty.toFixed(2)}
              </Typography>
              <Slider
                size="small"
                value={params.presencePenalty}
                min={0}
                max={2}
                step={0.01}
                onChange={(_, v) => setParams((p) => ({ ...p, presencePenalty: v as number }))}
                sx={{ color: '#2196f3' }}
                disabled={readOnly}
              />
            </Box>
          </Box>
        )}
      </Box>

      <Box
        sx={{
          borderTop: headerBorder,
          px: 2,
          py: 1.5,
          display: 'flex',
          flexDirection: 'column',
          gap: 1,
          flexShrink: 0,
        }}
      >
        {readOnly ? (
          <Typography variant="caption" sx={{ color: mutedCaption, textAlign: 'center', px: 1 }}>
            Параметры модели задаёт владелец. Вам они видны только для просмотра.
          </Typography>
        ) : (
          <>
            <Button
              variant="outlined"
              fullWidth
              startIcon={<RestoreIcon sx={AGENT_CONSTRUCTOR_SAVE_ICON_SX} />}
              onClick={handleReset}
              sx={restoreButtonSx}
            >
              Восстановить настройки
            </Button>
            <Button
              variant="contained"
              fullWidth
              startIcon={<SaveIcon sx={AGENT_CONSTRUCTOR_SAVE_ICON_SX} />}
              onClick={handleSave}
              sx={saveButtonSx}
            >
              Сохранить настройки
            </Button>
          </>
        )}
      </Box>
    </>
  );

  if (variant === 'panel') {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          overflow: 'hidden',
          background: panelBg,
          color: panelChrome.fg,
        }}
      >
        {content}
      </Box>
    );
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: {
          background: panelBg,
          color: panelChrome.fg,
          borderRadius: 2,
          border: headerBorder,
          maxHeight: '90vh',
        },
      }}
    >
      <DialogContent sx={{ p: 0, overflow: 'auto' }}>{content}</DialogContent>
    </Dialog>
  );
}
