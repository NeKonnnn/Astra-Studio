import React from 'react';
import {
  Box,
  Typography,
  Switch,
  IconButton,
  Tooltip,
  Accordion,
  AccordionSummary,
  AccordionDetails,
} from '@mui/material';
import { HelpOutline as HelpOutlineIcon, ExpandMore as ExpandMoreIcon, Settings as SettingsIcon } from '@mui/icons-material';
import {
  MODEL_SETTINGS_ACCORDION_SX,
  MODEL_SETTINGS_GRID_SX,
  MODEL_SETTINGS_GRID_COMPACT_SX,
  MODEL_SETTINGS_LABEL_WRAPPER_SX,
  MODEL_SETTINGS_HELP_ICON_BUTTON_SX,
  MODEL_SETTINGS_INPUT_SX,
  MODEL_SETTINGS_SWITCH_ROW_COMPACT_SX,
  AGENT_CONSTRUCTOR_FIELD_FONT_SIZE,
  AGENT_CONSTRUCTOR_FIELD_LINE_HEIGHT,
  MODEL_SETTINGS_DEFAULT,
  MODEL_SETTINGS_MAX_DEFAULT,
  modelSettingsSwitchSx,
  type ModelSettingsState,
  type ModelSettingsMaxValues,
} from '../constants/modelSettingsStyles';
import { getFormFieldInputSx } from '../constants/menuStyles';
import ClampedNumberField from './ClampedNumberField';

const TOOLTIPS: Record<string, string> = {
  context_size: 'Максимальное количество токенов, которые модель может использовать для понимания контекста. Больше значение = больше контекста, но больше потребление памяти.',
  output_tokens: 'Максимальное количество токенов, которые модель может сгенерировать в ответе. Увеличение позволяет получать более длинные ответы.',
  temperature: 'Контролирует случайность генерации. Низкие значения (0.1-0.5) — более точные ответы, высокие (0.8-1.5) — более креативные.',
  top_p: 'Работает совместно с top-k. Высокое значение (0.95) — разнообразнее текст, низкое (0.5) — более сфокусированный.',
  repeat_penalty: 'Запрещает модели повторять недавно использованные фразы и предложения. Значения выше 1.0 уменьшают повторения',
  top_k: 'Ограничивает выборку k наиболее вероятными токенами. Больше (100+) — разнообразнее, меньше (10-20) — консервативнее.',
  min_p: 'Минимальная вероятность токена относительно наиболее вероятного. Альтернатива top_p/top_k.',
  frequency_penalty: 'Чем чаще слово повторяется, тем сильнее модель избегает его использовать. Высокое значение сильнее наказывает за повторения',
  presence_penalty: 'Если слово уже встречалось хотя бы раз, модель старается использовать новые слова. При 0 отключено.',
  use_gpu: 'Использование GPU для ускорения. Требует CUDA.',
  streaming: 'Показывать ответ по мере генерации (токен за токеном).',
};

interface ModelSettingsFieldsProps {
  value: ModelSettingsState;
  onChange: (next: ModelSettingsState) => void;
  maxValues?: Partial<ModelSettingsMaxValues>;
  /** Заводские значения для подскзок "по умолчанию". Приходят с сервера из llm_settings.json. */
  defaults?: Partial<ModelSettingsState>;
  /** Показывать аккордеон «Тонкая настройка» (иначе только поля) */
  accordion?: boolean;
  /** Класс/стиль для тёмной панели (конструктор агентов) — тогда лейблы белые */
  darkPanel?: boolean;
  /** Компактная сетка: уже колонки, несколько полей в строке (в модалке выбора модели в конструкторе) */
  compact?: boolean;
  /** Только просмотр: аккордеон открывается, поля и переключатели заблокированы. */
  readOnly?: boolean;
  /** Контролируемое раскрытие аккордеона «Тонкая настройка». */
  fineTuneExpanded?: boolean;
  onFineTuneExpandedChange?: (expanded: boolean) => void;
}

const INTEGER_KEYS = new Set<keyof ModelSettingsState>(['context_size', 'output_tokens', 'top_k']);

