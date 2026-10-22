import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useLocation } from 'react-router-dom';
import { authFetch, getApiUrl } from '../config/api';
import { useAppContext } from './AppContext';
import { getSettings, initSettings } from '../settings';
import {
  isRagReindexBannerVisible,
  ragReindexBlockMessage,
  shouldBlockRagSend,
  type RagReindexStatusPayload,
  type RagSendBlockContext,
} from '../utils/ragReindexBlock';

interface RagReindexStatusContextValue {
  status: RagReindexStatusPayload | null;
  anyReindexing: boolean;
  agentHasKb: boolean;
  projectHasDocuments: boolean;
  blockMessage: string;
  shouldBlockRagSend: (ctx: Pick<RagSendBlockContext, 'libraryEnabled'>) => boolean;
  memoryRagEnabled: boolean;
  notifyReindexStarted: () => void;
}

const defaultStatus: RagReindexStatusPayload = {
  memory: { reindexing: false },
  project: { reindexing: false },
  kb: { reindexing: false },
  any_reindexing: false,
  agent_has_kb: false,
  project_has_documents: false,
  memory_rag_enabled: true,
  message: '',
  active: [],
};

const RagReindexStatusContext = createContext<RagReindexStatusContextValue>({
  status: null,
  anyReindexing: false,
  agentHasKb: false,
  projectHasDocuments: false,
  blockMessage: '',
  shouldBlockRagSend: () => false,
  memoryRagEnabled: true,
  notifyReindexStarted: () => {},
});

/** Пауза между опросами во время разгона, секунд. */
const REINDEX_KICK_INTERVAL_SECONDS = 1;

/**

* Сколько секунд держать разгон. Перечанковка запускается фоновой задачей,
* и до подъёма флага она успевает сходить в БД - под нагрузкой это заметно.
* Разгон снимается досрочно, как только флаг увидели.
 */
const REINDEX_KICK_WINDOW_SECONDS = 25;

/**

* Пауза между опросами, пока плашка висит.
* 
* Момента завершения не знает никто, кроме SVC-RAG: бэкенд снимает флаг прямо
* в обработчике статуса, увидев, что локи освободились. То есть пересборка
* заканчивается ровно тогда, когда мы спросили, - и плашка гаснет через эту
* паузу, а не через полный интервал опроса.
* 
* Дороже покоя, но пересборка - событие редкое и конечное.
 */
const REINDEX_ACTIVE_POLL_SECONDS = 3;

