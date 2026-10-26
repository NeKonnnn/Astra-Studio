/**
 * @jest-environment jsdom
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ChatGearSkillsPanel from '@/components/ChatGearSkillsPanel';

const mockNavigate = jest.fn();
const mockAuthFetch = jest.fn();
const mockOpenSkillInSidebar = jest.fn();
const mockToggleActiveSkill = jest.fn();
const mockClearActiveSkills = jest.fn();

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
  API_ENDPOINTS: { SKILLS: '/api/skills' },
}));

jest.mock('@/utils/skillSelectionStorage', () => ({
  getActiveSkillIds: () => [],
  clearActiveSkills: (...args: unknown[]) => mockClearActiveSkills(...args),
  toggleActiveSkill: (...args: unknown[]) => mockToggleActiveSkill(...args),
  SKILL_SELECTION_CHANGED_EVENT: 'astrachatSkillSelectionChanged',
}));

jest.mock('@/utils/openSkillSidebarNav', () => ({
  openSkillInSidebar: (...args: unknown[]) => mockOpenSkillInSidebar(...args),
}));

function jsonOk(body: unknown) {
  return Promise.resolve({
    ok: true,
    json: async () => body,
  });
}

describe('ChatGearSkillsPanel', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockAuthFetch.mockReset();
    mockOpenSkillInSidebar.mockReset();
    mockToggleActiveSkill.mockReset();
    mockClearActiveSkills.mockReset();
  });

  it('has My/Gallery tabs, search, no «Без skills», and empty gallery link', async () => {
    mockAuthFetch
      .mockImplementationOnce(() => jsonOk({ items: [] }))
      .mockImplementationOnce(() => jsonOk({ items: [] }));

    render(<ChatGearSkillsPanel isDarkMode={false} chatId="chat-1" />);

    expect(screen.getByPlaceholderText('Поиск skills…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Мои skills' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Skills из галереи' })).toBeInTheDocument();
    expect(screen.queryByText('Без skills')).not.toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Нет своих skills')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Открыть галерею' }));
    expect(mockNavigate).toHaveBeenCalledWith('/gallery?tab=skills');
  });

  it('lists skills with author and opens settings via gear', async () => {
    mockAuthFetch
      .mockImplementationOnce(() =>
        jsonOk({
          items: [
            {
              id: 7,
              slug: 'demo',
              name: 'Demo Skill',
              is_active: true,
              user_invocable: true,
              always_apply: false,
              author_id: 'admin',
              author_name: 'admin',
              author_full_name: 'Admin User',
            },
          ],
        }),
      )
      .mockImplementationOnce(() => jsonOk({ items: [] }));

    render(<ChatGearSkillsPanel isDarkMode={false} chatId="chat-1" />);

    await waitFor(() => {
      expect(screen.getByText('Demo Skill')).toBeInTheDocument();
    });
    expect(screen.getByText(/Admin User/)).toBeInTheDocument();
    expect(screen.queryByText('Без skills')).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Настройки skill'));
    expect(mockOpenSkillInSidebar).toHaveBeenCalledWith(7, mockNavigate);

    fireEvent.click(screen.getByText('Demo Skill'));
    expect(mockToggleActiveSkill).toHaveBeenCalledWith('chat-1', 'demo', true, 'Demo Skill');
  });

  it('loads bookmarks for gallery tab', async () => {
    mockAuthFetch
      .mockImplementationOnce(() => jsonOk({ items: [] }))
      .mockImplementationOnce(() => jsonOk({ items: [] }));

    render(<ChatGearSkillsPanel isDarkMode={false} chatId="chat-1" />);
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled());

    mockAuthFetch.mockReset();
    mockAuthFetch.mockImplementationOnce(() => jsonOk({ items: [] }));
    fireEvent.click(screen.getByRole('button', { name: 'Skills из галереи' }));

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/skills/my/bookmarks?limit=100',
        expect.any(Object),
      );
    });
    await waitFor(() => {
      expect(screen.getByText('В галерее пока нет skills')).toBeInTheDocument();
    });
  });
});
