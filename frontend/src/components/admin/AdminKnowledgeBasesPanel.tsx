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
import { authFetch, getApiUrl, API_ENDPOINTS } from '../../config/api';
import { getAdminCardSx } from './adminSurfaces';
import { openKnowledgeBaseInSidebar } from '../../utils/openKnowledgeBaseSidebarNav';

interface AdminKbRow {
  id: number;
  name: string;
  description?: string | null;
  author_id?: string;
  author_name?: string;
  author_full_name?: string | null;
  is_public?: boolean;
  document_count?: number;
}

interface AdminKnowledgeBasesPanelProps {
  isDarkMode?: boolean;
}

/** Все базы знаний платформы для admin (`/api/knowledge-bases/list`). */
export default function AdminKnowledgeBasesPanel({
  isDarkMode = true,
}: AdminKnowledgeBasesPanelProps) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [items, setItems] = useState<AdminKbRow[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const all: AdminKbRow[] = [];
      let page = 1;
      let pages = 1;
      let reportedTotal = 0;
      while (page <= pages) {
        const resp = await authFetch(
          getApiUrl(`${API_ENDPOINTS.KNOWLEDGE_BASES}/list?limit=200&page=${page}`),
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const batch: AdminKbRow[] = data.items || [];
        all.push(...batch);
        reportedTotal = Number(data.total) || all.length;
        pages = Math.max(1, Number(data.pages) || 1);
        if (batch.length === 0) break;
        page += 1;
      }
      setItems(all);
      setTotal(reportedTotal || all.length);
    } catch {
      setItems([]);
      setTotal(0);
      setError('Не удалось загрузить базы знаний');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter((kb) => {
      const hay = [
        kb.name,
        kb.description,
        kb.author_name,
        kb.author_full_name,
        kb.author_id,
        String(kb.id),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return hay.includes(q);
    });
  }, [items, search]);

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
          {loading ? '…' : `${filtered.length} из ${total || items.length}`}
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
          <Typography sx={{ p: 3, color: muted }}>Базы знаний не найдены</Typography>
        ) : (
          <Box sx={{ overflow: 'auto', maxHeight: 'min(62vh, 640px)' }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  {['ID', 'Название', 'Автор', 'Документы', 'Видимость', ''].map((h) => (
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
                {filtered.map((kb) => (
                  <TableRow key={kb.id} hover>
                    <TableCell sx={{ fontSize: '0.8rem', opacity: 0.7 }}>{kb.id}</TableCell>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600} noWrap sx={{ maxWidth: 280 }}>
                        {kb.name || 'Без имени'}
                      </Typography>
                      {kb.description ? (
                        <Typography variant="caption" sx={{ color: muted, display: 'block' }} noWrap>
                          {kb.description}
                        </Typography>
                      ) : null}
                    </TableCell>
                    <TableCell sx={{ fontSize: '0.8rem' }}>
                      {kb.author_full_name || kb.author_name || kb.author_id || '—'}
                    </TableCell>
                    <TableCell sx={{ fontSize: '0.8rem' }}>{kb.document_count ?? 0}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={kb.is_public ? 'Публичная' : 'Приватная'}
                        color={kb.is_public ? 'success' : 'default'}
                        variant="outlined"
                        sx={{ height: 22, fontSize: '0.7rem' }}
                      />
                    </TableCell>
                    <TableCell align="right" sx={{ width: 48, px: 0.5 }}>
                      <IconButton
                        size="small"
                        title="Открыть настройки базы знаний"
                        aria-label="Открыть настройки базы знаний"
                        onClick={() => openKnowledgeBaseInSidebar(kb.id, navigate)}
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
