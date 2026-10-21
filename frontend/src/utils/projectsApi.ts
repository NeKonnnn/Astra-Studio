import { getApiUrl, getAuthFetchHeaders } from '../config/api';
import type { Project } from '../contexts/AppContext';

export async function fetchProjectsFromServer(): Promise<Project[]> {
  const response = await fetch(getApiUrl('/api/projects'), {
    headers: getAuthFetchHeaders(),
  });
  if (!response.ok) {
    throw new Error(`GET /api/projects: ${response.status}`);
  }
  const data = (await response.json()) as { projects?: Project[] };
  return Array.isArray(data.projects) ? data.projects : [];
}

function projectToBody(
  project: Pick<
    Project,
    | 'id'
    | 'name'
    | 'instructions'
    | 'memory'
    | 'icon'
    | 'iconType'
    | 'iconColor'
    | 'knowledgeBasesEnabled'
    | 'knowledgeBaseIds'
  >,
) {
  return {
    id: project.id,
    name: project.name,
    instructions: project.instructions || '',
    memory: project.memory,
    icon: project.icon,
    iconType: project.iconType,
    iconColor: project.iconColor,
    knowledgeBasesEnabled: Boolean(project.knowledgeBasesEnabled),
    knowledgeBaseIds: Array.isArray(project.knowledgeBaseIds)
      ? project.knowledgeBaseIds
      : [],
  };
}

export async function syncProjectCreate(project: Project): Promise<void> {
  const response = await fetch(getApiUrl('/api/projects'), {
    method: 'POST',
    headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(projectToBody(project)),
  });
  if (!response.ok) {
    const details = await response.text().catch(() => '');
    throw new Error(`POST /api/projects: ${response.status}${details ? ` — ${details}` : ''}`);
  }
}

export async function syncProjectUpdate(projectId: string, updates: Partial<Project>): Promise<void> {
  const body: Record<string, unknown> = {};
  if (updates.name !== undefined) body.name = updates.name;
  if (updates.instructions !== undefined) body.instructions = updates.instructions;
  if (updates.memory !== undefined) body.memory = updates.memory;
  if (updates.icon !== undefined) body.icon = updates.icon;
  if (updates.iconType !== undefined) body.iconType = updates.iconType;
  if (updates.iconColor !== undefined) body.iconColor = updates.iconColor;
  if (updates.knowledgeBasesEnabled !== undefined) {
    body.knowledgeBasesEnabled = updates.knowledgeBasesEnabled;
  }
  if (updates.knowledgeBaseIds !== undefined) {
    body.knowledgeBaseIds = updates.knowledgeBaseIds;
  }

  const response = await fetch(getApiUrl(`/api/projects/${encodeURIComponent(projectId)}`), {
    method: 'PUT',
    headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const details = await response.text().catch(() => '');
    throw new Error(`PUT /api/projects/${projectId}: ${response.status}${details ? ` — ${details}` : ''}`);
  }
}

export async function migrateLocalProjectsToServer(projects: Project[]): Promise<void> {
  for (const project of projects) {
    try {
      await syncProjectCreate(project);
      console.debug(`[projects] миграция в БД: «${project.name}» (${project.id})`);
    } catch (error) {
      console.warn(`[projects] не удалось мигрировать проект ${project.id}:`, error);
    }
  }
}
