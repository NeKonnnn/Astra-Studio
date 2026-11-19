import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  TextField,
  Button,
  Alert,
  CircularProgress,
  Tabs,
  Tab,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  IconButton,
  Chip,
  Divider,
  Tooltip,
} from '@mui/material';
import {
  Image as ImageIcon,
  OpenInNew as OpenInNewIcon,
  Upload as UploadIcon,
  AutoFixHigh as AnalyzeIcon,
  Save as SaveIcon,
  Add as AddIcon,
  Delete as DeleteIcon,
  Fullscreen as FullscreenIcon,
} from '@mui/icons-material';
import { getApiUrl } from '../../config/api';
import { useAppActions } from '../../contexts/AppContext';
import { MODEL_SETTINGS_CARD_SX, MODEL_SETTINGS_SECTION_TITLE_SX } from '../../constants/modelSettingsStyles';
import {
  writeSelectedImageGenPresetId,
  type ImageGenPreset,
} from '../../utils/imageGenerationPresets';

type NodeMapEntry = { node: string; input: string };
type NodeMap = Record<string, NodeMapEntry>;

type StatusPayload = {
  enabled: boolean;
  configured: boolean;
  comfyui_reachable?: boolean | null;
  comfyui_error?: string | null;
  workflow_resolved?: string | null;
  has_node_map: boolean;
  available_checkpoints?: string[];
  comfyui_public_url?: string;
  default_width?: number;
  default_height?: number;
  default_steps?: number;
  workflow_path?: string;
  checkpoint_name?: string;
  node_map?: NodeMap;
};

type WorkflowItem = {
  filename: string;
  workflow_path: string;
  size_bytes?: number;
  modified_at?: string;
};

type WorkflowAnalysis = {
  suggested_node_map?: NodeMap;
  nodes?: Array<{ id: string; class_type: string; inputs: string[] }>;
  stats?: Record<string, number>;
};

type CustomPresetDraft = ImageGenPreset & {
  node_map?: NodeMap;
  custom?: boolean;
};

const EMPTY_PRESET = (): CustomPresetDraft => ({
  id: '',
  label: '',
  description: '',
  workflow_path: '',
  checkpoint_name: '',
  default_width: 1024,
  default_height: 1024,
  default_steps: 4,
  node_map: {},
});

