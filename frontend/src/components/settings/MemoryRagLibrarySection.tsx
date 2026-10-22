import React, { useState, useEffect, useMemo } from 'react';
import { useTheme } from '@mui/material/styles';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  IconButton,
  Tooltip,
  Popover,
} from '@mui/material';
import {
  LibraryBooks as LibraryBooksIcon,
  HelpOutline as HelpOutlineIcon,
  ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import MemoryRagLibraryModal from '../MemoryRagLibraryModal';
import { authFetch, getApiUrl, getAuthFetchHeaders } from '../../config/api';
import {
  getDropdownPopoverPaperSx,
  getDropdownItemSx,
  getDropdownTriggerButtonSx,
  getDropdownTriggerTextSx,
  getDropdownChevronSx,
  getDropdownItemStateSx,
} from '../../constants/menuStyles';

type Variant = 'prominent' | 'inline';

interface Props {
  /** prominent — отдельная карточка сверху; inline — внутри другой карточки */
  variant?: Variant;
}

/** Стратегия поиска Библиотеки — своя, отдельно от проектов и агентов. */
type MemoryStrategy = 'auto' | 'hybrid' | 'vector' | 'lexical' | 'graph';

const MEMORY_STRATEGY_OPTIONS: { value: MemoryStrategy; label: string }[] = [
  { value: 'auto', label: 'Автоматически' },
  { value: 'hybrid', label: 'Гибридный' },
  { value: 'vector', label: 'Векторный' },
  { value: 'lexical', label: 'Лексический' },
  { value: 'graph', label: 'Графовый' },
];

function normalizeMemoryStrategy(raw: unknown): MemoryStrategy {
  const s = String(raw || 'auto').trim().toLowerCase();
  return (MEMORY_STRATEGY_OPTIONS.some((o) => o.value === s) ? s : 'auto') as MemoryStrategy;
}

const LIBRARY_HELP =
  'Общие файлы для любого чата (не привязаны к проекту или агенту). Загрузите PDF, Word, Excel, TXT и включите переключатель — либо кнопку «Общий RAG» в чате.';

export default function MemoryRagLibrarySection({ variant = 'prominent' }: Props) {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const dropdownTriggerSx = useMemo(() => getDropdownTriggerButtonSx(isDarkMode), [isDarkMode]);
  const dropdownTriggerTextSx = useMemo(() => getDropdownTriggerTextSx(isDarkMode), [isDarkMode]);
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(isDarkMode), [isDarkMode]);
  const [memoryRagModalOpen, setMemoryRagModalOpen] = useState(false);
  const [memoryStrategy, setMemoryStrategy] = useState<MemoryStrategy>('auto');
  const [strategySaving, setStrategySaving] = useState(false);
  const [strategyPopoverAnchor, setStrategyPopoverAnchor] = useState<HTMLElement | null>(null);

  // Стратегия Библиотеки — общий ключ настроек, от скоупа project/agent не зависит.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await authFetch(getApiUrl('/api/rag/settings'), {
          headers: getAuthFetchHeaders(),
        });
        if (!r.ok) return;
        const data = await r.json();
        if (!cancelled) setMemoryStrategy(normalizeMemoryStrategy(data?.rag_memory_strategy));
      } catch {
        // настройки недоступны — остаётся 'auto'
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const saveMemoryStrategy = async (next: MemoryStrategy) => {
    setMemoryStrategy(next);
    setStrategySaving(true);
    try {
      await authFetch(getApiUrl('/api/rag/settings'), {
        method: 'PUT',
        headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ rag_memory_strategy: next }),
      });
    } catch {
      // молча: следующий заход перечитает актуальное значение
    } finally {
      setStrategySaving(false);
    }
  };

  const title = (
    <Typography
      variant={variant === 'prominent' ? 'h6' : 'subtitle2'}
      gutterBottom
      sx={{ display: 'flex', alignItems: 'center', gap: 1, fontWeight: variant === 'prominent' ? undefined : 500 }}
    >
      <LibraryBooksIcon color="primary" fontSize={variant === 'prominent' ? 'medium' : 'small'} />
      Общая библиотека документов
      <Tooltip title={LIBRARY_HELP} arrow>
        <IconButton
          size="small"
          sx={{
            ml: 0.5,
            opacity: 0.7,
            '&:hover': {
              opacity: 1,
              '& .MuiSvgIcon-root': {
                color: 'primary.main',
              },
            },
          }}
          aria-label="Справка: общая библиотека документов"
        >
          <HelpOutlineIcon fontSize="small" color="action" />
        </IconButton>
      </Tooltip>
    </Typography>
  );

  const inner = (
    <>
      {title}
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center' }}>
        <Button
          variant="contained"
          size="medium"
          startIcon={<LibraryBooksIcon />}
          onClick={() => setMemoryRagModalOpen(true)}
          sx={{
            px: 2.25,
            py: 1,
            borderRadius: 2.5,
            textTransform: 'none',
            fontWeight: 600,
            boxShadow: '0 10px 24px rgba(33,150,243,0.22)',
            background: 'linear-gradient(135deg, #1976d2 0%, #42a5f5 100%)',
            '&:hover': {
              boxShadow: '0 14px 28px rgba(33,150,243,0.28)',
              background: 'linear-gradient(135deg, #1565c0 0%, #1e88e5 100%)',
            },
          }}
        >
          Общая библиотека
        </Button>
      </Box>
      <Box
        sx={{
          mt: 2,
          display: 'flex',
          flexDirection: { xs: 'column', sm: 'row' },
          justifyContent: 'space-between',
          alignItems: { xs: 'flex-start', sm: 'center' },
          gap: 1.5,
        }}
      >
        <Box>
          <Typography variant="body1" sx={{ display: 'flex', alignItems: 'center', gap: 0.5, fontWeight: 500 }}>
            Стратегия поиска по библиотеке
            <Tooltip title="Способ поиска только по документам общей библиотеки." arrow>
              <IconButton
                size="small"
                sx={{
                  p: 0,
                  opacity: 0.7,
                  '&:hover': {
                    opacity: 1,
                    '& .MuiSvgIcon-root': {
                      color: 'primary.main',
                    },
                  },
                }}
                onClick={(e: React.MouseEvent<HTMLElement>) => e.stopPropagation()}
              >
                <HelpOutlineIcon fontSize="small" color="action" />
              </IconButton>
            </Tooltip>
          </Typography>
          <Typography variant="caption" color="text.secondary" display="block">
            Только для документов библиотеки. У проектов и агентов стратегия своя.
          </Typography>
        </Box>
        <Box sx={{ minWidth: 280, width: { xs: '100%', sm: 'auto' } }}>
          <Box
            onClick={(e: React.MouseEvent<HTMLElement>) => !strategySaving && setStrategyPopoverAnchor(e.currentTarget)}
            sx={{
              ...dropdownTriggerSx,
              opacity: strategySaving ? 0.7 : 1,
              pointerEvents: strategySaving ? 'none' : 'auto',
            }}
          >
            <Typography sx={dropdownTriggerTextSx}>
              {MEMORY_STRATEGY_OPTIONS.find((option) => option.value === memoryStrategy)?.label ?? 'Автоматически'}
            </Typography>
            <ExpandMoreIcon
              sx={{
                ...dropdownChevronSx,
                transform: strategyPopoverAnchor ? 'rotate(180deg)' : 'none',
              }}
            />
          </Box>
        </Box>
        <Popover
          open={Boolean(strategyPopoverAnchor)}
          anchorEl={strategyPopoverAnchor}
          onClose={() => setStrategyPopoverAnchor(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
          slotProps={{ paper: { sx: getDropdownPopoverPaperSx(strategyPopoverAnchor, isDarkMode) } }}
        >
          <Box sx={{ py: 0.5 }}>
            {MEMORY_STRATEGY_OPTIONS.map((option) => (
              <Box
                key={option.value}
                onClick={() => {
                  void saveMemoryStrategy(option.value);
                  setStrategyPopoverAnchor(null);
                }}
                sx={{
                  ...dropdownItemSx,
                  ...getDropdownItemStateSx(isDarkMode, memoryStrategy === option.value),
                }}
              >
                {option.label}
              </Box>
            ))}
          </Box>
        </Popover>
      </Box>
      <MemoryRagLibraryModal open={memoryRagModalOpen} onClose={() => setMemoryRagModalOpen(false)} />
    </>
  );

  if (variant === 'inline') {
    return (
      <Box
        sx={{
          mb: 2,
          p: 2,
          borderRadius: 1,
          bgcolor: 'action.hover',
          border: 1,
          borderColor: 'divider',
        }}
      >
        {inner}
      </Box>
    );
  }

  return (
    <Card sx={{ mb: 3 }}>
      <CardContent>{inner}</CardContent>
    </Card>
  );
}