function numField(
  key: keyof ModelSettingsState,
  label: string,
  value: number,
  onChange: (v: number) => void,
  min: number,
  max: number,
  step: number,
  defaultVal: number,
  tooltip: string,
  darkPanel: boolean,
  readOnly: boolean,
) {
  return (
    <Box key={key}>
      <ClampedNumberField
        label={
          <Box sx={MODEL_SETTINGS_LABEL_WRAPPER_SX} component="span">
            {label}
            <Tooltip title={tooltip} arrow>
              <IconButton size="small" sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX} onClick={(e: React.MouseEvent) => e.stopPropagation()}>
                <HelpOutlineIcon
                  fontSize="small"
                  sx={{ color: darkPanel ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)' }}
                />
              </IconButton>
            </Tooltip>
          </Box>
        }
        value={value}
        onValueChange={onChange}
        min={min}
        max={max}
        step={step}
        defaultValue={defaultVal}
        integer={INTEGER_KEYS.has(key)}
        disabled={readOnly}
        fullWidth
        size="small"
        sx={darkPanel ? { ...MODEL_SETTINGS_INPUT_SX } : getFormFieldInputSx(false)}
      />
    </Box>
  );
}

export default function ModelSettingsFields({
  value,
  onChange,
  maxValues: maxValuesProp,
  defaults: defaultsProp,
  accordion = true,
  darkPanel = false,
  compact = false,
  readOnly = false,
  fineTuneExpanded,
  onFineTuneExpandedChange,
}: ModelSettingsFieldsProps) {
  const maxValues = { ...MODEL_SETTINGS_MAX_DEFAULT, ...maxValuesProp };
  const defaults = { ...MODEL_SETTINGS_DEFAULT, ...defaultsProp };
  const set = (patch: Partial<ModelSettingsState>) => {
    if (readOnly) return;
    onChange({ ...value, ...patch });
  };

  const gridContent = (
    <Box sx={compact ? MODEL_SETTINGS_GRID_COMPACT_SX : MODEL_SETTINGS_GRID_SX}>
      {numField(
        'context_size',
        'Размер контекста',
        value.context_size,
        (v) => set({ context_size: v }),
        512,
        maxValues.context_size,
        512,
        defaults.context_size,
        TOOLTIPS.context_size,
        darkPanel,
        readOnly,
      )}
      {numField(
        'output_tokens',
        'Лимит токенов ответа',
        value.output_tokens,
        (v) => set({ output_tokens: v }),
        64,
        maxValues.output_tokens,
        64,
        defaults.output_tokens,
        TOOLTIPS.output_tokens,
        darkPanel,
        readOnly,
      )}
      {numField(
        'temperature',
        'Температура',
        value.temperature,
        (v) => set({ temperature: v }),
        0.1,
        maxValues.temperature,
        0.1,
        defaults.temperature,
        TOOLTIPS.temperature,
        darkPanel,
        readOnly,
      )}
      {numField(
        'top_p',
        'Top-p',
        value.top_p,
        (v) => set({ top_p: v }),
        0.1,
        maxValues.top_p,
        0.05,
        defaults.top_p,
        TOOLTIPS.top_p,
        darkPanel,
        readOnly,
      )}
      {numField(
        'repeat_penalty',
        'Штраф за повторы',
        value.repeat_penalty,
        (v) => set({ repeat_penalty: v }),
        1.0,
        maxValues.repeat_penalty,
        0.05,
        defaults.repeat_penalty,
        TOOLTIPS.repeat_penalty,
        darkPanel,
        readOnly,
      )}
      {numField(
        'top_k',
        'Top-k',
        value.top_k,
        (v) => set({ top_k: v }),
        1,
        maxValues.top_k,
        1,
        defaults.top_k,
        TOOLTIPS.top_k,
        darkPanel,
        readOnly,
      )}
      {numField(
        'min_p',
        'Min-p',
        value.min_p,
        (v) => set({ min_p: v }),
        0,
        maxValues.min_p,
        0.01,
        defaults.min_p,
        TOOLTIPS.min_p,
        darkPanel,
        readOnly,
      )}
      {numField(
        'frequency_penalty',
        'Штраф за частоту',
        value.frequency_penalty,
        (v) => set({ frequency_penalty: v }),
        0,
        maxValues.frequency_penalty,
        0.1,
        defaults.frequency_penalty,
        TOOLTIPS.frequency_penalty,
        darkPanel,
        readOnly,
      )}
      {numField(
        'presence_penalty',
        'Штраф за присутствие',
        value.presence_penalty,
        (v) => set({ presence_penalty: v }),
        0,
        maxValues.presence_penalty,
        0.1,
        defaults.presence_penalty,
        TOOLTIPS.presence_penalty,
        darkPanel,
        readOnly,
      )}
    </Box>
  );

  const switchRow = (label: string, tooltip: string, checked: boolean, onToggle: (v: boolean) => void) => (
    <Box key={label} sx={MODEL_SETTINGS_SWITCH_ROW_COMPACT_SX}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0, pr: 1 }}>
        <Typography
          variant="caption"
          sx={{
            color: darkPanel ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.87)',
            fontSize: '0.78rem',
            lineHeight: 1.3,
          }}
        >
          {label}
        </Typography>
        <Tooltip title={tooltip} arrow>
          <HelpOutlineIcon
            sx={{
              fontSize: 12,
              color: darkPanel ? 'rgba(255,255,255,0.25)' : 'rgba(0,0,0,0.45)',
              cursor: 'help',
              flexShrink: 0,
            }}
          />
        </Tooltip>
      </Box>
      <Switch
        checked={checked}
        disabled={readOnly}
        sx={modelSettingsSwitchSx(darkPanel)}
        onChange={(e) => {
          if (readOnly) return;
          onToggle(e.target.checked);
        }}
      />
    </Box>
  );

  const switchList = (
    <Box sx={{ mt: 1.5, display: 'flex', flexDirection: 'column', gap: 0.5 }}>
      {switchRow('Использовать GPU', TOOLTIPS.use_gpu, value.use_gpu, (v) => set({ use_gpu: v }))}
      {switchRow('Потоковая генерация', TOOLTIPS.streaming, value.streaming, (v) => set({ streaming: v }))}
    </Box>
  );

  if (accordion) {
    return (
      <>
        <Accordion
          {...(typeof fineTuneExpanded === 'boolean'
            ? {
                expanded: fineTuneExpanded,
                onChange: (_: React.SyntheticEvent, next: boolean) => {
                  onFineTuneExpandedChange?.(next);
                },
              }
            : {})}
          sx={{
            ...MODEL_SETTINGS_ACCORDION_SX,
            ...(darkPanel
              ? {
                  bgcolor: 'rgba(0,0,0,0.2)',
                  color: 'white',
                  '& .MuiAccordionSummary-root': { color: 'rgba(255,255,255,0.9)' },
                  '& .MuiAccordionDetails-root': { color: 'rgba(255,255,255,0.9)' },
                }
              : {
                  bgcolor: 'transparent',
                  color: 'rgba(0,0,0,0.87)',
                  '& .MuiAccordionSummary-root': { color: 'rgba(0,0,0,0.87)' },
                  '& .MuiAccordionDetails-root': { color: 'rgba(0,0,0,0.87)' },
                }),
          }}
        >
          <AccordionSummary expandIcon={<ExpandMoreIcon sx={{ color: darkPanel ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.55)' }} />}>
            <Box sx={MODEL_SETTINGS_LABEL_WRAPPER_SX}>
              <SettingsIcon sx={{ color: darkPanel ? 'rgba(255,255,255,0.85)' : 'rgba(0,0,0,0.65)' }} />
              <Typography
                sx={{
                  fontSize: AGENT_CONSTRUCTOR_FIELD_FONT_SIZE,
                  lineHeight: AGENT_CONSTRUCTOR_FIELD_LINE_HEIGHT,
                  fontWeight: 500,
                  color: darkPanel ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.87)',
                }}
              >
                Тонкая настройка
              </Typography>
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            {gridContent}
            {switchList}
          </AccordionDetails>
        </Accordion>
      </>
    );
  }

  return (
    <Box>
      {gridContent}
      {switchList}
    </Box>
  );
}

export type { ModelSettingsState, ModelSettingsMaxValues };
