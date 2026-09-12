import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  Box,
  Typography,
  TextField,
  Button,
  IconButton,
  Select,
  MenuItem,
  FormControl,
  Checkbox,
  FormControlLabel,
  Switch,
  Tooltip,
  CircularProgress,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  ListItemIcon,
  Chip,
  Alert,
  InputLabel,
  OutlinedInput,
  InputAdornment,
  Popover,
  alpha,
} from '@mui/material';
import type { SxProps, Theme } from '@mui/material';
import {
  Close as CloseIcon,
  Delete as DeleteIcon,
  Upload as UploadIcon,
  Code as CodeIcon,
  Search as SearchIcon,
  AttachFile as AttachIcon,
  Extension as ToolsIcon,
  ContactSupport as SupportIcon,
  Settings as SettingsIcon,
  History as VersionIcon,
  Save as SaveIcon,
  Share as ShareIcon,
  Public as PublicIcon,
  PublicOff as PublicOffIcon,
  Description as FileIcon,
  PictureAsPdf as PdfIcon,
  TableChart as ExcelIcon,
  TextSnippet as TxtIcon,
  Article as DocxIcon,
  HelpOutline as HelpIcon,
  CheckBox as CheckBoxIcon,
  CheckBoxOutlineBlank as CheckBoxBlankIcon,
  ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import AgentIcon from '../../icons/AgentIcon';
import { getApiUrl, API_ENDPOINTS, getAuthFetchHeaders } from '../../config/api';
import { useAuth } from '../../contexts/AuthContext';
import { useAppActions } from '../../contexts/AppContext';
import { useRagReindexStatus } from '../../contexts/RagReindexStatusContext';
import { loadAgentModelOnly } from '../../utils/applyAgentServer';
import { saveEntityRagSettings, type EntityRagDraft } from '../../utils/entityRagSettings';
import {
  fetchRagEntityDefaults,
  resolveRagEmbeddingModelPath,
  resolveRagRerankerModelPath,
} from '../../constants/ragEntityDefaults';
import {
  ASTRA_OPEN_AGENT_CONSTRUCTOR,
  ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY,
} from '../../constants/hotkeys';
import {
  getDropdownChevronSx,
  getDropdownPopoverPaperSx,
  getDropdownItemStateSx,
  getDropdownItemSx,
  getFormFieldInputSx,
  getFormFieldTriggerSx,
  getFormFieldTriggerValueSx,
  getCategoryFieldSx,
  flattenSx,
  AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX,
  AGENT_CONSTRUCTOR_FIELD_PADDING_X_PX,
  AGENT_CONSTRUCTOR_FIELD_PADDING_Y_PX,
  SIDEBAR_HIDE_SCROLLBAR_SX,
} from '../../constants/menuStyles';
import ModelParametersModal, { type ModelParamsState } from '../ModelParametersModal';
import { MODEL_SETTINGS_DEFAULT, type ModelSettingsState } from '../../constants/modelSettingsStyles';
import ShareAgentDialog from '../ShareAgentDialog';
import { fetchMcpServers } from '../../mcp/api';
import type { McpServerConfigPublic } from '../../mcp/types';
import { fetchPlugins } from '../../plugins/api';
import type { PluginPublic } from '../../plugins/types';
import { applyAgentMcpToChat, persistAgentMcpConfig } from '../../utils/applyAgentMcp';
import { persistAgentArtifactsEnabled } from '../../utils/agentArtifactsEnabled';
import RAGSettings from '../settings/RAGSettings';
import { useRagEntityReadyMessage } from '../../hooks/useRagEntityReadyMessage';
import { fetchMergedUserAgents } from '../../utils/fetchMergedUserAgents';
import { getSidebarPanelBackground, getSidebarPanelChrome, getSidebarSecondaryButtonSx } from '../../constants/sidebarPanelColor';
import RagUploadingFileThumb from '../RagUploadingFileThumb';
import RagFilesSearchField from '../RagFilesSearchField';
import { filterByRagFilenameQuery } from '../../utils/ragFilesSearch';
import AgentChainEditor from './AgentChainEditor';
import AgentSubagentsEditor, { EMPTY_SUBAGENT_CONFIG, type SubagentConfig } from './AgentSubagentsEditor';
import MaxAgentStepsField from './MaxAgentStepsField';
import { fetchAgentChainConfig, parseAgentIds } from '../../constants/agentChain';
import {
  createRagPendingUploads,
  commitRagUploadUiUpdate,
  getRagFileTypeLabel,
  mapWithConcurrency,
  mergeRagDocumentsById,
  parseRagUploadDocumentId,
  removeRagPendingUploads,
  RAG_UPLOAD_CONCURRENCY,
  type RagPendingUpload,
} from '../../utils/ragPendingUpload';

// ─── Types ───────────────────────────────────────────────────────────────────

interface KbDocument {
  id: number;
  filename: string;
  created_at: string | null;
  size: number | null;
  file_type: string | null;
  /** metadata.agent_id из SVC-RAG — документы агента, даже если config ещё не сохранён. */
  agentId: number | null;
}

interface Agent {
  id: number;
  name: string;
  description?: string;
  system_prompt: string;
  config?: Record<string, any>;
  tools?: string[];
  author_id: string;
  author_name: string;
  is_public: boolean;
  tags?: any[];
  my_permission?: 'owner' | 'editor' | 'viewer' | null;
  is_shared_with_me?: boolean;
}

interface ProviderModelItem {
  name: string;
  path: string;
  display_name?: string;
  provider_id?: string;
  llm_host_id?: string;
}

interface AgentConstructorPanelProps {
  isDarkMode: boolean;
  isOpen: boolean;
}

const CATEGORIES = ['Общий', 'Код', 'Письмо', 'Анализ', 'Исследование', 'Обучение', 'Другое'];

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Цвет квадратика-иконки по типу файла. */
function getFileIconBg(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return '#e53935';
  if (['docx', 'doc'].includes(ext)) return '#1976d2';
  if (['xlsx', 'xls', 'csv'].includes(ext)) return '#43a047';
  if (ext === 'txt') return '#607d8b';
  return '#5c6bc0';
}

/** Подпись типа файла для карточки. */
function getFileTypeLabel(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return 'PDF';
  if (['docx', 'doc'].includes(ext)) return 'Word';
  if (['xlsx', 'xls', 'csv'].includes(ext)) return 'Excel';
  if (ext === 'txt') return 'TXT';
  return 'File';
}

const fileIconSx = { fontSize: 18, color: 'white' };

function getFileIcon(filename: string) {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return <PdfIcon sx={fileIconSx} />;
  if (['xlsx', 'xls', 'csv'].includes(ext)) return <ExcelIcon sx={fileIconSx} />;
  if (ext === 'txt') return <TxtIcon sx={fileIconSx} />;
  if (['docx', 'doc'].includes(ext)) return <DocxIcon sx={fileIconSx} />;
  return <FileIcon sx={fileIconSx} />;
}

function shortFileName(name: string, max = 22): string {
  if (name.length <= max) return name;
  const ext = name.includes('.') ? '.' + name.split('.').pop() : '';
  return name.slice(0, max - ext.length - 3) + '...' + ext;
}

const INSTRUCTIONS_MIN_LENGTH = 10;

function formatApiDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        typeof item === 'object' && item && 'msg' in item
          ? String((item as { msg: unknown }).msg)
          : String(item),
      )
      .join('; ');
  }
  if (detail && typeof detail === 'object' && 'msg' in detail) {
    return String((detail as { msg: unknown }).msg);
  }
  return fallback;
}

// ─── Label with tooltip ───────────────────────────────────────────────────────

function FieldLabel({ text, help, required }: { text: string; help?: string; required?: boolean }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
      <Typography variant="caption" sx={{ color: 'inherit', opacity: 0.85, fontWeight: 500, fontSize: '0.8rem' }}>
        {text}{required && <span style={{ color: '#f44336', marginLeft: 2 }}>*</span>}
      </Typography>
      {help && (
        <Tooltip title={help} placement="top" arrow>
          <HelpIcon sx={{ fontSize: 13, color: 'inherit', opacity: 0.45, cursor: 'help' }} />
        </Tooltip>
      )}
    </Box>
  );
}

