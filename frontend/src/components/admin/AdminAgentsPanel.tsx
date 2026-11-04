import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box,
  Chip,
  CircularProgress,
  IconButton,
  InputAdornment,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import RefreshIcon from '@mui/icons-material/Refresh';
import SettingsOutlinedIcon from '@mui/icons-material/SettingsOutlined';
import { useNavigate } from 'react-router-dom';
import {
  fetchAllAgentsForAdmin,
  type UserAgentListItem,
} from '../../utils/fetchMergedUserAgents';
import { getAdminCardSx } from './adminSurfaces';
import { openAgentInConstructor } from '../../utils/openAgentConstructorNav';

interface AdminAgentsPanelProps {
  isDarkMode?: boolean;
}

/** Все агенты платформы (admin/all) — таблица для модерации. */
export default function AdminAgentsPanel({ isDarkMode = true }: AdminAgentsPanelProps) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [agents, setAgents] = useState<UserAgentListItem[]>([]);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const all = await fetchAllAgentsForAdmin();
      setAgents(all);
    } catch {
      setAgents([]);
      setError('Не удалось загрузить список агентов');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter((a) => {
      const hay = [
        a.name,
        a.description,
        a.author_name,
        a.author_full_name,
        a.author_id,
        String(a.id),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return hay.includes(q);
    });
  }, [agents, search]);

  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const cardSx = getAdminCardSx(isDarkMode);
  const headColor = isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, minHeight: 0, flex: 1 }}>
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        <TextField
          size="small"
          placeholder="Поиск по имени, автору, id…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ flex: '1 1 240px', minWidth: 200 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" sx={{ opacity: 0.5 }} />
              </InputAdornment>
            ),
          }}
        />
        <Typography variant="body2" sx={{ color: muted, ml: 'auto' }}>
          {loading ? '…' : `${filtered.length} из ${agents.length}`}
        </Typography>
        <IconButton size="small" onClick={() => void load()} disabled={loading} title="Обновить">
          <RefreshIcon fontSize="small" />
        </IconButton>
      </Box>

      <Box sx={{ ...cardSx, p: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', minHeight: 280 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
            <CircularProgress size={28} />
          </Box>
        ) : error ? (
          <Typography sx={{ p: 3, color: 'error.main' }}>{error}</Typography>
        ) : filtered.length === 0 ? (
          <Typography sx={{ p: 3, color: muted }}>Агенты не найдены</Typography>
        ) : (
          <Box sx={{ overflow: 'auto', maxHeight: 'min(62vh, 640px)' }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  {['ID', 'Название', 'Автор', 'Видимость', 'Доступ', ''].map((h) => (
                    <TableCell
                      key={h || 'actions'}
                      sx={{
                        bgcolor: isDarkMode ? 'rgba(30,30,34,0.95)' : 'rgba(250,250,250,0.95)',
                        color: headColor,
                        fontSize: '0.72rem',
                        fontWeight: 600,
                        borderBottom: isDarkMode
                          ? '1px solid rgba(255,255,255,0.08)'
                          : '1px solid rgba(0,0,0,0.08)',
                      }}
                    >
                      {h}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {filtered.map((a) => (
                  <TableRow key={a.id} hover>
                    <TableCell sx={{ fontSize: '0.8rem', opacity: 0.7 }}>{a.id}</TableCell>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600} noWrap sx={{ maxWidth: 280 }}>
                        {a.name || 'Без имени'}
                      </Typography>
                      {a.description ? (
                        <Typography
                          variant="caption"
                          sx={{ color: muted, display: 'block' }}
                          noWrap
                        >
                          {a.description}
                        </Typography>
                      ) : null}
                    </TableCell>
                    <TableCell sx={{ fontSize: '0.8rem' }}>
                      {a.author_full_name || a.author_name || a.author_id || '—'}
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={a.is_public ? 'Публичный' : 'Приватный'}
                        color={a.is_public ? 'success' : 'default'}
                        variant="outlined"
                        sx={{ height: 22, fontSize: '0.7rem' }}
                      />
                    </TableCell>
                    <TableCell sx={{ fontSize: '0.8rem', opacity: 0.75 }}>
                      {a.my_permission || '—'}
                    </TableCell>
                    <TableCell align="right" sx={{ width: 48, px: 0.5 }}>
                      <IconButton
                        size="small"
                        title="Открыть настройки агента"
                        aria-label="Открыть настройки агента"
                        onClick={() => openAgentInConstructor(a.id, navigate)}
                      >
                        <SettingsOutlinedIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </Box>
    </Box>
  );
}
