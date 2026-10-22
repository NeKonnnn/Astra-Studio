import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Box,
  Typography,
  CircularProgress,
  Alert,
  IconButton,
  Popover,
  Autocomplete,
} from '@mui/material';
import {
  Close as CloseIcon,
  PersonAdd as PersonAddIcon,
  Delete as DeleteIcon,
  ExpandMore as ExpandMoreIcon,
  Check as CheckIcon,
} from '@mui/icons-material';
import { authFetch, getApiUrl } from '../config/api';
import { useAuth } from '../contexts/AuthContext';
import {
  directoryUserOptionLabel,
  directoryUserOptionSecondary,
  formatShareFeedback,
  resolveShareUsernames,
  searchDirectoryUsers,
  type DirectoryUserHit,
  type ShareApiResponse,
} from '../utils/directoryUserSearch';

type SharePermission = 'viewer' | 'editor';

interface ShareEntry {
  user_id: string;
  full_name?: string | null;
  permission: string; // owner | editor | viewer
}

interface SharesResponse {
  owner: ShareEntry;
  shares: ShareEntry[];
}

const PERMISSION_LABEL: Record<string, string> = {
  owner: 'Владелец',
  editor: 'Редактор',
  viewer: 'Зритель',
};

const PERMISSION_HINT: Record<SharePermission, string> = {
  viewer: 'Может смотреть и использовать базу знаний, но не может менять настройки.',
  editor: 'Может смотреть, использовать и изменять базу знаний (без удаления и повторного шаринга).',
};

const ASSIGNABLE_ROLES: SharePermission[] = ['viewer', 'editor'];

interface ShareKnowledgeBaseDialogProps {
  open: boolean;
  onClose: () => void;
  kbId: number;
  kbName: string;
  isDarkMode: boolean;
}

