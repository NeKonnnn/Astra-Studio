export const ASTRA_RAG_ENTITY_SETTINGS_APPLIED = 'astrachat:rag-entity-settings-applied';

export interface RagEntitySettingsAppliedDetail {
  scope: 'agent' | 'project' | 'kb';
  entityId: string | number;
  entityName?: string;
}

export function dispatchRagEntitySettingsApplied(detail: RagEntitySettingsAppliedDetail): void {
  window.dispatchEvent(new CustomEvent(ASTRA_RAG_ENTITY_SETTINGS_APPLIED, { detail }));
}
