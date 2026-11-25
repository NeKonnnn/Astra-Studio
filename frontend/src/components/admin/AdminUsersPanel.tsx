import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  InputAdornment,
  Switch,
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
import PersonAddAltIcon from '@mui/icons-material/PersonAddAlt';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import KeyIcon from '@mui/icons-material/Key';
import { authFetch, getApiUrl, API_ENDPOINTS } from '../../config/api';
import { getAdminCardSx } from './adminSurfaces';

interface AdminUsersPanelProps {
  isDarkMode?: boolean;
}

type LocalUser = {
  username: string;
  is_admin: boolean;
  is_active: boolean;
  auth_source?: string;
  created_at?: string | null;
  updated_at?: string | null;
};

type RegistrationPolicy = {
  registration_enabled: boolean;
  default_is_admin: boolean;
  bootstrap_admin: boolean;
  min_password_length: number;
};

function formatTs(iso?: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/** Управление локальными пользователями + политика регистрации. */
export default function AdminUsersPanel({ isDarkMode = true }: AdminUsersPanelProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [users, setUsers] = useState<LocalUser[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState('');
  const [policy, setPolicy] = useState<RegistrationPolicy | null>(null);
  const [policySaving, setPolicySaving] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newIsAdmin, setNewIsAdmin] = useState(false);
  const [creating, setCreating] = useState(false);

  const [pwdOpen, setPwdOpen] = useState(false);
  const [pwdUser, setPwdUser] = useState<string | null>(null);
  const [pwdValue, setPwdValue] = useState('');
  const [pwdSaving, setPwdSaving] = useState(false);

  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const cardSx = getAdminCardSx(isDarkMode);
  const headColor = isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const q = search.trim()
        ? `?search=${encodeURIComponent(search.trim())}&limit=200`
        : '?limit=200';
      const resp = await authFetch(getApiUrl(`${API_ENDPOINTS.ADMIN_USERS}${q}`));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setUsers(Array.isArray(data?.users) ? data.users : []);
      setTotal(Number(data?.total) || 0);
      if (data?.registration_policy) {
        setPolicy(data.registration_policy as RegistrationPolicy);
      }
    } catch {
      setUsers([]);
      setError('Не удалось загрузить пользователей');
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => users, [users]);

  const patchUser = async (
    username: string,
    body: Partial<{ is_admin: boolean; is_active: boolean; password: string }>,
  ) => {
    setError(null);
    const resp = await authFetch(
      getApiUrl(`${API_ENDPOINTS.ADMIN_USERS}/${encodeURIComponent(username)}`),
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    );
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const data = await resp.json();
        detail = data?.detail || detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    await load();
  };

  const deleteUser = async (username: string) => {
    if (!window.confirm(`Удалить пользователя «${username}»?`)) return;
    setError(null);
    const resp = await authFetch(
      getApiUrl(`${API_ENDPOINTS.ADMIN_USERS}/${encodeURIComponent(username)}`),
      { method: 'DELETE' },
    );
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const data = await resp.json();
        detail = data?.detail || detail;
      } catch {
        /* ignore */
      }
      setError(String(detail));
      return;
    }
    await load();
  };

  const createUser = async () => {
    setCreating(true);
    setError(null);
    try {
      const resp = await authFetch(getApiUrl(API_ENDPOINTS.ADMIN_USERS), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: newUsername.trim(),
          password: newPassword,
          is_admin: newIsAdmin,
          is_active: true,
        }),
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
          const data = await resp.json();
          detail = data?.detail || detail;
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }
      setCreateOpen(false);
      setNewUsername('');
      setNewPassword('');
      setNewIsAdmin(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось создать пользователя');
    } finally {
      setCreating(false);
    }
  };

  const savePassword = async () => {
    if (!pwdUser) return;
    setPwdSaving(true);
    setError(null);
    try {
      await patchUser(pwdUser, { password: pwdValue });
      setPwdOpen(false);
      setPwdUser(null);
      setPwdValue('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось сменить пароль');
    } finally {
      setPwdSaving(false);
    }
  };

  const savePolicy = async (patch: Partial<RegistrationPolicy>) => {
    if (!policy) return;
    setPolicySaving(true);
    setError(null);
    try {
      const next = { ...policy, ...patch };
      const resp = await authFetch(getApiUrl(API_ENDPOINTS.ADMIN_REGISTRATION_POLICY), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setPolicy(data as RegistrationPolicy);
    } catch {
      setError('Не удалось сохранить политику регистрации');
    } finally {
      setPolicySaving(false);
    }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, minHeight: 0, flex: 1 }}>
      {policy && (
        <Box sx={{ ...cardSx, p: 2 }}>
          <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 1 }}>
            Политика регистрации
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center' }}>
            <FormControlLabel
              control={
                <Switch
                  checked={!!policy.registration_enabled}
                  disabled={policySaving}
                  onChange={(_, v) => void savePolicy({ registration_enabled: v })}
                />
              }
              label="Самостоятельная регистрация"
            />
            <FormControlLabel
              control={
                <Switch
                  checked={!!policy.default_is_admin}
                  disabled={policySaving || !policy.registration_enabled}
                  onChange={(_, v) => void savePolicy({ default_is_admin: v })}
                />
              }
              label="Новые = admin"
            />
            <FormControlLabel
              control={
                <Switch
                  checked={!!policy.bootstrap_admin}
                  disabled={policySaving}
                  onChange={(_, v) => void savePolicy({ bootstrap_admin: v })}
                />
              }
              label="Первый пользователь → admin"
            />
            <Typography variant="caption" sx={{ color: muted }}>
              Мин. пароль: {policy.min_password_length} символов
            </Typography>
          </Box>
        </Box>
      )}

      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        <TextField
          size="small"
          placeholder="Поиск по логину…"
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
          {loading ? '…' : `${filtered.length} из ${total}`}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          startIcon={<PersonAddAltIcon />}
          onClick={() => setCreateOpen(true)}
        >
          Создать
        </Button>
        <IconButton size="small" onClick={() => void load()} disabled={loading} title="Обновить">
          <RefreshIcon fontSize="small" />
        </IconButton>
      </Box>

      {error ? <Alert severity="error">{error}</Alert> : null}

      <Box sx={{ ...cardSx, p: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', minHeight: 280 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
            <CircularProgress size={28} />
          </Box>
        ) : filtered.length === 0 ? (
          <Typography sx={{ p: 3, color: muted }}>Локальные пользователи не найдены</Typography>
        ) : (
          <Box sx={{ overflow: 'auto', maxHeight: 'min(62vh, 640px)' }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  {['Логин', 'Роль', 'Статус', 'Создан', ''].map((h) => (
                    <TableCell
                      key={h || 'actions'}
                      sx={{ color: headColor, bgcolor: isDarkMode ? 'rgba(0,0,0,0.35)' : 'rgba(255,255,255,0.9)' }}
                    >
                      {h}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {filtered.map((u) => (
                  <TableRow key={u.username} hover>
                    <TableCell>{u.username}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={u.is_admin ? 'admin' : 'user'}
                        color={u.is_admin ? 'primary' : 'default'}
                        variant={u.is_admin ? 'filled' : 'outlined'}
                        onClick={() => {
                          void patchUser(u.username, { is_admin: !u.is_admin }).catch((e) =>
                            setError(e instanceof Error ? e.message : 'Ошибка'),
                          );
                        }}
                      />
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={u.is_active ? 'активен' : 'заблокирован'}
                        color={u.is_active ? 'success' : 'warning'}
                        variant="outlined"
                        onClick={() => {
                          void patchUser(u.username, { is_active: !u.is_active }).catch((e) =>
                            setError(e instanceof Error ? e.message : 'Ошибка'),
                          );
                        }}
                      />
                    </TableCell>
                    <TableCell sx={{ color: muted, whiteSpace: 'nowrap' }}>
                      {formatTs(u.created_at)}
                    </TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                      <IconButton
                        size="small"
                        title="Сменить пароль"
                        onClick={() => {
                          setPwdUser(u.username);
                          setPwdValue('');
                          setPwdOpen(true);
                        }}
                      >
                        <KeyIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        size="small"
                        title="Удалить"
                        color="error"
                        onClick={() => void deleteUser(u.username)}
                      >
                        <DeleteOutlineIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </Box>

      <Dialog open={createOpen} onClose={() => !creating && setCreateOpen(false)} fullWidth maxWidth="xs">
        <DialogTitle>Новый пользователь</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
          <TextField
            label="Логин"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            autoFocus
            fullWidth
          />
          <TextField
            label="Пароль"
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            fullWidth
          />
          <FormControlLabel
            control={
              <Switch checked={newIsAdmin} onChange={(_, v) => setNewIsAdmin(v)} />
            }
            label="Администратор"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)} disabled={creating}>
            Отмена
          </Button>
          <Button
            variant="contained"
            disabled={creating || !newUsername.trim() || !newPassword}
            onClick={() => void createUser()}
          >
            {creating ? <CircularProgress size={18} /> : 'Создать'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={pwdOpen} onClose={() => !pwdSaving && setPwdOpen(false)} fullWidth maxWidth="xs">
        <DialogTitle>Сменить пароль — {pwdUser}</DialogTitle>
        <DialogContent>
          <TextField
            label="Новый пароль"
            type="password"
            value={pwdValue}
            onChange={(e) => setPwdValue(e.target.value)}
            fullWidth
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPwdOpen(false)} disabled={pwdSaving}>
            Отмена
          </Button>
          <Button
            variant="contained"
            disabled={pwdSaving || !pwdValue}
            onClick={() => void savePassword()}
          >
            {pwdSaving ? <CircularProgress size={18} /> : 'Сохранить'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