export default function ShareKnowledgeBaseDialog({
  open,
  onClose,
  kbId,
  kbName,
  isDarkMode,
}: ShareKnowledgeBaseDialogProps) {
  const { token } = useAuth();
  const [username, setUsername] = useState('');
  const [permission, setPermission] = useState<SharePermission>('viewer');
  const [owner, setOwner] = useState<ShareEntry | null>(null);
  const [shares, setShares] = useState<ShareEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [userOptions, setUserOptions] = useState<DirectoryUserHit[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchAbortRef = useRef<AbortController | null>(null);

  // Якоря выпадающих списков (стиль как «Агенты» в конструкторе)
  const [addRoleAnchor, setAddRoleAnchor] = useState<HTMLElement | null>(null);
  const [rowRoleAnchor, setRowRoleAnchor] = useState<{ el: HTMLElement; userId: string } | null>(null);

  const bg = isDarkMode ? '#1e1e1e' : '#fff';
  const text = isDarkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.87)';
  const muted = isDarkMode ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.5)';
  const border = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)';
  const borderHover = isDarkMode ? 'rgba(255,255,255,0.28)' : 'rgba(0,0,0,0.28)';
  const fieldBg = isDarkMode ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.03)';
  const fieldBgHover = isDarkMode ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.05)';
  const itemHover = isDarkMode ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.05)';

  const triggerSx = useMemo(
    () => ({
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      boxSizing: 'border-box' as const,
      minHeight: 38,
      px: 1.5,
      py: 0.75,
      borderRadius: 1,
      bgcolor: fieldBg,
      border: `1px solid ${border}`,
      cursor: 'pointer',
      userSelect: 'none' as const,
      transition: 'background 0.15s, border-color 0.15s',
      color: text,
      '&:hover': { borderColor: borderHover, bgcolor: fieldBgHover },
    }),
    [fieldBg, border, text, borderHover, fieldBgHover],
  );

  const authHeaders = useCallback((): HeadersInit => {
    const h: HeadersInit = { 'Content-Type': 'application/json' };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadShares = useCallback(async () => {
    if (!token || !kbId) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await authFetch(getApiUrl(`/api/knowledge-bases/${kbId}/shares`), {
        headers: authHeaders(),
      });
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        throw new Error(j.detail || 'Не удалось загрузить список доступа');
      }
      const data: SharesResponse = await resp.json();
      setOwner(data?.owner ?? null);
      setShares(Array.isArray(data?.shares) ? data.shares : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  }, [kbId, token, authHeaders]);

  useEffect(() => {
    if (open) {
      setUsername('');
      setPermission('viewer');
      setError(null);
      setSuccess(null);
      setWarning(null);
      setUserOptions([]);
      void loadShares();
    }
  }, [open, loadShares]);

  const scheduleUserSearch = useCallback(
    (q: string) => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
      searchAbortRef.current?.abort();
      const query = q.trim();
      const last = query.split(/[,;\n]+/).pop()?.trim() || '';
      if (last.length < 2 || !token) {
        setUserOptions([]);
        setSearchLoading(false);
        return;
      }
      setSearchLoading(true);
      searchTimerRef.current = setTimeout(() => {
        const controller = new AbortController();
        searchAbortRef.current = controller;
        void (async () => {
          try {
            const hits = await searchDirectoryUsers(token, last, 10, controller.signal, true);
            if (controller.signal.aborted) return;
            setUserOptions(hits);
          } catch {
            if (controller.signal.aborted) return;
            setUserOptions([]);
          } finally {
            if (!controller.signal.aborted) {
              setSearchLoading(false);
            }
          }
        })();
      }, 400);
    },
    [token],
  );

  useEffect(
    () => () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
      searchAbortRef.current?.abort();
    },
    [],
  );

  const applyShare = useCallback(
    async (usernames: string[], perm: SharePermission): Promise<ShareApiResponse> => {
      const resp = await authFetch(getApiUrl(`/api/knowledge-bases/${kbId}/share`), {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ usernames, permission: perm }),
      });
      const data = (await resp.json().catch(() => ({}))) as ShareApiResponse & { detail?: string };
      if (!resp.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось поделиться базой знаний');
      }
      return data;
    },
    [kbId, authHeaders],
  );

  const handleShare = async () => {
    if (!username.trim()) {
      setError('Укажите ФИО, почту или gpbu');
      return;
    }
    if (!token) {
      setError('Нет авторизации');
      return;
    }
    setSaving(true);
    setError(null);
    setSuccess(null);
    setWarning(null);
    try {
      const { usernames, unresolved } = await resolveShareUsernames(token, username);
      if (unresolved.length > 0) {
        throw new Error(`Не найдены: ${unresolved.join(', ')}`);
      }
      if (usernames.length === 0) {
        setError('Укажите ФИО, почту или gpbu');
        return;
      }
      const data = await applyShare(usernames, permission);
      setUsername('');
      setUserOptions([]);
      const feedback = formatShareFeedback(data);
      setSuccess(feedback.success);
      setWarning(feedback.warning);
      await loadShares();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка');
    } finally {
      setSaving(false);
    }
  };

  const handleChangeRole = async (userId: string, newPermission: SharePermission) => {
    setRowRoleAnchor(null);
    setSaving(true);
    setError(null);
    setSuccess(null);
    setWarning(null);
    try {
      const data = await applyShare([userId], newPermission);
      const feedback = formatShareFeedback(data);
      setSuccess(feedback.success || 'Роль обновлена');
      setWarning(feedback.warning);
      await loadShares();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка');
    } finally {
      setSaving(false);
    }
  };

  const handleUnshare = async (userId: string) => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const resp = await authFetch(
        getApiUrl(`/api/knowledge-bases/${kbId}/share/${encodeURIComponent(userId)}`),
        { method: 'DELETE', headers: authHeaders() },
      );
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось снять доступ');
      }
      setSuccess(data.message || 'Доступ снят');
      await loadShares();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка');
    } finally {
      setSaving(false);
    }
  };

  const popoverPaperSx = useMemo(
    () => ({
      mt: 0.5,
      bgcolor: bg,
      color: text,
      border: `1px solid ${border}`,
      borderRadius: 1.5,
      boxShadow: isDarkMode
        ? '0 8px 24px rgba(0,0,0,0.5)'
        : '0 8px 24px rgba(0,0,0,0.15)',
      overflow: 'hidden',
    }),
    [bg, text, border, isDarkMode],
  );

  const dropdownItemSx = useCallback(
    (active: boolean) => ({
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 1,
      px: 1.5,
      py: 1,
      cursor: 'pointer',
      fontSize: '0.82rem',
      color: text,
      bgcolor: active ? itemHover : 'transparent',
      '&:hover': { bgcolor: itemHover },
    }),
    [text, itemHover],
  );

  // Аватар-инициалы по ФИО/логину
  const initialsOf = (entry: ShareEntry): string => {
    const src = (entry.full_name || entry.user_id || '').trim();
    if (!src) return '?';
    const parts = src.split(/\s+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return src.slice(0, 2).toUpperCase();
  };

  const renderParticipantRow = (entry: ShareEntry, isOwner: boolean) => (
    <Box
      key={entry.user_id}
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.25,
        px: 1.5,
        py: 1,
        borderBottom: `1px solid ${border}`,
        '&:last-of-type': { borderBottom: 'none' },
      }}
    >
      <Box
        sx={{
          width: 30,
          height: 30,
          borderRadius: '50%',
          flexShrink: 0,
          bgcolor: isOwner ? 'primary.main' : fieldBgHover,
          color: isOwner ? '#fff' : muted,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '0.72rem',
          fontWeight: 600,
        }}
      >
        {initialsOf(entry)}
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          sx={{
            fontSize: '0.85rem',
            color: text,
            fontWeight: 500,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {entry.full_name || entry.user_id}
        </Typography>
        {entry.full_name && (
          <Typography
            sx={{
              fontSize: '0.72rem',
              color: muted,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {entry.user_id}
          </Typography>
        )}
      </Box>

      {isOwner ? (
        <Typography
          sx={{
            fontSize: '0.72rem',
            fontWeight: 600,
            color: muted,
            px: 1,
            py: 0.4,
            flexShrink: 0,
          }}
        >
          {PERMISSION_LABEL.owner}
        </Typography>
      ) : (
        <>
          <Box
            onClick={(e) =>
              !saving && setRowRoleAnchor({ el: e.currentTarget, userId: entry.user_id })
            }
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              px: 1,
              py: 0.4,
              borderRadius: 1,
              border: `1px solid ${border}`,
              bgcolor: fieldBg,
              cursor: saving ? 'default' : 'pointer',
              flexShrink: 0,
              '&:hover': { borderColor: saving ? border : borderHover },
            }}
          >
            <Typography sx={{ fontSize: '0.72rem', color: text }}>
              {PERMISSION_LABEL[entry.permission] || PERMISSION_LABEL.viewer}
            </Typography>
            <ExpandMoreIcon sx={{ fontSize: 16, color: muted }} />
          </Box>
          <IconButton
            size="small"
            onClick={() => void handleUnshare(entry.user_id)}
            disabled={saving}
            sx={{ color: muted, '&:hover': { color: '#ef5350' }, flexShrink: 0 }}
            title="Отозвать доступ"
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        </>
      )}
    </Box>
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: {
          bgcolor: bg,
          color: text,
          border: `1px solid ${border}`,
        },
      }}
    >
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', pr: 1, pb: 1 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h6" sx={{ fontSize: '1.05rem', fontWeight: 600 }}>
            Поделиться базой знаний
          </Typography>
          <Typography
            variant="body2"
            sx={{ color: muted, mt: 0.25, overflow: 'hidden', textOverflow: 'ellipsis' }}
          >
            {kbName}
          </Typography>
        </Box>
        <IconButton onClick={onClose} size="small" sx={{ color: muted }}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ pt: 1 }}>
        <Typography variant="body2" sx={{ color: muted, mb: 1.5, fontSize: '0.8rem' }}>
          Найдите сотрудника по ФИО, корпоративной почте или gpbu (как при входе). Можно несколько через
          запятую. Получатель увидит базу знаний в списке и сможет использовать её в чате.
        </Typography>

        {/* Добавление: поиск + роль + кнопка */}
        <Box sx={{ display: 'flex', gap: 1, mb: 0.75, alignItems: 'stretch' }}>
          <Autocomplete
            freeSolo
            fullWidth
            size="small"
            options={userOptions}
            filterOptions={(x) => x}
            loading={searchLoading}
            inputValue={username}
            onInputChange={(_, value, reason) => {
              if (reason === 'reset') return;
              setUsername(value);
              scheduleUserSearch(value);
            }}
            onChange={(_, value) => {
              if (value && typeof value === 'object') {
                const login = value.username || value.user_id;
                setUsername(login);
                setUserOptions([]);
              }
            }}
            getOptionLabel={(opt) =>
              typeof opt === 'string' ? opt : directoryUserOptionLabel(opt) || opt.username || opt.user_id
            }
            isOptionEqualToValue={(a, b) =>
              (a.username || a.user_id).toLowerCase() === (b.username || b.user_id).toLowerCase()
            }
            renderOption={(props, option) => (
              <li {...props} key={option.user_id || option.username}>
                <Box sx={{ py: 0.25 }}>
                  <Typography sx={{ fontSize: '0.85rem', color: text }}>
                    {directoryUserOptionLabel(option)}
                  </Typography>
                  <Typography sx={{ fontSize: '0.72rem', color: muted }}>
                    {directoryUserOptionSecondary(option)}
                  </Typography>
                </Box>
              </li>
            )}
            renderInput={(params) => (
              <TextField
                {...params}
                placeholder="ФИО, почта или gpbu…"
                disabled={saving}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    void handleShare();
                  }
                }}
                sx={{
                  '& .MuiOutlinedInput-root': {
                    color: text,
                    bgcolor: fieldBg,
                    '& fieldset': { borderColor: border },
                  },
                  '& .MuiInputBase-input::placeholder': { color: muted, opacity: 1 },
                }}
              />
            )}
          />
          <Box
            onClick={(e) => !saving && setAddRoleAnchor(e.currentTarget)}
            sx={{ ...triggerSx, minWidth: 130, flexShrink: 0 }}
          >
            <Typography sx={{ fontSize: '0.82rem', color: text }}>
              {PERMISSION_LABEL[permission]}
            </Typography>
            <ExpandMoreIcon
              sx={{
                fontSize: 18,
                color: muted,
                transform: addRoleAnchor ? 'rotate(180deg)' : 'none',
                transition: 'transform 0.15s',
              }}
            />
          </Box>
          <Button
            variant="contained"
            startIcon={saving ? <CircularProgress size={14} color="inherit" /> : <PersonAddIcon />}
            onClick={() => void handleShare()}
            disabled={saving || !username.trim()}
            sx={{ textTransform: 'none', whiteSpace: 'nowrap', flexShrink: 0 }}
          >
            Добавить
          </Button>
        </Box>
        <Typography
          variant="caption"
          sx={{ display: 'block', color: muted, mb: 2, fontSize: '0.72rem' }}
        >
          {PERMISSION_HINT[permission]}
        </Typography>

        {/* Дропдаун выбора роли при добавлении */}
        <Popover
          open={Boolean(addRoleAnchor)}
          anchorEl={addRoleAnchor}
          onClose={() => setAddRoleAnchor(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
          slotProps={{ paper: { sx: { ...popoverPaperSx, minWidth: 130 } } }}
        >
          {ASSIGNABLE_ROLES.map((role) => (
            <Box
              key={role}
              onClick={() => {
                setPermission(role);
                setAddRoleAnchor(null);
              }}
              sx={dropdownItemSx(permission === role)}
            >
              <Typography sx={{ fontSize: '0.82rem', color: text }}>
                {PERMISSION_LABEL[role]}
              </Typography>
              {permission === role && <CheckIcon sx={{ fontSize: 16 }} />}
            </Box>
          ))}
        </Popover>

        {error && (
          <Alert severity="error" sx={{ mb: 1.5, py: 0.5 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        {success && (
          <Alert severity="success" sx={{ mb: 1.5, py: 0.5 }} onClose={() => setSuccess(null)}>
            {success}
          </Alert>
        )}
        {warning && (
          <Alert severity="warning" sx={{ mb: 1.5, py: 0.5 }} onClose={() => setWarning(null)}>
            {warning}
          </Alert>
        )}

        <Typography variant="subtitle2" sx={{ mb: 0.5, fontWeight: 600 }}>
          Кому доступен
        </Typography>

        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
            <CircularProgress size={24} />
          </Box>
        ) : (
          <Box sx={{ border: `1px solid ${border}`, borderRadius: 1.5, overflow: 'hidden' }}>
            {owner && renderParticipantRow(owner, true)}
            {shares.length === 0 ? (
              <Box sx={{ px: 1.5, py: 1.5 }}>
                <Typography variant="body2" sx={{ color: muted, textAlign: 'center' }}>
                  Пока ни с кем не поделились
                </Typography>
              </Box>
            ) : (
              shares.map((s) => renderParticipantRow(s, false))
            )}
          </Box>
        )}

        {/* Дропдаун смены роли конкретного получателя */}
        <Popover
          open={Boolean(rowRoleAnchor)}
          anchorEl={rowRoleAnchor?.el ?? null}
          onClose={() => setRowRoleAnchor(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
          transformOrigin={{ vertical: 'top', horizontal: 'right' }}
          slotProps={{ paper: { sx: { ...popoverPaperSx, minWidth: 150 } } }}
        >
          {ASSIGNABLE_ROLES.map((role) => {
            const currentPerm = shares.find((s) => s.user_id === rowRoleAnchor?.userId)?.permission;
            return (
              <Box
                key={role}
                onClick={() => {
                  if (rowRoleAnchor) void handleChangeRole(rowRoleAnchor.userId, role);
                }}
                sx={dropdownItemSx(currentPerm === role)}
              >
                <Box>
                  <Typography sx={{ fontSize: '0.82rem', color: text }}>
                    {PERMISSION_LABEL[role]}
                  </Typography>
                  <Typography sx={{ fontSize: '0.68rem', color: muted }}>
                    {PERMISSION_HINT[role]}
                  </Typography>
                </Box>
                {currentPerm === role && <CheckIcon sx={{ fontSize: 16, flexShrink: 0 }} />}
              </Box>
            );
          })}
        </Popover>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} sx={{ textTransform: 'none', color: muted }}>
          Закрыть
        </Button>
      </DialogActions>
    </Dialog>
  );
}