// ─── Section header ───────────────────────────────────────────────────────────

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <Typography
      variant="caption"
      sx={{
        color: 'inherit',
        opacity: 0.65,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
        fontSize: '0.7rem',
      }}
    >
      {children}
    </Typography>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function AgentConstructorPanel({ isDarkMode, isOpen }: AgentConstructorPanelProps) {
  const { token, user } = useAuth();
  const ragUserId = String(user?.user_id || user?.username || '').trim().toLowerCase();
  const { showNotification } = useAppActions();
  const { notifyReindexStarted } = useRagReindexStatus();
  const [sidebarColorTick, setSidebarColorTick] = useState(0);
  const panelBg = useMemo(
    () => getSidebarPanelBackground(isDarkMode),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isDarkMode, sidebarColorTick],
  );
  const panelChrome = useMemo(() => getSidebarPanelChrome(panelBg), [panelBg]);
  const secondaryBtnSx = useMemo(() => getSidebarSecondaryButtonSx(panelChrome), [panelChrome]);
  const secondaryDashedBtnSx = useMemo(
    () => getSidebarSecondaryButtonSx(panelChrome, { dashed: true }),
    [panelChrome],
  );
  const footerActionBtnSx = useMemo(
    () => ({
      textTransform: 'none' as const,
      fontSize: '0.72rem',
      fontWeight: 600,
      py: 0.55,
      px: 1.25,
      whiteSpace: 'nowrap' as const,
      minHeight: 30,
      flex: '1 1 140px',
      justifyContent: 'flex-start',
      textAlign: 'left' as const,
      '& .MuiButton-startIcon': {
        marginRight: '8px',
        marginLeft: 0,
        color: 'inherit',
      },
      '& .MuiButton-startIcon .MuiSvgIcon-root': {
        fontSize: '0.9rem',
      },
    }),
    [],
  );
  const footerNeutralActionBtnSx = useMemo(
    () => ({
      ...footerActionBtnSx,
      color: panelChrome.fgMuted,
      border: panelChrome.buttonBorder,
      bgcolor: 'transparent',
      '&:hover': { bgcolor: panelChrome.hoverBg, color: panelChrome.fgMuted },
    }),
    [footerActionBtnSx, panelChrome],
  );
  const footerDeleteActionBtnSx = useMemo(
    () => ({
      ...footerActionBtnSx,
      color: '#ef5350',
      border: '1px solid rgba(239,83,80,0.45)',
      bgcolor: 'rgba(239,83,80,0.08)',
      '&:hover': {
        bgcolor: 'rgba(239,83,80,0.16)',
        color: '#ef5350',
        borderColor: 'rgba(239,83,80,0.55)',
      },
    }),
    [footerActionBtnSx],
  );
  useEffect(() => {
    const onColorChanged = () => setSidebarColorTick((n) => n + 1);
    window.addEventListener('sidebarColorChanged', onColorChanged);
    return () => window.removeEventListener('sidebarColorChanged', onColorChanged);
  }, []);
  /** Тёмный chrome полей/меню — только на тёмной/цветной панели; на белой — чёрный текст. */
  const darkFields = !panelChrome.isLight;
  const dropdownItemSx = useMemo(() => getDropdownItemSx(darkFields), [darkFields]);
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(darkFields), [darkFields]);
  const formFieldTriggerSx = useMemo(() => getFormFieldTriggerSx(darkFields), [darkFields]);
  const formFieldTriggerValueSx = useMemo(
    () => getFormFieldTriggerValueSx(darkFields),
    [darkFields],
  );
  const formFieldInputSx = useMemo(() => getFormFieldInputSx(darkFields), [darkFields]);

  /** Красная звёздочка у обязательного поля (MUI по умолчанию не всегда error.main). */
  const nameFieldSx = useMemo(
    () =>
      [formFieldInputSx, { '& .MuiFormLabel-asterisk': { color: '#f44336' } }] as SxProps<Theme>,
    [formFieldInputSx],
  );

  /** Multiline: убираем двойной отступ (padding на root + на textarea). */
  const instructionsFieldSx = useMemo(
    () =>
      [
        formFieldInputSx,
        {
          '& .MuiOutlinedInput-root.MuiInputBase-multiline': {
            padding: 0,
          },
          '& .MuiOutlinedInput-root.MuiInputBase-multiline .MuiOutlinedInput-input': {
            padding: `${AGENT_CONSTRUCTOR_FIELD_PADDING_Y_PX}px ${AGENT_CONSTRUCTOR_FIELD_PADDING_X_PX}px !important`,
          },
        },
      ] as SxProps<Theme>,
    [formFieldInputSx],
  );

  /** Категория / MCP / Skills: outlined без синей обводки при фокусе (открытии списка). */
  const categoryFieldSx = useMemo(
    () =>
      flattenSx(getCategoryFieldSx(darkFields), {
        '& .MuiFormLabel-asterisk': { color: '#f44336' },
      }),
    [darkFields],
  );

  const categoryOutlinedRef = useRef<HTMLDivElement>(null);

  // Agent list & selection
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<number | 'new'>('new');
  const [isLoadingAgents, setIsLoadingAgents] = useState(false);

  // Form fields
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('Общий');
  const [instructions, setInstructions] = useState('');
  const instructionsTooShort =
    instructions.length > 0 && instructions.trim().length < INSTRUCTIONS_MIN_LENGTH;
  const [model, setModel] = useState('');
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  /** Каталог моделей с провайдером (CORSUR / Phoenix …) для выбора провайдер→модель. */
  const [providerModels, setProviderModels] = useState<ProviderModelItem[]>([]);
  const [providerIds, setProviderIds] = useState<string[]>([]);

  // Capabilities
  const [codeInterpreter, setCodeInterpreter] = useState(false);
  const [webSearch, setWebSearch] = useState(false);

  // Artifacts
  const [artifactsEnabled, setArtifactsEnabled] = useState(false);
  const [shadcnEnabled, setShadcnEnabled] = useState(false);
  const [userPromptMode, setUserPromptMode] = useState(false);

  /**
   * Черновик настроек РАГ: панель их не пишет, они уезжают вместе с агентом.
   * null — не трогали, тогда у агента остаются его текущие настройки.
   */
  const [ragDraft, setRagDraft] = useState<EntityRagDraft | null>(null);

  // File search (KB)
  const [fileSearchEnabled, setFileSearchEnabled] = useState(false);
  const [kbDocuments, setKbDocuments] = useState<KbDocument[]>([]);
  /** ID документов KB, привязанных к этому агенту (config.kb_document_ids). */
  const [kbDocumentIds, setKbDocumentIds] = useState<number[]>([]);
  const [isLoadingKb, setIsLoadingKb] = useState(false);
  const [isUploadingKb, setIsUploadingKb] = useState(false);
  const [pendingKbUploads, setPendingKbUploads] = useState<RagPendingUpload[]>([]);
  const [kbFileSearchQuery, setKbFileSearchQuery] = useState('');

  // Skills attached to agent (config.skill_ids — slugs)
  const [availableSkills, setAvailableSkills] = useState<Array<{ id: number; slug: string; name: string; description?: string }>>([]);
  const [skillIds, setSkillIds] = useState<string[]>([]);
  const [skillsEnabled, setSkillsEnabled] = useState(false);

  // MCP servers (config.mcp_enabled, config.mcp_server_ids)
  const [mcpEnabled, setMcpEnabled] = useState(false);
  const [mcpServerIds, setMcpServerIds] = useState<string[]>([]);
  const [availableMcpServers, setAvailableMcpServers] = useState<McpServerConfigPublic[]>([]);
  const [mcpPopoverAnchor, setMcpPopoverAnchor] = useState<HTMLElement | null>(null);
  const mcpTriggerRef = useRef<HTMLDivElement>(null);
  const [skillsPopoverAnchor, setSkillsPopoverAnchor] = useState<HTMLElement | null>(null);
  const skillsTriggerRef = useRef<HTMLDivElement>(null);

  // Plugins (config.plugins_enabled, config.plugin_ids) — из галереи плагинов
  const [pluginsEnabled, setPluginsEnabled] = useState(false);
  const [pluginIds, setPluginIds] = useState<string[]>([]);
  const [availablePlugins, setAvailablePlugins] = useState<PluginPublic[]>([]);
  const [pluginsPopoverAnchor, setPluginsPopoverAnchor] = useState<HTMLElement | null>(null);
  const pluginsTriggerRef = useRef<HTMLDivElement>(null);

  // Sequential agent chain (config.agent_ids — LibreChat Mixture-of-Agents)
  const [chainAgentIds, setChainAgentIds] = useState<number[]>([]);
  const [hideSequentialOutputs, setHideSequentialOutputs] = useState(false);
  const [chainMaxAgents, setChainMaxAgents] = useState(10);
  const [defaultGraphSteps, setDefaultGraphSteps] = useState(50);
  const [maxRecursionLimit, setMaxRecursionLimit] = useState(500);
  const [recursionLimit, setRecursionLimit] = useState<number | ''>('');
  const [subagentsConfig, setSubagentsConfig] = useState<SubagentConfig>(EMPTY_SUBAGENT_CONFIG);

  // Support contacts
  const [supportName, setSupportName] = useState('');
  const [supportEmail, setSupportEmail] = useState('');

  // Saving
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [showModelParamsPanel, setShowModelParamsPanel] = useState(false);
  const [showAgentRagSettingsPanel, setShowAgentRagSettingsPanel] = useState(false);
  const [modelParams, setModelParams] = useState<Partial<ModelParamsState>>({});
  const [agentModelSettings, setAgentModelSettings] = useState<ModelSettingsState>({ ...MODEL_SETTINGS_DEFAULT });
  const [modelSettingsTouched, setModelSettingsTouched] = useState(false);
  const [userModelSettings, setUserModelSettings] = useState<ModelSettingsState>({ ...MODEL_SETTINGS_DEFAULT });
  const [agentSearchQuery, setAgentSearchQuery] = useState('');
  const [agentPopoverAnchor, setAgentPopoverAnchor] = useState<HTMLElement | null>(null);
  const [categoryPopoverAnchor, setCategoryPopoverAnchor] = useState<HTMLElement | null>(null);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  /** Оранжевая табличка у блока документов агента (нет модели / нет чекбокса). */
  const [kbGuardMessage, setKbGuardMessage] = useState<string | null>(null);

  const kbFileInputRef = useRef<HTMLInputElement>(null);
  const selectedAgentIdRef = useRef<number | 'new'>(selectedAgentId);
  selectedAgentIdRef.current = selectedAgentId;
  const showNotificationRef = useRef(showNotification);
  showNotificationRef.current = showNotification;
  const tokenRef = useRef(token);
  tokenRef.current = token;
  /** Чтобы не перезагружать данные на каждый ре-рендер родителя, пока панель открыта. */
  const bootstrappedOpenRef = useRef(false);

  // ─── Load agents ────────────────────────────────────────────────────────────

  const loadAgents = useCallback(async () => {
    setIsLoadingAgents(true);
    try {
      const merged = await fetchMergedUserAgents(tokenRef.current);
      setAgents((prev) => {
        const byId = new Map<number, Agent>();
        for (const a of merged) byId.set(a.id, a as Agent);
        // Не затирать агента, открытого из галереи (ещё не в «Мои» / shared / закладках)
        const keepId = selectedAgentIdRef.current;
        if (typeof keepId === 'number') {
          const kept = prev.find((a) => a.id === keepId);
          if (kept && !byId.has(keepId)) byId.set(keepId, kept);
        }
        return Array.from(byId.values());
      });
    } catch {
      /* silent */
    } finally {
      setIsLoadingAgents(false);
    }
  }, []);

  /** Открыть агента из галереи / по id (подтянуть актуальные данные и роль). */
  const selectExternalAgent = useCallback(async (agentId: number) => {
    if (!Number.isFinite(agentId) || agentId <= 0) return;
    try {
      const headers: HeadersInit = tokenRef.current
        ? { Authorization: `Bearer ${tokenRef.current}` }
        : {};
      const resp = await fetch(getApiUrl(`/api/agents/${agentId}`), { headers });
      if (!resp.ok) {
        showNotificationRef.current('error', 'Не удалось открыть агента в конструкторе');
        return;
      }
      const full = (await resp.json()) as Agent;
      setAgents((prev) => {
        const others = prev.filter((a) => a.id !== full.id);
        return [...others, full];
      });
      setSelectedAgentId(full.id);
    } catch {
      showNotificationRef.current('error', 'Не удалось открыть агента в конструкторе');
    }
  }, []);

  const loadModels = useCallback(async () => {
    try {
      const [catalogResp, providersResp] = await Promise.all([
        fetch(getApiUrl('/api/models/available')),
        fetch(getApiUrl('/api/llm-providers?include_health=false')),
      ]);
      if (catalogResp.ok) {
        const data = await catalogResp.json();
        const items: ProviderModelItem[] = (data.models || data || [])
          .map((m: any) =>
            typeof m === 'string'
              ? { name: m, path: m }
              : {
                  name: m.name || m.model_id || m.path || '',
                  path: m.path || m.name || '',
                  display_name: m.display_name,
                  provider_id: m.provider_id || m.llm_host_id,
                  llm_host_id: m.llm_host_id,
                },
          )
          .filter((m: ProviderModelItem) => m.path);
        setProviderModels(items);
        // Плоский список путей — совместимость с ModelParametersModal
        setAvailableModels(items.map((m) => m.path));
      }
      if (providersResp.ok) {
        const pdata = await providersResp.json();
        const ids: string[] = (pdata.providers || [])
          .map((p: any) => (p?.id || '').toString().trim())
          .filter(Boolean);
        setProviderIds(ids);
      }
    } catch (e) {
      // silent
    }
  }, []);

  const loadUserModelSettings = useCallback(async () => {
    try {
      const headers: HeadersInit = tokenRef.current
        ? { Authorization: `Bearer ${tokenRef.current}` }
        : {};
      const resp = await fetch(getApiUrl('/api/models/settings'), { headers });
      if (!resp.ok) return;
      const data = await resp.json();
      if (data && typeof data === 'object') {
        setUserModelSettings({ ...MODEL_SETTINGS_DEFAULT, ...data });
      }
    } catch (e) {
      // silent: панель откроется на заводских значениях
    }
  }, []);

  const loadKbDocuments = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) setIsLoadingKb(true);
    try {
      const url = getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_LIST);
      const resp = await fetch(url, { headers: getAuthFetchHeaders() });
      if (!resp.ok) return;
      const data = await resp.json();
      const raw = (data.documents || data || []) as Array<Record<string, unknown>>;
      // Нормализуем id → number, иначе includes() после upload может не совпасть.
      const mapped: KbDocument[] = raw
        .map(d => {
          const meta =
            d.metadata && typeof d.metadata === 'object'
              ? (d.metadata as Record<string, unknown>)
              : {};
          const rawAgentId = meta.agent_id;
          let agentId: number | null = null;
          if (rawAgentId != null && rawAgentId !== '') {
            const n = Number(rawAgentId);
            if (Number.isFinite(n)) agentId = n;
          }
          return {
            id: Number(d.id),
            filename: String(d.filename ?? ''),
            size: d.size != null ? Number(d.size) : null,
            file_type: d.file_type != null ? String(d.file_type) : null,
            created_at: d.created_at != null ? String(d.created_at) : null,
            agentId,
          };
        })
        .filter(d => Number.isFinite(d.id));
      setKbDocuments((prev) => (options?.silent ? mergeRagDocumentsById(prev, mapped) : mapped));
    } catch (e) {
      // silent
    } finally {
      if (!options?.silent) setIsLoadingKb(false);
    }
  }, []);

  /** Записать kb_document_ids в config агента, чтобы карточки и чат не теряли файлы. */
  const persistAgentKbDocumentIds = useCallback(
    async (agentId: number, ids: number[]) => {
      if (!token || !Number.isFinite(agentId) || agentId <= 0) return;
      try {
        const headers: HeadersInit = {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        };
        const getResp = await fetch(getApiUrl(`/api/agents/${agentId}`), { headers });
        if (!getResp.ok) return;
        const agent = (await getResp.json()) as Agent;
        const cfg = { ...(agent.config || {}) };
        cfg.kb_document_ids = ids;
        cfg.file_search_enabled = true;
        await fetch(getApiUrl(`/api/agents/${agentId}`), {
          method: 'PUT',
          headers,
          body: JSON.stringify({ config: cfg }),
        });
        setAgents(prev =>
          prev.map(a => (a.id === agentId ? { ...a, config: { ...cfg } } : a)),
        );
      } catch {
        /* silent — локальный список уже обновлён */
      }
    },
    [token],
  );

  // Загрузка при открытии панели — один раз на сессию открытия (без зависимости от нестабильных колбэков)
  useEffect(() => {
    if (!isOpen) {
      bootstrappedOpenRef.current = false;
      return;
    }
    if (bootstrappedOpenRef.current) return;
    bootstrappedOpenRef.current = true;

    void loadModels();
    void loadUserModelSettings();
    void loadKbDocuments();
    void fetchAgentChainConfig().then((cfg) => {
      setChainMaxAgents(cfg.maxAgents);
      setDefaultGraphSteps(cfg.defaultRecursionLimit);
      setMaxRecursionLimit(cfg.maxRecursionLimit);
    });
    void (async () => {
      try {
        const srv = await fetchMcpServers();
        setAvailableMcpServers(srv.filter((s) => s.enabled));
      } catch {
        setAvailableMcpServers([]);
      }
    })();
    void (async () => {
      try {
        const list = await fetchPlugins(false);
        setAvailablePlugins(list.filter((p) => p.enabled !== false));
      } catch {
        setAvailablePlugins([]);
      }
    })();
    void (async () => {
      try {
        const headers: HeadersInit = tokenRef.current
          ? { Authorization: `Bearer ${tokenRef.current}` }
          : {};
        const resp = await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/list?limit=100`, { headers });
        if (!resp.ok) return;
        const data = await resp.json();
        setAvailableSkills(
          (data.items || [])
            .filter((s: { is_active?: boolean }) => s.is_active !== false)
            .map((s: { id: number; slug: string; name: string; description?: string }) => ({
              id: s.id,
              slug: s.slug,
              name: s.name,
              description: s.description,
            })),
        );
      } catch {
        /* silent */
      }
    })();

    void (async () => {
      await loadAgents();
      try {
        const raw = sessionStorage.getItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY);
        if (raw) {
          sessionStorage.removeItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY);
          const id = Number(raw);
          if (Number.isFinite(id) && id > 0) {
            await selectExternalAgent(id);
          }
        }
      } catch {
        /* */
      }
    })();
  }, [isOpen, loadAgents, loadModels, loadUserModelSettings, loadKbDocuments, selectExternalAgent]);

  // Слушаем событие открытия с agentId (галерея / хоткей с detail)
  useEffect(() => {
    const onOpen = (e: Event) => {
      const detail = (e as CustomEvent<{ agentId?: number }>).detail;
      const id = detail?.agentId;
      if (typeof id === 'number' && Number.isFinite(id) && id > 0) {
        void selectExternalAgent(id);
      }
    };
    window.addEventListener(ASTRA_OPEN_AGENT_CONSTRUCTOR, onOpen);
    return () => window.removeEventListener(ASTRA_OPEN_AGENT_CONSTRUCTOR, onOpen);
  }, [selectExternalAgent]);

  const agentsRef = useRef(agents);
  agentsRef.current = agents;

  // ─── Load selected agent into form (только при смене выбранного id) ──────────

  useEffect(() => {
    // Черновик РАГ принадлежит тому агенту, у которого его набрали. Переключились
    // на другого — сбрасываем, иначе настройки одного уехали бы второму.
    setRagDraft(null);
    setKbGuardMessage(null);
    if (selectedAgentId === 'new') {
      resetForm();
      return;
    }
    const agent = agentsRef.current.find((a) => a.id === selectedAgentId);
    if (!agent) return;
    setName(agent.name);
    setDescription(agent.description || '');
    setInstructions(agent.system_prompt || '');
    const cfg = agent.config || {};
    setCategory(cfg.category || 'Общий');
    setModel(cfg.model || '');
    setModelParams((cfg.model_params as Partial<ModelParamsState>) || {});
    const cfgModelSettings = cfg.model_settings as Partial<ModelSettingsState> | undefined;
    setModelSettingsTouched(!!cfgModelSettings);
    setAgentModelSettings(
      cfgModelSettings
        ? { ...MODEL_SETTINGS_DEFAULT, ...cfgModelSettings }
        : { ...MODEL_SETTINGS_DEFAULT }
    );
    setCodeInterpreter(!!cfg.code_interpreter);
    setWebSearch(!!cfg.web_search);
    setArtifactsEnabled(!!cfg.artifacts_enabled);
    setShadcnEnabled(!!cfg.shadcn_enabled);
    setUserPromptMode(!!cfg.user_prompt_mode);
    setFileSearchEnabled(!!cfg.file_search_enabled);
    setKbDocumentIds(
      Array.isArray(cfg.kb_document_ids)
        ? cfg.kb_document_ids.map((x: unknown) => Number(x)).filter((x: number) => Number.isFinite(x))
        : []
    );
    setSkillIds(
      Array.isArray(cfg.skill_ids)
        ? cfg.skill_ids.map((x: unknown) => String(x).trim()).filter(Boolean)
        : Array.isArray(cfg.skills)
          ? cfg.skills.map((x: unknown) => String(x).trim()).filter(Boolean)
          : []
    );
    setSkillsEnabled(
      typeof cfg.skills_enabled === 'boolean'
        ? cfg.skills_enabled
        : Array.isArray(cfg.skill_ids) && cfg.skill_ids.length > 0
    );
    setMcpEnabled(!!cfg.mcp_enabled);
    setMcpServerIds(
      Array.isArray(cfg.mcp_server_ids)
        ? cfg.mcp_server_ids.map((x: unknown) => String(x).trim()).filter(Boolean)
        : []
    );
    setPluginIds(
      Array.isArray(cfg.plugin_ids)
        ? cfg.plugin_ids.map((x: unknown) => String(x).trim()).filter(Boolean)
        : []
    );
    setPluginsEnabled(
      typeof cfg.plugins_enabled === 'boolean'
        ? cfg.plugins_enabled
        : Array.isArray(cfg.plugin_ids) && cfg.plugin_ids.length > 0
    );
    setSupportName(cfg.support_name || '');
    setSupportEmail(cfg.support_email || '');
    setChainAgentIds(
      parseAgentIds(
        cfg.agent_ids,
        typeof selectedAgentId === 'number' ? selectedAgentId : null,
        chainMaxAgents,
      ),
    );
    setHideSequentialOutputs(!!cfg.hide_sequential_outputs);
    const rawRecursion = cfg.recursion_limit;
    if (typeof rawRecursion === 'number' && rawRecursion > 0) {
      setRecursionLimit(rawRecursion);
    } else {
      setRecursionLimit('');
    }
    const rawSub = cfg.subagents;
    if (rawSub && typeof rawSub === 'object') {
      setSubagentsConfig({
        enabled: rawSub.enabled === true,
        allow_self: rawSub.allow_self !== false && rawSub.allowSelf !== false,
        agent_ids: parseAgentIds(
          rawSub.agent_ids,
          typeof selectedAgentId === 'number' ? selectedAgentId : null,
          10,
        ),
      });
    } else {
      setSubagentsConfig(EMPTY_SUBAGENT_CONFIG);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- только смена агента; agents читаем из ref
  }, [selectedAgentId]);

  function resetForm() {
    setName('');
    setDescription('');
    setCategory('Общий');
    setInstructions('');
    setModel(availableModels[0] || '');
    setModelParams({});
    setAgentModelSettings({ ...MODEL_SETTINGS_DEFAULT });
    setModelSettingsTouched(false);
    setCodeInterpreter(false);
    setWebSearch(false);
    setArtifactsEnabled(false);
    setShadcnEnabled(false);
    setUserPromptMode(false);
    setFileSearchEnabled(false);
    setKbDocumentIds([]);
    setKbGuardMessage(null);
    setSkillIds([]);
    setSkillsEnabled(false);
    setMcpEnabled(false);
    setMcpServerIds([]);
    setPluginsEnabled(false);
    setPluginIds([]);
    setChainAgentIds([]);
    setHideSequentialOutputs(false);
    setRecursionLimit('');
    setSubagentsConfig(EMPTY_SUBAGENT_CONFIG);
    setSupportName('');
    setSupportEmail('');
  }

  const showKbGuard = useCallback(
    (message: string) => {
      setKbGuardMessage(message);
      showNotification('warning', message);
    },
    [showNotification],
  );

  /** Путь эмбеддера/реранкера: черновик панели, иначе сохранённые настройки агента. */
  const resolveAgentRagModelPaths = useCallback(async (): Promise<{
    embeddingPath: string;
    rerankerPath: string;
    rerankingEnabled: boolean;
  }> => {
    const draftEmb = ragDraft?.rag_embedding_model_path;
    const draftRer = ragDraft?.rag_reranker_model_path;
    let embeddingPath = typeof draftEmb === 'string' ? draftEmb.trim() : '';
    let rerankerPath = typeof draftRer === 'string' ? draftRer.trim() : '';
    let rerankingEnabled =
      typeof ragDraft?.rag_reranking_enabled === 'boolean'
        ? ragDraft.rag_reranking_enabled
        : true;

    const needServerEmb = draftEmb === null || draftEmb === undefined;
    const needServerRer = draftRer === null || draftRer === undefined;
    const needServerFlag = ragDraft == null || typeof ragDraft.rag_reranking_enabled !== 'boolean';

    if (
      typeof selectedAgentId === 'number' &&
      (needServerEmb || needServerRer || needServerFlag || ragDraft == null)
    ) {
      try {
        const resp = await fetch(
          getApiUrl(`/api/rag/settings?scope=agent&agent_id=${selectedAgentId}`),
          { headers: getAuthFetchHeaders() },
        );
        if (resp.ok) {
          const data = (await resp.json()) as Record<string, unknown>;
          if (needServerEmb || ragDraft == null) {
            embeddingPath = String(data.rag_embedding_model_path || '').trim();
          }
          if (needServerRer || ragDraft == null) {
            rerankerPath = String(data.rag_reranker_model_path || '').trim();
          }
          if (needServerFlag) {
            rerankingEnabled =
              typeof data.rag_reranking_enabled === 'boolean'
                ? data.rag_reranking_enabled
                : true;
          }
        }
      } catch {
        /* сеть — оставим то, что уже есть из черновика */
      }
    }

    const envDefaults = await fetchRagEntityDefaults('agent');
    return {
      embeddingPath: resolveRagEmbeddingModelPath(
        embeddingPath,
        envDefaults.embeddingPath,
      ),
      rerankerPath: resolveRagRerankerModelPath(
        rerankerPath,
        envDefaults.rerankerPath,
      ),
      rerankingEnabled,
    };
  }, [ragDraft, selectedAgentId]);

  // ─── KB Upload ───────────────────────────────────────────────────────────────

  const handleKbUpload = async (files: FileList | File[]) => {
    // FileList — live-коллекция input: если обнулить value до конца async,
    // список станет пустым и загрузка молча оборвётся без запроса на backend.
    const fileArr = Array.from(files || []);
    const pendingEntries = createRagPendingUploads(fileArr);
    const pendingIds = pendingEntries.map((entry) => entry.clientId);
    const dropPending = (ids: string[] = pendingIds) => {
      setPendingKbUploads((prev) => removeRagPendingUploads(prev, ids));
    };

    if (!fileArr.length) {
      showKbGuard('Файл не выбран.');
      return;
    }

    if (!fileSearchEnabled) {
      dropPending();
      showKbGuard('Сначала включите чекбокс «Искать по файлам агента».');
      return;
    }
    if (selectedAgentId === 'new' || typeof selectedAgentId !== 'number') {
      dropPending();
      showKbGuard('Сначала сохраните агента, затем выберите модели в настройках РАГ.');
      return;
    }

    const { embeddingPath, rerankerPath, rerankingEnabled } =
      await resolveAgentRagModelPaths();
    if (!embeddingPath) {
      dropPending();
      showKbGuard(
        'Сначала выберите модель эмбеддингов в настройках РАГ для агента',
      );
      return;
    }
    if (rerankingEnabled && !rerankerPath) {
      dropPending();
      showKbGuard(
        'Сначала выберите модель реранкера в настройках РАГ для агента',
      );
      return;
    }

    setKbGuardMessage(null);
    setPendingKbUploads((prev) => [...prev, ...pendingEntries]);
    setIsUploadingKb(true);
    const uploadedIds: number[] = [];
    const chunkingStrategy =
      (typeof localStorage !== 'undefined' &&
        (localStorage.getItem(ragUserId ? `rag_chunking_strategy:${ragUserId}` : '') ||
          localStorage.getItem('rag_chunking_strategy'))) ||
      'hierarchical';

    await mapWithConcurrency(fileArr, RAG_UPLOAD_CONCURRENCY, async (file, index) => {
      const pendingId = pendingEntries[index]?.clientId;
      const formData = new FormData();
      formData.append('file', file);
      // Agent KB: применяем стратегию чанкования из настроек RAG
      formData.append('chunking_strategy', chunkingStrategy);
      // Привязка к агенту: owner_user_id = автор агента (не uploader)
      formData.append('agent_id', String(selectedAgentId));
      try {
        const url = getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_UPLOAD);
        const resp = await fetch(url, {
          method: 'POST',
          headers: getAuthFetchHeaders(),
          body: formData,
        });
        const data = (await resp.json().catch(() => ({}))) as Record<string, unknown>;
        if (!resp.ok) {
          const detail = data.detail;
          const msg =
            typeof detail === 'string'
              ? detail
              : Array.isArray(detail)
                ? detail.map((d) => (typeof d === 'object' && d && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d))).join('; ')
                : `Не удалось загрузить «${file.name}» (${resp.status})`;
          showKbGuard(msg);
          if (pendingId) {
            commitRagUploadUiUpdate(() => {
              setPendingKbUploads((prev) => removeRagPendingUploads(prev, [pendingId]));
            });
          }
          return;
        }
        const docId = parseRagUploadDocumentId(data);
        if (docId != null) {
          uploadedIds.push(docId);
        } else {
          showKbGuard(
            `Файл «${file.name}» загружен, но сервер не вернул id документа`,
          );
        }
        commitRagUploadUiUpdate(() => {
          if (docId != null) {
            setKbDocumentIds((prev) => Array.from(new Set([...prev, docId])));
            setKbDocuments((prev) => {
              if (prev.some((d) => d.id === docId)) return prev;
              return [
                ...prev,
                {
                  id: docId,
                  filename: file.name,
                  size: file.size,
                  file_type: getRagFileTypeLabel(file.name),
                  created_at: new Date().toISOString(),
                  agentId: selectedAgentId,
                },
              ];
            });
          }
          if (pendingId) {
            setPendingKbUploads((prev) => removeRagPendingUploads(prev, [pendingId]));
          }
        });
        if (docId == null) {
          void loadKbDocuments({ silent: true });
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        showKbGuard(`Ошибка загрузки «${file.name}»: ${msg}`);
        if (pendingId) {
          commitRagUploadUiUpdate(() => {
            setPendingKbUploads((prev) => removeRagPendingUploads(prev, [pendingId]));
          });
        }
      }
    });

    setIsUploadingKb(false);
    if (uploadedIds.length) {
      const nextIds = Array.from(new Set([...kbDocumentIds, ...uploadedIds]));
      void persistAgentKbDocumentIds(selectedAgentId, nextIds);
      showNotification(
        'success',
        uploadedIds.length === 1
          ? 'Файл добавлен в базу агента'
          : `Добавлено файлов: ${uploadedIds.length}`,
      );
    }
    void loadKbDocuments({ silent: true });
  };

  const handleKbDelete = async (docId: number) => {
    try {
      const url = `${getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_DELETE)}/${docId}`;
      const resp = await fetch(url, { method: 'DELETE' });
      // Ответ проверяем до правки списков и до persist.
      if (!resp.ok) {
        const detail = await resp.json().then(d => d?.detail).catch(() => null);
        throw new Error(detail || `HTTP ${resp.status}`);
      }
      setKbDocuments(prev => prev.filter(d => d.id !== docId));
      let nextIds: number[] = [];
      setKbDocumentIds(prev => {
        nextIds = prev.filter(id => id !== docId);
        return nextIds;
      });
      if (typeof selectedAgentId === 'number') {
        await persistAgentKbDocumentIds(selectedAgentId, nextIds);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      showNotification('error', `Не удалось удалить файл: ${msg}`);
    }
  };

  // ─── Save agent ──────────────────────────────────────────────────────────────

  const handleSave = async () => {
    if (!name.trim()) {
      setSaveError('Заполните обязательное поле: Имя');
      return;
    }
    const trimmedInstructions = instructions.trim();
    if (trimmedInstructions.length > 0 && trimmedInstructions.length < INSTRUCTIONS_MIN_LENGTH) {
      setSaveError('Длина инструкции не может быть меньше 10 символов');
      return;
    }
    setSaveError('');
    setIsSaving(true);
    const currentIsPublic =
      selectedAgentId !== 'new'
        ? !!(agents.find((a) => a.id === selectedAgentId)?.is_public)
        : false;
    const payload = {
      name: name.trim(),
      description: description.trim() || undefined,
      system_prompt: instructions.trim() || 'Системные инструкции не заданы.',
      is_public: currentIsPublic,
      tools: [
        ...(codeInterpreter ? ['code_interpreter'] : []),
        ...(webSearch ? ['web_search'] : []),
      ],
      config: {
        category,
        model: model.replace(/^1lm-svc:\/\//i, 'llm-svc://').replace(/\s+/g, ''),
        model_params: modelParams,
        ...(modelSettingsTouched ? { model_settings: agentModelSettings } : {}),
        code_interpreter: codeInterpreter,
        web_search: webSearch,
        artifacts_enabled: artifactsEnabled,
        shadcn_enabled: shadcnEnabled,
        user_prompt_mode: userPromptMode,
        file_search_enabled: fileSearchEnabled,
        kb_document_ids: kbDocumentIds,
        skill_ids: skillIds,
        skills_enabled: skillsEnabled,
        mcp_enabled: mcpEnabled,
        mcp_server_ids: mcpServerIds,
        plugins_enabled: pluginsEnabled,
        plugin_ids: pluginIds,
        support_name: supportName,
        support_email: supportEmail,
        agent_ids: parseAgentIds(
          chainAgentIds,
          typeof selectedAgentId === 'number' ? selectedAgentId : null,
          chainMaxAgents,
        ),
        hide_sequential_outputs: hideSequentialOutputs,
        ...(recursionLimit !== '' ? { recursion_limit: recursionLimit } : {}),
        subagents: subagentsConfig.enabled
          ? {
              enabled: true,
              allow_self: subagentsConfig.allow_self,
              agent_ids: parseAgentIds(
                subagentsConfig.agent_ids,
                typeof selectedAgentId === 'number' ? selectedAgentId : null,
                10,
              ),
            }
          : { enabled: false, allow_self: subagentsConfig.allow_self, agent_ids: [] },
      },
      tag_ids: [],
      new_tags: [],
    };

    try {
      const isEdit = selectedAgentId !== 'new';
      const url = isEdit
        ? getApiUrl(`/api/agents/${selectedAgentId}`)
        : getApiUrl('/api/agents');
      const method = isEdit ? 'PUT' : 'POST';
      const resp = await fetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        const fallback = `${resp.status} ${resp.statusText}`.trim();
        const detail = formatApiDetail(err.detail, fallback);
        if (resp.status === 404) {
          throw new Error(
            'Не найден API создания агентов (404). Обычно это POST /api/agents не дошёл до backend — проверьте деплой и Network.',
          );
        }
        throw new Error(detail || fallback);
      }
      const result = await resp.json();
      if (!isEdit && result.agent_id) {
        setSelectedAgentId(result.agent_id);
      }
      const savedId = isEdit ? selectedAgentId : result.agent_id;
      if (savedId && savedId !== 'new') {
        try {
          localStorage.setItem('active_agent_id', String(savedId));
          localStorage.setItem('active_agent_name', name.trim());
          localStorage.setItem('active_agent_prompt', instructions.trim() || 'Системные инструкции не заданы.');
          persistAgentMcpConfig({
            mcp_enabled: mcpEnabled,
            mcp_server_ids: mcpServerIds,
          });
          persistAgentArtifactsEnabled({
            artifacts_enabled: artifactsEnabled,
            shadcn_enabled: shadcnEnabled,
            user_prompt_mode: userPromptMode,
          });
        } catch {
          /* */
        }
        window.dispatchEvent(
          new CustomEvent('agentSelected', {
            detail: {
              id: savedId,
              name: name.trim(),
              system_prompt: instructions.trim(),
              config: {
                mcp_enabled: mcpEnabled,
                mcp_server_ids: mcpServerIds,
                artifacts_enabled: artifactsEnabled,
                shadcn_enabled: shadcnEnabled,
                user_prompt_mode: userPromptMode,
              },
            },
          }),
        );
      }
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
      await loadAgents();

      if (token && model.trim()) {
        const loaded = await loadAgentModelOnly(token, model.trim());
        if (loaded.ok && !('pending' in loaded && loaded.pending)) {
          showNotification('success', 'Модель агента загружена на сервер');
        } else if (loaded.ok && 'pending' in loaded && loaded.pending) {
          showNotification(
            'info',
            'Агент сохранён; модель подтянется, когда сервис моделей будет готов',
          );
        } else if (!loaded.ok) {
          showNotification('warning', `Агент сохранён; не удалось загрузить модель: ${loaded.message}`);
        }
      }

      // Настройки РАГ уезжают ПОСЛЕ агента и одним запросом: до этого момента id
      // нового агента ещё не существует. Здесь же backend решает, менялось ли то,
      // что лежит в индексе, и ставит перечанковку.
      if (token && savedId && savedId !== 'new') {
        const ragApplied = await saveEntityRagSettings({
          scope: 'agent',
          entityId: savedId,
          entityName: name.trim(),
          // Именно то, что человек написал. Заглушка нужна только карточке
          // агента (там промпт обязателен), а в настройках РАГ она становилась
          // системным промптом сущности
          instructions: instructions.trim(),
          draft: ragDraft,
        });
        if (ragApplied.ok) {
          setRagDraft(null);
          if (ragApplied.reindexed) {
            notifyReindexStarted();
          }
        } else {
          showNotification(
            'warning',
            `Агент сохранён; настройки РАГ не применены: ${ragApplied.message}`,
          );
        }
      }
    } catch (e: any) {
      setSaveError(e.message);
    } finally {
      setIsSaving(false);
    }
  };

  // ─── Publish / unpublish to gallery ──────────────────────────────────────────

  const handleTogglePublish = async () => {
    if (selectedAgentId === 'new' || typeof selectedAgentId !== 'number') return;
    const agent = agents.find((a) => a.id === selectedAgentId);
    if (!agent) return;
    const nextPublic = !agent.is_public;
    setIsPublishing(true);
    try {
      const resp = await fetch(getApiUrl(`/api/agents/${selectedAgentId}`), {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ is_public: nextPublic }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(formatApiDetail(err.detail, resp.statusText));
      }
      await loadAgents();
      showNotification(
        'success',
        nextPublic
          ? 'Агент опубликован в галерее'
          : 'Агент снят с публикации в галерее',
      );
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      showNotification('error', `Не удалось изменить публикацию: ${msg}`);
    } finally {
      setIsPublishing(false);
    }
  };

  // ─── Delete agent ────────────────────────────────────────────────────────────

  const handleDelete = async () => {
    if (selectedAgentId === 'new') return;
    if (!window.confirm(`Удалить агента «${name}»?`)) return;
    try {
      const url = getApiUrl(`/api/agents/${selectedAgentId}`);
      const resp = await fetch(url, {
        method: 'DELETE',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      // Сброс формы — только после успеха.
      if (!resp.ok) {
        const detail = await resp.json().then(d => d?.detail).catch(() => null);
        throw new Error(detail || `HTTP ${resp.status}`);
      }
      setSelectedAgentId('new');
      resetForm();
      await loadAgents();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      showNotification('error', `Не удалось удалить агента: ${msg}`);
    }
  };

  // ─── "Use agent" — sets system prompt as context ─────────────────────────────

  const handleUseAgent = () => {
    if (selectedAgentId === 'new') return;
    const agent = agents.find(a => a.id === selectedAgentId);
    if (!agent) return;
    // Store selected agent in localStorage so chat can pick it up
    localStorage.setItem('active_agent_id', String(agent.id));
    localStorage.setItem('active_agent_prompt', agent.system_prompt);
    localStorage.setItem('active_agent_name', agent.name);
    persistAgentMcpConfig(agent.config || {});
    persistAgentArtifactsEnabled((agent.config || {}) as Record<string, unknown>);
    window.dispatchEvent(new CustomEvent('agentSelected', { detail: agent }));
  };

  const selectedKbDocuments = useMemo(() => {
    const idSet = new Set(kbDocumentIds);
    return kbDocuments.filter(doc => {
      if (idSet.has(doc.id)) return true;
      // Файлы уже в RAG с metadata.agent_id, а config агента ещё без id —
      // всё равно показываем карточки рядом с «Добавить файлы».
      return (
        typeof selectedAgentId === 'number' &&
        doc.agentId != null &&
        doc.agentId === selectedAgentId
      );
    });
  }, [kbDocuments, kbDocumentIds, selectedAgentId]);

  const filteredPendingKbUploads = useMemo(
    () => filterByRagFilenameQuery(pendingKbUploads, kbFileSearchQuery),
    [pendingKbUploads, kbFileSearchQuery],
  );
  const filteredSelectedKbDocuments = useMemo(
    () => filterByRagFilenameQuery(selectedKbDocuments, kbFileSearchQuery),
    [selectedKbDocuments, kbFileSearchQuery],
  );
  const kbTotalFiles = selectedKbDocuments.length + pendingKbUploads.length;
  const kbFilteredTotal = filteredSelectedKbDocuments.length + filteredPendingKbUploads.length;
  const hasActiveKbFileSearch = kbFileSearchQuery.trim().length > 0;

  useEffect(() => {
    setKbFileSearchQuery('');
  }, [selectedAgentId]);

  // Подтянуть id из RAG в локальный список (и при необходимости в config).
  useEffect(() => {
    if (typeof selectedAgentId !== 'number' || !fileSearchEnabled) return;
    const fromRag = kbDocuments
      .filter(d => d.agentId === selectedAgentId)
      .map(d => d.id);
    if (!fromRag.length) return;
    const missing = fromRag.filter(id => !kbDocumentIds.includes(id));
    if (!missing.length) return;
    const nextIds = Array.from(new Set([...kbDocumentIds, ...fromRag]));
    setKbDocumentIds(nextIds);
    void persistAgentKbDocumentIds(selectedAgentId, nextIds);
  }, [
    selectedAgentId,
    fileSearchEnabled,
    kbDocuments,
    kbDocumentIds,
    persistAgentKbDocumentIds,
  ]);

  const ragReadyEntityId = typeof selectedAgentId === 'number' ? selectedAgentId : null;
  const ragReadyEntityName =
    typeof selectedAgentId === 'number'
      ? agents.find((a) => a.id === selectedAgentId)?.name
      : undefined;
  const { readyMessage: ragReadyMessage, clearReadyMessage: clearRagReadyMessage } =
    useRagEntityReadyMessage('agent', ragReadyEntityId, ragReadyEntityName);

  if (!isOpen) return null;

  const agentIdStr = selectedAgentId !== 'new'
    ? `agent_${String(selectedAgentId).padStart(6, '0')}`
    : '';

  // ─── Роль текущего пользователя для выбранного агента ────────────────────────
  const selectedAgent = selectedAgentId !== 'new' ? agents.find(a => a.id === selectedAgentId) : null;
  const selectedRole: 'owner' | 'editor' | 'viewer' =
    selectedAgentId === 'new' ? 'owner' : (selectedAgent?.my_permission ?? 'viewer');
  const readOnly = selectedRole === 'viewer';
  const canEdit = selectedRole === 'owner' || selectedRole === 'editor';
  const isOwner = selectedRole === 'owner';

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
        color: panelChrome.fg,
      }}
    >
      {showModelParamsPanel ? (
        <ModelParametersModal
          variant="panel"
          open={true}
          onClose={() => setShowModelParamsPanel(false)}
          currentModel={model}
          availableModels={availableModels}
          providerModels={providerModels}
          providerIds={providerIds}
          initialParams={Object.keys(modelParams).length ? modelParams : undefined}
          initialModelSettings={modelSettingsTouched ? agentModelSettings : userModelSettings}
          onSaveModelSettings={(s) => {
            // Сохранение в панели — это и есть «у агента своя тонкая настройка».
            setAgentModelSettings(s);
            setModelSettingsTouched(true);
          }}
          readOnly={readOnly}
          onSave={(newModel, params) => {
            if (readOnly) {
              setShowModelParamsPanel(false);
              return;
            }
            setModel(newModel);
            setModelParams(params ?? {});
            setShowModelParamsPanel(false);
          }}
        />
      ) : showAgentRagSettingsPanel ? (
        <RAGSettings
          variant="panel"
          lockedScope="agent"
          entityId={typeof selectedAgentId === 'number' ? selectedAgentId : null}
          entityName={selectedAgent?.name}
          entityInstructionsPrompt={instructions}
          draft
          draftValue={ragDraft}
          onDraftChange={readOnly ? undefined : setRagDraft}
          readOnly={readOnly}
          isDarkMode={darkFields}
          panelTitle={
            selectedAgent?.name
              ? `Настройки РАГ: ${selectedAgent.name}`
              : 'Настройки РАГ для агента'
          }
          onClose={() => setShowAgentRagSettingsPanel(false)}
        />
      ) : (
        <>
      {/* ── Выбор агента ─────────────────────────────────────────────────────── */}
      <Box sx={{ px: 2, pt: 1.5, pb: 1 }}>
        {/* Кнопка «Агенты» */}
        <Box
          onClick={e => setAgentPopoverAnchor(e.currentTarget)}
          sx={formFieldTriggerSx}
        >
          <Typography sx={{ ...formFieldTriggerValueSx, fontWeight: 600 }}>
            Агенты
          </Typography>
          <ExpandMoreIcon
            sx={{ ...dropdownChevronSx, transform: Boolean(agentPopoverAnchor) ? 'rotate(180deg)' : 'none' }}
          />
        </Box>

        {/* Всплывающий список — стиль из constants/menuStyles */}
        <Popover
          open={Boolean(agentPopoverAnchor)}
          anchorEl={agentPopoverAnchor}
          onClose={() => { setAgentPopoverAnchor(null); setAgentSearchQuery(''); }}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
          slotProps={{
            paper: { sx: getDropdownPopoverPaperSx(agentPopoverAnchor, darkFields) },
          }}
        >
          {/* Строка поиска */}
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              px: 1.5,
              py: 0.9,
              gap: 1,
              borderBottom: darkFields ? '1px solid rgba(255,255,255,0.07)' : '1px solid rgba(0,0,0,0.08)',
            }}
          >
            <SearchIcon sx={{ color: darkFields ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.4)', fontSize: 16, flexShrink: 0 }} />
            <Box
              component="input"
              autoFocus
              placeholder="Поиск агентов по имени"
              value={agentSearchQuery}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setAgentSearchQuery(e.target.value)}
              sx={{
                flex: 1,
                bgcolor: 'transparent',
                border: 'none',
                outline: 'none',
                color: darkFields ? 'white' : 'rgba(0,0,0,0.87)',
                fontSize: '0.82rem',
                '&::placeholder': { color: darkFields ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.4)' },
              }}
            />
          </Box>

          {/* Список */}
          <Box
            sx={{
              maxHeight: 220,
              overflowY: 'auto',
              py: 0.5,
              ...SIDEBAR_HIDE_SCROLLBAR_SX,
            }}
          >
            {/* «+ Новый агент» */}
            <Box
              onClick={() => { setSelectedAgentId('new'); setAgentPopoverAnchor(null); setAgentSearchQuery(''); }}
              sx={{
                ...dropdownItemSx,
                ...getDropdownItemStateSx(darkFields, selectedAgentId === 'new'),
                fontStyle: 'italic',
                opacity: selectedAgentId === 'new' ? 1 : 0.7,
              }}
            >
              + Новый агент
            </Box>

            {/* Существующие агенты */}
            {(agents || [])
              .filter(a => !agentSearchQuery.trim() || a.name.toLowerCase().includes(agentSearchQuery.toLowerCase()))
              .map(a => (
                <Box
                  key={a.id}
                  onClick={() => { setSelectedAgentId(a.id); setAgentPopoverAnchor(null); setAgentSearchQuery(''); }}
                  sx={{
                    ...dropdownItemSx,
                    ...getDropdownItemStateSx(darkFields, selectedAgentId === a.id),
                  }}
                >
                  {a.name}
                </Box>
              ))}

            {/* Ничего не найдено */}
            {agentSearchQuery.trim() && (agents || []).filter(a => a.name.toLowerCase().includes(agentSearchQuery.toLowerCase())).length === 0 && (
              <Box sx={{ px: 1.5, py: 1.5, fontSize: '0.78rem', color: darkFields ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.4)', textAlign: 'center' }}>
                Не найдено
              </Box>
            )}
          </Box>
        </Popover>
      </Box>

      {/* Баннер роли для чужого агента */}
      {selectedAgentId !== 'new' && !isOwner && (
        <Box
          sx={{
            mx: 2,
            mb: 1,
            px: 1.25,
            py: 0.75,
            borderRadius: 1,
            border: '1px solid',
            borderColor: readOnly ? 'rgba(100,181,246,0.35)' : 'rgba(102,187,106,0.35)',
            bgcolor: readOnly ? 'rgba(100,181,246,0.08)' : 'rgba(102,187,106,0.08)',
          }}
        >
          <Typography sx={{ fontSize: '0.72rem', color: 'inherit', opacity: 0.8 }}>
            {readOnly
              ? 'Общий агент · роль «Зритель» — только просмотр и использование, изменение недоступно.'
              : 'Общий агент · роль «Редактор» — можно изменять; удаление и повторный шаринг доступны только владельцу.'}
          </Typography>
        </Box>
      )}

      {/* ── Scrollable form ─────────────────────────────────────────────────── */}
      <Box
        sx={{
          flex: 1,
          overflowY: 'auto',
          px: 2,
          py: 1.5,
          display: 'flex',
          flexDirection: 'column',
          gap: 1.5,
          // Зрителю нужны клики по «Модель LLM» и «Настройки РАГ» — не глушим весь form.
          ...SIDEBAR_HIDE_SCROLLBAR_SX,
        }}
      >

        {/* Имя и Описание — такие же по размеру, как поля «Имя» и «Электронная почта» в контактах поддержки */}
        <Box>
          <TextField
            value={name}
            onChange={e => setName(e.target.value)}
            label="Имя"
            placeholder="Введите имя агента"
            variant="outlined"
            size="small"
            fullWidth
            required
            disabled={readOnly}
            sx={nameFieldSx}
            inputProps={{ maxLength: 255 }}
          />
          {agentIdStr && (
            <Typography variant="caption" sx={{ color: panelChrome.fgSubtle, fontSize: '0.68rem', display: 'block', mt: 0.25 }}>
              {agentIdStr}
            </Typography>
          )}
        </Box>

        <Box>
          <TextField
            value={description}
            onChange={e => setDescription(e.target.value)}
            label="Описание"
            placeholder="Необязательно: описание вашего агента"
            variant="outlined"
            size="small"
            fullWidth
            disabled={readOnly}
            sx={formFieldInputSx}
          />
        </Box>

        {/* Category — outlined с «плавающей» подписью; без синей подсветки при фокусе */}
        <Box>
          <FormControl variant="outlined" fullWidth size="small" required sx={categoryFieldSx}>
            <InputLabel htmlFor="agent-constructor-category">Категория</InputLabel>
            <OutlinedInput
              ref={categoryOutlinedRef}
              id="agent-constructor-category"
              label="Категория"
              value={category}
              readOnly
              sx={AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX}
              onClick={() => {
                if (readOnly) return;
                setCategoryPopoverAnchor(categoryOutlinedRef.current);
              }}
              endAdornment={
                <InputAdornment position="end">
                  <ExpandMoreIcon
                    sx={{ ...dropdownChevronSx, transform: Boolean(categoryPopoverAnchor) ? 'rotate(180deg)' : 'none' }}
                  />
                </InputAdornment>
              }
            />
          </FormControl>
          <Popover
            open={!readOnly && Boolean(categoryPopoverAnchor)}
            anchorEl={categoryPopoverAnchor}
            onClose={() => setCategoryPopoverAnchor(null)}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
            transformOrigin={{ vertical: 'top', horizontal: 'left' }}
            slotProps={{
              paper: { sx: getDropdownPopoverPaperSx(categoryPopoverAnchor, darkFields) },
            }}
          >
            <Box sx={{ py: 0.5 }}>
              {CATEGORIES.map(c => (
                <Box
                  key={c}
                  onClick={() => { setCategory(c); setCategoryPopoverAnchor(null); }}
                  sx={{
                    ...dropdownItemSx,
                    ...getDropdownItemStateSx(darkFields, category === c),
                  }}
                >
                  {c}
                </Box>
              ))}
            </Box>
          </Popover>
        </Box>

        {/* Instructions */}
        <Box>
          <TextField
            value={instructions}
            onChange={e => setInstructions(e.target.value)}
            label="Инструкции"
            placeholder="Системные инструкции, используемые агентом"
            variant="outlined"
            size="small"
            fullWidth
            multiline
            minRows={3}
            maxRows={8}
            disabled={readOnly}
            sx={instructionsFieldSx}
          />
          {instructionsTooShort && (
            <Box
              role="alert"
              sx={{
                mt: 1,
                px: 1.5,
                py: 1,
                borderRadius: 1,
                border: '2px solid',
                borderColor: 'error.main',
                bgcolor: (theme) =>
                  theme.palette.mode === 'dark'
                    ? 'rgba(183, 28, 28, 0.25)'
                    : 'rgba(211, 47, 47, 0.08)',
              }}
            >
              <Typography variant="body2" color="error" sx={{ fontWeight: 600, fontSize: '0.78rem' }}>
                Длина инструкции не может быть меньше 10 символов
              </Typography>
            </Box>
          )}
        </Box>

        {/* Model — outlined с «плавающей» подписью; без синей подсветки при фокусе */}
        <Box>
          <FormControl variant="outlined" fullWidth size="small" required sx={categoryFieldSx}>
            <InputLabel htmlFor="agent-constructor-model">Модель LLM</InputLabel>
            <OutlinedInput
              id="agent-constructor-model"
              label="Модель LLM"
              value={model ? (model.replace('llm-svc://', '').split('/').pop() || model) : ''}
              readOnly
              placeholder="Выберите модель"
              sx={AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX}
              onClick={() => setShowModelParamsPanel(true)}
              endAdornment={
                <InputAdornment position="end">
                  <ExpandMoreIcon sx={dropdownChevronSx} />
                </InputAdornment>
              }
            />
          </FormControl>
        </Box>

        {/* ── Capabilities (скрыто — см. COMMENTS.md) ──────────────────────── */}
        {/*
        <Box>
          <SectionHeader>Возможности</SectionHeader>

          <Box sx={{ mt: 1 }}>
            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.45)', fontSize: '0.72rem', display: 'block', mb: 0.5 }}>
              API Интерпретатора кода
            </Typography>
            <FormControlLabel
              control={
                <Checkbox
                  checked={codeInterpreter}
                  onChange={e => setCodeInterpreter(e.target.checked)}
                  size="small"
                  sx={{ color: 'rgba(255,255,255,0.4)', '&.Mui-checked': { color: '#2196f3' }, p: 0.5 }}
                />
              }
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.75)', fontSize: '0.78rem' }}>Выполнить код</Typography>
                  <Tooltip title="Выполнять Python-код в изолированной среде" arrow>
                    <HelpIcon sx={{ fontSize: 12, color: 'rgba(255,255,255,0.25)' }} />
                  </Tooltip>
                </Box>
              }
              sx={{ ml: 0, '& .MuiFormControlLabel-label': { ml: 0.5 } }}
            />

            {codeInterpreter && (
              <Button
                size="small"
                startIcon={<UploadIcon sx={{ fontSize: '0.85rem !important' }} />}
                fullWidth
                sx={{
                  mt: 0.5,
                  fontSize: '0.72rem',
                  textTransform: 'none',
                  color: 'rgba(255,255,255,0.6)',
                  border: '1px dashed rgba(255,255,255,0.2)',
                  py: 0.75,
                  justifyContent: 'flex-start',
                  '&:hover': { bgcolor: 'rgba(255,255,255,0.06)', borderColor: 'rgba(255,255,255,0.35)' },
                }}
              >
                Загрузить для Интерпретатора кода
              </Button>
            )}
          </Box>

          <Box>
            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.45)', fontSize: '0.72rem', display: 'block', mb: 0.5 }}>
              Веб-поиск
            </Typography>
            <FormControlLabel
              control={
                <Checkbox
                  checked={webSearch}
                  onChange={e => setWebSearch(e.target.checked)}
                  size="small"
                  sx={{ color: 'rgba(255,255,255,0.4)', '&.Mui-checked': { color: '#2196f3' }, p: 0.5 }}
                />
              }
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.75)', fontSize: '0.78rem' }}>Веб-поиск</Typography>
                  <Tooltip title="Поиск актуальной информации в интернете" arrow>
                    <HelpIcon sx={{ fontSize: 12, color: 'rgba(255,255,255,0.25)' }} />
                  </Tooltip>
                </Box>
              }
              sx={{ ml: 0, '& .MuiFormControlLabel-label': { ml: 0.5 } }}
            />
          </Box>

          <Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.75 }}>
              <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.45)', fontSize: '0.72rem' }}>
                Контекст файла
              </Typography>
              <Tooltip title="Файл добавляется в документы этого агента (не в общую библиотеку). После загрузки сохраните агента." arrow>
                <HelpIcon sx={{ fontSize: 12, color: 'rgba(255,255,255,0.25)' }} />
              </Tooltip>
            </Box>
            <Button
              size="small"
              startIcon={<AttachIcon sx={{ fontSize: '0.85rem !important' }} />}
              fullWidth
              sx={{
                fontSize: '0.72rem',
                textTransform: 'none',
                color: 'rgba(255,255,255,0.6)',
                border: '1px dashed rgba(255,255,255,0.2)',
                py: 0.75,
                justifyContent: 'flex-start',
                '&:hover': { bgcolor: 'rgba(255,255,255,0.06)', borderColor: 'rgba(255,255,255,0.35)' },
              }}
            >
              Загрузить файл контекста
            </Button>
          </Box>
        </Box>
        */}

        {/* ── Artifacts ────────────────────────────────────────────────────── */}
        <Box>
          <SectionHeader>Артефакты</SectionHeader>
          <Box sx={{ mt: 1, display: 'flex', flexDirection: 'column', gap: 0.5 }}>
            {[
              { label: 'Включить артефакты', help: 'Промпт артефактов + HTML/Mermaid/SVG/presentation viewer. Также viewer включается presentation-skill’ом. Уже показанные сообщения сохраняют viewer.', val: artifactsEnabled, set: setArtifactsEnabled, disabled: readOnly },
              { label: 'Включить компоненты shadcn/ui', help: 'Доп. инструкции: модель может использовать shadcn/ui в React-артефактах (нужны включённые артефакты)', val: shadcnEnabled, set: setShadcnEnabled, disabled: readOnly || !artifactsEnabled },
              { label: 'Режим пользовательского промта', help: 'ВНИМАНИЕ: отключает стандартный промпт артефактов. Для теста оставьте ВЫКЛ. Включайте только если формат :::artifact описан вручную в инструкциях агента.', val: userPromptMode, set: setUserPromptMode, disabled: readOnly || !artifactsEnabled },
            ].map(({ label, help, val, set, disabled }) => (
              <Box key={label} sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.7)', fontSize: '0.78rem' }}>{label}</Typography>
                  <Tooltip title={help} arrow>
                    <HelpIcon sx={{ fontSize: 12, color: 'rgba(255,255,255,0.25)' }} />
                  </Tooltip>
                </Box>
                <Switch
                  checked={val}
                  disabled={disabled}
                  onChange={e => set(e.target.checked)}
                  size="small"
                  sx={{
                    '& .MuiSwitch-switchBase.Mui-checked': { color: '#2196f3' },
                    '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': { bgcolor: 'rgba(33,150,243,0.5)' },
                    '& .MuiSwitch-track': { bgcolor: 'rgba(255,255,255,0.2)' },
                  }}
                />
              </Box>
            ))}
          </Box>
        </Box>

        {/* ── Skills ───────────────────────────────────────────────────────── */}
        <Box sx={{ minWidth: 0 }}>
          <SectionHeader>Skills</SectionHeader>
          <Box sx={{ mt: 1 }}>
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={skillsEnabled}
                  disabled={readOnly}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setSkillsEnabled(checked);
                    if (!checked) setSkillIds([]);
                  }}
                  sx={{ color: panelChrome.fgSubtle, '&.Mui-checked': { color: '#2196f3' }, p: 0.5 }}
                />
              }
              label={
                <Typography variant="caption" sx={{ color: panelChrome.fgMuted, fontSize: '0.78rem' }}>
                  Включить skills для агента
                </Typography>
              }
              sx={{ m: 0, mb: skillsEnabled ? 1 : 0, '& .MuiFormControlLabel-label': { ml: 0.5 } }}
            />
            {skillsEnabled && (
              <Box>
                <FormControl variant="outlined" fullWidth size="small" sx={categoryFieldSx}>
                  <InputLabel htmlFor="agent-constructor-skills">Skills</InputLabel>
                  <OutlinedInput
                    ref={skillsTriggerRef}
                    id="agent-constructor-skills"
                    label="Skills"
                    value={
                      skillIds.length === 0
                        ? ''
                        : skillIds.length === 1
                          ? (availableSkills.find((s) => s.slug === skillIds[0])?.name || skillIds[0])
                          : `Выбрано: ${skillIds.length}`
                    }
                    readOnly
                    placeholder="Выберите skills"
                    sx={AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX}
                    onClick={() => {
                      if (readOnly) return;
                      setSkillsPopoverAnchor(skillsTriggerRef.current);
                    }}
                    endAdornment={
                      <InputAdornment position="end">
                        <ExpandMoreIcon
                          sx={{ ...dropdownChevronSx, transform: Boolean(skillsPopoverAnchor) ? 'rotate(180deg)' : 'none' }}
                        />
                      </InputAdornment>
                    }
                  />
                </FormControl>
                <Popover
                  open={!readOnly && Boolean(skillsPopoverAnchor)}
                  anchorEl={skillsPopoverAnchor}
                  onClose={() => setSkillsPopoverAnchor(null)}
                  anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                  transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                  slotProps={{
                    paper: { sx: getDropdownPopoverPaperSx(skillsPopoverAnchor, darkFields) },
                  }}
                >
                  <Box sx={{ py: 0.5, maxHeight: 220, overflowY: 'auto', ...SIDEBAR_HIDE_SCROLLBAR_SX }}>
                    {availableSkills.length === 0 ? (
                      <Box sx={{ px: 1.5, py: 1.5, fontSize: '0.78rem', color: darkFields ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.45)', textAlign: 'center' }}>
                        Нет доступных skills
                      </Box>
                    ) : (
                      availableSkills.map((sk) => {
                        const selected = skillIds.includes(sk.slug);
                        return (
                          <Box
                            key={sk.id}
                            onClick={() => {
                              setSkillIds((prev) =>
                                prev.includes(sk.slug)
                                  ? prev.filter((x) => x !== sk.slug)
                                  : [...prev, sk.slug],
                              );
                            }}
                            sx={{
                              ...dropdownItemSx,
                              display: 'flex',
                              alignItems: 'center',
                              gap: 0.75,
                              ...getDropdownItemStateSx(darkFields, selected),
                            }}
                          >
                            {selected ? (
                              <CheckBoxIcon sx={{ fontSize: 16, color: '#2196f3', flexShrink: 0 }} />
                            ) : (
                              <CheckBoxBlankIcon sx={{ fontSize: 16, color: darkFields ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.35)', flexShrink: 0 }} />
                            )}
                            <Box sx={{ minWidth: 0 }}>
                              <Box component="span" sx={{ display: 'block' }}>{sk.name}</Box>
                              <Box component="span" sx={{ display: 'block', opacity: 0.5, fontSize: '0.72rem' }}>
                                {sk.slug}
                              </Box>
                            </Box>
                          </Box>
                        );
                      })
                    )}
                  </Box>
                </Popover>
              </Box>
            )}
          </Box>
        </Box>

        {/* ── MCP ──────────────────────────────────────────────────────────── */}
        <Box sx={{ minWidth: 0 }}>
          <SectionHeader>MCP</SectionHeader>
          <Box sx={{ mt: 1 }}>
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  disabled={readOnly}
                  checked={mcpEnabled}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setMcpEnabled(checked);
                    if (!checked) setMcpServerIds([]);
                  }}
                  sx={{ color: panelChrome.fgSubtle, '&.Mui-checked': { color: '#2196f3' }, p: 0.5 }}
                />
              }
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography variant="caption" sx={{ color: panelChrome.fgMuted, fontSize: '0.78rem' }}>
                    Добавить MCP
                  </Typography>
                  <Tooltip title="Подключить MCP-серверы astrachat к этому агенту. Настройка сохраняется в карточке агента и применяется при использовании из галереи." arrow>
                    <HelpIcon sx={{ fontSize: 12, color: panelChrome.fgSubtle }} />
                  </Tooltip>
                </Box>
              }
              sx={{ m: 0, mb: mcpEnabled ? 1 : 0, '& .MuiFormControlLabel-label': { ml: 0.5 } }}
            />
            {mcpEnabled && (
              <Box>
                <FormControl variant="outlined" fullWidth size="small" sx={categoryFieldSx}>
                  <InputLabel htmlFor="agent-constructor-mcp">MCP-серверы</InputLabel>
                  <OutlinedInput
                    ref={mcpTriggerRef}
                    id="agent-constructor-mcp"
                    label="MCP-серверы"
                    value={
                      mcpServerIds.length === 0
                        ? ''
                        : mcpServerIds.length === 1
                          ? (availableMcpServers.find((s) => s.id === mcpServerIds[0])?.display_name || mcpServerIds[0])
                          : `Выбрано: ${mcpServerIds.length}`
                    }
                    readOnly
                    placeholder="Выберите MCP-серверы"
                    sx={AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX}
                    onClick={() => {
                      if (readOnly) return;
                      setMcpPopoverAnchor(mcpTriggerRef.current);
                    }}
                    endAdornment={
                      <InputAdornment position="end">
                        <ExpandMoreIcon
                          sx={{ ...dropdownChevronSx, transform: Boolean(mcpPopoverAnchor) ? 'rotate(180deg)' : 'none' }}
                        />
                      </InputAdornment>
                    }
                  />
                </FormControl>
                <Popover
                  open={!readOnly && Boolean(mcpPopoverAnchor)}
                  anchorEl={mcpPopoverAnchor}
                  onClose={() => setMcpPopoverAnchor(null)}
                  anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                  transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                  slotProps={{
                    paper: { sx: getDropdownPopoverPaperSx(mcpPopoverAnchor, darkFields) },
                  }}
                >
                  <Box sx={{ py: 0.5, maxHeight: 220, overflowY: 'auto', ...SIDEBAR_HIDE_SCROLLBAR_SX }}>
                    {availableMcpServers.length === 0 ? (
                      <Box sx={{ px: 1.5, py: 1.5, fontSize: '0.78rem', color: darkFields ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.45)', textAlign: 'center' }}>
                        Нет подключённых MCP-серверов
                      </Box>
                    ) : (
                      availableMcpServers.map((srv) => {
                        const selected = mcpServerIds.includes(srv.id);
                        return (
                          <Box
                            key={srv.id}
                            onClick={() => {
                              setMcpServerIds((prev) =>
                                prev.includes(srv.id)
                                  ? prev.filter((x) => x !== srv.id)
                                  : [...prev, srv.id],
                              );
                            }}
                            sx={{
                              ...dropdownItemSx,
                              display: 'flex',
                              alignItems: 'center',
                              gap: 0.75,
                              ...getDropdownItemStateSx(darkFields, selected),
                            }}
                          >
                            {selected ? (
                              <CheckBoxIcon sx={{ fontSize: 16, color: '#2196f3', flexShrink: 0 }} />
                            ) : (
                              <CheckBoxBlankIcon sx={{ fontSize: 16, color: darkFields ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.35)', flexShrink: 0 }} />
                            )}
                            {srv.display_name || srv.id}
                          </Box>
                        );
                      })
                    )}
                  </Box>
                </Popover>
              </Box>
            )}
          </Box>
        </Box>

        {/* ── Plugins ──────────────────────────────────────────────────────── */}
        <Box sx={{ minWidth: 0 }}>
          <SectionHeader>Плагины</SectionHeader>
          <Box sx={{ mt: 1 }}>
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  disabled={readOnly}
                  checked={pluginsEnabled}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setPluginsEnabled(checked);
                    if (!checked) setPluginIds([]);
                  }}
                  sx={{ color: panelChrome.fgSubtle, '&.Mui-checked': { color: '#2196f3' }, p: 0.5 }}
                />
              }
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography variant="caption" sx={{ color: panelChrome.fgMuted, fontSize: '0.78rem' }}>
                    Добавить плагины
                  </Typography>
                  <Tooltip title="Подключить плагины из «Галереи плагинов» к этому агенту. Настройка сохраняется в карточке агента." arrow>
                    <HelpIcon sx={{ fontSize: 12, color: panelChrome.fgSubtle }} />
                  </Tooltip>
                </Box>
              }
              sx={{ m: 0, mb: pluginsEnabled ? 1 : 0, '& .MuiFormControlLabel-label': { ml: 0.5 } }}
            />
            {pluginsEnabled && (
              <Box>
                <FormControl variant="outlined" fullWidth size="small" sx={categoryFieldSx}>
                  <InputLabel htmlFor="agent-constructor-plugins">Плагины</InputLabel>
                  <OutlinedInput
                    ref={pluginsTriggerRef}
                    id="agent-constructor-plugins"
                    label="Плагины"
                    value={
                      pluginIds.length === 0
                        ? ''
                        : pluginIds.length === 1
                          ? (availablePlugins.find((p) => p.id === pluginIds[0])?.display_name || pluginIds[0])
                          : `Выбрано: ${pluginIds.length}`
                    }
                    readOnly
                    placeholder="Выберите плагины"
                    sx={AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX}
                    onClick={() => {
                      if (readOnly) return;
                      setPluginsPopoverAnchor(pluginsTriggerRef.current);
                    }}
                    endAdornment={
                      <InputAdornment position="end">
                        <ExpandMoreIcon
                          sx={{ ...dropdownChevronSx, transform: Boolean(pluginsPopoverAnchor) ? 'rotate(180deg)' : 'none' }}
                        />
                      </InputAdornment>
                    }
                  />
                </FormControl>
                <Popover
                  open={!readOnly && Boolean(pluginsPopoverAnchor)}
                  anchorEl={pluginsPopoverAnchor}
                  onClose={() => setPluginsPopoverAnchor(null)}
                  anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                  transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                  slotProps={{
                    paper: { sx: getDropdownPopoverPaperSx(pluginsPopoverAnchor, darkFields) },
                  }}
                >
                  <Box sx={{ py: 0.5, maxHeight: 220, overflowY: 'auto', ...SIDEBAR_HIDE_SCROLLBAR_SX }}>
                    {availablePlugins.length === 0 ? (
                      <Box sx={{ px: 1.5, py: 1.5, fontSize: '0.78rem', color: darkFields ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.45)', textAlign: 'center' }}>
                        Нет плагинов в галерее
                      </Box>
                    ) : (
                      availablePlugins.map((pl) => {
                        const selected = pluginIds.includes(pl.id);
                        return (
                          <Box
                            key={pl.id}
                            onClick={() => {
                              setPluginIds((prev) =>
                                prev.includes(pl.id)
                                  ? prev.filter((x) => x !== pl.id)
                                  : [...prev, pl.id],
                              );
                            }}
                            sx={{
                              ...dropdownItemSx,
                              display: 'flex',
                              alignItems: 'center',
                              gap: 0.75,
                              ...getDropdownItemStateSx(darkFields, selected),
                            }}
                          >
                            {selected ? (
                              <CheckBoxIcon sx={{ fontSize: 16, color: '#2196f3', flexShrink: 0 }} />
                            ) : (
                              <CheckBoxBlankIcon sx={{ fontSize: 16, color: darkFields ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.35)', flexShrink: 0 }} />
                            )}
                            <Box sx={{ minWidth: 0 }}>
                              <Box component="span" sx={{ display: 'block' }}>{pl.display_name || pl.id}</Box>
                              <Box component="span" sx={{ display: 'block', opacity: 0.5, fontSize: '0.72rem' }}>
                                {pl.id}
                              </Box>
                            </Box>
                          </Box>
                        );
                      })
                    )}
                  </Box>
                </Popover>
              </Box>
            )}
          </Box>
        </Box>

        {/* ── Advanced agent limits ─────────────────────────────────────────── */}
        <MaxAgentStepsField
          value={recursionLimit}
          onChange={setRecursionLimit}
          defaultSteps={defaultGraphSteps}
          maxSteps={maxRecursionLimit}
          readOnly={readOnly}
          panelChrome={panelChrome}
          categoryFieldSx={categoryFieldSx}
        />

        <AgentSubagentsEditor
          currentAgentId={selectedAgentId}
          config={subagentsConfig}
          onChange={setSubagentsConfig}
          agents={agents}
          readOnly={readOnly}
          panelChrome={panelChrome}
          categoryFieldSx={categoryFieldSx}
        />

        {/* ── Agent chain (LibreChat Mixture-of-Agents) ─────────────────────── */}
        <AgentChainEditor
          currentAgentId={selectedAgentId}
          currentAgentName={name}
          agentIds={chainAgentIds}
          onChange={setChainAgentIds}
          hideSequential={hideSequentialOutputs}
          onHideSequentialChange={setHideSequentialOutputs}
          agents={agents}
          readOnly={readOnly}
          maxAgents={chainMaxAgents}
          panelChrome={panelChrome}
          categoryFieldSx={categoryFieldSx}
        />

        {/* ── File Search (KB) ─────────────────────────────────────────────── */}
        <Box sx={{ minWidth: 0 }}>
          <SectionHeader>Документы агента</SectionHeader>

          <Box sx={{ mt: 1, minWidth: 0 }}>
            <FormControlLabel
              control={
                <Checkbox
                  checked={fileSearchEnabled}
                  disabled={readOnly}
                  onChange={e => {
                    const on = e.target.checked;
                    setFileSearchEnabled(on);
                    if (on) setKbGuardMessage(null);
                  }}
                  size="small"
                  sx={{
                    color: panelChrome.fgSubtle,
                    '&.Mui-checked': { color: '#2196f3' },
                    p: 0.5,
                  }}
                />
              }
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography variant="caption" sx={{ color: panelChrome.fgMuted, fontSize: '0.78rem' }}>
                    Искать по файлам агента
                  </Typography>
                  <Tooltip
                    title="Файлы привязаны к этому агенту (не общая библиотека и не документы проекта). В чате поиск включается при выбранном агенте с этим флагом. Параметры индексации — в «Настройки РАГ для агента»."
                    arrow
                  >
                    <HelpIcon sx={{ fontSize: 12, color: panelChrome.fgSubtle, opacity: 0.7 }} />
                  </Tooltip>
                </Box>
              }
              sx={{ ml: 0, '& .MuiFormControlLabel-label': { ml: 0.5 } }}
            />

            {/* KB files list — сетка по 2 карточки в ряд, цвет по типу файла */}
            {isLoadingKb ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 1 }}>
                <CircularProgress size={16} sx={{ color: panelChrome.fgSubtle }} />
              </Box>
            ) : fileSearchEnabled && kbTotalFiles > 0 ? (
              <Box sx={{ mt: 0.5, mb: 1, minWidth: 0, width: '100%' }}>
                <RagFilesSearchField
                  value={kbFileSearchQuery}
                  onChange={setKbFileSearchQuery}
                  totalCount={kbTotalFiles}
                  variant="sidebar"
                  size="small"
                />
                {hasActiveKbFileSearch && (
                  <Typography
                    variant="caption"
                    sx={{ color: 'rgba(255,255,255,0.45)', fontSize: '0.65rem', display: 'block', mb: 0.5 }}
                  >
                    {kbFilteredTotal} из {kbTotalFiles}
                  </Typography>
                )}
                {kbFilteredTotal === 0 ? (
                  <Typography
                    variant="caption"
                    sx={{ color: 'rgba(255,255,255,0.45)', fontSize: '0.72rem', display: 'block', py: 0.5 }}
                  >
                    Ничего не найдено
                  </Typography>
                ) : (
                  <Box
                    sx={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
                      gap: 0.75,
                      minWidth: 0,
                      width: '100%',
                    }}
                  >
                    {filteredPendingKbUploads.map((pending) => (
                      <Box
                        key={pending.clientId}
                        sx={{
                          position: 'relative',
                          borderRadius: 1,
                          bgcolor: '#2a2d3a',
                          border: '1px solid rgba(255,255,255,0.08)',
                          p: 0.5,
                          minWidth: 0,
                          display: 'flex',
                          flexDirection: 'column',
                          justifyContent: 'center',
                        }}
                      >
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 0, pr: 2 }}>
                          <RagUploadingFileThumb filename={pending.filename} size={32} />
                          <Box sx={{ minWidth: 0, flex: 1 }}>
                            <Typography
                              variant="caption"
                              noWrap
                              component="span"
                              sx={{ color: 'white', fontSize: '0.68rem', display: 'block', fontWeight: 500, lineHeight: 1.3 }}
                              title={pending.filename}
                            >
                              {shortFileName(pending.filename, 16)}
                            </Typography>
                            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.45)', fontSize: '0.62rem', lineHeight: 1.2 }}>
                              {getFileTypeLabel(pending.filename)}
                            </Typography>
                          </Box>
                        </Box>
                      </Box>
                    ))}
                    {filteredSelectedKbDocuments.map(doc => (
                      <Box
                        key={doc.id}
                        sx={{
                          position: 'relative',
                          borderRadius: 1,
                          bgcolor: '#2a2d3a',
                          border: '1px solid rgba(255,255,255,0.08)',
                          p: 0.5,
                          minWidth: 0,
                          display: 'flex',
                          flexDirection: 'column',
                          justifyContent: 'center',
                        }}
                      >
                        {/* Крестик удаления */}
                        {!readOnly && (
                        <IconButton
                          size="small"
                          onClick={() => handleKbDelete(doc.id)}
                          sx={{
                            position: 'absolute',
                            top: 3,
                            right: 3,
                            p: 0.2,
                            color: 'rgba(255,255,255,0.45)',
                            '&:hover': { color: '#ef5350', bgcolor: 'rgba(239,83,80,0.12)' },
                          }}
                        >
                          <CloseIcon sx={{ fontSize: 11 }} />
                        </IconButton>
                        )}

                        {/* Иконка + имя в строку */}
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 0, pr: 2 }}>
                          {/* Цветной квадратик с иконкой */}
                          <Box
                            sx={{
                              width: 32,
                              height: 32,
                              borderRadius: 1,
                              bgcolor: getFileIconBg(doc.filename),
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              flexShrink: 0,
                            }}
                          >
                            {getFileIcon(doc.filename)}
                          </Box>

                          {/* Имя + тип */}
                          <Box sx={{ minWidth: 0, flex: 1 }}>
                            <Tooltip
                              title={doc.filename}
                              placement="top"
                              slotProps={{
                                tooltip: {
                                  sx: {
                                    bgcolor: 'rgba(42, 45, 58, 0.98)',
                                    color: '#fff',
                                    borderRadius: 3,
                                    border: '1px solid rgba(255,255,255,0.12)',
                                    fontSize: '0.75rem',
                                    fontWeight: 500,
                                    py: 0.75,
                                    px: 1.25,
                                    boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
                                  },
                                },
                              }}
                            >
                              <Typography
                                variant="caption"
                                noWrap
                                component="span"
                                sx={{ color: 'white', fontSize: '0.68rem', display: 'block', fontWeight: 500, lineHeight: 1.3 }}
                              >
                                {shortFileName(doc.filename, 16)}
                              </Typography>
                            </Tooltip>
                            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.45)', fontSize: '0.62rem', lineHeight: 1.2 }}>
                              {getFileTypeLabel(doc.filename)}
                            </Typography>
                          </Box>
                        </Box>
                      </Box>
                    ))}
                  </Box>
                )}
              </Box>
            ) : null}

            {ragReadyMessage && (
              <Alert
                severity="success"
                onClose={clearRagReadyMessage}
                sx={{
                  mt: 0.75,
                  mb: 0.5,
                  py: 0.25,
                  px: 1,
                  fontSize: '0.72rem',
                  bgcolor: 'rgba(76, 175, 80, 0.12)',
                  color: '#81c784',
                  border: '1px solid rgba(76, 175, 80, 0.35)',
                  '& .MuiAlert-icon': { color: '#4caf50', fontSize: '1.1rem', py: 0.5 },
                  '& .MuiAlert-message': { py: 0.6 },
                  '& .MuiAlert-action': { pt: 0.25 },
                }}
              >
                {ragReadyMessage}
              </Alert>
            )}

            {kbGuardMessage && (
              <Alert
                severity="warning"
                onClose={() => setKbGuardMessage(null)}
                sx={{
                  mt: 0.75,
                  mb: 0.5,
                  py: 0.25,
                  px: 1,
                  fontSize: '0.72rem',
                  bgcolor: 'rgba(255, 152, 0, 0.12)',
                  color: '#ffb74d',
                  border: '1px solid rgba(255, 152, 0, 0.35)',
                  '& .MuiAlert-icon': { color: '#ff9800', fontSize: '1.1rem', py: 0.5 },
                  '& .MuiAlert-message': { py: 0.6 },
                  '& .MuiAlert-action': { pt: 0.25 },
                }}
              >
                {kbGuardMessage}
              </Alert>
            )}

            {/* Upload button */}
            <input
              ref={kbFileInputRef}
              type="file"
              multiple
              accept=".pdf,.docx,.doc,.docm,.xlsx,.xls,.xlsm,.txt,.csv,.md,.log,.rtf"
              style={{ display: 'none' }}
              onChange={e => {
                const list = e.target.files;
                if (!list?.length) return;
                // Снимок ДО очистки input: FileList живой и обнуляется вместе с value.
                const snapshot = Array.from(list);
                e.target.value = '';
                void handleKbUpload(snapshot);
              }}
            />
            <Button
              size="small"
              startIcon={<SettingsIcon sx={{ fontSize: '0.85rem !important' }} />}
              fullWidth
              disabled={!fileSearchEnabled}
              onClick={() => {
                setKbGuardMessage(null);
                setShowAgentRagSettingsPanel(true);
              }}
              sx={{
                mt: 0.5,
                fontSize: '0.72rem',
                textTransform: 'none',
                py: 0.75,
                justifyContent: 'flex-start',
                ...secondaryBtnSx,
              }}
            >
              Настройки РАГ для агента
            </Button>
            <Button
              size="small"
              startIcon={isUploadingKb ? <CircularProgress size={13} sx={{ color: 'inherit' }} /> : <UploadIcon sx={{ fontSize: '0.85rem !important' }} />}
              fullWidth
              disabled={isUploadingKb || readOnly || !fileSearchEnabled}
              onClick={() => kbFileInputRef.current?.click()}
              sx={{
                mt: 0.5,
                fontSize: '0.72rem',
                textTransform: 'none',
                py: 0.75,
                justifyContent: 'flex-start',
                ...secondaryDashedBtnSx,
              }}
            >
              {isUploadingKb ? 'Загрузка...' : 'Добавить файлы'}
            </Button>
          </Box>
        </Box>

        {/* ── Tools and Actions (скрыто — см. COMMENTS.md) ─────────────────── */}
        {/*
        <Box>
          <SectionHeader>Tools and Actions</SectionHeader>
          <Box sx={{ mt: 1, display: 'flex', gap: 1 }}>
            <Button
              size="small"
              startIcon={<ToolsIcon sx={{ fontSize: '0.85rem !important' }} />}
              sx={{
                flex: 1,
                fontSize: '0.72rem',
                textTransform: 'none',
                color: 'rgba(255,255,255,0.6)',
                border: '1px solid rgba(255,255,255,0.15)',
                py: 0.75,
                '&:hover': { bgcolor: 'rgba(255,255,255,0.06)', borderColor: 'rgba(255,255,255,0.3)' },
              }}
            >
              Добавить инструменты
            </Button>
            <Button
              size="small"
              startIcon={<SparkleIcon sx={{ fontSize: '0.85rem !important' }} />}
              sx={{
                flex: 1,
                fontSize: '0.72rem',
                textTransform: 'none',
                color: 'rgba(255,255,255,0.6)',
                border: '1px solid rgba(255,255,255,0.15)',
                py: 0.75,
                '&:hover': { bgcolor: 'rgba(255,255,255,0.06)', borderColor: 'rgba(255,255,255,0.3)' },
              }}
            >
              Добавить действия
            </Button>
          </Box>
        </Box>
        */}

        {/* ── Author Contacts ──────────────────────────────────────────────── */}
        <Box>
          <SectionHeader>Контакты автора</SectionHeader>
          <Box sx={{ mt: 1, display: 'flex', flexDirection: 'column', gap: 1 }}>
            <Box>
              <TextField
                value={supportName}
                onChange={e => setSupportName(e.target.value)}
                label="Имя"
                placeholder="Имя контактного лица"
                variant="outlined"
                size="small"
                fullWidth
                disabled={readOnly}
                sx={formFieldInputSx}
              />
            </Box>
            <Box>
              <TextField
                value={supportEmail}
                onChange={e => setSupportEmail(e.target.value)}
                label="Электронная почта"
                placeholder="support@example.com"
                variant="outlined"
                size="small"
                fullWidth
                type="email"
                disabled={readOnly}
                sx={formFieldInputSx}
              />
            </Box>
          </Box>
        </Box>

        {/* Spacer */}
        <Box sx={{ pb: 1 }} />
      </Box>

      {/* ── Footer buttons ──────────────────────────────────────────────────── */}
      <Box sx={{ px: 2, py: 1.5, display: 'flex', flexDirection: 'column', gap: 1 }}>

        {/* Errors / Success */}
        {saveError && (
          <Typography variant="caption" sx={{ color: '#ef5350', fontSize: '0.72rem', textAlign: 'center' }}>
            {saveError}
          </Typography>
        )}
        {saveSuccess && (
          <Box sx={{ textAlign: 'center' }}>
            <Typography variant="caption" sx={{ color: '#66bb6a', fontSize: '0.72rem', display: 'block' }}>
              Агент успешно сохранён
            </Typography>
            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.5)', fontSize: '0.65rem', display: 'block', mt: 0.25 }}>
              Данные сохранены в базу приложения (PostgreSQL, таблица agents). Агент отображается в блоке «Агенты» выше — выберите его для редактирования или нажмите «Использовать в чате».
            </Typography>
          </Box>
        )}

        {/* Advanced + Version (скрыто — см. COMMENTS.md) */}
        {/*
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            size="small"
            startIcon={<SettingsIcon sx={{ fontSize: '0.85rem !important' }} />}
            fullWidth
            sx={{
              fontSize: '0.72rem',
              textTransform: 'none',
              color: 'rgba(255,255,255,0.5)',
              border: '1px solid rgba(255,255,255,0.12)',
              py: 0.6,
              '&:hover': { bgcolor: 'rgba(255,255,255,0.06)' },
            }}
          >
            Расширенные
          </Button>
          <Button
            size="small"
            startIcon={<VersionIcon sx={{ fontSize: '0.85rem !important' }} />}
            fullWidth
            sx={{
              fontSize: '0.72rem',
              textTransform: 'none',
              color: 'rgba(255,255,255,0.5)',
              border: '1px solid rgba(255,255,255,0.12)',
              py: 0.6,
              '&:hover': { bgcolor: 'rgba(255,255,255,0.06)' },
            }}
          >
            Версия
          </Button>
        </Box>
        */}

        {/* Share + Publish + Delete + Save — дизайн как в GPB_ASTRA */}
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          {selectedAgentId !== 'new' && isOwner && (
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', width: '100%' }}>
              <Box component="span" sx={{ flex: '1 1 140px', minWidth: 0, display: 'flex' }}>
                <Button
                  size="small"
                  fullWidth
                  startIcon={<ShareIcon />}
                  onClick={() => setShareDialogOpen(true)}
                  sx={footerNeutralActionBtnSx}
                >
                  Поделиться агентом
                </Button>
              </Box>
              <Box component="span" sx={{ flex: '1 1 140px', minWidth: 0, display: 'flex' }}>
                <Button
                  size="small"
                  fullWidth
                  disabled={isPublishing}
                  startIcon={
                    isPublishing ? (
                      <CircularProgress size={12} sx={{ color: 'inherit' }} />
                    ) : selectedAgent?.is_public ? (
                      <PublicOffIcon />
                    ) : (
                      <PublicIcon />
                    )
                  }
                  onClick={() => void handleTogglePublish()}
                  sx={{
                    ...footerActionBtnSx,
                    flex: '1 1 auto',
                    width: '100%',
                    color: selectedAgent?.is_public ? '#2e7d32' : panelChrome.fgMuted,
                    border: `1px solid ${selectedAgent?.is_public ? 'rgba(46,125,50,0.45)' : panelChrome.buttonBorder.replace('1px solid ', '')}`,
                    bgcolor: selectedAgent?.is_public ? 'rgba(46,125,50,0.1)' : 'transparent',
                    '&:hover': {
                      bgcolor: selectedAgent?.is_public
                        ? 'rgba(46,125,50,0.16)'
                        : panelChrome.hoverBg,
                      color: selectedAgent?.is_public ? '#2e7d32' : panelChrome.fgMuted,
                    },
                    '&:disabled': { color: panelChrome.fgSubtle },
                  }}
                >
                  {selectedAgent?.is_public ? 'Снять с публикации' : 'Опубликовать в галерее'}
                </Button>
              </Box>
              <Box component="span" sx={{ flex: '1 1 140px', minWidth: 0, display: 'flex' }}>
                <Button
                  size="small"
                  fullWidth
                  startIcon={<DeleteIcon />}
                  onClick={handleDelete}
                  sx={footerDeleteActionBtnSx}
                >
                  Удалить агента
                </Button>
              </Box>
            </Box>
          )}
          {canEdit ? (
            <Button
              fullWidth
              variant="contained"
              startIcon={isSaving ? <CircularProgress size={14} sx={{ color: 'white' }} /> : <SaveIcon />}
              onClick={handleSave}
              disabled={isSaving}
              sx={{
                bgcolor: '#2e7d32',
                textTransform: 'none',
                fontWeight: 600,
                fontSize: '0.82rem',
                py: 0.9,
                justifyContent: 'flex-start',
                textAlign: 'left',
                '& .MuiButton-startIcon': {
                  marginRight: '8px',
                  marginLeft: 0,
                  color: 'inherit',
                },
                '&:hover': { bgcolor: '#388e3c' },
                '&:disabled': { bgcolor: 'rgba(46,125,50,0.4)', color: 'rgba(255,255,255,0.5)' },
              }}
            >
              {isSaving ? 'Сохраняю...' : 'Сохранить'}
            </Button>
          ) : (
            <Button
              fullWidth
              variant="outlined"
              startIcon={<AgentIcon />}
              onClick={handleUseAgent}
              sx={{
                textTransform: 'none',
                fontWeight: 600,
                fontSize: '0.82rem',
                py: 0.9,
                justifyContent: 'flex-start',
                textAlign: 'left',
                color: panelChrome.fg,
                borderColor: panelChrome.buttonBorderHover,
                '& .MuiButton-startIcon': {
                  marginRight: '8px',
                  marginLeft: 0,
                  color: 'inherit',
                },
                '&:hover': { borderColor: panelChrome.fg, bgcolor: panelChrome.hoverBg },
              }}
            >
              Использовать в чате
            </Button>
          )}
        </Box>
      </Box>
        </>
      )}

      {selectedAgentId !== 'new' && (
        <ShareAgentDialog
          open={shareDialogOpen}
          onClose={() => setShareDialogOpen(false)}
          agentId={typeof selectedAgentId === 'number' ? selectedAgentId : 0}
          agentName={agents.find((a) => a.id === selectedAgentId)?.name || name || 'Агент'}
          isDarkMode={isDarkMode}
        />
      )}
    </Box>
  );
}
