/**
 * @jest-environment jsdom
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ChatGearMyAgentsTab from '@/components/ChatGearMyAgentsTab';

const mockNavigate = jest.fn();
const mockAuthFetch = jest.fn();
const mockOpenAgentInConstructor = jest.fn();
const mockClearActiveAgent = jest.fn();
const mockShowNotification = jest.fn();

jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ token: 'jwt' }),
}));

jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockShowNotification }),
}));

jest.mock('@/config/api', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
  getApiUrl: (p: string) => p,
}));

jest.mock('@/utils/openAgentConstructorNav', () => ({
  openAgentInConstructor: (...args: unknown[]) => mockOpenAgentInConstructor(...args),
}));

jest.mock('@/utils/clearActiveAgent', () => ({
  clearActiveAgent: (...args: unknown[]) => mockClearActiveAgent(...args),
}));

jest.mock('@/utils/applyAgentServer', () => ({
  loadAgentModelOnly: jest.fn(async () => ({ ok: true })),
}));

jest.mock('@/utils/applyAgentMcp', () => ({
  persistAgentMcpConfig: jest.fn(),
}));

jest.mock('@/utils/agentArtifactsEnabled', () => ({
  persistAgentArtifactsEnabled: jest.fn(),
}));

jest.mock('@/components/AgentSelector', () => ({
  getActiveAgentFromStorage: () => {
    const id = globalThis.localStorage.getItem('active_agent_id');
    if (!id) return null;
    return {
      id: Number(id),
      name: globalThis.localStorage.getItem('active_agent_name') || '',
      system_prompt: globalThis.localStorage.getItem('active_agent_prompt') || '',
    };
  },
}));

jest.mock('@/components/ChatGearAgentReindexHint', () => () => null);

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, json: async () => body });
}

describe('ChatGearMyAgentsTab', () => {
  beforeEach(() => {
    localStorage.clear();
    mockNavigate.mockReset();
    mockAuthFetch.mockReset();
    mockOpenAgentInConstructor.mockReset();
    mockClearActiveAgent.mockReset();
    mockShowNotification.mockReset();
  });

  it('loads via authFetch, has no «Без агента», shows empty gallery link', async () => {
    mockAuthFetch
      .mockImplementationOnce(() => jsonOk({ agents: [] }))
      .mockImplementationOnce(() => jsonOk({ agents: [] }));

    render(<ChatGearMyAgentsTab isDarkMode={false} searchQuery="" visible />);

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalledWith('/api/agents/my/agents?limit=100');
    });
    expect(screen.queryByText('Без агента')).not.toBeInTheDocument();
    expect(await screen.findByText('Нет своих агентов')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Открыть галерею' }));
    expect(mockNavigate).toHaveBeenCalledWith('/gallery?tab=agents');
  });

  it('shows author and settings gear; re-click clears agent like KB toggle', async () => {
    mockAuthFetch
      .mockImplementationOnce(() =>
        jsonOk({
          agents: [
            {
              id: 5,
              name: 'My Agent',
              system_prompt: 'p',
              author_id: 'admin',
              author_name: 'admin',
              author_full_name: 'Admin User',
            },
          ],
        }),
      )
      .mockImplementationOnce(() => jsonOk({ agents: [] }));

    render(<ChatGearMyAgentsTab isDarkMode={false} searchQuery="" visible />);

    await waitFor(() => {
      expect(screen.getByText('My Agent')).toBeInTheDocument();
    });
    expect(screen.getByText(/Admin User/)).toBeInTheDocument();
    expect(screen.queryByText('Без агента')).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Настройки агента'));
    expect(mockOpenAgentInConstructor).toHaveBeenCalledWith(5, mockNavigate);

    mockAuthFetch.mockImplementation(() =>
      jsonOk({
        id: 5,
        name: 'My Agent',
        system_prompt: 'p',
        config: {},
      }),
    );
    fireEvent.click(screen.getByText('My Agent'));
    await waitFor(() => {
      expect(localStorage.getItem('active_agent_id')).toBe('5');
    });

    fireEvent.click(screen.getByText('My Agent'));
    await waitFor(() => {
      expect(mockClearActiveAgent).toHaveBeenCalled();
    });
  });
});
