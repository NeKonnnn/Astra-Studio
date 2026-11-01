/**
 * @jest-environment jsdom
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import BulkChatManageDialog from '@/components/left_bar/BulkChatManageDialog';

const mockArchiveChat = jest.fn();
const mockMoveChatToFolder = jest.fn();
const mockMoveChatToProject = jest.fn();
const mockCreateFolder = jest.fn();
const mockCreateProject = jest.fn();
const mockDeleteChat = jest.fn();
const mockSetCurrentChat = jest.fn();
const mockUpdateProject = jest.fn();
const mockDeleteProject = jest.fn();
const mockOnClose = jest.fn();

const mockChats = [
  {
    id: 'c1',
    title: 'Чат один',
    updatedAt: '2026-01-02T00:00:00.000Z',
    isArchived: false,
    messages: [],
  },
  {
    id: 'c2',
    title: 'Чат два',
    updatedAt: '2026-01-01T00:00:00.000Z',
    isArchived: false,
    messages: [],
  },
];

jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({
    state: {
      chats: mockChats,
      projects: [{ id: 'p1', name: 'Проект А' }],
      folders: [{ id: 'f1', name: 'Папка А' }],
      currentChatId: null,
    },
  }),
  useAppActions: () => ({
    deleteChat: mockDeleteChat,
    setCurrentChat: mockSetCurrentChat,
    archiveChat: mockArchiveChat,
    moveChatToFolder: mockMoveChatToFolder,
    moveChatToProject: mockMoveChatToProject,
    createFolder: mockCreateFolder,
    createProject: mockCreateProject,
    updateProject: mockUpdateProject,
    deleteProject: mockDeleteProject,
  }),
  chatIsListedInAllChatsSection: () => true,
}));

jest.mock('@/components/NewProjectModal', () => ({
  __esModule: true,
  default: () => null,
}));

function setup() {
  return render(
    <BulkChatManageDialog open onClose={mockOnClose} isDarkMode={false} initialSelectedChatId="c1" />,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

describe('BulkChatManageDialog «Еще»', () => {
  it('в режиме проектов показывает «В проект», архивирует выбранные', () => {
    localStorage.setItem('use_folders_mode', 'false');
    setup();
    fireEvent.click(screen.getByRole('button', { name: /Еще/i }));
    expect(screen.getByText('В архив')).toBeInTheDocument();
    expect(screen.getByText('В проект')).toBeInTheDocument();
    expect(screen.queryByText('В папку')).toBeNull();

    fireEvent.click(screen.getByText('В архив'));
    expect(mockArchiveChat).toHaveBeenCalledWith('c1');
  });

  it('в режиме папок показывает «В папку»', () => {
    localStorage.setItem('use_folders_mode', 'true');
    setup();
    fireEvent.click(screen.getByRole('button', { name: /Еще/i }));
    expect(screen.getByText('В папку')).toBeInTheDocument();
    expect(screen.queryByText('В проект')).toBeNull();
  });

  it('переносит в выбранный проект', () => {
    localStorage.setItem('use_folders_mode', 'false');
    setup();
    fireEvent.click(screen.getByRole('button', { name: /Еще/i }));
    fireEvent.click(screen.getByText('В проект'));
    fireEvent.click(screen.getByText('Проект А'));
    expect(mockMoveChatToProject).toHaveBeenCalledWith('c1', 'p1');
  });
});