function readActiveAgentId(): number | null {
  if (typeof localStorage === 'undefined') return null;
  const raw = localStorage.getItem('active_agent_id');
  const parsed = raw ? parseInt(raw, 10) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function projectIdFromPathname(pathname: string): string | null {
  const match = pathname.match(/^\/project\/([^/]+)/);
  return match?.[1] ?? null;
}

export function RagReindexStatusProvider({ children }: { children: ReactNode }) {
  const { state } = useAppContext();
  const location = useLocation();
  const [status, setStatus] = useState<RagReindexStatusPayload | null>(null);

  const pollProjectId = useMemo(() => {
    const routeProjectId = projectIdFromPathname(location.pathname);
    if (routeProjectId) return routeProjectId;
    const chat = state.chats.find((c) => c.id === state.currentChatId);
    return chat?.projectId ?? null;
  }, [location.pathname, state.chats, state.currentChatId]);

  const pollAgentId = useMemo(() => readActiveAgentId(), [location.pathname, state.currentChatId]);

  // Разгон опроса после запуска перечанковки.
  const fastPollUntilRef = useRef(0);
  const anyReindexingRef = useRef(false);
  // Пересборку запустили отсюда: только тогда учащаем опрос, пока висит плашка.
  const ourReindexRef = useRef(false);
  const kickRef = useRef<(() => void) | null>(null);

  const notifyReindexStarted = useCallback(() => {
    ourReindexRef.current = true;
    fastPollUntilRef.current = Date.now() + REINDEX_KICK_WINDOW_SECONDS * 1000;
    kickRef.current?.();
  }, []);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;

    const startPolling = async () => {
      let settings;
      try {
        settings = getSettings();
      } catch {
        try {
          settings = await initSettings();
        } catch {
          return;
        }
      }
      if (cancelled) return;

      const pollIntervalSeconds = settings.app.ragReindexStatusPollSeconds;

      const poll = async () => {
        const activeAgentId = readActiveAgentId();
        // Берём готовое значение из useMemo выше, а не пересчитываем по
        // state.chats
        const activeProjectId = pollProjectId;
        const params = new URLSearchParams();
        if (activeAgentId != null) {
          params.set('agent_id', String(activeAgentId));
        }
        if (activeProjectId) {
          params.set('project_id', activeProjectId);
        }
        const qs = params.toString() ? `?${params.toString()}` : '';
        try {
          const res = await authFetch(getApiUrl(`/api/rag/reindex-status${qs}`));
          if (!res.ok) return;
          const data = (await res.json()) as RagReindexStatusPayload;
          // Темп опроса выбирается по этому флагу, поэтому держим его в ref
          anyReindexingRef.current = Boolean(data.any_reindexing);
          if (anyReindexingRef.current) {
            // Флаг увидели - разгон своё отработал, дальше хватает паузы
            // активной пересборки.
            fastPollUntilRef.current = 0;
          } else if (Date.now() >= fastPollUntilRef.current) {
            ourReindexRef.current = false;
          }
          setStatus({
            memory: { reindexing: Boolean(data.memory?.reindexing) },
            project: { reindexing: Boolean(data.project?.reindexing) },
            kb: { reindexing: Boolean(data.kb?.reindexing) },
            any_reindexing: Boolean(data.any_reindexing),
            agent_has_kb: Boolean(data.agent_has_kb),
            project_has_documents: Boolean(data.project_has_documents),
            memory_rag_enabled: data.memory_rag_enabled !== false,
            message: typeof data.message === 'string' ? data.message : '',
            active: Array.isArray(data.active) ? data.active : [],
          });
        } catch {
          /* ignore transient network errors */
        }
      };

      // Три темпа: ждём подъёма флага - разгон; пересборка идёт - часто,
      // чтобы плашка погасла почти сразу; в покое - как настроено
      let inFlight = false;
      
      const scheduleNext = () => {
        if (cancelled) return;
        let seconds = pollIntervalSeconds;
        if (Date.now() < fastPollUntilRef.current) {
          seconds = REINDEX_KICK_INTERVAL_SECONDS;
        } else if (anyReindexingRef.current) {
          seconds = REINDEX_ACTIVE_POLL_SECONDS;
        }
        timer = setTimeout(tick, seconds * 1000);
      };

      // inFlight держит цепочку в единственном экземпляре
      const tick = async () => {
        if (cancelled || inFlight) return;
        inFlight = true;
        try {
          await poll();
        } finally {
          inFlight = false;
        }
        scheduleNext();
      };

      // Немедленный опрос по требованию: сбрасываем текущее ожидание и
      // спрашиваем сейчас. Дальше цепочка сама пойдёт в разгонном темпе.
      kickRef.current = () => {
        if (cancelled) return;
        if (timer) clearTimeout(timer);
        void tick();
      };

      await tick();
    };

    void startPolling();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // state.chats здесь БЫЛ и всё ломал. Это массив, React сравнивает
    // зависимости по ссылке, а редьюсер пересоздаёт его в 17 местах
    // (chats: state.chats.map(...)) Любое изменение чата - в том числе
    // каждый кусочек ответа модели при стриминге - роняло эффект и
    // запускало заново: срабатывал немедленный poll(), а прежний таймер
    // сбрасывался, так и не досчитав до конца.
    //
    // Из-за этого настроенные 10 секунд превращались примерно в один запрос
    // в секунду, пока человек работает в чате. На SVC-RAG это умножалось на
    // пять: бэкенд на каждый такой запрос дёргает три /reindex/status и два
    // списка документов.
    //
    // pollProjectId - useMemo, возвращает строку или null. Он сам зависит от
    // state.chats, но при пересчёте отдаёт то же значение, и эффект на это
    // не реагирует.
  }, [location.pathname, state.currentChatId, pollProjectId, pollAgentId]);

  const effectiveStatus = status ?? defaultStatus;
  const bannerCtx = useMemo(
    () => ({ agentId: pollAgentId, projectId: pollProjectId }),
    [pollAgentId, pollProjectId],
  );
  // Плашка не по глобальному any_reindexing: чужой проект не должен светить
  // оранжевым у всех. Агенты — как раньше (любой agent в active).
  const anyReindexing = isRagReindexBannerVisible(effectiveStatus, bannerCtx);
  const blockMessage = ragReindexBlockMessage(effectiveStatus, bannerCtx);

  const shouldBlock = useCallback(
    (ctx: Pick<RagSendBlockContext, 'libraryEnabled'>) =>
      shouldBlockRagSend(effectiveStatus, {
        libraryEnabled: ctx.libraryEnabled,
        projectHasDocuments: effectiveStatus.project_has_documents,
        agentHasKb: effectiveStatus.agent_has_kb,
        // Чей это чат: пересборка соседнего агента отправку блокировать не должна.
        agentId: pollAgentId,
        projectId: pollProjectId,
      }),
    [effectiveStatus, pollAgentId, pollProjectId],
  );

  const value = useMemo(
    () => ({
      status: effectiveStatus,
      anyReindexing,
      agentHasKb: effectiveStatus.agent_has_kb,
      projectHasDocuments: effectiveStatus.project_has_documents,
      memoryRagEnabled: effectiveStatus.memory_rag_enabled !== false,
      blockMessage,
      shouldBlockRagSend: shouldBlock,
      notifyReindexStarted,
    }),
    [effectiveStatus, anyReindexing, blockMessage, shouldBlock, notifyReindexStarted],
  );

  return (
    <RagReindexStatusContext.Provider value={value}>{children}</RagReindexStatusContext.Provider>
  );
}

export function useRagReindexStatus(): RagReindexStatusContextValue {
  return useContext(RagReindexStatusContext);
}
