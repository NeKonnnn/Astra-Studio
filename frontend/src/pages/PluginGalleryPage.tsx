import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardActions,
  CardContent,
  Chip,
  CircularProgress,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  Bookmark as BookmarkIcon,
  BookmarkBorder as BookmarkBorderIcon,
  Extension as PluginIcon,
  GetApp as DownloadIcon,
  PlayArrow as RunIcon,
} from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';
import { useNavigate, Navigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useAppActions } from '../contexts/AppContext';
import { usePluginRuns } from '../contexts/PluginRunContext';
import { fetchPluginsResponse } from '../plugins/api';
import type { PluginPublic } from '../plugins/types';
import { formatDuration } from '../plugins/verdict';
import ArtifactMarkdownPreview from '../components/artifacts/ArtifactMarkdownPreview';
import { GallerySearchBookmarksBar } from '../components/galleryCards';
import { getGalleryCardGlassSx } from '../constants/galleryCardGlass';
import { getPluginBookmarkIds, togglePluginBookmark } from '../utils/pluginBookmarks';

function downloadMarkdown(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.endsWith('.md') ? filename : `${filename}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function PluginGalleryPage() {
  return <Navigate to="/gallery?tab=plugins" replace />;
}

export function PluginGalleryContent({ embedded = false }: { embedded?: boolean }) {
  const { token } = useAuth();
  const { showNotification } = useAppActions();
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const navigate = useNavigate();
  // Запуск живёт в контексте: закрытие окна и переход в чат его не отменяют.
  const { getRun, isRunning, startRun, elapsedSec: runElapsedSec } = usePluginRuns();

  const [plugins, setPlugins] = useState<PluginPublic[]>([]);
  const [platformEnabled, setPlatformEnabled] = useState(true);
  const [loading, setLoading] = useState(false);
  const [healthLoading, setHealthLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [showBookmarks, setShowBookmarks] = useState(false);
  const [bookmarkedIds, setBookmarkedIds] = useState<Set<string>>(() => getPluginBookmarkIds());
  const [runPlugin, setRunPlugin] = useState<PluginPublic | null>(null);
  const [modelFile, setModelFile] = useState<File | null>(null);
  const [qualityFile, setQualityFile] = useState<File | null>(null);
  const [prompt, setPrompt] = useState('');
  const [verdictTab, setVerdictTab] = useState<'preview' | 'source' | 'json'>('preview');
  const loadingRef = useRef(false);
  const notifyRef = useRef(showNotification);
  const resultRef = useRef<HTMLDivElement | null>(null);
  notifyRef.current = showNotification;

  const activeRun = runPlugin ? getRun(runPlugin.id) : null;
  const running = runPlugin ? isRunning(runPlugin.id) : false;
  const elapsedSec = runPlugin ? runElapsedSec(runPlugin.id) : 0;
  const verdictMd = activeRun?.markdown || null;
  const runError = activeRun?.status === 'error' ? activeRun.error || 'Аудит не завершён' : null;
  const runSummary = activeRun?.status === 'done' ? activeRun.summary || null : null;
  const rawResponse = activeRun?.raw ?? null;
  const invokeCompleted = Boolean(activeRun && activeRun.status !== 'running');

  const loadPlugins = useCallback(async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setLoadError(null);
    try {
      // Сначала каталог без health — карточки сразу, без «мигания» на долгих health-check.
      const catalog = await fetchPluginsResponse(false);
      setPlatformEnabled(catalog.enabled);
      setPlugins(catalog.plugins);
      if (!catalog.enabled) {
        setLoadError(
          'Платформа плагинов выключена (plugins.enabled=false или PLUGINS_ENABLED=false).',
        );
        return;
      }
      if (!catalog.plugins.length) {
        setLoadError(
          catalog.catalog_ids?.length
            ? `Каталог в config.yml есть (${catalog.catalog_ids.join(', ')}), но API вернул пустой список.`
            : 'Каталог plugins.catalog в config.yml пуст или не загружен.',
        );
        return;
      }

      setHealthLoading(true);
      try {
        const withHealth = await fetchPluginsResponse(true);
        setPlatformEnabled(withHealth.enabled);
        if (withHealth.plugins.length) {
          setPlugins(withHealth.plugins);
        }
      } catch (healthErr: unknown) {
        console.warn('[plugins] health enrich failed', healthErr);
      } finally {
        setHealthLoading(false);
      }
    } catch (e: unknown) {
      setPlugins([]);
      setPlatformEnabled(true);
      const msg = e instanceof Error ? e.message : 'Не удалось загрузить плагины';
      setLoadError(msg);
      notifyRef.current('error', msg);
    } finally {
      setLoading(false);
      loadingRef.current = false;
    }
  }, []);

  useEffect(() => {
    if (!token) return;
    void loadPlugins();
  }, [token, loadPlugins]);

  // Результат приехал (или его открыли заново) — подводим к нему окно.
  useEffect(() => {
    if (!activeRun || activeRun.status === 'running') return undefined;
    const timer = window.setTimeout(() => {
      resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);
    return () => window.clearTimeout(timer);
  }, [activeRun?.pluginId, activeRun?.status, activeRun?.finishedAtMs]);

  const filtered = useMemo(() => {
    let list = plugins;
    if (showBookmarks) {
      list = list.filter((p) => bookmarkedIds.has(p.id));
    }
    const q = searchQuery.trim().toLowerCase();
    if (!q || showBookmarks) return list;
    return list.filter(
      (p) =>
        p.display_name.toLowerCase().includes(q) ||
        (p.description || '').toLowerCase().includes(q) ||
        p.id.toLowerCase().includes(q) ||
        (p.tags || []).some((t) => t.toLowerCase().includes(q)),
    );
  }, [plugins, searchQuery, showBookmarks, bookmarkedIds]);

  const emptyMessage = useMemo(() => {
    if (loadError) return loadError;
    if (!platformEnabled) {
      return 'Платформа плагинов выключена. Проверьте plugins.enabled в config.yml / PLUGINS_ENABLED.';
    }
    if (showBookmarks) {
      return 'В закладках пока нет плагинов. Добавьте плагин кнопкой-закладкой на карточке.';
    }
    if (searchQuery.trim() && plugins.length > 0) {
      return 'По запросу ничего не найдено.';
    }
    return 'Плагины не найдены. Проверьте секцию plugins в config.yml и доступность сервисов.';
  }, [loadError, platformEnabled, searchQuery, plugins.length, showBookmarks]);

  const handleToggleBookmark = (pluginId: string) => {
    const next = togglePluginBookmark(pluginId);
    setBookmarkedIds(getPluginBookmarkIds());
    showNotification('success', next ? 'Добавлено в закладки' : 'Удалено из закладок');
  };

  const handleRun = () => {
    if (!runPlugin || !modelFile) {
      notifyRef.current('error', 'Выберите Excel-файл модели');
      return;
    }
    setVerdictTab('preview');
    const started = startRun(runPlugin, { modelFile, qualityFile, prompt });
    if (!started) {
      notifyRef.current('info', 'Этот плагин уже выполняется — дождитесь результата');
    }
  };

  /** Открыть окно запуска: поля сбрасываем только при смене плагина. */
  const openRunDialog = (plugin: PluginPublic) => {
    if (runPlugin?.id !== plugin.id) {
      setModelFile(null);
      setQualityFile(null);
      setPrompt('');
      setVerdictTab('preview');
    }
    setRunPlugin(plugin);
  };

  // Окно закрывается в любой момент: аудит продолжается в фоне, а результат
  // остаётся доступным, пока не запустят плагин с новым файлом.
  const closeRunDialog = () => {
    setRunPlugin(null);
  };

  const handleDownloadMd = () => {
    if (!verdictMd) return;
    const source = activeRun?.modelFileName || modelFile?.name || 'cash-flow-audit';
    const base = source.replace(/\.(xlsx|xlsm|xltx|xltm)$/i, '') || 'cash-flow-audit';
    downloadMarkdown(`${base}-verdict.md`, verdictMd);
    notifyRef.current('success', 'Файл .md сохранён');
  };

  const renderResultBlock = () => {
    if (!invokeCompleted || running || !verdictMd) return null;
    const rawJson = rawResponse ? JSON.stringify(rawResponse, null, 2) : '';
    const displayMd = verdictMd || '# Пустой ответ\n\nMarkdown не сформирован.';
    return (
      <Box
        ref={resultRef}
        sx={{
          mt: 1,
          mb: 2,
          p: 2,
          borderRadius: 1,
          border: '2px solid',
          borderColor: 'success.main',
          bgcolor: isDarkMode ? 'rgba(76, 175, 80, 0.08)' : 'rgba(76, 175, 80, 0.06)',
        }}
      >
        <Typography variant="subtitle1" sx={{ mb: 1, fontWeight: 700 }}>
          Результат аудита
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
          Файл: {activeRun?.modelFileName || '—'} · длительность {formatDuration(elapsedSec)}
          {rawJson ? '' : ' · сырой JSON доступен только до перезагрузки страницы'}
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1, mb: 1, flexWrap: 'wrap' }}>
          <Tabs
            value={verdictTab}
            onChange={(_, v) => setVerdictTab(v)}
            sx={{ minHeight: 36, '& .MuiTab-root': { minHeight: 36, py: 0.5 } }}
          >
            <Tab value="preview" label="Превью" />
            <Tab value="source" label="Markdown" />
            <Tab value="json" label="JSON" />
          </Tabs>
          <Button size="small" startIcon={<DownloadIcon />} onClick={handleDownloadMd} variant="outlined">
            Скачать .md
          </Button>
        </Box>
        <Box
          sx={{
            borderRadius: 1,
            overflow: 'hidden',
            border: isDarkMode ? '1px solid rgba(255,255,255,0.12)' : '1px solid rgba(0,0,0,0.1)',
            maxHeight: 420,
            minHeight: 180,
          }}
        >
          {verdictTab === 'preview' ? (
            <Box sx={{ height: 400 }}>
              <ArtifactMarkdownPreview content={displayMd} />
            </Box>
          ) : verdictTab === 'source' ? (
            <Box
              sx={{
                p: 2,
                height: 400,
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                fontSize: '0.82rem',
                bgcolor: isDarkMode ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)',
              }}
            >
              {displayMd}
            </Box>
          ) : (
            <Box
              sx={{
                p: 2,
                height: 400,
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                fontSize: '0.75rem',
                bgcolor: isDarkMode ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)',
              }}
            >
              {rawJson || '{}'}
            </Box>
          )}
        </Box>
      </Box>
    );
  };

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {!embedded && (
      <Box sx={{ py: 2 }}>
        <Container maxWidth="xl">
          <Box sx={{ textAlign: 'center' }}>
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'row',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 1.5,
                mb: 0.5,
                flexWrap: 'wrap',
              }}
            >
              <PluginIcon color="primary" />
              <Typography variant="h4" fontWeight="bold">
                Галерея плагинов
              </Typography>
              <Button size="small" onClick={() => navigate('/')}>
                К чату
              </Button>
              {healthLoading ? (
                <Typography variant="caption" color="text.secondary">
                  проверка online…
                </Typography>
              ) : null}
            </Box>
            <Typography variant="body2" color="text.secondary">
              HTTP-плагины платформы. Подключайте их к агенту в конструкторе · «Запустить» — проверить сервис
            </Typography>
          </Box>
        </Container>
      </Box>
      )}
      {embedded && healthLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
          <Typography variant="caption" color="text.secondary">
            проверка online…
          </Typography>
        </Box>
      ) : null}

      <Box sx={{ py: embedded ? 0 : 2, pb: embedded ? 1.5 : undefined }}>
        <Container maxWidth="xl">
          <GallerySearchBookmarksBar
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            searchPlaceholder="Поиск плагинов…"
            showBookmarks={showBookmarks}
            onToggleBookmarks={() => setShowBookmarks((v) => !v)}
            bookmarksEnabled={Boolean(token)}
            allLabel="Все плагины"
            bookmarksLabel="Закладки"
          />
        </Container>
      </Box>

      <Container maxWidth="xl" sx={{ flex: 1, overflowY: 'auto', py: 3 }}>
        {loading && plugins.length === 0 ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
            <CircularProgress />
          </Box>
        ) : filtered.length === 0 ? (
          <Alert severity={loadError || !platformEnabled ? 'warning' : 'info'}>{emptyMessage}</Alert>
        ) : (
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', md: 'repeat(2, 1fr)', lg: 'repeat(3, 1fr)' },
              gap: 3,
            }}
          >
            {filtered.map((plugin) => (
              <Card
                key={plugin.id}
                sx={getGalleryCardGlassSx(isDarkMode, { clickable: false })}
              >
                <CardContent sx={{ flex: 1 }}>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1, gap: 1 }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', flex: 1, minWidth: 0 }}>
                      <Typography variant="h6" fontWeight="bold">
                        {plugin.display_name}
                      </Typography>
                      <Chip
                        size="small"
                        label={
                          !plugin.enabled
                            ? 'выкл'
                            : plugin.healthy === true
                              ? 'online'
                              : plugin.healthy === false
                                ? 'offline'
                                : healthLoading
                                  ? '…'
                                  : '—'
                        }
                        color={
                          !plugin.enabled
                            ? 'default'
                            : plugin.healthy === true
                              ? 'success'
                              : plugin.healthy === false
                                ? 'error'
                                : 'default'
                        }
                        variant="outlined"
                      />
                    </Box>
                    <Tooltip title={bookmarkedIds.has(plugin.id) ? 'Удалить из закладок' : 'Добавить в закладки'}>
                      <IconButton
                        size="small"
                        onClick={() => handleToggleBookmark(plugin.id)}
                        aria-label="Закладка"
                      >
                        {bookmarkedIds.has(plugin.id) ? (
                          <BookmarkIcon fontSize="small" color="primary" />
                        ) : (
                          <BookmarkBorderIcon fontSize="small" />
                        )}
                      </IconButton>
                    </Tooltip>
                  </Box>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                    {plugin.id}
                    {plugin.category ? ` · ${plugin.category}` : ''}
                  </Typography>
                  {plugin.enabled && plugin.healthy === false ? (
                    <Typography
                      variant="caption"
                      color="error"
                      sx={{ display: 'block', mb: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
                    >
                      {typeof plugin.health_detail?.error === 'string'
                        ? plugin.health_detail.error
                        : 'Сервис недоступен (health check failed). Смотрите логи backend: Plugin health'}
                    </Typography>
                  ) : null}
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{
                      mb: 1.5,
                      display: '-webkit-box',
                      WebkitLineClamp: 5,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                    }}
                  >
                    {plugin.description || 'Без описания'}
                  </Typography>
                  {(plugin.tags || []).length > 0 && (
                    <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                      {plugin.tags!.map((tag) => (
                        <Chip key={tag} size="small" label={tag} />
                      ))}
                    </Box>
                  )}
                  {(() => {
                    const run = getRun(plugin.id);
                    if (!run) return null;
                    const isRun = run.status === 'running';
                    return (
                      <Typography
                        variant="caption"
                        color={isRun ? 'info.main' : run.status === 'error' ? 'error' : 'success.main'}
                        sx={{ display: 'block', mt: 1, fontWeight: 600 }}
                      >
                        {isRun
                          ? `Аудит выполняется — ${formatDuration(runElapsedSec(plugin.id))} · ${run.modelFileName}`
                          : run.status === 'error'
                            ? `Последний запуск не завершён · ${run.modelFileName}`
                            : `Результат готов · ${run.modelFileName}`}
                      </Typography>
                    );
                  })()}
                </CardContent>
                <CardActions>
                  <Button
                    size="small"
                    startIcon={
                      isRunning(plugin.id) ? <CircularProgress size={14} color="inherit" /> : <RunIcon />
                    }
                    variant="contained"
                    fullWidth
                    disabled={!plugin.enabled}
                    onClick={() => openRunDialog(plugin)}
                  >
                    {isRunning(plugin.id)
                      ? 'Идёт аудит — открыть'
                      : getRun(plugin.id)
                        ? 'Открыть результат'
                        : 'Запустить'}
                  </Button>
                </CardActions>
              </Card>
            ))}
          </Box>
        )}
      </Container>

      <Dialog open={Boolean(runPlugin)} onClose={closeRunDialog} fullWidth maxWidth="md">
        <DialogTitle>Запуск: {runPlugin?.display_name}</DialogTitle>
        <DialogContent sx={{ maxHeight: '80vh', overflowY: 'auto' }}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {runPlugin?.id === 'cash-flow'
              ? 'Ответ сервера показывается прямо в этом окне: markdown-превью, исходный текст и JSON. В чат результат сам не попадает. Прошлый результат сохраняется до запуска с новым файлом.'
              : 'Передайте входные файлы плагина.'}
          </Typography>
          {running && (
            <Alert severity="info" sx={{ mb: 2 }}>
              Выполняется аудит… {formatDuration(elapsedSec)}. При полном режиме с LLM это может
              занять десятки минут. Окно можно закрыть и уйти в чат — аудит продолжится, а результат
              будет ждать здесь. Не перезагружайте страницу: это оборвёт запрос.
            </Alert>
          )}
          {runError && !running && (
            <Alert severity="error" sx={{ mb: 2 }}>
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {runError}
              </Typography>
              {elapsedSec > 0 && (
                <Typography variant="caption" color="text.secondary">
                  Прервано через {formatDuration(elapsedSec)} после запуска.
                </Typography>
              )}
            </Alert>
          )}
          {runSummary && !running && (
            <Alert severity="success" sx={{ mb: 2 }}>
              {runSummary}
            </Alert>
          )}
          {renderResultBlock()}
          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
            Входные данные
          </Typography>
          <Button variant="outlined" component="label" sx={{ mr: 1, mb: 1 }}>
            Модель Excel *
            <input
              hidden
              type="file"
              accept=".xlsx,.xlsm,.xltx,.xltm"
              onChange={(e) => setModelFile(e.target.files?.[0] || null)}
            />
          </Button>
          <Typography variant="caption" sx={{ display: 'inline', mr: 2 }}>
            {modelFile?.name || 'не выбран'}
          </Typography>
          <Button variant="outlined" component="label" sx={{ mr: 1, mb: 1 }}>
            Файл качества
            <input
              hidden
              type="file"
              accept=".xlsx,.xlsm,.xltx,.xltm"
              onChange={(e) => setQualityFile(e.target.files?.[0] || null)}
            />
          </Button>
          <Typography variant="caption" sx={{ display: 'block', mb: 2 }}>
            {qualityFile?.name || ''}
          </Typography>
          <TextField
            fullWidth
            multiline
            minRows={2}
            label="Промпт аналитика (опционально)"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            sx={{ mb: 2 }}
          />
          {running && (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
              <CircularProgress />
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={closeRunDialog}>{running ? 'Закрыть (аудит продолжится)' : 'Закрыть'}</Button>
          <Button onClick={handleRun} variant="contained" disabled={running || !modelFile}>
            {running ? 'Выполняется…' : 'Запустить аудит'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

