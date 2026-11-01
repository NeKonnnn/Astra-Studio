import { renderHook } from '@testing-library/react';
import { useFeatureAccess } from '@/hooks/useFeatureAccess';

const mockUseAuth = jest.fn();

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

function access(user: Record<string, unknown> | null) {
  mockUseAuth.mockReturnValue({ user });
  return renderHook(() => useFeatureAccess()).result.current;
}

describe('useFeatureAccess', () => {
  it('does not hide the UI when the backend sent no flags (old token)', () => {
    expect(access({})).toEqual({
      canUseAgents: true,
      canUseMcp: true,
      canCreateAgents: true,
      canPublishToLibrary: true,
      platformRole: null,
    });
    expect(access(null).canUseAgents).toBe(true);
  });

  it('gives admins everything regardless of flags', () => {
    const result = access({
      is_admin: true,
      can_use_agents: false,
      can_use_mcp: false,
      can_create_agents: false,
      can_publish_to_library: false,
      platform_role: 'base',
    });

    expect(result).toEqual({
      canUseAgents: true,
      canUseMcp: true,
      canCreateAgents: true,
      canPublishToLibrary: true,
      platformRole: 'base',
    });
  });

  it('honours explicit false flags', () => {
    const result = access({ can_use_agents: false, can_use_mcp: false });

    expect(result.canUseAgents).toBe(false);
    expect(result.canUseMcp).toBe(false);
  });

  it('prefers top-level flags over the features object', () => {
    const result = access({ can_use_agents: true, features: { agents: false } });

    expect(result.canUseAgents).toBe(true);
  });

  it('falls back to the features object', () => {
    const result = access({
      features: { agents: false, mcp: false, create_agents: false, publish_to_library: false },
    });

    expect(result).toMatchObject({
      canUseAgents: false,
      canUseMcp: false,
      canCreateAgents: false,
      canPublishToLibrary: false,
    });
  });

  it.each([
    ['base', false, false],
    ['viewer', false, false],
    ['editor', true, false],
    ['admin', true, true],
    [null, true, true],
  ])('derives creation rights from the %p role', (role, canCreate, canPublish) => {
    const result = access({ platform_role: role });

    expect(result.canCreateAgents).toBe(canCreate);
    expect(result.canPublishToLibrary).toBe(canPublish);
    expect(result.platformRole).toBe(role);
  });

  it('lets an explicit flag override the role-based default', () => {
    const result = access({ platform_role: 'base', can_create_agents: true, can_publish_to_library: true });

    expect(result.canCreateAgents).toBe(true);
    expect(result.canPublishToLibrary).toBe(true);
  });
});
