import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useTheme } from '@mui/material/styles';
import type { SxProps, Theme } from '@mui/material';
import {
  Box,
  Typography,
  IconButton,
  Tooltip,
  Popover,
  Button,
  Switch,
  TextField,
  FormControl,
  InputLabel,
  OutlinedInput,
  InputAdornment,
} from '@mui/material';
import {
  HelpOutline as HelpOutlineIcon,
  ExpandMore as ExpandMoreIcon,
  Restore as RestoreIcon,
  ArrowBack as ArrowBackIcon,
  Save as SaveIcon,
} from '@mui/icons-material';
import { getSidebarPanelBackground } from '../../constants/sidebarPanelColor';
import { useAppActions } from '../../contexts/AppContext';
import { useRagReindexStatus } from '../../contexts/RagReindexStatusContext';
import { getApiUrl, getAuthFetchHeaders } from '../../config/api';
import ClampedNumberField from '../ClampedNumberField';
import {
  fetchRagEntityDefaults,
  resolveRagEmbeddingModelPath,
  resolveRagRerankerModelPath,
} from '../../constants/ragEntityDefaults';
import {
  getDropdownPopoverPaperSx,
  getDropdownItemSx,
  getDropdownItemStateSx,
  getCategoryFieldSx,
  flattenSx,
  getFormFieldInputSx,
  getDropdownChevronSx,
  AGENT_CONSTRUCTOR_FIELD_INPUT_PROPS,
  AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX,
  getAgentConstructorRestoreButtonSx,
  AGENT_CONSTRUCTOR_SAVE_BUTTON_SX,
  AGENT_CONSTRUCTOR_SAVE_ICON_SX,
  SIDEBAR_HIDE_SCROLLBAR_SX,
} from '../../constants/menuStyles';
import MemoryRagLibrarySection from './MemoryRagLibrarySection';
import RagModelSelector from '../RagModelSelector';
import { saveEntityRagSettings, type EntityRagDraft } from '../../utils/entityRagSettings';
import { dispatchRagEntitySettingsApplied } from '../../constants/ragEntityEvents';
import { useAuth } from '../../contexts/AuthContext';
import {
  MODEL_SETTINGS_LABEL_WRAPPER_SX,
  MODEL_SETTINGS_HELP_ICON_BUTTON_SX,
  modelSettingsSwitchSx,
} from '../../constants/modelSettingsStyles';

const RAG_NUM_FIELDS_ROW_SX = {
  display: 'flex',
  flexDirection: { xs: 'column', sm: 'row' },
  gap: 1.5,
  alignItems: { sm: 'flex-start' },
  flexWrap: 'wrap',
} as const;

const SECTION_HEADER_SX_DARK = {
  color: 'rgba(255,255,255,0.5)',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
  fontSize: '0.7rem',
} as const;

const SECTION_HEADER_SX_LIGHT = {
  color: 'rgba(0,0,0,0.87)',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
  fontSize: '0.7rem',
} as const;

const SWITCH_LABEL_SX_DARK = {
  color: 'rgba(255,255,255,0.7)',
  fontSize: '0.78rem',
} as const;

const SWITCH_LABEL_SX_LIGHT = {
  color: 'rgba(0,0,0,0.87)',
  fontSize: '0.78rem',
} as const;

type RAGStrategy = 'auto' | 'hybrid' | 'vector' | 'graph' | 'lexical';
type ChunkingStrategy = 'hierarchical' | 'fixed' | 'markdown' | 'separators' | 'semantic';
const RAG_STRATEGY_STORAGE_KEY = 'rag_strategy';
const RAG_CHUNKING_STORAGE_KEY = 'rag_chunking_strategy';
const DEFAULT_RAG_SYSTEM_PROMPT = '';

function ragStorageKey(base: string, userId?: string | null): string {
  const uid = (userId || '').trim().toLowerCase();
  return uid ? `${base}:${uid}` : base;
}

function normalizeStoredStrategy(raw: string | null): RAGStrategy {
  const s = (raw || 'hybrid').trim().toLowerCase();
  if (s === 'reranking') return 'hybrid';
  // Однократная миграция старого внутреннего имени; новые запросы его не используют.
  if (s === 'standard') return 'vector';
  if (s === 'auto' || s === 'hybrid' || s === 'vector' || s === 'graph' || s === 'lexical') {
    return s;
  }
  return 'hybrid';
}

function normalizeChunkingStrategy(raw: string | null): ChunkingStrategy {
  const s = (raw || 'fixed').trim().toLowerCase();
  if (s === 'hierarchical' || s === 'fixed' || s === 'markdown' || s === 'separators' || s === 'semantic') {
    return s;
  }
  return 'fixed';
}

interface RAGSettingsProps {
  isDarkMode?: boolean;
  /**
   * Зафиксированный скоуп (без переключателя «Настройки для»).
   * Используется в конструкторе агента (scope=agent) и в проектах (scope=project).
   */
  lockedScope?: 'project' | 'agent' | 'kb';
  /** ID конкретного проекта или агента — настройки изолированы по сущности. */
  entityId?: string | number | null;
  /** Название проекта/агента для отладочных логов. */
  entityName?: string;
  /** Ленивое получение id (например, черновик проекта при создании). */
  onResolveEntityId?: () => string | number | Promise<string | number>;
  /** page — страница настроек; panel — подменю в сайдбаре/модалке. */
  variant?: 'page' | 'panel';
  /** Кнопка «Назад» в режиме panel. */
  onClose?: () => void;
  /** Заголовок панели (по умолчанию зависит от lockedScope). */
  panelTitle?: string;
  /**
   * Текст «Инструкции» из конструктора агента или модалки проекта —
   * единый источник rag_system_prompt для scope=agent|project.
   */
  entityInstructionsPrompt?: string;
  /**
   * Режим черновика: панель ничего не пишет в БД, а отдаёт значения наверх.
   * Запись и перечанковка — по кнопке «Сохранить» самой сущности.
   */
  draft?: boolean;
  /** Черновик от родителя — чтобы правки не терялись при повторном открытии панели. */
  draftValue?: EntityRagDraft | null;
  /** Вызывается по «Сохранить настройки» в режиме черновика. */
  onDraftChange?: (draft: EntityRagDraft) => void;
  /** Принудительный только просмотр (роль «Зритель»), даже в режиме draft. */
  readOnly?: boolean;
}

function buildEntityQuery(
  scope: 'project' | 'agent' | 'kb',
  entityId?: string | number | null,
): string {
  if (entityId == null || entityId === '') return '';
  if (scope === 'project') {
    return `&project_id=${encodeURIComponent(String(entityId))}`;
  }
  if (scope === 'kb') {
    return `&knowledge_base_id=${encodeURIComponent(String(entityId))}`;
  }
  return `&agent_id=${encodeURIComponent(String(entityId))}`;
}

