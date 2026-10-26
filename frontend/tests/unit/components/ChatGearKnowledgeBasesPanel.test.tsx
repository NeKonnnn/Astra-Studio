/**
 * @jest-environment jsdom
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ChatGearKnowledgeBasesPanel from '@/components/ChatGearKnowledgeBasesPanel';

const mockNavigate = jest.fn();
const mockAuthFetch = jest.fn();
const mockOpenKnowledgeBaseInSidebar = jest.fn();
const mockToggleActiveKnowledgeBase = jest.fn();

jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ token: 'jwt', user: { user_id: 'admin' } }),
}));

jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: { currentChatId: 'chat-1' } }),
}));

jest.mock('@/config/api', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
  getApiUrl: (p: string) => p,
  API_ENDPOINTS: { KNOWLEDGE_BASES: '/api/knowledge-bases' },
}));

jest.mock('@/utils/knowledgeBaseSelectionStorage', () => ({
  getActiveKnowledgeBaseIds: () => [],
  clearActiveKnowledgeBases: jest.fn(),
  toggleActiveKnowledgeBase: (...args: unknown[]) => mockToggleActiveKnowledgeBase(...args),
  KB_SELECTION_CHANGED_EVENT: 'astrachatKnowledgeBaseSelectionChanged',
}));

jest.mock('@/utils/openKnowledgeBaseSidebarNav', () => ({
  openKnowledgeBaseInSidebar: (...args: unknown[]) => mockOpenKnowledgeBaseInSidebar(...args),
}));

describe('ChatGearKnowledgeBasesPanel', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockAuthFetch.mockReset();
    mockOpenKnowledgeBaseInSidebar.mockReset();
    mockToggleActiveKnowledgeBase.mockReset();
  });

  it('shows settings gear that opens KB sidebar', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            id: 9,
            name: 'Docs KB',
            author_id: 'admin',
            author_name: 'admin',
            author_full_name: 'Admin User',
            document_count: 2,
          },
        ],
      }),
    });

    render(<ChatGearKnowledgeBasesPanel isDarkMode={false} chatId="chat-1" />);

    await waitFor(() => {
      expect(screen.getByText('Docs KB')).toBeInTheDocument();
    });
    expect(screen.getByText(/Admin User/)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Настройки базы знаний'));
    expect(mockOpenKnowledgeBaseInSidebar).toHaveBeenCalledWith(9, mockNavigate);

    fireEvent.click(screen.getByText('Docs KB'));
    expect(mockToggleActiveKnowledgeBase).toHaveBeenCalledWith('chat-1', 9, 'Docs KB');
  });

  it('empty my tab links to gallery', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    render(<ChatGearKnowledgeBasesPanel isDarkMode={false} chatId="chat-1" />);
    await waitFor(() => {
      expect(screen.getByText('Нет своих баз знаний')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Открыть галерею' }));
    expect(mockNavigate).toHaveBeenCalledWith('/gallery?tab=knowledge-bases');
  });
});
