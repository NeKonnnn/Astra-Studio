import React, { useEffect, useMemo, useState } from 'react';
import {
  Autocomplete,
  Box,
  IconButton,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import type { SxProps, Theme } from '@mui/material/styles';
import { fetchAgentTags, subscribeAgentTagsCache, type AgentTag } from '../../constants/AgentTags';
import {
  DROPDOWN_PAPER_MARGIN_TOP,
  getDropdownItemSx,
  getDropdownItemStateSx,
  getDropdownPanelSx,
} from '../../constants/menuStyles';

/**
 * Тег в значении поля. У существующего есть id, у только что набранного -
 * только имя: id появится после сохранения карточки (backend создаст тег
 * через new_tags или привяжет существующий с таким именем).
 */
export interface AgentTagValue {
  id?: number;
  name: string;
}

/** Ключ сравнения тегов. В freeSolo MUI передаёт сюда и строки (набранный текст). */
const tagKey = (t: string | AgentTagValue | null | undefined): string =>
  (typeof t === 'string' ? t : t?.name || '').trim().toLowerCase();

/** Справочник тегов, один запрос на компонент (кэш - в fetchAgentTags). */
export function useAgentTags(): AgentTag[] {
  const [tags, setTags] = useState<AgentTag[]>([]);
  useEffect(() => {
    let alive = true;
    const apply = (list: AgentTag[]) => {
      if (alive) setTags(list);
    };
    void fetchAgentTags().then(apply);
    // После save агента кэш сбрасывается — подтягиваем новые теги без перезагрузки.
    const unsubscribe = subscribeAgentTagsCache(() => {
      void fetchAgentTags(true).then(apply);
    });
    return () => {
      alive = false;
      unsubscribe();
    };
  }, []);
  return tags;
}

/** agent.tags из API (объекты {id, name} или строки) -> значение поля. */
export function normalizeAgentTags(raw: unknown): AgentTagValue[] {
  if (!Array.isArray(raw)) return [];
  const out: AgentTagValue[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    let name = '';
    let id: number | undefined;
    if (typeof item === 'string') {
      name = item.trim();
    } else if (item && typeof item === 'object') {
      const rec = item as Record<string, unknown>;
      name = String(rec.name ?? '').trim();
      const n = Number(rec.id);
      if (Number.isFinite(n) && n > 0) id = Math.trunc(n);
    }
    const key = name.toLowerCase();
    if (!name || seen.has(key)) continue;
    seen.add(key);
    out.push(id !== undefined ? { id, name } : { name });
  }
  return out;
}

interface AgentTagsFieldProps {
  value: AgentTagValue[];
  onChange: (value: AgentTagValue[]) => void;
  label: string;
  placeholder?: string;
  /** Текст подсказки в «?» справа от поля (как «Разрешённые инструменты» в skills). */
  help?: string;
  /** Заголовок во всплывающей подсказке; по умолчанию — label. */
  helpTitle?: string;
  required?: boolean;
  readOnly?: boolean;
  /** true - новое слово создаёт тег (карточка); false - только из справочника (обязательные). */
  allowCreate?: boolean;
  /** Тёмные поля конструктора — стиль выпадающего списка как у «Категория». */
  darkFields?: boolean;
  sx?: SxProps<Theme>;
}

export default function AgentTagsField({
  value,
  onChange,
  label,
  placeholder,
  help,
  helpTitle,
  required = false,
  readOnly = false,
  allowCreate = true,
  darkFields = true,
  sx,
}: AgentTagsFieldProps) {
  const catalog = useAgentTags();
  const options = useMemo<AgentTagValue[]>(
    () => catalog.map((t) => ({ id: t.id, name: t.name })),
    [catalog],
  );
  const dropdownItemSx = useMemo(() => getDropdownItemSx(darkFields), [darkFields]);
  const paperSx = useMemo(
    () => ({
      ...getDropdownPanelSx(darkFields),
      mt: DROPDOWN_PAPER_MARGIN_TOP,
    }),
    [darkFields],
  );

  const handleChange = (_event: unknown, next: Array<string | AgentTagValue>) => {
    const out: AgentTagValue[] = [];
    const seen = new Set<string>();
    for (const item of next) {
      const v: AgentTagValue = typeof item === 'string' ? { name: item.trim() } : item;
      if (!v || !v.name) continue;
      const key = v.name.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      // Набрали имя существующего тега - берём его id, а не плодим дубль:
      // имя в справочнике уникально, backend всё равно привязал бы его же.
      const known = options.find((o) => o.name.toLowerCase() === key);
      out.push(known || v);
    }
    onChange(out);
  };

  // Управляемый ввод: запятая - конец тега («ВНД, Презентации» даёт два
  // чипа без Enter), а набранное при уходе из поля не теряется (autoSelect).
  const [inputValue, setInputValue] = useState('');
  const handleInputChange = (_event: unknown, next: string, reason: string) => {
    if (reason === 'reset') {
      setInputValue('');
      return;
    }
    if (allowCreate && next.includes(',')) {
      const parts = next.split(',');
      const rest = parts.pop() || '';
      const fresh = parts.map((p) => p.trim()).filter((p) => p.length > 0);
      if (fresh.length) handleChange(null, [...value, ...fresh]);
      setInputValue(rest.replace(/^\s+/, ''));
      return;
    }
    setInputValue(next);
  };

  const field = (
    <Autocomplete<AgentTagValue, true, false, boolean>
      multiple
      freeSolo={allowCreate}
      autoSelect={allowCreate}
      inputValue={inputValue}
      onInputChange={handleInputChange}
      size="small"
      disabled={readOnly}
      options={options}
      value={value}
      onChange={handleChange}
      getOptionLabel={(option) => (typeof option === 'string' ? option : option.name)}
      isOptionEqualToValue={(option, selected) => tagKey(option) === tagKey(selected)}
      filterSelectedOptions
      noOptionsText={
        <Typography
          variant="caption"
          sx={{ display: 'block', px: 1.5, py: 1, opacity: 0.6 }}
        >
          {allowCreate
            ? 'Введите имя и нажмите Enter или запятую'
            : options.length === 0
              ? 'Тегов пока нет: задайте их в карточках агентов и сохраните'
              : 'Нет тегов'}
        </Typography>
      }
      slotProps={{
        paper: { sx: paperSx },
        listbox: { sx: { py: 0.5, px: 0 } },
      }}
      renderOption={(props, option, { selected }) => {
        const { key, ...rest } = props as typeof props & { key?: React.Key };
        const hoverBg = darkFields
          ? 'rgba(255,255,255,0.10)'
          : 'rgba(0,0,0,0.06)';
        return (
          <Box
            component="li"
            key={key}
            {...rest}
            sx={{
              ...dropdownItemSx,
              ...getDropdownItemStateSx(darkFields, selected),
              listStyle: 'none',
              display: 'flex',
              alignItems: 'center',
              minHeight: 'auto',
              '&.MuiAutocomplete-option': {
                minHeight: 'auto',
              },
              '&.MuiAutocomplete-option.Mui-focused': {
                bgcolor: hoverBg,
              },
            }}
          >
            {option.name}
          </Box>
        );
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          label={label}
          placeholder={value.length ? undefined : placeholder}
          variant="outlined"
          size="small"
          required={required}
          sx={[
            ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
            { '& .MuiFormLabel-asterisk': { color: '#f44336' } },
          ] as SxProps<Theme>}
        />
      )}
    />
  );

  if (!help) {
    return <Box>{field}</Box>;
  }

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5, width: '100%' }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>{field}</Box>
      <Tooltip
        title={
          <Box sx={{ maxWidth: 300 }}>
            <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 0.5 }}>
              {helpTitle || label}
            </Typography>
            <Typography variant="body2" sx={{ opacity: 0.95 }}>
              {help}
            </Typography>
          </Box>
        }
        arrow
        placement="top"
      >
        <IconButton
          size="small"
          aria-label={`Справка: ${label}`}
          sx={{
            mt: 0.75,
            p: 0.35,
            color: 'inherit',
            opacity: 0.45,
            '&:hover': { opacity: 0.75, bgcolor: 'transparent' },
          }}
        >
          <HelpOutlineIcon sx={{ fontSize: 16 }} />
        </IconButton>
      </Tooltip>
    </Box>
  );
}
