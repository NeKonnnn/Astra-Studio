import type { NavigateFunction } from 'react-router-dom';
import {
  ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR,
  ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR_ID_KEY,
} from '../constants/hotkeys';

export const OPEN_KB_SIDEBAR_NAV_STATE_KEY = 'openKnowledgeBaseSidebarId';

export function stashKnowledgeBaseSidebarOpen(kbId: number): void {
  try {
    sessionStorage.setItem(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR_ID_KEY, String(kbId));
  } catch {
    /* */
  }
}

export function peekPendingKnowledgeBaseSidebarId(): number | null {
  try {
    const raw = sessionStorage.getItem(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR_ID_KEY);
    if (!raw) return null;
    const id = Number(raw);
    return Number.isFinite(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}

export function resolvePendingKnowledgeBaseSidebarId(locationState: unknown): number | null {
  const state = locationState as { [OPEN_KB_SIDEBAR_NAV_STATE_KEY]?: number } | null;
  const fromState = state?.[OPEN_KB_SIDEBAR_NAV_STATE_KEY];
  if (typeof fromState === 'number' && Number.isFinite(fromState) && fromState > 0) {
    return fromState;
  }
  return peekPendingKnowledgeBaseSidebarId();
}

/** Открыть базу знаний в правой панели на главной (чат). */
export function openKnowledgeBaseInSidebar(kbId: number, navigate: NavigateFunction): void {
  stashKnowledgeBaseSidebarOpen(kbId);
  navigate('/', {
    state: { [OPEN_KB_SIDEBAR_NAV_STATE_KEY]: kbId },
  });
  window.dispatchEvent(
    new CustomEvent(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR, { detail: { kbId } }),
  );
  window.requestAnimationFrame(() => {
    window.dispatchEvent(
      new CustomEvent(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR, { detail: { kbId } }),
    );
  });
}
