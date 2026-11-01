import { useEffect, useMemo, useState } from 'react';
import {
  AGENT_ARTIFACTS_CHANGED_EVENT,
  artifactsConfigFromAgentDetail,
  persistAgentArtifactsEnabled,
  readAgentPresentationSkills,
  readAgentViewerGate,
} from '../utils/agentArtifactsEnabled';
import {
  MESSAGE_ARTIFACTS_VIEWER_CHANGED_EVENT,
  isMessageArtifactsViewerPinned,
  skillImpliesArtifactsViewer,
  skillImpliesPresentation,
} from '../utils/messageArtifactsViewerStorage';
import {
  getActiveSkillIds,
  SKILL_SELECTION_CHANGED_EVENT,
} from '../utils/skillSelectionStorage';
import {
  ARTIFACTS_SELECTION_CHANGED_EVENT,
  getChatArtifactsToolsState,
  isChatArtifactsToolsActive,
} from '../utils/artifactsSelectionStorage';
import { hasDeckSlideClass } from '../utils/presentationViewer';

function chatSkillsAllowViewer(chatId?: string | null): boolean {
  return getActiveSkillIds(chatId).some(skillImpliesArtifactsViewer);
}

function chatArtifactsAllowViewer(chatId?: string | null): boolean {
  if (!chatId) return false;
  return isChatArtifactsToolsActive(getChatArtifactsToolsState(chatId));
}

function chatSkillsAllowPresentation(chatId?: string | null): boolean {
  try {
    const agentOn = Boolean(localStorage.getItem('active_agent_id'));
    if (agentOn && !readAgentPresentationSkills()) return false;
  } catch {
    /* */
  }
  return getActiveSkillIds(chatId).some(skillImpliesPresentation);
}

/** Контент, который раньше уже был «артефактом» / презентацией — держим viewer. */
export function contentImpliesLegacyArtifactsViewer(content?: string | null): boolean {
  const text = content || '';
  if (!text) return false;
  if (text.includes(':::artifact{')) return true;
  if (hasDeckSlideClass(text)) return true;
  return false;
}

/**
 * Можно ли показывать ArtifactCard / presentation viewer для сообщения.
 * live: агент.artifacts_enabled / presentation skill агента / presentation-like skill чата
 * persist: message уже помечался / явный :::artifact / GPB slides
 */
export function useArtifactsViewerAllowed(opts?: {
  chatId?: string | null;
  messageId?: string | null;
  content?: string | null;
  force?: boolean;
}): boolean {
  const chatId = opts?.chatId;
  const messageId = opts?.messageId;
  const content = opts?.content;
  const force = Boolean(opts?.force);

  const [agentEnabled, setAgentEnabled] = useState(() => readAgentViewerGate());
  const [skillsEnabled, setSkillsEnabled] = useState(() => chatSkillsAllowViewer(chatId));
  const [chatArtifactsEnabled, setChatArtifactsEnabled] = useState(() =>
    chatArtifactsAllowViewer(chatId),
  );
  const [pinned, setPinned] = useState(() => isMessageArtifactsViewerPinned(messageId));

  useEffect(() => {
    const syncAgent = () => setAgentEnabled(readAgentViewerGate());
    const onAgentSelected = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail === null || detail === undefined) {
        persistAgentArtifactsEnabled(null);
        setAgentEnabled(false);
        return;
      }
      const cfg = artifactsConfigFromAgentDetail(detail);
      if (cfg) {
        persistAgentArtifactsEnabled(cfg);
        setAgentEnabled(readAgentViewerGate());
      } else {
        syncAgent();
      }
    };
    window.addEventListener('agentSelected', onAgentSelected as EventListener);
    window.addEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, syncAgent);
    window.addEventListener('storage', syncAgent);
    return () => {
      window.removeEventListener('agentSelected', onAgentSelected as EventListener);
      window.removeEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, syncAgent);
      window.removeEventListener('storage', syncAgent);
    };
  }, []);

  useEffect(() => {
    const syncSkills = (e?: Event) => {
      const detailChatId = (e as CustomEvent<{ chatId?: string }> | undefined)?.detail?.chatId;
      if (detailChatId && chatId && detailChatId !== chatId) return;
      setSkillsEnabled(chatSkillsAllowViewer(chatId));
    };
    syncSkills();
    window.addEventListener(SKILL_SELECTION_CHANGED_EVENT, syncSkills as EventListener);
    window.addEventListener('storage', syncSkills);
    return () => {
      window.removeEventListener(SKILL_SELECTION_CHANGED_EVENT, syncSkills as EventListener);
      window.removeEventListener('storage', syncSkills);
    };
  }, [chatId]);

  useEffect(() => {
    const syncArtifacts = (e?: Event) => {
      const detailChatId = (e as CustomEvent<{ chatId?: string }> | undefined)?.detail?.chatId;
      if (detailChatId && chatId && detailChatId !== chatId) return;
      setChatArtifactsEnabled(chatArtifactsAllowViewer(chatId));
    };
    syncArtifacts();
    window.addEventListener(ARTIFACTS_SELECTION_CHANGED_EVENT, syncArtifacts as EventListener);
    window.addEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, syncArtifacts);
    window.addEventListener('agentSelected', syncArtifacts as EventListener);
    window.addEventListener('storage', syncArtifacts);
    return () => {
      window.removeEventListener(ARTIFACTS_SELECTION_CHANGED_EVENT, syncArtifacts as EventListener);
      window.removeEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, syncArtifacts);
      window.removeEventListener('agentSelected', syncArtifacts as EventListener);
      window.removeEventListener('storage', syncArtifacts);
    };
  }, [chatId]);

  useEffect(() => {
    const syncPinned = () => {
      const next = isMessageArtifactsViewerPinned(messageId);
      setPinned((prev) => (prev === next ? prev : next));
    };
    syncPinned();
    window.addEventListener(MESSAGE_ARTIFACTS_VIEWER_CHANGED_EVENT, syncPinned);
    window.addEventListener('storage', syncPinned);
    return () => {
      window.removeEventListener(MESSAGE_ARTIFACTS_VIEWER_CHANGED_EVENT, syncPinned);
      window.removeEventListener('storage', syncPinned);
    };
  }, [messageId]);

  return useMemo(() => {
    if (force) return true;
    if (agentEnabled || skillsEnabled || chatArtifactsEnabled) return true;
    if (pinned) return true;
    if (contentImpliesLegacyArtifactsViewer(content)) return true;
    return false;
  }, [force, agentEnabled, skillsEnabled, chatArtifactsEnabled, pinned, content]);
}