function buildEntityBody(
  scope: 'project' | 'agent' | 'kb',
  entityId?: string | number | null,
  entityName?: string,
): Record<string, string | number> {
  const body: Record<string, string | number> = {};
  if (entityId == null || entityId === '') return body;
  if (scope === 'project') {
    body.project_id = String(entityId);
  } else if (scope === 'kb') {
    body.knowledge_base_id = Number(entityId);
  } else {
    body.agent_id = Number(entityId);
  }
  const name = (entityName || '').trim();
  if (name) body.entity_name = name;
  return body;
}

function notifyEntitySettingsApplied(
  scope: 'project' | 'agent' | 'kb',
  resolvedEntityId: string | number | null | undefined,
  name?: string,
): void {
  if (resolvedEntityId == null || resolvedEntityId === '') return;
  dispatchRagEntitySettingsApplied({
    scope,
    entityId: resolvedEntityId,
    entityName: name,
  });
}

export default function RAGSettings({
  isDarkMode: isDarkModeProp,
  lockedScope,
  entityId,
  entityName,
  onResolveEntityId,
  variant = 'page',
  onClose,
  panelTitle,
  entityInstructionsPrompt,
  draft = false,
  draftValue,
  onDraftChange,
  readOnly = false,
}: RAGSettingsProps = {}) {
  const theme = useTheme();
  const isPanel = variant === 'panel';
  const isDarkMode = isDarkModeProp ?? (isPanel ? true : theme.palette.mode === 'dark');
  const { user } = useAuth();
  const ragUserId = user?.user_id || user?.username || '';
  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const formFieldInputSx = useMemo(() => getFormFieldInputSx(isDarkMode), [isDarkMode]);
  const categoryFieldSx = useMemo(() => getCategoryFieldSx(isDarkMode), [isDarkMode]);
  const ragTextFieldSx = formFieldInputSx;
  const switchSx = useMemo(() => modelSettingsSwitchSx(isDarkMode), [isDarkMode]);
  const sectionHeaderSx = isDarkMode ? SECTION_HEADER_SX_DARK : SECTION_HEADER_SX_LIGHT;
  const switchLabelSx = isDarkMode ? SWITCH_LABEL_SX_DARK : SWITCH_LABEL_SX_LIGHT;
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(isDarkMode), [isDarkMode]);
  const saveButtonSx = AGENT_CONSTRUCTOR_SAVE_BUTTON_SX;
  const restoreButtonSx = useMemo(() => getAgentConstructorRestoreButtonSx(isDarkMode), [isDarkMode]);
  const mutedCaptionColor = isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.55)';
  const helpIconColor = isDarkMode ? 'rgba(255,255,255,0.25)' : 'rgba(0,0,0,0.45)';
  const triggerMinWidth = isPanel ? 0 : 280;
  const [selectedStrategy, setSelectedStrategy] = useState<RAGStrategy>('hybrid');
  const [isLoading, setIsLoading] = useState(false);
  const [strategyPopoverAnchor, setStrategyPopoverAnchor] = useState<HTMLElement | null>(null);
  const [chunkingPopoverAnchor, setChunkingPopoverAnchor] = useState<HTMLElement | null>(null);
  const strategyOutlinedRef = useRef<HTMLDivElement>(null);
  const chunkingOutlinedRef = useRef<HTMLDivElement>(null);
  const [agenticRagEnabled, setAgenticRagEnabled] = useState(true);
  const [ragQueryFixTypos, setRagQueryFixTypos] = useState(false);
  const [ragMultiQueryEnabled, setRagMultiQueryEnabled] = useState(false);
  const [ragHydeEnabled, setRagHydeEnabled] = useState(false);
  const [ragChatTopK, setRagChatTopK] = useState(12);
  const [ragChunkingStrategy, setRagChunkingStrategy] = useState<ChunkingStrategy>('fixed');
  const [ragChunkOverlap, setRagChunkOverlap] = useState(100);
  const [ragChunkSize, setRagChunkSize] = useState(4000);
  const [ragSimilarityThreshold, setRagSimilarityThreshold] = useState(0);
  const [ragRerankingEnabled, setRagRerankingEnabled] = useState(true);
  const [ragRerankTopN, setRagRerankTopN] = useState(12);
  const [ragSystemPrompt, setRagSystemPrompt] = useState(DEFAULT_RAG_SYSTEM_PROMPT);
  /** Черновики моделей: применяются только по «Сохранить настройки». */
  const [draftEmbeddingPath, setDraftEmbeddingPath] = useState<string | null>(null);
  const [draftRerankerPath, setDraftRerankerPath] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  /** Может ли текущий пользователь менять настройки этой сущности (роль с сервера). */
  const [canEdit, setCanEdit] = useState(true);
  /** Для каждого стора показываются и сохраняются настройки: проекты | агенты */
  const [projectsAgentsScope, setProjectsAgentsScope] = useState<'project' | 'agent' | 'kb'>(
    lockedScope ?? 'project'
  );
  const isInitializedRef = useRef(false);
  const { showNotification } = useAppActions();
  const { notifyReindexStarted } = useRagReindexStatus();

  const entityLogLabel = useMemo(() => {
    const name = (entityName || '').trim();
    if (projectsAgentsScope === 'project') {
      return name ? `проекта «${name}»` : 'проекта';
    }
    return name ? `агента «${name}»` : 'агента';
  }, [entityName, projectsAgentsScope]);

  const resolveEntityId = async (): Promise<string | number | null> => {
    if (entityId != null && entityId !== '') return entityId;
    if (!onResolveEntityId) return null;
    const resolved = await Promise.resolve(onResolveEntityId());
    return resolved ?? null;
  };

  // Синхронизация lockedScope (если проп изменился)
  useEffect(() => {
    if (lockedScope) {
      setProjectsAgentsScope(lockedScope);
    }
  }, [lockedScope]);

  // Дефолты моделей из ConfigMap backend — кэшируем для resolve/collectDraft.
  useEffect(() => {
    if (!isPanel) return;
    void fetchRagEntityDefaults(projectsAgentsScope);
  }, [isPanel, projectsAgentsScope]);

  // Перечитываем при смене пользователя / скоупа (только в panel — страница Settings
  // показывает лишь библиотеку памяти).
  useEffect(() => {
    if (!isPanel) return;
    isInitializedRef.current = false;
    setDraftEmbeddingPath(null);
    setDraftRerankerPath(null);
    void loadRAGSettings();
  }, [ragUserId, projectsAgentsScope, isPanel, entityId, readOnly, draft]);

  // Если родитель переключил роль на «Зритель» при уже открытой панели.
  useEffect(() => {
    if (readOnly) setCanEdit(false);
  }, [readOnly]);

  const syncLocalStorageCache = (strategy: RAGStrategy, chunking: ChunkingStrategy) => {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(ragStorageKey(RAG_STRATEGY_STORAGE_KEY, ragUserId), strategy);
    localStorage.setItem(ragStorageKey(RAG_CHUNKING_STORAGE_KEY, ragUserId), chunking);
  };

  const applyModelSelection = async (
    kind: 'embedding' | 'reranker',
    modelPath: string,
  ): Promise<boolean> => {
    if (kind === 'embedding') {
      const ok = window.confirm(
        'Смена embedding-модели загрузит её для поиска/индексации и переиндексирует документы этого проекта или KB этого агента. Memory RAG не затрагивается. Модели с другой размерностью (dim) — только через ConfigMap. Продолжить?',
      );
      if (!ok) return false;
    }
    const resolvedEntityId = await resolveEntityId();
    const response = await fetch(getApiUrl('/api/rag/models/select'), {
      method: 'POST',
      headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        model_type: kind,
        model_path: modelPath,
        scope: projectsAgentsScope,
        ...buildEntityBody(projectsAgentsScope, resolvedEntityId, entityName),
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        typeof data?.detail === 'string'
          ? data.detail
          : data?.message || `Не удалось применить модель ${kind}`,
      );
    }
    if (Boolean(data?.reindexed)) {
      notifyReindexStarted();
      showNotification(
        'success',
        'Модель загружена. Запущена переиндексация ваших документов (без Memory RAG).',
      );
    }
    return true;
  };

  /** Текущее состояние формы как черновик — то, что уедет наверх и в БД. */
  const collectDraft = (): EntityRagDraft => ({
    strategy: selectedStrategy,
    agentic_rag_enabled: agenticRagEnabled,
    rag_query_fix_typos: ragQueryFixTypos,
    rag_multi_query_enabled: ragMultiQueryEnabled,
    rag_hyde_enabled: ragHydeEnabled,
    rag_chat_top_k: ragChatTopK,
    rag_chunking_strategy: ragChunkingStrategy,
    rag_chunk_size: ragChunkSize,
    rag_chunk_overlap: ragChunkOverlap,
    rag_similarity_threshold: ragSimilarityThreshold,
    rag_reranking_enabled: ragRerankingEnabled,
    rag_rerank_top_n: ragRerankTopN,
    rag_embedding_model_path: draft
      ? resolveRagEmbeddingModelPath(draftEmbeddingPath)
      : draftEmbeddingPath,
    rag_reranker_model_path: draft
      ? resolveRagRerankerModelPath(draftRerankerPath)
      : draftRerankerPath,
  });

  const applyDraftToForm = (value: EntityRagDraft) => {
    setSelectedStrategy(normalizeStoredStrategy(value.strategy));
    setAgenticRagEnabled(value.agentic_rag_enabled);
    setRagQueryFixTypos(value.rag_query_fix_typos);
    setRagMultiQueryEnabled(value.rag_multi_query_enabled);
    setRagHydeEnabled(value.rag_hyde_enabled);
    setRagChatTopK(value.rag_chat_top_k);
    setRagChunkingStrategy(normalizeChunkingStrategy(value.rag_chunking_strategy));
    setRagChunkSize(value.rag_chunk_size);
    setRagChunkOverlap(value.rag_chunk_overlap);
    setRagSimilarityThreshold(value.rag_similarity_threshold);
    setRagRerankingEnabled(value.rag_reranking_enabled);
    setRagRerankTopN(value.rag_rerank_top_n);
    setDraftEmbeddingPath(resolveRagEmbeddingModelPath(value.rag_embedding_model_path));
    setDraftRerankerPath(resolveRagRerankerModelPath(value.rag_reranker_model_path));
  };

  const loadRAGSettings = async () => {
    // Незакоммиченный черновик важнее серверных значений: пользователь уже
    // что-то накрутил, повторное открытие панели не должно это стирать.
    if (draft && draftValue) {
      applyDraftToForm(draftValue);
      // draft сам по себе не даёт право писать: зритель смотрит чужой черновик только на чтение.
      setCanEdit(!readOnly);
      setIsLoading(false);
      isInitializedRef.current = true;
      return;
    }
    try {
      setIsLoading(true);
      const resolvedEntityId = await resolveEntityId();
      const entityQuery = buildEntityQuery(projectsAgentsScope, resolvedEntityId);
      const response = await fetch(
        getApiUrl(`/api/rag/settings?scope=${projectsAgentsScope}${entityQuery}`),
        { headers: getAuthFetchHeaders() },
      );
      if (response.ok) {
        const data = await response.json();
        // Роль приходит с сервера: читателю расшаренного агента настройки видны,
        // но заблокированы — вычислять это на фронте не нужно.
        // draft=true (создание проекта/агента): сущность ещё может не существовать
        // в Postgres, а сервер тогда отдаёт can_edit=false — не блокируем создателя.
        setCanEdit(
          readOnly
            ? false
            : draft
              ? true
              : typeof data.can_edit === 'boolean'
                ? data.can_edit
                : true,
        );
        let nextStrategy: RAGStrategy = 'hybrid';
        let nextChunking: ChunkingStrategy = 'fixed';
        if (data.strategy) {
          // Источник истины — Postgres пользователя, не общий localStorage браузера.
          nextStrategy = normalizeStoredStrategy(String(data.strategy));
          setSelectedStrategy(nextStrategy);
        }
        if (typeof data.agentic_rag_enabled === 'boolean') {
          setAgenticRagEnabled(data.agentic_rag_enabled);
        }
        if (typeof data.rag_query_fix_typos === 'boolean') {
          setRagQueryFixTypos(data.rag_query_fix_typos);
        }
        if (typeof data.rag_multi_query_enabled === 'boolean') {
          setRagMultiQueryEnabled(data.rag_multi_query_enabled);
        }
        if (typeof data.rag_hyde_enabled === 'boolean') {
          setRagHydeEnabled(data.rag_hyde_enabled);
        }
        if (typeof data.rag_chat_top_k === 'number' && Number.isFinite(data.rag_chat_top_k)) {
          const k = Math.max(1, Math.min(64, Math.round(data.rag_chat_top_k)));
          setRagChatTopK(k);
        }
        if (typeof data.rag_chunking_strategy === 'string') {
          nextChunking = normalizeChunkingStrategy(data.rag_chunking_strategy);
          setRagChunkingStrategy(nextChunking);
        }
        if (typeof data.rag_chunk_overlap === 'number' && Number.isFinite(data.rag_chunk_overlap)) {
          setRagChunkOverlap(Math.max(0, Math.min(2000, Math.round(data.rag_chunk_overlap))));
        }
        if (typeof data.rag_chunk_size === 'number' && Number.isFinite(data.rag_chunk_size)) {
          setRagChunkSize(Math.max(200, Math.min(8000, Math.round(data.rag_chunk_size))));
        }
        if (typeof data.rag_similarity_threshold === 'number' && Number.isFinite(data.rag_similarity_threshold)) {
          setRagSimilarityThreshold(Math.max(0, Math.min(1, data.rag_similarity_threshold)));
        }
        if (typeof data.rag_reranking_enabled === 'boolean') {
          setRagRerankingEnabled(data.rag_reranking_enabled);
        }
        if (typeof data.rag_rerank_top_n === 'number' && Number.isFinite(data.rag_rerank_top_n)) {
          setRagRerankTopN(Math.max(1, Math.min(64, Math.round(data.rag_rerank_top_n))));
        }
        if (typeof data.rag_system_prompt === 'string') {
          // Пустая строка — валидное значение (мягкие правила).
          setRagSystemPrompt(data.rag_system_prompt);
        }
        // Кэш для SocketContext / upload — только применённые (серверные) значения.
        syncLocalStorageCache(nextStrategy, nextChunking);
        if (draft) {
          const emb = String(data.rag_embedding_model_path || '').trim();
          const rer = String(data.rag_reranker_model_path || '').trim();
          const envDefaults = await fetchRagEntityDefaults(projectsAgentsScope);
          setDraftEmbeddingPath(emb || envDefaults.embeddingPath);
          setDraftRerankerPath(rer || envDefaults.rerankerPath);
        } else {
          setDraftEmbeddingPath(null);
          setDraftRerankerPath(null);
        }
        if (resolvedEntityId != null && resolvedEntityId !== '') {
          console.debug(`[RAG] Настройки применены для ${entityLogLabel}`);
        }
      } else if (response.status === 404) {
        // Оставляем текущее значение (локальное), если endpoint не найден
      }
    } catch (error) {
      console.error('Ошибка загрузки настроек RAG:', error);
    } finally {
      setIsLoading(false);
      isInitializedRef.current = true;
    }
  };

  const saveRAGSettings = async (): Promise<boolean> => {
    // Режим черновика: сначала запоминаем у родителя. Если у сущности уже есть
    // id (черновик проекта создан при открытии RAG) — сразу пишем в БД, иначе
    // пользователь думает, что «Применить» ничего не сделало.
    if (draft) {
      const collected = collectDraft();
      onDraftChange?.(collected);
      syncLocalStorageCache(selectedStrategy, ragChunkingStrategy);

      let resolvedEntityId: string | number | null = null;
      try {
        resolvedEntityId = await resolveEntityId();
      } catch {
        resolvedEntityId = null;
      }

      if (resolvedEntityId != null && resolvedEntityId !== '') {
        const saved = await saveEntityRagSettings({
          scope: projectsAgentsScope,
          entityId: resolvedEntityId,
          entityName: entityName || String(resolvedEntityId),
          instructions:
            entityInstructionsPrompt !== undefined
              ? entityInstructionsPrompt.trim()
              : ragSystemPrompt.trim() || DEFAULT_RAG_SYSTEM_PROMPT,
          draft: collected,
        });
        if (saved.ok) {
          if (saved.reindexed) {
            notifyReindexStarted();
          }
          showNotification('success', 'Настройки РАГ сохранены');
          notifyEntitySettingsApplied(
            projectsAgentsScope,
            resolvedEntityId,
            entityName || String(resolvedEntityId),
          );
        } else {
          showNotification(
            'warning',
            `Настройки запомнены в форме. На сервер пока не записались (${saved.message}). Нажмите «Создать» / «Сохранить» у проекта или агента.`,
          );
        }
      } else {
        showNotification(
          'success',
          'Настройки РАГ запомнены. Они запишутся, когда вы сохраните проект или агента.',
        );
      }
      onClose?.();
      return true;
    }
    try {
      setIsSaving(true);
      const resolvedEntityId = await resolveEntityId();
      if (onResolveEntityId && (resolvedEntityId == null || resolvedEntityId === '')) {
        showNotification('error', 'Не удалось определить проект или агента для сохранения настроек');
        return false;
      }

      if (draftEmbeddingPath) {
        const applied = await applyModelSelection('embedding', draftEmbeddingPath);
        if (!applied) return false;
      }
      if (draftRerankerPath) {
        const applied = await applyModelSelection('reranker', draftRerankerPath);
        if (!applied) return false;
      }

      const response = await fetch(getApiUrl('/api/rag/settings'), {
        method: 'PUT',
        headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          scope: projectsAgentsScope,
          ...buildEntityBody(projectsAgentsScope, resolvedEntityId, entityName),
          strategy: selectedStrategy,
          agentic_rag_enabled: agenticRagEnabled,
          rag_query_fix_typos: ragQueryFixTypos,
          rag_multi_query_enabled: ragMultiQueryEnabled,
          rag_hyde_enabled: ragHydeEnabled,
          rag_chat_top_k: ragChatTopK,
          rag_chunking_strategy: ragChunkingStrategy,
          rag_chunk_size: ragChunkSize,
          rag_chunk_overlap: ragChunkOverlap,
          rag_similarity_threshold: ragSimilarityThreshold,
          rag_reranking_enabled: ragRerankingEnabled,
          rag_rerank_top_n: ragRerankTopN,
          rag_system_prompt:
            entityInstructionsPrompt !== undefined
              ? entityInstructionsPrompt.trim()
              : ragSystemPrompt.trim() || DEFAULT_RAG_SYSTEM_PROMPT,
        }),
      });

      if (response.ok) {
        // Правка нарезки или эмбеддера тоже запускает перечанковку — сервер
        // сообщает об этом полем reindexed.
        const saved = await response.json().catch(() => ({}));
        if (Boolean(saved?.reindexed)) {
          notifyReindexStarted();
        }
        syncLocalStorageCache(selectedStrategy, ragChunkingStrategy);
        setDraftEmbeddingPath(null);
        setDraftRerankerPath(null);
        if (resolvedEntityId != null && resolvedEntityId !== '') {
          console.debug(`[RAG] Настройки сохранены для ${entityLogLabel}`);
        }
        notifyEntitySettingsApplied(
          projectsAgentsScope,
          resolvedEntityId,
          entityName || (resolvedEntityId != null ? String(resolvedEntityId) : undefined),
        );
        showNotification('success', 'Настройки RAG сохранены');
        return true;
      }
      const details = await response.text().catch(() => '');
      throw new Error(`Ошибка сохранения настроек RAG: ${response.status}${details ? ` — ${details}` : ''}`);
    } catch (error) {
      console.error('Ошибка сохранения настроек RAG:', error);
      const message = error instanceof Error ? error.message : 'Не удалось сохранить настройки RAG';
      showNotification('error', message);
      return false;
    } finally {
      setIsSaving(false);
    }
  };

  const resetRAGSettings = async () => {
    // В черновике сбрасывать на сервере нечего: сущность там ещё либо не
    // создана, либо не трогалась. Возвращаем дефолты кластера в форму.
    if (draft) {
      try {
        const response = await fetch(getApiUrl(`/api/rag/settings?scope=${projectsAgentsScope}`), {
          headers: getAuthFetchHeaders(),
        });
        if (!response.ok) throw new Error(`defaults ${response.status}`);
        const data = await response.json();
        const envDefaults = await fetchRagEntityDefaults(projectsAgentsScope);
        applyDraftToForm({
          strategy: String(data.strategy || 'hybrid'),
          agentic_rag_enabled: Boolean(data.agentic_rag_enabled),
          rag_query_fix_typos: Boolean(data.rag_query_fix_typos),
          rag_multi_query_enabled: Boolean(data.rag_multi_query_enabled),
          rag_hyde_enabled: Boolean(data.rag_hyde_enabled),
          rag_chat_top_k: Number(data.rag_chat_top_k) || 12,
          rag_chunking_strategy: String(data.rag_chunking_strategy || 'fixed'),
          rag_chunk_size: Number(data.rag_chunk_size) || 4000,
          rag_chunk_overlap: Number(data.rag_chunk_overlap) || 100,
          rag_similarity_threshold: Number(data.rag_similarity_threshold) || 0,
          rag_reranking_enabled: Boolean(data.rag_reranking_enabled),
          rag_rerank_top_n: Number(data.rag_rerank_top_n) || 12,
          rag_embedding_model_path: envDefaults.embeddingPath,
          rag_reranker_model_path: envDefaults.rerankerPath,
        });
        showNotification('success', 'Настройки РАГ возвращены к значениям по умолчанию');
      } catch (error) {
        console.error('Ошибка сброса настроек RAG:', error);
        showNotification('error', 'Не удалось получить настройки по умолчанию');
      }
      return;
    }
    try {
      const resolvedEntityId = await resolveEntityId();
      const entityQuery = buildEntityQuery(projectsAgentsScope, resolvedEntityId);
      const response = await fetch(getApiUrl(`/api/rag/settings/reset?scope=${projectsAgentsScope}${entityQuery}`), {
        method: 'POST',
        headers: getAuthFetchHeaders(),
      });
      if (!response.ok) {
        throw new Error(`reset ${response.status}`);
      }
      syncLocalStorageCache('auto', 'hierarchical');
      setDraftEmbeddingPath(null);
      setDraftRerankerPath(null);
      await loadRAGSettings();
      showNotification('success', 'Настройки RAG восстановлены по умолчанию');
    } catch (error) {
      console.error('Ошибка сброса настроек RAG:', error);
      showNotification('error', 'Не удалось сбросить настройки RAG');
    }
  };

  const getStrategyLabel = (strategy: RAGStrategy): string => {
    switch (strategy) {
      case 'auto':
        return 'Автоматический выбор';
      case 'hybrid':
        return 'Гибридный';
      case 'vector':
        return 'Векторный';
      case 'lexical':
        return 'Лексический';
      case 'graph':
        return 'Графовый';
      default:
        return 'Автоматический выбор';
    }
  };

  const getStrategyDescription = (strategy: RAGStrategy): string => {
    switch (strategy) {
      case 'auto':
        return 'Анализирует формулировку вопроса и сам выбирает одну из четырёх стратегий. Точные фразы и коды направляет в лексический поиск, вопросы по смыслу — в векторный, связи между фактами — в графовый, а для остальных запросов использует гибридный.';
      case 'hybrid':
        return 'Одновременно ищет и по смыслу, и по точным словам. Хорошо подходит для большинства обычных вопросов, когда заранее неизвестно, какой способ поиска даст лучший результат.';
      case 'vector':
        return 'Ищет фрагменты, близкие к вопросу по смыслу, даже если в документе использованы другие слова. Лучше выбирать для пересказов, объяснений и вопросов со свободной формулировкой.';
      case 'lexical':
        return 'Ищет точные слова и формулировки из вопроса. Лучше выбирать для кодов, номеров, артикулов, имён, цитат и терминов, которые должны совпасть с текстом документа.';
      case 'graph':
        return 'Находит подходящие фрагменты и добавляет связанный с ними контекст из документа. Лучше выбирать для сравнений, причин и последствий, цепочек событий и вопросов, ответ на которые расположен в нескольких связанных фрагментах.';
      default:
        return '';
    }
  };

  const getStrategyUseCase = (strategy: RAGStrategy): string => {
    switch (strategy) {
      case 'auto':
        return 'Выбирайте, если не уверены, какая стратегия лучше подходит к вопросу.';
      case 'hybrid':
        return 'Выбирайте как универсальный режим для повседневной работы с документами.';
      case 'vector':
        return 'Выбирайте, когда смысл важнее совпадения конкретных слов.';
      case 'lexical':
        return 'Выбирайте, когда в документе нужно найти конкретное слово, имя, номер или выражение.';
      case 'graph':
        return 'Выбирайте для сложных вопросов, требующих собрать несколько связанных фактов.';
      default:
        return '';
    }
  };

  const getChunkingLabel = (strategy: ChunkingStrategy): string => {
    switch (strategy) {
      case 'hierarchical':
        return 'Иерархическое';
      case 'fixed':
        return 'Фиксированное';
      case 'markdown':
        return 'По разметке';
      case 'separators':
        return 'По разделителям';
      case 'semantic':
        return 'Семантическое';
      default:
        return 'Иерархическое';
    }
  };

  const getChunkingDescription = (strategy: ChunkingStrategy): string => {
    const scope =
      ' Действует только для документов проекта и документов агента. Общая библиотека всегда режется своим чанкером и этот выбор не использует.';
    switch (strategy) {
      case 'hierarchical':
        return (
          'Документ сначала делится на крупные смысловые блоки, затем на более мелкие фрагменты. Это обычно дает лучший баланс между полнотой контекста и точностью поиска.' +
          scope
        );
      case 'fixed':
        return (
          'Текст режется на чанки фиксированной длины. Предсказуемо по размеру и скорости, но может разрывать мысль на границах.' +
          scope
        );
      case 'markdown':
        return (
          'Чанкование ориентируется на структуру разметки (заголовки, списки, секции). Хорошо подходит для технической документации и markdown-файлов.' +
          scope
        );
      case 'separators':
        return (
          'Разделение по естественным разделителям (абзацы, переносы, знаки, служебные маркеры). Менее жесткое, чем fixed, и обычно более читабельное.' +
          scope
        );
      case 'semantic':
        return (
          'Смысловое чанкование пытается сохранять цельные идеи внутри чанка (абзацный режим). Обычно дает лучшее качество retrieval.' +
          scope
        );
      default:
        return '';
    }
  };

  const getChunkingUseCase = (strategy: ChunkingStrategy): string => {
    switch (strategy) {
      case 'hierarchical':
        return 'Рекомендуется как универсальный режим для смешанных корпусов документов.';
      case 'fixed':
        return 'Подходит для простых однотипных документов, где важна стабильная производительность.';
      case 'markdown':
        return 'Используйте для wiki/документации с выраженной структурой разделов.';
      case 'separators':
        return 'Подходит для текстов с понятной абзацной структурой без сложной разметки.';
      case 'semantic':
        return 'Используйте, когда приоритет - максимальная релевантность и смысловая целостность чанков.';
      default:
        return '';
    }
  };

  // Страница «Настройки → RAG»: только библиотека памяти.
  // Карточки project/agent перенесены в конструктор агента и модалки проекта.
  if (!isPanel) {
    return (
      <Box sx={{ p: 3 }}>
        <MemoryRagLibrarySection variant="prominent" />
      </Box>
    );
  }

  const strategyFieldSx = flattenSx(categoryFieldSx, {
    flex: 1,
    minWidth: 0,
    opacity: isLoading || isSaving || !canEdit ? 0.7 : 1,
    pointerEvents: isLoading || isSaving || !canEdit ? 'none' : 'auto',
  });

  const renderSwitchRow = (
    label: string,
    help: React.ReactNode,
    checked: boolean,
    onChange: (checked: boolean) => void,
  ) => (
    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0 }}>
        <Typography variant="caption" sx={switchLabelSx}>
          {label}
        </Typography>
        <Tooltip title={help} arrow>
          <HelpOutlineIcon sx={{ fontSize: 12, color: helpIconColor, flexShrink: 0 }} />
        </Tooltip>
      </Box>
      <Switch
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={isLoading || isSaving || !canEdit}
        sx={switchSx}
      />
    </Box>
  );

  const settingsBody = (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        gap: 1.5,
        color: isDarkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.87)',
        '& .MuiFormHelperText-root': {
          color: isDarkMode ? 'rgba(255,255,255,0.45) !important' : 'rgba(0,0,0,0.55) !important',
        },
      }}
    >
      {/* ── Модели РАГ ── */}
      <Box>
        <Typography variant="caption" sx={sectionHeaderSx}>
          Модели РАГ
        </Typography>
        <Box sx={{ mt: 1, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <RagModelSelector
        kind="embedding"
        scope={projectsAgentsScope}
        entityId={entityId}
        onResolveEntityId={onResolveEntityId}
        isDarkMode={isDarkMode}
        disabled={isLoading || isSaving || !canEdit}
        triggerMaxWidth={null}
        deferApply
        fieldSx={categoryFieldSx}
        onModelSelect={setDraftEmbeddingPath}
        preferredPath={draftEmbeddingPath}
        label="Модель эмбеддингов"
        helpTooltip={
          projectsAgentsScope === 'project'
            ? 'Для документов проекта. Общая библиотека использует свою модель (задаётся администратором) и этим выбором не затрагивается.'
            : 'Для документов агента. Общая библиотека использует свою модель (задаётся администратором) и этим выбором не затрагивается.'
        }
      />
      <RagModelSelector
        kind="reranker"
        scope={projectsAgentsScope}
        entityId={entityId}
        onResolveEntityId={onResolveEntityId}
        isDarkMode={isDarkMode}
        disabled={isLoading || isSaving || !canEdit}
        triggerMaxWidth={null}
        deferApply
        fieldSx={categoryFieldSx}
        onModelSelect={setDraftRerankerPath}
        preferredPath={draftRerankerPath}
        label="Модель реранкера"
        helpTooltip={
          projectsAgentsScope === 'project'
            ? 'Переупорядочивает найденные чанки после первичного поиска для документов проекта.'
            : 'Переупорядочивает найденные чанки после первичного поиска для документов агента.'
        }
      />
        </Box>
      </Box>

      {/* ── Стратегии РАГ ── */}
      <Box>
        <Typography variant="caption" sx={sectionHeaderSx}>
          Стратегии РАГ
        </Typography>
        <Box sx={{ mt: 1, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.5,
          minWidth: triggerMinWidth,
          width: '100%',
        }}
      >
        <FormControl variant="outlined" fullWidth size="small" sx={strategyFieldSx}>
          <InputLabel htmlFor="rag-strategy-select">Стратегия поиска</InputLabel>
          <OutlinedInput
            ref={strategyOutlinedRef}
            id="rag-strategy-select"
            label="Стратегия поиска"
            value={getStrategyLabel(selectedStrategy)}
            readOnly
            inputProps={AGENT_CONSTRUCTOR_FIELD_INPUT_PROPS}
            sx={AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX}
            onClick={() => !isLoading && setStrategyPopoverAnchor(strategyOutlinedRef.current)}
            endAdornment={
              <InputAdornment position="end">
                <ExpandMoreIcon
                  sx={{
                    ...dropdownChevronSx,
                    transform: strategyPopoverAnchor ? 'rotate(180deg)' : 'none',
                  }}
                />
              </InputAdornment>
            }
          />
        </FormControl>
        <Tooltip
          title={
            <Box>
              <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 0.5 }}>
                {getStrategyLabel(selectedStrategy)}
              </Typography>
              <Typography variant="body2" sx={{ mb: 1, opacity: 0.9 }}>
                {getStrategyDescription(selectedStrategy)}
              </Typography>
              <Typography variant="body2" fontWeight={500}>
                {getStrategyUseCase(selectedStrategy)}
              </Typography>
            </Box>
          }
          arrow
        >
          <IconButton size="small" sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX} aria-label="Справка: стратегия поиска">
            <HelpOutlineIcon fontSize="small" color="action" />
          </IconButton>
        </Tooltip>
        <Popover
          open={Boolean(strategyPopoverAnchor)}
          anchorEl={strategyPopoverAnchor}
          onClose={() => setStrategyPopoverAnchor(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
          slotProps={{ paper: { sx: getDropdownPopoverPaperSx(strategyPopoverAnchor, isDarkMode) } }}
        >
          <Box sx={{ py: 0.5 }}>
            {(['auto', 'vector', 'lexical', 'hybrid', 'graph'] as const).map((strategy) => (
              <Box
                key={strategy}
                onClick={() => {
                  setSelectedStrategy(strategy);
                  setStrategyPopoverAnchor(null);
                }}
                sx={{
                  ...dropdownItemSx,
                  ...getDropdownItemStateSx(isDarkMode, selectedStrategy === strategy),
                }}
              >
                {getStrategyLabel(strategy)}
              </Box>
            ))}
          </Box>
        </Popover>
      </Box>

      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.5,
          minWidth: triggerMinWidth,
          width: '100%',
        }}
      >
        <FormControl variant="outlined" fullWidth size="small" sx={strategyFieldSx}>
          <InputLabel htmlFor="rag-chunking-select">Стратегия чанкования</InputLabel>
          <OutlinedInput
            ref={chunkingOutlinedRef}
            id="rag-chunking-select"
            label="Стратегия чанкования"
            value={getChunkingLabel(ragChunkingStrategy)}
            readOnly
            inputProps={AGENT_CONSTRUCTOR_FIELD_INPUT_PROPS}
            sx={AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX}
            onClick={() => !isLoading && setChunkingPopoverAnchor(chunkingOutlinedRef.current)}
            endAdornment={
              <InputAdornment position="end">
                <ExpandMoreIcon
                  sx={{
                    ...dropdownChevronSx,
                    transform: chunkingPopoverAnchor ? 'rotate(180deg)' : 'none',
                  }}
                />
              </InputAdornment>
            }
          />
        </FormControl>
        <Tooltip
          title={
            <Box>
              <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 0.5 }}>
                {getChunkingLabel(ragChunkingStrategy)}
              </Typography>
              <Typography variant="body2" sx={{ mb: 1, opacity: 0.9 }}>
                {getChunkingDescription(ragChunkingStrategy)}
              </Typography>
              <Typography variant="body2" fontWeight={500}>
                {getChunkingUseCase(ragChunkingStrategy)}
              </Typography>
            </Box>
          }
          arrow
        >
          <IconButton size="small" sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX} aria-label="Справка: стратегия чанкования">
            <HelpOutlineIcon fontSize="small" color="action" />
          </IconButton>
        </Tooltip>
        <Popover
          open={Boolean(chunkingPopoverAnchor)}
          anchorEl={chunkingPopoverAnchor}
          onClose={() => setChunkingPopoverAnchor(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
          slotProps={{ paper: { sx: getDropdownPopoverPaperSx(chunkingPopoverAnchor, isDarkMode) } }}
        >
          <Box sx={{ py: 0.5 }}>
            {(['hierarchical', 'fixed', 'markdown', 'separators', 'semantic'] as const).map((strategy) => (
              <Box
                key={strategy}
                onClick={() => {
                  setRagChunkingStrategy(strategy);
                  setChunkingPopoverAnchor(null);
                }}
                sx={{
                  ...dropdownItemSx,
                  ...getDropdownItemStateSx(isDarkMode, ragChunkingStrategy === strategy),
                }}
              >
                {getChunkingLabel(strategy)}
              </Box>
            ))}
          </Box>
        </Popover>
      </Box>
        </Box>
      </Box>

      {/* ── Настройки чанкования РАГ ── */}
      <Box>
        <Typography variant="caption" sx={sectionHeaderSx}>
          Настройки чанкования РАГ
        </Typography>
        <Box sx={{ mt: 1, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Box sx={RAG_NUM_FIELDS_ROW_SX}>
        <Box sx={{ width: '100%', flex: { sm: '1 1 140px' }, minWidth: { sm: 140 } }}>
          <ClampedNumberField
            fullWidth
            size="small"
            disabled={isLoading || isSaving || !canEdit}
            label={
              <Box sx={MODEL_SETTINGS_LABEL_WRAPPER_SX} component="span">
                Количество чанков (K)
                <Tooltip
                  title={
                    projectsAgentsScope === 'project'
                      ? 'Сколько фрагментов подмешивать в промпт для документов проекта (1–64, по умолчанию 5). Для общей библиотеки K задаётся администратором.'
                      : 'Сколько фрагментов подмешивать в промпт для документов агента (1–64, по умолчанию 5). Для общей библиотеки K задаётся администратором.'
                  }
                  arrow
                >
                  <IconButton
                    size="small"
                    sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX}
                    onClick={(e) => e.stopPropagation()}
                    aria-label="Справка: количество чанков"
                  >
                    <HelpOutlineIcon fontSize="small" color="action" />
                  </IconButton>
                </Tooltip>
              </Box>
            }
            value={ragChatTopK}
            onValueChange={setRagChatTopK}
            min={1}
            max={64}
            step={1}
            defaultValue={5}
            integer
            InputLabelProps={{ shrink: true }}
            sx={ragTextFieldSx}
          />
        </Box>
        <Box sx={{ width: '100%', flex: { sm: '1 1 140px' }, minWidth: { sm: 140 } }}>
          <ClampedNumberField
            fullWidth
            size="small"
            disabled={isLoading || isSaving || !canEdit}
            label={
              <Box sx={MODEL_SETTINGS_LABEL_WRAPPER_SX} component="span">
                Размер чанка
                <Tooltip
                  title="Целевой размер одного чанка в символах при нарезке документа. Диапазон 200–8000, по умолчанию 1000. Меньше — точнее, но больше чанков; больше — шире контекст в каждом фрагменте."
                  arrow
                >
                  <IconButton
                    size="small"
                    sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX}
                    onClick={(e) => e.stopPropagation()}
                    aria-label="Справка: размер чанка"
                  >
                    <HelpOutlineIcon fontSize="small" color="action" />
                  </IconButton>
                </Tooltip>
              </Box>
            }
            value={ragChunkSize}
            onValueChange={setRagChunkSize}
            min={200}
            max={8000}
            step={50}
            defaultValue={1000}
            integer
            InputLabelProps={{ shrink: true }}
            sx={ragTextFieldSx}
          />
        </Box>
        <Box sx={{ width: '100%', flex: { sm: '1 1 140px' }, minWidth: { sm: 140 } }}>
          <ClampedNumberField
            fullWidth
            size="small"
            disabled={isLoading || isSaving || !canEdit}
            label={
              <Box sx={MODEL_SETTINGS_LABEL_WRAPPER_SX} component="span">
                Размер перекрытия
                <Tooltip
                  title="Количество символов перекрытия между соседними чанками при нарезке документа. Диапазон 0–2000, по умолчанию 200."
                  arrow
                >
                  <IconButton
                    size="small"
                    sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX}
                    onClick={(e) => e.stopPropagation()}
                    aria-label="Справка: размер перекрытия"
                  >
                    <HelpOutlineIcon fontSize="small" color="action" />
                  </IconButton>
                </Tooltip>
              </Box>
            }
            value={ragChunkOverlap}
            onValueChange={setRagChunkOverlap}
            min={0}
            max={2000}
            step={10}
            defaultValue={200}
            integer
            InputLabelProps={{ shrink: true }}
            sx={ragTextFieldSx}
          />
        </Box>
        <Box sx={{ width: '100%', flex: { sm: '1 1 140px' }, minWidth: { sm: 140 } }}>
          <ClampedNumberField
            fullWidth
            size="small"
            disabled={isLoading || isSaving || !canEdit}
            label={
              <Box sx={MODEL_SETTINGS_LABEL_WRAPPER_SX} component="span">
                Порог схожести
                <Tooltip
                  title="Минимальный порог схожести (0..1) для project/agent RAG. 0 — без фильтрации. Для Библиотеки (memory) порог задаётся в ConfigMap: RAG_MEMORY_SIMILARITY_THRESHOLD."
                  arrow
                >
                  <IconButton
                    size="small"
                    sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX}
                    onClick={(e) => e.stopPropagation()}
                    aria-label="Справка: порог схожести"
                  >
                    <HelpOutlineIcon fontSize="small" color="action" />
                  </IconButton>
                </Tooltip>
              </Box>
            }
            value={ragSimilarityThreshold}
            onValueChange={setRagSimilarityThreshold}
            min={0}
            max={1}
            step={0.01}
            defaultValue={0}
            integer={false}
            decimals={4}
            InputLabelProps={{ shrink: true }}
            sx={ragTextFieldSx}
          />
        </Box>
      </Box>

        </Box>
      </Box>

      {/* ── Методы улучшения запросов ── */}
      <Box>
        <Typography variant="caption" sx={sectionHeaderSx}>
          Методы улучшения запросов
        </Typography>
        <Box sx={{ mt: 1, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
        {renderSwitchRow(
          'Agentic RAG',
          'В режиме чата «Агент»: модель сама запрашивает документы инструментом retrieve_rag_context. Выключите, чтобы фрагменты из проекта, документов агента и общей библиотеки заранее подмешивались в запрос. Нужен режим «Агент» в чате. Стратегия поиска выше применяется к вызовам из инструментов.',
          agenticRagEnabled,
          setAgenticRagEnabled,
        )}
        {renderSwitchRow(
          'Переранжирование',
          'Cross-encoder переупорядочивает найденные чанки после первичного retrieval.',
          ragRerankingEnabled,
          setRagRerankingEnabled,
        )}
      </Box>

      <ClampedNumberField
        fullWidth
        size="small"
        disabled={isLoading || isSaving || !canEdit || !ragRerankingEnabled}
        label={
          <Box sx={MODEL_SETTINGS_LABEL_WRAPPER_SX} component="span">
            Количество чанков после реранкинга (Top-N)
            <Tooltip
              title="Сколько лучших чанков оставить после переранжирования cross-encoder. Диапазон 1–64, по умолчанию 5. Работает только при включённом переранжировании."
              arrow
            >
              <IconButton
                size="small"
                sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX}
                onClick={(e) => e.stopPropagation()}
                aria-label="Справка: Top-N после реранкинга"
              >
                <HelpOutlineIcon fontSize="small" color="action" />
              </IconButton>
            </Tooltip>
          </Box>
        }
        value={ragRerankTopN}
        onValueChange={setRagRerankTopN}
        min={1}
        max={64}
        step={1}
        defaultValue={5}
        integer
        InputLabelProps={{ shrink: true }}
        sx={ragTextFieldSx}
      />

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
        {renderSwitchRow(
          'Исправление опечаток в запросе',
          'Один короткий запрос к LLM: исправить опечатки в вашей фразе, не меняя смысл. Удобно для ключевых слов и имён; снижает риск промаха лексического поиска (BM25). До поиска по базе — отдельный вызов LLM. Выключено: фраза уходит в RAG как есть (после нормализации пробелов).',
          ragQueryFixTypos,
          setRagQueryFixTypos,
        )}
        {renderSwitchRow(
          'Несколько формулировок (multi-query)',
          'LLM генерирует 3-5 коротких альтернативных формулировок того же смысла. По каждой выполняется поиск в RAG, затем результаты объединяются. Помогает, когда в документе другие слова (например «soft skills» и «софт скилы», «автомобиль» и «машина»), если модель попала в удачные синонимы. Вызывает LLM и несколько запросов к RAG за один вопрос — ответ в чате медленнее, но выше шанс найти нужные формулировки в документах.',
          ragMultiQueryEnabled,
          setRagMultiQueryEnabled,
        )}
        {renderSwitchRow(
          'HyDE (гипотетический ответ для поиска)',
          'HyDE: LLM пишет короткий гипотетический ответ на ваш вопрос. Текст добавляется при построении вектора запроса, чтобы ближе по смыслу совпасть с абзацами в документах. Не подставляет реальные факты из файлов — только улучшает retrieval. Один вызов LLM для гипотетического текста, затем обогащенный запрос уходит в эмбеддинг. Можно включать вместе с multi-query.',
          ragHydeEnabled,
          setRagHydeEnabled,
        )}
      </Box>
        </Box>
      </Box>

      {canEdit ? (
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            gap: 1,
            pt: 0.5,
          }}
        >
          <Button
            variant="outlined"
            fullWidth
            startIcon={<RestoreIcon sx={AGENT_CONSTRUCTOR_SAVE_ICON_SX} />}
            onClick={resetRAGSettings}
            disabled={isLoading || isSaving}
            sx={restoreButtonSx}
          >
            Восстановить настройки
          </Button>
          <Button
            variant="contained"
            fullWidth
            startIcon={<SaveIcon sx={AGENT_CONSTRUCTOR_SAVE_ICON_SX} />}
            onClick={() => void saveRAGSettings()}
            disabled={isLoading || isSaving}
            sx={saveButtonSx}
          >
            {draft ? 'Применить настройки' : isSaving ? 'Сохранение…' : 'Сохранить настройки'}
          </Button>
          {draft ? (
            <Typography
              variant="caption"
              sx={{ color: mutedCaptionColor, textAlign: 'center', px: 1 }}
            >
              «Применить» записывает настройки. Если проект/агент ещё не создан
              окончательно — значения также остаются в форме до кнопки «Создать» /
              «Сохранить».
            </Typography>
          ) : null}
        </Box>
      ) : (
        <Typography
          variant="caption"
          sx={{ color: mutedCaptionColor, textAlign: 'center', px: 1, pt: 0.5 }}
        >
          Настройки задаёт владелец. Вам они видны, чтобы понимать, как
          {projectsAgentsScope === 'project'
            ? ' этот проект'
            : projectsAgentsScope === 'kb'
              ? ' эта база знаний'
              : ' этот агент'}{' '}
          ищет по документам.
        </Typography>
      )}
    </Box>
  );

  if (isPanel) {
    const resolvedTitle =
      panelTitle ??
      (lockedScope === 'project'
        ? 'Настройки РАГ для проектов'
        : lockedScope === 'kb'
          ? 'Настройки РАГ для базы знаний'
          : 'Настройки РАГ для агента');
    const panelBg = isDarkMode ? getSidebarPanelBackground() : undefined;
    const headerBorder = isDarkMode ? '1px solid rgba(255,255,255,0.08)' : '1px solid rgba(0,0,0,0.08)';
    const headerColor = isDarkMode ? 'white' : 'rgba(0,0,0,0.87)';
    const backBtnColor = isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.65)';
    const backBtnHover = isDarkMode ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)';

    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          overflow: 'hidden',
          background: panelBg,
          color: isDarkMode ? 'white' : 'rgba(0,0,0,0.87)',
        }}
      >
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            px: 2,
            py: 1.5,
            borderBottom: headerBorder,
            flexShrink: 0,
          }}
        >
          <IconButton
            size="small"
            onClick={onClose}
            sx={{ color: backBtnColor, '&:hover': { bgcolor: backBtnHover } }}
          >
            <ArrowBackIcon />
          </IconButton>
          <Typography variant="h6" sx={{ color: headerColor, fontSize: '1rem', fontWeight: 600 }}>
            {resolvedTitle}
          </Typography>
        </Box>
        <Box
          sx={{
            px: 2,
            py: 1.5,
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            ...SIDEBAR_HIDE_SCROLLBAR_SX,
          }}
        >
          {settingsBody}
        </Box>
      </Box>
    );
  }

  return null;
}

