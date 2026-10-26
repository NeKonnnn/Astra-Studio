/**
 * @jest-environment jsdom
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ChatGearGalleryAgentsTab from '@/components/ChatGearGalleryAgentsTab';

const mockNavigate = jest.fn();
const mockAuthFetch = jest.fn();
const mockOpenAgentInConstructor = jest.fn();

jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ token: 'jwt' }),
}));

jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: jest.fn() }),
}));

jest.mock('@/config/api', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
  getApiUrl: (p: string) => p,
}));

jest.mock('@/utils/openAgentConstructorNav', () => ({
  openAgentInConstructor: (...args: unknown[]) => mockOpenAgentInConstructor(...args),
}));

jest.mock('@/utils/clearActiveAgent', () => ({
  clearActiveAgent: jest.fn(),
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
  getActiveAgentFromStorage: () => null,
}));

jest.mock('@/components/ChatGearAgentReindexHint', () => () => null);

describe('ChatGearGalleryAgentsTab', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockAuthFetch.mockReset();
    mockOpenAgentInConstructor.mockReset();
  });

  it('loads bookmarks via authFetch and has no «Без агента»', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({ agents: [] }) });
    render(<ChatGearGalleryAgentsTab isDarkMode={false} searchQuery="" visible />);

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalledWith('/api/agents/my/bookmarks?limit=100');
    });
    expect(screen.queryByText('Без агента')).not.toBeInTheDocument();
    expect(await screen.findByText('В галерее пока нет агентов')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Открыть галерею' }));
    expect(mockNavigate).toHaveBeenCalledWith('/gallery?tab=agents');
  });

  it('shows gallery agent with settings gear', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        agents: [
          {
            id: 8,
            name: 'Gallery Agent',
            system_prompt: 'x',
            author_id: 'other',
            author_name: 'other',
            author_full_name: 'Other User',
          },
        ],
      }),
    });

    render(<ChatGearGalleryAgentsTab isDarkMode={false} searchQuery="" visible />);
    await waitFor(() => {
      expect(screen.getByText('Gallery Agent')).toBeInTheDocument();
    });
    expect(screen.getByText(/Other User/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Настройки агента'));
    expect(mockOpenAgentInConstructor).toHaveBeenCalledWith(8, mockNavigate);
  });
});