function fileToDataUri(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

const COMFYUI_TAB_INDEX = 3;

type ImageGenerationSettingsSectionProps = {
  /** Переключить на вкладку ComfyUI и проскроллить к секции. */
  openComfyUiTab?: boolean;
  onComfyUiTabHandled?: () => void;
};

export default function ImageGenerationSettingsSection({
  openComfyUiTab = false,
  onComfyUiTabHandled,
}: ImageGenerationSettingsSectionProps = {}) {
  const { showNotification } = useAppActions();
  const [tab, setTab] = useState(0);
  const sectionRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!openComfyUiTab) return;
    setTab(COMFYUI_TAB_INDEX);
    const t = window.setTimeout(() => {
      sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      onComfyUiTabHandled?.();
    }, 80);
    return () => window.clearTimeout(t);
  }, [openComfyUiTab, onComfyUiTabHandled]);
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [presets, setPresets] = useState<CustomPresetDraft[]>([]);
  const [defaultPresetId, setDefaultPresetId] = useState('');
  const [workflows, setWorkflows] = useState<WorkflowItem[]>([]);
  const [loadingStatus, setLoadingStatus] = useState(true);

  // Генерация
  const [selectedPresetId, setSelectedPresetId] = useState('');
  const [prompt, setPrompt] = useState('');
  const [negativePrompt, setNegativePrompt] = useState('');
  const [width, setWidth] = useState('');
  const [height, setHeight] = useState('');
  const [steps, setSteps] = useState('');
  const [cfg, setCfg] = useState('');
  const [denoise, setDenoise] = useState('');
  const [seed, setSeed] = useState('');
  const [referencePreview, setReferencePreview] = useState<string | null>(null);
  const [referenceDataUri, setReferenceDataUri] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);

  // Workflow editor
  const [wfFilename, setWfFilename] = useState('my_pipeline.json');
  const [wfJson, setWfJson] = useState('');
  const [wfAnalysis, setWfAnalysis] = useState<WorkflowAnalysis | null>(null);
  const [wfBusy, setWfBusy] = useState(false);

  // Custom preset editor
  const [editingPreset, setEditingPreset] = useState<CustomPresetDraft | null>(null);
  const [presetDialogOpen, setPresetDialogOpen] = useState(false);

  // ComfyUI embed
  const [comfyOpen, setComfyOpen] = useState(false);

  const selectedPreset = useMemo(
    () => presets.find((p) => p.id === selectedPresetId) || presets[0],
    [presets, selectedPresetId],
  );

  const loadStatus = useCallback(async () => {
    setLoadingStatus(true);
    try {
      const r = await fetch(getApiUrl('/api/image-generation/status'));
      if (!r.ok) throw new Error(String(r.status));
      const data = (await r.json()) as StatusPayload;
      setStatus(data);
    } catch {
      setStatus(null);
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  const loadPresets = useCallback(async () => {
    try {
      const r = await fetch(getApiUrl('/api/image-generation/presets'));
      if (!r.ok) return;
      const data = await r.json();
      const list = Array.isArray(data.presets) ? data.presets : [];
      setPresets(list);
      const def = data.default_preset_id || list[0]?.id || '';
      setDefaultPresetId(def);
      setSelectedPresetId((prev) => (list.some((p: ImageGenPreset) => p.id === prev) ? prev : def));
    } catch {
      // ignore
    }
  }, []);

  const loadWorkflows = useCallback(async () => {
    try {
      const r = await fetch(getApiUrl('/api/image-generation/workflows'));
      if (!r.ok) return;
      const data = await r.json();
      setWorkflows(Array.isArray(data) ? data : []);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    void loadPresets();
    void loadWorkflows();
  }, [loadStatus, loadPresets, loadWorkflows]);

  useEffect(() => {
    if (!selectedPreset) return;
    if (!width) setWidth(String(selectedPreset.default_width || ''));
    if (!height) setHeight(String(selectedPreset.default_height || ''));
    if (!steps) setSteps(String(selectedPreset.default_steps || ''));
  }, [selectedPreset, width, height, steps]);

  const applyPresetDefaults = (preset: CustomPresetDraft) => {
    setWidth(String(preset.default_width || ''));
    setHeight(String(preset.default_height || ''));
    setSteps(String(preset.default_steps || ''));
  };

  const handleGenerate = async () => {
    const p = prompt.trim();
    if (!p) {
      showNotification('warning', 'Введите промпт');
      return;
    }
    setBusy(true);
    setPreviewSrc(null);
    try {
      const body: Record<string, unknown> = {
        prompt: p,
        preset_id: selectedPresetId || undefined,
      };
      const w = parseInt(width, 10);
      const h = parseInt(height, 10);
      const s = parseInt(steps, 10);
      const sd = parseInt(seed, 10);
      const cfgVal = parseFloat(cfg);
      const denoiseVal = parseFloat(denoise);
      if (!Number.isNaN(w)) body.width = w;
      if (!Number.isNaN(h)) body.height = h;
      if (!Number.isNaN(s)) body.steps = s;
      if (!Number.isNaN(sd)) body.seed = sd;
      if (!Number.isNaN(cfgVal)) body.cfg = cfgVal;
      if (!Number.isNaN(denoiseVal)) body.denoise = denoiseVal;
      if (negativePrompt.trim()) body.negative_prompt = negativePrompt.trim();
      if (referenceDataUri) body.reference_image_data_uri = referenceDataUri;

      const r = await fetch(getApiUrl('/api/image-generation/generate'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = typeof data?.detail === 'string' ? data.detail : JSON.stringify(data?.detail || data);
        throw new Error(detail || `HTTP ${r.status}`);
      }
      const imgs = Array.isArray(data?.images) ? data.images : [];
      const uri = imgs[0]?.data_uri as string | undefined;
      if (uri) {
        setPreviewSrc(uri);
        showNotification('success', 'Изображение готово');
      } else {
        showNotification('warning', 'Ответ без изображений');
      }
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка генерации');
    } finally {
      setBusy(false);
    }
  };

  const handleReferenceFile = async (file: File | null) => {
    if (!file) {
      setReferencePreview(null);
      setReferenceDataUri(null);
      return;
    }
    try {
      const uri = await fileToDataUri(file);
      setReferenceDataUri(uri);
      setReferencePreview(uri);
      showNotification('info', 'Референс загружен — будет передан в ComfyUI (LoadImage)');
    } catch {
      showNotification('error', 'Не удалось прочитать файл');
    }
  };

  const loadWorkflowIntoEditor = async (filename: string) => {
    setWfBusy(true);
    try {
      const r = await fetch(getApiUrl(`/api/image-generation/workflows/${encodeURIComponent(filename)}`));
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      setWfFilename(filename);
      setWfJson(JSON.stringify(data.workflow, null, 2));
      showNotification('success', `Workflow ${filename} загружен`);
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка загрузки workflow');
    } finally {
      setWfBusy(false);
    }
  };

  const analyzeWorkflow = async () => {
    setWfBusy(true);
    try {
      let body: Record<string, unknown>;
      if (wfJson.trim()) {
        body = { workflow: JSON.parse(wfJson) };
      } else if (wfFilename) {
        body = { filename: wfFilename };
      } else {
        throw new Error('Вставьте JSON workflow или выберите файл');
      }
      const r = await fetch(getApiUrl('/api/image-generation/workflows/analyze'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Ошибка анализа');
      setWfAnalysis(data as WorkflowAnalysis);
      showNotification('success', 'node_map предложен автоматически');
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка анализа');
    } finally {
      setWfBusy(false);
    }
  };

  const saveWorkflow = async () => {
    setWfBusy(true);
    try {
      const workflow = JSON.parse(wfJson);
      const r = await fetch(getApiUrl('/api/image-generation/workflows'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: wfFilename, workflow }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Ошибка сохранения');
      setWfAnalysis(data.analysis as WorkflowAnalysis);
      await loadWorkflows();
      showNotification('success', `Workflow сохранён: ${data.saved?.filename || wfFilename}`);
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка сохранения workflow');
    } finally {
      setWfBusy(false);
    }
  };

  const openPresetEditor = (preset?: CustomPresetDraft) => {
    if (preset) {
      setEditingPreset({
        ...preset,
        node_map: preset.node_map || status?.node_map || {},
      });
    } else {
      const draft = EMPTY_PRESET();
      draft.id = `custom_${Date.now()}`;
      draft.workflow_path = workflows[0]?.workflow_path || status?.workflow_path || '';
      draft.node_map = wfAnalysis?.suggested_node_map || status?.node_map || {};
      setEditingPreset(draft);
    }
    setPresetDialogOpen(true);
  };

  const saveCustomPresets = async (nextPresets: CustomPresetDraft[], defId: string) => {
    try {
      const r = await fetch(getApiUrl('/api/image-generation/user-presets'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ presets: nextPresets.filter((p) => p.custom), default_preset_id: defId }),
      });
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(typeof data?.detail === 'string' ? data.detail : String(r.status));
      }
      await loadPresets();
      if (defId) {
        setDefaultPresetId(defId);
        writeSelectedImageGenPresetId(defId);
      }
      showNotification('success', 'Пресеты сохранены');
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка сохранения пресетов');
    }
  };

  const saveEditingPreset = async () => {
    if (!editingPreset?.id || !editingPreset.label.trim()) {
      showNotification('warning', 'Укажите id и название пресета');
      return;
    }
    const row: CustomPresetDraft = { ...editingPreset, custom: true };
    const existing = presets.filter((p) => p.custom && p.id !== row.id);
    const next = [...presets.filter((p) => !p.custom), ...existing, row];
    setPresets(next);
    setPresetDialogOpen(false);
    await saveCustomPresets(next.filter((p) => p.custom), row.id);
    setSelectedPresetId(row.id);
    writeSelectedImageGenPresetId(row.id);
  };

  const deleteCustomPreset = async (id: string) => {
    const next = presets.filter((p) => p.id !== id);
    setPresets(next);
    const def = next[0]?.id || '';
    await saveCustomPresets(next.filter((p) => p.custom), def);
  };

  const createPresetFromWorkflow = () => {
    const draft = EMPTY_PRESET();
    draft.id = wfFilename.replace(/\.json$/i, '') || `pipeline_${Date.now()}`;
    draft.label = draft.id.replace(/_/g, ' ');
    draft.workflow_path = `config/comfy_workflows/${wfFilename}`;
    draft.node_map = wfAnalysis?.suggested_node_map || status?.node_map || {};
    setEditingPreset(draft);
    setPresetDialogOpen(true);
  };

  const comfyUrl = status?.comfyui_public_url || 'http://127.0.0.1:8188';

  const statusAlerts = (
    <>
      {!status?.enabled && (
        <Alert severity="info" sx={{ mb: 1 }}>
          Выключено: в <code>config.yml</code> задайте <code>image_generation.enabled: true</code>.
        </Alert>
      )}
      {status?.enabled && (!status.configured || !status.has_node_map) && (
        <Alert severity="warning" sx={{ mb: 1 }}>
          Заполните workflow, node_map и запустите ComfyUI с <code>--listen</code>.
        </Alert>
      )}
      {status?.comfyui_reachable === false && (
        <Alert severity="error" sx={{ mb: 1 }}>
          ComfyUI недоступен: {status.comfyui_error || 'нет ответа'}
        </Alert>
      )}
      {status?.comfyui_reachable === true && (
        <Alert severity="success" sx={{ mb: 1 }}>
          ComfyUI отвечает
          {Array.isArray(status.available_checkpoints) && status.available_checkpoints.length > 0
            ? ` — checkpoints: ${status.available_checkpoints.join(', ')}`
            : ' — checkpoints не найдены'}
        </Alert>
      )}
    </>
  );

  return (
    <Card ref={sectionRef} sx={MODEL_SETTINGS_CARD_SX}>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 1, mb: 1 }}>
          <Typography variant="h6" sx={MODEL_SETTINGS_SECTION_TITLE_SX}>
            <ImageIcon color="primary" sx={{ mr: 0.75 }} />
            Генерация изображений (ComfyUI)
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            <Button
              size="small"
              variant="outlined"
              startIcon={<OpenInNewIcon />}
              href={comfyUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              ComfyUI в новой вкладке
            </Button>
            <Button size="small" variant="contained" startIcon={<FullscreenIcon />} onClick={() => setComfyOpen(true)}>
              ComfyUI здесь
            </Button>
            <Button size="small" variant="text" onClick={() => void loadStatus()} disabled={loadingStatus}>
              Обновить статус
            </Button>
          </Box>
        </Box>

        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Настройте pipeline как в ComfyUI: сохраните workflow (API format), привяжите node_map к промпту, шагам,
          denoise, ControlNet/референсу. В чате: включите <strong>Инструменты → Режим генерации</strong>.
        </Typography>

        {loadingStatus ? (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <CircularProgress size={20} />
            <Typography variant="body2">Проверка конфигурации…</Typography>
          </Box>
        ) : (
          statusAlerts
        )}

        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}>
          <Tab label="Пресеты" />
          <Tab label="Генерация" />
          <Tab label="Workflows" />
          <Tab label="ComfyUI" />
        </Tabs>

        {tab === 0 && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <Alert severity="warning">
              Для чата включите <strong>Инструменты → Режим генерации</strong> и выберите пресет в
              {' '}<strong>Модели → Изображения</strong> (это не LLM-модель).
              Активный пресет отображается под кнопкой «Модели» в чате.
            </Alert>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
              <Button startIcon={<AddIcon />} variant="outlined" onClick={() => openPresetEditor()}>
                Свой пресет
              </Button>
              <Typography variant="caption" color="text.secondary">
                Пресет = модель: workflow + checkpoint + размеры + свой node_map для многоэтапных pipeline
              </Typography>
            </Box>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2 }}>
              {presets.map((preset) => (
                <Box key={preset.id}>
                  <Card
                    variant="outlined"
                    onClick={() => {
                      setSelectedPresetId(preset.id);
                      writeSelectedImageGenPresetId(preset.id);
                      applyPresetDefaults(preset);
                    }}
                    sx={{
                      borderColor: preset.id === selectedPresetId ? 'primary.main' : 'divider',
                      bgcolor: preset.id === defaultPresetId ? 'action.hover' : 'background.paper',
                      cursor: 'pointer',
                    }}
                  >
                    <CardContent sx={{ pb: '12px !important' }}>
                      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 1 }}>
                        <Box>
                          <Typography variant="subtitle1">{preset.label}</Typography>
                          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                            {preset.description || preset.id}
                          </Typography>
                          <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                            {preset.custom && <Chip size="small" label="свой" color="secondary" />}
                            {preset.id === defaultPresetId && <Chip size="small" label="по умолчанию" color="primary" />}
                            {preset.available === false && <Chip size="small" label="checkpoint нет" color="warning" />}
                          </Box>
                        </Box>
                        {preset.custom && (
                          <IconButton size="small" color="error" onClick={() => void deleteCustomPreset(preset.id)}>
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        )}
                      </Box>
                      <Typography variant="caption" display="block" sx={{ mt: 1, wordBreak: 'break-all' }}>
                        {preset.workflow_path}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {preset.default_width}×{preset.default_height}, {preset.default_steps} шагов
                        {preset.checkpoint_name ? ` · ${preset.checkpoint_name}` : ''}
                      </Typography>
                      <Box sx={{ display: 'flex', gap: 1, mt: 1.5, flexWrap: 'wrap' }}>
                        <Button
                          size="small"
                          variant={preset.id === selectedPresetId ? 'contained' : 'outlined'}
                          onClick={() => {
                            setSelectedPresetId(preset.id);
                            writeSelectedImageGenPresetId(preset.id);
                            applyPresetDefaults(preset);
                          }}
                        >
                          Выбрать
                        </Button>
                        <Button
                          size="small"
                          onClick={() => {
                            setDefaultPresetId(preset.id);
                            writeSelectedImageGenPresetId(preset.id);
                            void saveCustomPresets(presets.filter((p) => p.custom), preset.id);
                          }}
                        >
                          По умолчанию
                        </Button>
                        {preset.custom && (
                          <Button size="small" onClick={() => openPresetEditor(preset)}>
                            Изменить
                          </Button>
                        )}
                      </Box>
                    </CardContent>
                  </Card>
                </Box>
              ))}
            </Box>
          </Box>
        )}

        {tab === 1 && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, maxWidth: 720 }}>
            <FormControl fullWidth size="small">
              <InputLabel>Пресет (pipeline)</InputLabel>
              <Select
                label="Пресет (pipeline)"
                value={selectedPresetId}
                onChange={(e) => {
                  const id = String(e.target.value);
                  setSelectedPresetId(id);
                  writeSelectedImageGenPresetId(id);
                  const p = presets.find((x) => x.id === id);
                  if (p) applyPresetDefaults(p);
                }}
              >
                {presets.map((p) => (
                  <MenuItem key={p.id} value={p.id}>
                    {p.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <TextField label="Промпт" value={prompt} onChange={(e) => setPrompt(e.target.value)} multiline minRows={2} disabled={busy} />
            <TextField
              label="Негативный промпт (если есть в node_map)"
              value={negativePrompt}
              onChange={(e) => setNegativePrompt(e.target.value)}
              multiline
              minRows={1}
              disabled={busy}
            />

            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
              <TextField label="Ширина" value={width} onChange={(e) => setWidth(e.target.value)} type="number" sx={{ width: 120 }} disabled={busy} />
              <TextField label="Высота" value={height} onChange={(e) => setHeight(e.target.value)} type="number" sx={{ width: 120 }} disabled={busy} />
              <TextField label="Шаги" value={steps} onChange={(e) => setSteps(e.target.value)} type="number" sx={{ width: 120 }} disabled={busy} />
              <TextField label="CFG" value={cfg} onChange={(e) => setCfg(e.target.value)} type="number" sx={{ width: 100 }} disabled={busy} />
              <TextField label="Denoise" value={denoise} onChange={(e) => setDenoise(e.target.value)} type="number" inputProps={{ step: 0.05, min: 0, max: 1 }} sx={{ width: 110 }} disabled={busy} />
              <TextField label="Seed" value={seed} onChange={(e) => setSeed(e.target.value)} type="number" sx={{ width: 140 }} disabled={busy} />
            </Box>

            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Референс (ControlNet / img2img)
              </Typography>
              <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                <Button component="label" variant="outlined" startIcon={<UploadIcon />} disabled={busy}>
                  Загрузить референс
                  <input
                    type="file"
                    hidden
                    accept="image/*"
                    onChange={(e) => void handleReferenceFile(e.target.files?.[0] || null)}
                  />
                </Button>
                {referencePreview && (
                  <Box
                    component="img"
                    src={referencePreview}
                    alt="Референс"
                    sx={{ height: 64, borderRadius: 1, border: 1, borderColor: 'divider' }}
                  />
                )}
                {referencePreview && (
                  <Button size="small" onClick={() => void handleReferenceFile(null)}>
                    Убрать
                  </Button>
                )}
              </Box>
              <Typography variant="caption" color="text.secondary">
                Нужна нода LoadImage в workflow и ключ <code>reference_image</code> в node_map
              </Typography>
            </Box>

            <Button variant="contained" onClick={() => void handleGenerate()} disabled={busy}>
              {busy ? (
                <>
                  <CircularProgress size={18} sx={{ mr: 1 }} color="inherit" />
                  Генерация…
                </>
              ) : (
                'Сгенерировать'
              )}
            </Button>

            {previewSrc && (
              <Box>
                <Typography variant="subtitle2" gutterBottom>
                  Результат
                </Typography>
                <Box component="img" src={previewSrc} alt="Результат" sx={{ maxWidth: '100%', borderRadius: 1, border: 1, borderColor: 'divider' }} />
              </Box>
            )}
          </Box>
        )}

        {tab === 2 && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <Alert severity="info">
              В ComfyUI: меню → Save (API Format). Вставьте JSON сюда или загрузите существующий файл. Для upscale /
              multi-stage pipeline сохраните весь граф — AstraChat подставит только поля из node_map.
            </Alert>

            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
              <FormControl size="small" sx={{ minWidth: 260 }}>
                <InputLabel>Файлы workflow</InputLabel>
                <Select
                  label="Файлы workflow"
                  value={wfFilename}
                  onChange={(e) => setWfFilename(String(e.target.value))}
                >
                  {workflows.map((w) => (
                    <MenuItem key={w.filename} value={w.filename}>
                      {w.filename}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <Button variant="outlined" disabled={wfBusy} onClick={() => void loadWorkflowIntoEditor(wfFilename)}>
                Загрузить
              </Button>
              <Button variant="outlined" startIcon={<AnalyzeIcon />} disabled={wfBusy} onClick={() => void analyzeWorkflow()}>
                Анализ node_map
              </Button>
              <Button variant="contained" startIcon={<SaveIcon />} disabled={wfBusy} onClick={() => void saveWorkflow()}>
                Сохранить
              </Button>
              <Button variant="text" onClick={() => void createPresetFromWorkflow()}>
                Создать пресет из workflow
              </Button>
            </Box>

            <TextField
              label="Имя файла"
              value={wfFilename}
              onChange={(e) => setWfFilename(e.target.value)}
              size="small"
              sx={{ maxWidth: 360 }}
              helperText="config/comfy_workflows/…"
            />

            <TextField
              label="Workflow JSON (API format)"
              value={wfJson}
              onChange={(e) => setWfJson(e.target.value)}
              multiline
              minRows={12}
              maxRows={24}
              fullWidth
              InputProps={{ sx: { fontFamily: 'monospace', fontSize: 12 } }}
            />

            {wfAnalysis && (
              <Box>
                <Typography variant="subtitle2" gutterBottom>
                  Предложенный node_map
                </Typography>
                <Box component="pre" sx={{ p: 1.5, bgcolor: 'action.hover', borderRadius: 1, overflow: 'auto', fontSize: 12 }}>
                  {JSON.stringify(wfAnalysis.suggested_node_map, null, 2)}
                </Box>
                {wfAnalysis.stats && (
                  <Typography variant="caption" color="text.secondary">
                    Ноды: {Object.entries(wfAnalysis.stats).map(([k, v]) => `${k}=${v}`).join(', ')}
                  </Typography>
                )}
              </Box>
            )}

            {status?.node_map && Object.keys(status.node_map).length > 0 && (
              <>
                <Divider />
                <Typography variant="subtitle2">Глобальный node_map (config.yml)</Typography>
                <Box component="pre" sx={{ p: 1.5, bgcolor: 'action.hover', borderRadius: 1, overflow: 'auto', fontSize: 12 }}>
                  {JSON.stringify(status.node_map, null, 2)}
                </Box>
              </>
            )}
          </Box>
        )}

        {tab === 3 && (
          <Box>
            <Alert severity="info" sx={{ mb: 2 }}>
              Если видите <strong>HTTP 403</strong>: ComfyUI блокирует запросы с другого origin (AstraChat на :3000).
              Пересоберите контейнер: <code>docker compose build comfyui &amp;&amp; docker compose up -d comfyui</code>
              (включён <code>--enable-cors-header</code>). Открывайте также через <code>localhost:8188</code>, не только 127.0.0.1.
            </Alert>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Полный интерфейс ComfyUI: соберите pipeline (upscale, ControlNet, несколько KSampler), сохраните в API
              format и импортируйте на вкладке Workflows.
            </Typography>
            <Box
              sx={{
                width: '100%',
                height: 600,
                border: 1,
                borderColor: 'divider',
                borderRadius: 1,
                overflow: 'hidden',
                bgcolor: '#1a1a1a',
              }}
            >
              <iframe
                title="ComfyUI"
                src={comfyUrl}
                style={{ width: '100%', height: '100%', border: 'none' }}
              />
            </Box>
          </Box>
        )}
      </CardContent>

      <Dialog open={comfyOpen} onClose={() => setComfyOpen(false)} fullScreen>
        <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          ComfyUI
          <Button href={comfyUrl} target="_blank" rel="noopener noreferrer" startIcon={<OpenInNewIcon />}>
            Открыть отдельно
          </Button>
        </DialogTitle>
        <DialogContent sx={{ p: 0 }}>
          <iframe title="ComfyUI fullscreen" src={comfyUrl} style={{ width: '100%', height: '100%', border: 'none', minHeight: '80vh' }} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setComfyOpen(false)}>Закрыть</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={presetDialogOpen} onClose={() => setPresetDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Свой пресет pipeline</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
          {editingPreset && (
            <>
              <TextField label="ID" value={editingPreset.id} onChange={(e) => setEditingPreset({ ...editingPreset, id: e.target.value })} />
              <TextField label="Название" value={editingPreset.label} onChange={(e) => setEditingPreset({ ...editingPreset, label: e.target.value })} />
              <TextField label="Описание" value={editingPreset.description || ''} onChange={(e) => setEditingPreset({ ...editingPreset, description: e.target.value })} multiline />
              <FormControl fullWidth size="small">
                <InputLabel>Workflow</InputLabel>
                <Select
                  label="Workflow"
                  value={editingPreset.workflow_path || ''}
                  onChange={(e) => setEditingPreset({ ...editingPreset, workflow_path: String(e.target.value) })}
                >
                  {workflows.map((w) => (
                    <MenuItem key={w.workflow_path} value={w.workflow_path}>
                      {w.filename}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <FormControl fullWidth size="small">
                <InputLabel>Checkpoint</InputLabel>
                <Select
                  label="Checkpoint"
                  value={editingPreset.checkpoint_name || ''}
                  onChange={(e) => setEditingPreset({ ...editingPreset, checkpoint_name: String(e.target.value) })}
                >
                  <MenuItem value="">(из workflow)</MenuItem>
                  {(status?.available_checkpoints || []).map((c) => (
                    <MenuItem key={c} value={c}>
                      {c}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                <TextField label="Ширина" type="number" value={editingPreset.default_width} onChange={(e) => setEditingPreset({ ...editingPreset, default_width: parseInt(e.target.value, 10) || 1024 })} sx={{ width: 120 }} />
                <TextField label="Высота" type="number" value={editingPreset.default_height} onChange={(e) => setEditingPreset({ ...editingPreset, default_height: parseInt(e.target.value, 10) || 1024 })} sx={{ width: 120 }} />
                <TextField label="Шаги" type="number" value={editingPreset.default_steps} onChange={(e) => setEditingPreset({ ...editingPreset, default_steps: parseInt(e.target.value, 10) || 4 })} sx={{ width: 120 }} />
              </Box>
              <TextField
                label="node_map (JSON)"
                value={JSON.stringify(editingPreset.node_map || {}, null, 2)}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(e.target.value) as NodeMap;
                    setEditingPreset({ ...editingPreset, node_map: parsed });
                  } catch {
                    // allow typing
                  }
                }}
                multiline
                minRows={6}
                InputProps={{ sx: { fontFamily: 'monospace', fontSize: 12 } }}
                helperText="Ключи: prompt, negative_prompt, width, height, steps, seed, cfg, denoise, checkpoint, reference_image"
              />
              <Tooltip title="Подставить результат анализа workflow">
                <Button
                  size="small"
                  startIcon={<AnalyzeIcon />}
                  onClick={() => {
                    if (wfAnalysis?.suggested_node_map) {
                      setEditingPreset({ ...editingPreset, node_map: wfAnalysis.suggested_node_map });
                    }
                  }}
                >
                  Взять из анализа workflow
                </Button>
              </Tooltip>
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPresetDialogOpen(false)}>Отмена</Button>
          <Button variant="contained" onClick={() => void saveEditingPreset()}>
            Сохранить пресет
          </Button>
        </DialogActions>
      </Dialog>
    </Card>
  );
}