/** Live-гейт без persist (агент/skill) — чтобы решать, нужно ли pin'ить сообщение. */
export function useArtifactsViewerLiveGate(chatId?: string | null): boolean {
  const [agentEnabled, setAgentEnabled] = useState(() => readAgentViewerGate());
  const [skillsEnabled, setSkillsEnabled] = useState(() => chatSkillsAllowViewer(chatId));
  const [chatArtifactsEnabled, setChatArtifactsEnabled] = useState(() =>
    chatArtifactsAllowViewer(chatId),
  );

  useEffect(() => {
    const sync = () => setAgentEnabled(readAgentViewerGate());
    const onAgentSelected = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail === null || detail === undefined) {
        persistAgentArtifactsEnabled(null);
        setAgentEnabled(false);
        return;
      }
      const cfg = artifactsConfigFromAgentDetail(detail);
      if (cfg) {
        persistAgentArtifactsEnabled(cfg);
        setAgentEnabled(readAgentViewerGate());
      } else sync();
    };
    window.addEventListener('agentSelected', onAgentSelected as EventListener);
    window.addEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, sync);
    return () => {
      window.removeEventListener('agentSelected', onAgentSelected as EventListener);
      window.removeEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, sync);
    };
  }, []);

  useEffect(() => {
    const syncSkills = (e?: Event) => {
      const detailChatId = (e as CustomEvent<{ chatId?: string }> | undefined)?.detail?.chatId;
      if (detailChatId && chatId && detailChatId !== chatId) return;
      setSkillsEnabled(chatSkillsAllowViewer(chatId));
    };
    syncSkills();
    window.addEventListener(SKILL_SELECTION_CHANGED_EVENT, syncSkills as EventListener);
    return () => window.removeEventListener(SKILL_SELECTION_CHANGED_EVENT, syncSkills as EventListener);
  }, [chatId]);

  useEffect(() => {
    const syncArtifacts = (e?: Event) => {
      const detailChatId = (e as CustomEvent<{ chatId?: string }> | undefined)?.detail?.chatId;
      if (detailChatId && chatId && detailChatId !== chatId) return;
      setChatArtifactsEnabled(chatArtifactsAllowViewer(chatId));
    };
    syncArtifacts();
    window.addEventListener(ARTIFACTS_SELECTION_CHANGED_EVENT, syncArtifacts as EventListener);
    window.addEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, syncArtifacts);
    return () => {
      window.removeEventListener(ARTIFACTS_SELECTION_CHANGED_EVENT, syncArtifacts as EventListener);
      window.removeEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, syncArtifacts);
    };
  }, [chatId]);

  return agentEnabled || skillsEnabled || chatArtifactsEnabled;
}

/**
 * Ожидаем именно presentation viewer (не просто HTML ArtifactCard):
 * skill презентации у агента или в чате.
 */
export function usePresentationViewerExpected(chatId?: string | null): boolean {
  const [agentPresentation, setAgentPresentation] = useState(() => readAgentPresentationSkills());
  const [chatPresentation, setChatPresentation] = useState(() => chatSkillsAllowPresentation(chatId));

  useEffect(() => {
    const sync = () => setAgentPresentation(readAgentPresentationSkills());
    const onAgentSelected = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail === null || detail === undefined) {
        persistAgentArtifactsEnabled(null);
        setAgentPresentation(false);
        return;
      }
      const cfg = artifactsConfigFromAgentDetail(detail);
      if (cfg) {
        persistAgentArtifactsEnabled(cfg);
        setAgentPresentation(readAgentPresentationSkills());
      } else sync();
    };
    window.addEventListener('agentSelected', onAgentSelected as EventListener);
    window.addEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, sync);
    return () => {
      window.removeEventListener('agentSelected', onAgentSelected as EventListener);
      window.removeEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, sync);
    };
  }, []);

  useEffect(() => {
    const syncSkills = (e?: Event) => {
      const detailChatId = (e as CustomEvent<{ chatId?: string }> | undefined)?.detail?.chatId;
      if (detailChatId && chatId && detailChatId !== chatId) return;
      setChatPresentation(chatSkillsAllowPresentation(chatId));
    };
    syncSkills();
    window.addEventListener(SKILL_SELECTION_CHANGED_EVENT, syncSkills as EventListener);
    return () => window.removeEventListener(SKILL_SELECTION_CHANGED_EVENT, syncSkills as EventListener);
  }, [chatId]);

  return agentPresentation || chatPresentation;
}
