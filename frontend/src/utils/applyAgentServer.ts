/**
 * Загрузка весов модели агента на сервер (без перезаписи глобальных настроек «Модели»).
 * Промпт и тонкая настройка берутся из карточки агента в БД при каждом сообщении в чате.
 *
 * После пересборки контейнеров llm-svc может ещё не быть готов: тогда модель
 * всё равно фиксируется в localStorage как выбранная, а веса подтянутся
 * через ensureSelectedModelWeights (селектор при открытии / фокус вкладки /
 * перед первым сообщением) и backend ensure_model_loaded.
 */
import { authFetch, getApiUrl } from '../config/api';
import { MODEL_PATH_CHANGED_EVENT } from './contextTokens';
import {
  isValidSelectedModelPath,
  LAST_SELECTED_MODEL_PATH_STORAGE_KEY,
} from './modelThinking';

function apiErrorMessage(data: { message?: string; detail?: unknown }, fallback: string): string {
  if (typeof data.message === 'string' && data.message.trim()) return data.message;
  const detail = data.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object' && 'msg' in detail) {
    const msg = (detail as { msg?: unknown }).msg;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

/** Событие: UI (селектор) показывает спиннер, пока ensure тянет веса. */
export const MODEL_WEIGHTS_ENSURE_EVENT = 'astrachatModelWeightsEnsure';

export type ModelWeightsEnsureDetail = {
  phase: 'start' | 'done' | 'error';
  path: string;
  message?: string;
};

function emitWeightsEnsure(detail: ModelWeightsEnsureDetail): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(MODEL_WEIGHTS_ENSURE_EVENT, { detail }));
}

function sanitizeModelPath(p: string): string {
  let s = p.trim().replace(/\s+/g, '');
  if (/^1lm-svc:\/\//i.test(s)) {
    s = 'llm-svc://' + s.slice(10);
  }
  return s;
}

/** Сравниваем пути вида llm-svc://id, provider/id, id. */
export function modelPathsLikelyEqual(a: string | null | undefined, b: string | null | undefined): boolean {
  const na = normalizeModelPathKey(a);
  const nb = normalizeModelPathKey(b);
  if (!na || !nb) return false;
  if (na === nb) return true;
  if (na.startsWith(nb + '-') || nb.startsWith(na + '-')) return true;
  const aTail = na.includes('/') ? na.split('/').slice(1).join('/') : na;
  const bTail = nb.includes('/') ? nb.split('/').slice(1).join('/') : nb;
  if (aTail && bTail && (aTail === bTail || aTail.startsWith(bTail + '-') || bTail.startsWith(aTail + '-'))) {
    return true;
  }
  return false;
}

function normalizeModelPathKey(path: string | null | undefined): string {
  let s = sanitizeModelPath(String(path || ''));
  if (!s) return '';
  const lower = s.toLowerCase();
  if (lower.startsWith('llm-svc://')) {
    s = s.slice('llm-svc://'.length).replace(/^\/+/, '');
  }
  return s.trim().toLowerCase();
}

export function rememberAgentModelPath(model_path?: string | null): void {
  const mp = typeof model_path === 'string' ? normalizeForLoad(model_path) : '';
  if (mp) rememberSelectedModelPath(mp);
}

function rememberSelectedModelPath(mp: string): void {
  if (!isValidSelectedModelPath(mp)) return;
  try {
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, mp);
  } catch {
    /* ignore quota / private mode */
  }
  window.dispatchEvent(new CustomEvent(MODEL_PATH_CHANGED_EVENT, { detail: { path: mp } }));
}

function normalizeForLoad(mp: string): string {
  let out = sanitizeModelPath(mp);
  if (!out) return '';
  if (!out.startsWith('llm-svc://') && !out.includes('/') && !out.toLowerCase().endsWith('.gguf')) {
    out = `llm-svc://${out}`;
  }
  return out;
}

async function resolveDefaultModelPath(token: string): Promise<string> {
  const stored = localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY);
  if (isValidSelectedModelPath(stored)) {
    return normalizeForLoad(stored!);
  }
  try {
    const curRes = await authFetch(getApiUrl('/api/models/current'), {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (curRes.ok) {
      const cur = (await curRes.json().catch(() => ({}))) as { path?: string };
      if (isValidSelectedModelPath(cur.path)) {
        return normalizeForLoad(String(cur.path));
      }
    }
  } catch {
    /* */
  }
  return '';
}

export type LoadAgentModelResult =
  | { ok: true; skipped?: boolean }
  | { ok: true; pending: true; message: string }
  | { ok: false; message: string };

/** Path, для которого последний ensure завершился success (в этой вкладке). */
let weightsReadyPath: string | null = null;
/** In-flight ensure: два AgentSelector / send не дублируют POST /load. */
let weightsEnsureInflight: Promise<LoadAgentModelResult> | null = null;
let weightsEnsureInflightPath: string | null = null;

export function isSelectedModelWeightsReady(model_path?: string | null): boolean {
  const mp =
    typeof model_path === 'string' && model_path.trim()
      ? normalizeForLoad(model_path)
      : normalizeForLoad(localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY) || '');
  return Boolean(mp && weightsReadyPath === mp);
}

