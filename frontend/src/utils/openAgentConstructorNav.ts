import type { NavigateFunction } from 'react-router-dom';
import {
  ASTRA_OPEN_AGENT_CONSTRUCTOR,
  ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY,
} from '../constants/hotkeys';

export const OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY = 'openAgentConstructorId';
export const OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY = 'openAgentConstructorRelatedIds';

export type OpenAgentConstructorDetail = {
  agentId: number;
  /** Доп. id (клоны цепочки/субагентов) — подтянуть в список конструктора. */
  relatedAgentIds?: number[];
};

function stashRelatedAgentIds(ids: number[] | undefined): void {
  try {
    if (!ids || ids.length === 0) {
      sessionStorage.removeItem(OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY);
      return;
    }
    const unique = Array.from(
      new Set(ids.map(Number).filter((x) => Number.isFinite(x) && x > 0)),
    );
    sessionStorage.setItem(OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY, JSON.stringify(unique));
  } catch {
    /* */
  }
}

/** Прочитать и очистить related ids, сохранённые при открытии из галереи. */
export function consumePendingRelatedAgentIds(): number[] {
  try {
    const raw = sessionStorage.getItem(OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY);
    sessionStorage.removeItem(OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(Number).filter((x) => Number.isFinite(x) && x > 0);
  } catch {
    return [];
  }
}

/** Сохранить id агента для открытия в конструкторе после navigate. */
export function stashAgentConstructorOpen(agentId: number): void {
  try {
    sessionStorage.setItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY, String(agentId));
  } catch {
    /* */
  }
}

/** Прочитать отложенный id без удаления (удаляет AgentConstructorPanel после выбора). */
export function peekPendingAgentConstructorId(): number | null {
  try {
    const raw = sessionStorage.getItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY);
    if (!raw) return null;
    const id = Number(raw);
    return Number.isFinite(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}

export function resolvePendingAgentConstructorId(
  locationState: unknown,
): number | null {
  const state = locationState as { [OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY]?: number } | null;
  const fromState = state?.[OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY];
  if (typeof fromState === 'number' && Number.isFinite(fromState) && fromState > 0) {
    return fromState;
  }
  return peekPendingAgentConstructorId();
}

function dispatchOpen(detail: OpenAgentConstructorDetail): void {
  window.dispatchEvent(
    new CustomEvent(ASTRA_OPEN_AGENT_CONSTRUCTOR, { detail }),
  );
}

/** Открыть агента в конструкторе на главной (чат): navigate + надёжная доставка события. */
export function openAgentInConstructor(
  agentId: number,
  navigate: NavigateFunction,
  options?: { relatedAgentIds?: number[] },
): void {
  stashAgentConstructorOpen(agentId);
  stashRelatedAgentIds(options?.relatedAgentIds);
  navigate('/', {
    state: { [OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY]: agentId },
  });
  const detail: OpenAgentConstructorDetail = {
    agentId,
    relatedAgentIds: options?.relatedAgentIds,
  };
  // Сразу — если чат уже смонтирован; rAF — после mount при переходе с другой страницы.
  dispatchOpen(detail);
  window.requestAnimationFrame(() => {
    dispatchOpen(detail);
  });
}
