import { useAuth } from '../contexts/AuthContext';

/**
 * Фичи по платформенной роли (AGENT_ROLE_* / base).
 * Если бэкенд ещё не прислал флаги (старый токен) — не скрываем UI.
 * Явный `false` / роль base с бэка — агенты и MCP недоступны.
 */
export function useFeatureAccess() {
  const { user } = useAuth();

  if (user?.is_admin) {
    return {
      canUseAgents: true,
      canUseMcp: true,
      canCreateAgents: true,
      canPublishToLibrary: true,
      platformRole: user.platform_role ?? null,
    };
  }

  const canUseAgents =
    typeof user?.can_use_agents === 'boolean'
      ? user.can_use_agents
      : typeof user?.features?.agents === 'boolean'
        ? user.features.agents
        : true;

  const canUseMcp =
    typeof user?.can_use_mcp === 'boolean'
      ? user.can_use_mcp
      : typeof user?.features?.mcp === 'boolean'
        ? user.features.mcp
        : true;

  const role = user?.platform_role ?? null;

  const canCreateAgents =
    typeof user?.can_create_agents === 'boolean'
      ? user.can_create_agents
      : typeof user?.features?.create_agents === 'boolean'
        ? user.features.create_agents
        : role === 'viewer' || role === 'base'
          ? false
          : true;

  const canPublishToLibrary =
    typeof user?.can_publish_to_library === 'boolean'
      ? user.can_publish_to_library
      : typeof user?.features?.publish_to_library === 'boolean'
        ? user.features.publish_to_library
        : role === 'editor' || role === 'viewer' || role === 'base'
          ? false
          : true;

  return {
    canUseAgents,
    canUseMcp,
    canCreateAgents,
    canPublishToLibrary,
    platformRole: role,
  };
}
