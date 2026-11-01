import { useEffect, useState } from 'react';
import {
  AGENT_ARTIFACTS_CHANGED_EVENT,
  artifactsConfigFromAgentDetail,
  persistAgentArtifactsEnabled,
  readAgentArtifactsEnabled,
} from '../utils/agentArtifactsEnabled';

/**
 * Viewer артефактов (HTML/Mermaid/SVG/:::artifact) разрешён только если
 * у активного агента config.artifacts_enabled=true.
 */
export function useArtifactsEnabled(): boolean {
  const [enabled, setEnabled] = useState(() => readAgentArtifactsEnabled());

  useEffect(() => {
    const syncFromStorage = () => setEnabled(readAgentArtifactsEnabled());

    const onAgentSelected = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail === null || detail === undefined) {
        persistAgentArtifactsEnabled(null);
        setEnabled(false);
        return;
      }
      const cfg = artifactsConfigFromAgentDetail(detail);
      // Если в detail нет config — не затираем уже сохранённый флаг.
      if (cfg) {
        persistAgentArtifactsEnabled(cfg);
        setEnabled(Boolean(cfg.artifacts_enabled));
      } else {
        syncFromStorage();
      }
    };

    window.addEventListener('agentSelected', onAgentSelected as EventListener);
    window.addEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, syncFromStorage);
    window.addEventListener('storage', syncFromStorage);
    return () => {
      window.removeEventListener('agentSelected', onAgentSelected as EventListener);
      window.removeEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, syncFromStorage);
      window.removeEventListener('storage', syncFromStorage);
    };
  }, []);

  return enabled;
}