export function markSelectedModelWeightsStale(): void {
  weightsReadyPath = null;
}

/**
 * Гарантирует веса выбранной модели в LLM-бэкенде.
 * POST /api/models/load → ensure_model_loaded (no-op, если уже в пуле).
 * Dedup in-flight; skip, если в этой вкладке уже успешно ensure для того же path
 * (force=true — всегда снова, напр. клик по той же модели / возврат на вкладку).
 */
export async function ensureSelectedModelWeights(
  token: string,
  model_path?: string | null,
  opts?: { force?: boolean; silent?: boolean },
): Promise<LoadAgentModelResult> {
  if (!token) {
    return { ok: false, message: 'Нет токена авторизации' };
  }

  let mp = typeof model_path === 'string' ? normalizeForLoad(model_path) : '';
  if (!mp) {
    mp = await resolveDefaultModelPath(token);
  }
  if (!mp) {
    return { ok: true, skipped: true };
  }

  const force = Boolean(opts?.force);
  const silent = Boolean(opts?.silent);
  if (!force && weightsReadyPath === mp && !weightsEnsureInflight) {
    return { ok: true, skipped: true };
  }

  if (weightsEnsureInflight && weightsEnsureInflightPath === mp) {
    return weightsEnsureInflight;
  }

  if (!silent) {
    emitWeightsEnsure({ phase: 'start', path: mp });
  }
  weightsEnsureInflightPath = mp;
  const run = (async (): Promise<LoadAgentModelResult> => {
    const result = await loadAgentModelOnly(token, mp);
    if (result.ok && !('pending' in result && result.pending)) {
      weightsReadyPath = mp;
      if (!silent) {
        emitWeightsEnsure({ phase: 'done', path: mp });
      }
    } else {
      weightsReadyPath = null;
      const message =
        ('message' in result && result.message) || 'Не удалось загрузить веса модели';
      // Ошибку всегда показываем (даже silent background), иначе UI врёт «модель готова».
      emitWeightsEnsure({ phase: 'error', path: mp, message });
    }
    return result;
  })().finally(() => {
    if (weightsEnsureInflightPath === mp) {
      weightsEnsureInflight = null;
      weightsEnsureInflightPath = null;
    }
  });

  weightsEnsureInflight = run;
  return run;
}

export async function loadAgentModelOnly(
  token: string,
  model_path?: string | null,
): Promise<LoadAgentModelResult> {
  let mp = typeof model_path === 'string' ? normalizeForLoad(model_path) : '';

  // Агент без своей модели — всё равно ensure текущей/дефолтной в пул (после rebuild пусто).
  if (!mp) {
    mp = await resolveDefaultModelPath(token);
    if (!mp) {
      return { ok: true };
    }
  }

  const jsonHeaders: HeadersInit = {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  };

  // Всегда зовём /load: backend ensure_model_loaded — no-op, если модель уже в RAM.
  // Нельзя skip по /api/models/current: после rebuild settings могут врать «loaded».
  rememberSelectedModelPath(mp);

  try {
    const loadRes = await authFetch(getApiUrl('/api/models/load'), {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify({ model_path: mp }),
    });
    const loadData = (await loadRes.json().catch(() => ({}))) as {
      success?: boolean;
      message?: string;
      detail?: unknown;
    };
    if (!loadRes.ok || !loadData.success) {
      return {
        ok: true,
        pending: true,
        message: apiErrorMessage(loadData, 'Не удалось загрузить модель агента'),
      };
    }
    rememberSelectedModelPath(mp);
    return { ok: true };
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : String(e);
    return { ok: true, pending: true, message };
  }
}
/**
 * @deprecated Не перезаписывает глобальные настройки. Используйте loadAgentModelOnly.
 */
export async function applyAgentModelAndSettings(
  token: string,
  opts: {
    system_prompt: string;
    model_path?: string | null;
    model_settings?: Record<string, unknown> | null;
  },
): Promise<LoadAgentModelResult> {
  return loadAgentModelOnly(token, opts.model_path);
}
