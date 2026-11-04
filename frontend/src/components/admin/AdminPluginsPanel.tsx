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
import { fetchPluginsResponse } from '../../plugins/api';
import type { PluginPublic } from '../../plugins/types';
import { getAdminCardSx } from './adminSurfaces';

interface AdminPluginsPanelProps {
  isDarkMode?: boolean;
}

/** Каталог HTTP-плагинов платформы (+ health). */
export default function AdminPluginsPanel({ isDarkMode = true }: AdminPluginsPanelProps) {
  const [loading, setLoading] = useState(true);
  const [plugins, setPlugins] = useState<PluginPublic[]>([]);
  const [platformEnabled, setPlatformEnabled] = useState(true);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const catalog = await fetchPluginsResponse(true);
      setPlugins(catalog.plugins || []);
      setPlatformEnabled(catalog.enabled !== false);
    } catch {
      setPlugins([]);
      setError('Не удалось загрузить плагины');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return plugins;
    return plugins.filter((p) => {
      const hay = [p.id, p.display_name, p.description, p.category, ...(p.tags || [])]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return hay.includes(q);
    });
  }, [plugins, search]);

  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const cardSx = getAdminCardSx(isDarkMode);
  const headColor = isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, minHeight: 0, flex: 1 }}>
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        <TextField
          size="small"
          placeholder="Поиск по id, названию, тегам…"
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
        <Chip
          size="small"
          label={platformEnabled ? 'Каталог включён' : 'Каталог выключен'}
          color={platformEnabled ? 'success' : 'warning'}
          variant="outlined"
          sx={{ height: 24, fontSize: '0.72rem' }}
        />
        <Typography variant="body2" sx={{ color: muted, ml: 'auto' }}>
          {loading ? '…' : `${filtered.length} из ${plugins.length}`}
        </Typography>
        <IconButton size="small" onClick={() => void load()} disabled={loading} title="Обновить">
          <RefreshIcon fontSize="small" />
        </IconButton>
      </Box>

      <Box sx={{ ...cardSx, p: 0, overflow: 'hidden', minHeight: 280 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
            <CircularProgress size={28} />
          </Box>
        ) : error ? (
          <Typography sx={{ p: 3, color: 'error.main' }}>{error}</Typography>
        ) : filtered.length === 0 ? (
          <Typography sx={{ p: 3, color: muted }}>Плагины не найдены</Typography>
        ) : (
          <Box sx={{ overflow: 'auto', maxHeight: 'min(62vh, 640px)' }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  {['ID', 'Название', 'Категория', 'Включён', 'Health'].map((h) => (
                    <TableCell
                      key={h}
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
                {filtered.map((p) => (
                  <TableRow key={p.id} hover>
                    <TableCell
                      sx={{
                        fontSize: '0.78rem',
                        opacity: 0.75,
                        maxWidth: 160,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {p.id}
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600} noWrap sx={{ maxWidth: 260 }}>
                        {p.display_name || p.id}
                      </Typography>
                      {p.description ? (
                        <Typography variant="caption" sx={{ color: muted, display: 'block' }} noWrap>
                          {p.description}
                        </Typography>
                      ) : null}
                    </TableCell>
                    <TableCell sx={{ fontSize: '0.8rem', opacity: 0.75 }}>
                      {p.category || '—'}
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={p.enabled ? 'Да' : 'Нет'}
                        color={p.enabled ? 'success' : 'default'}
                        variant="outlined"
                        sx={{ height: 22, fontSize: '0.7rem' }}
                      />
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={
                          p.healthy === true
                            ? 'OK'
                            : p.healthy === false
                              ? 'Fail'
                              : '—'
                        }
                        color={
                          p.healthy === true
                            ? 'success'
                            : p.healthy === false
                              ? 'error'
                              : 'default'
                        }
                        variant="outlined"
                        sx={{ height: 22, fontSize: '0.7rem' }}
                      />
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
