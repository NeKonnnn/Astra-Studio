/**
 * Самодостаточные регрессионные тесты LeftBar (BUG-FE-46, BUG-FE-47).
 * Не зависят от sidebarSuite/sidebarHarness: контексты и модалки замокированы здесь же.
 */
import React from 'react';
import { act, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import LeftBar from '@/components/left_bar/LeftBar';

type TestChat = {
  id: string;
  title: string;
  messages: unknown[];
  isArchived: boolean;
  createdAt: Date;
  updatedAt: Date;
  projectId?: string;
};
type TestFolder = { id: string; name: string; chatIds: string[]; expanded: boolean };

const ctx: { chats: TestChat[]; folders: TestFolder[] } = { chats: [], folders: [] };

jest.mock('react-router-dom', () => ({
  useNavigate: () => jest.fn(),
  useLocation: () => ({ pathname: '/', search: '', hash: '', state: null, key: 'k' }),
}));

jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({
    state: {
      chats: ctx.chats,
      currentChatId: null,
      loadingChatIds: [],
    },
  }),
  useAppActions: () => ({
    createChat: jest.fn(),
    setCurrentChat: jest.fn(),
    deleteChat: jest.fn(),
    updateChatTitle: jest.fn(),
    getCurrentChat: () => undefined,
    createFolder: jest.fn(),
    updateFolder: jest.fn(),
    deleteFolder: jest.fn(),
    moveChatToFolder: jest.fn(),
    toggleFolder: jest.fn(),
    getFolders: () => ctx.folders,
    archiveChat: jest.fn(),
    archiveFolder: jest.fn(),
    createProject: jest.fn(),
    updateProject: jest.fn(),
    deleteProject: jest.fn(),
    getProjects: () => [],
    moveChatToProject: jest.fn(),
    getChatById: (id: string) => ctx.chats.find((c) => c.id === id),
    togglePinInProject: jest.fn(),
  }),
  chatIsListedInAllChatsSection: () => true,
}));

jest.mock('@/contexts/SocketContext', () => ({ useSocket: () => ({ isConnected: true }) }));
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'u' }, logout: jest.fn(), token: 't' }),
}));

jest.mock('@/components/UserAvatar', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/ArchiveModal', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/NewProjectModal', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/EditProjectModal', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/HotkeysSettingsDialog', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/left_bar/UpdatesDialog', () => ({ __esModule: true, default: () => null }));
jest.mock('@/utils/newChatSettings', () => ({ prepareNewChatSettings: jest.fn() }));

const chat = (id: string, title: string): TestChat => ({
  id,
  title,
  messages: [],
  isArchived: false,
  createdAt: new Date(),
  updatedAt: new Date(),
});

function renderBar() {
  return render(
    <LeftBar open onToggle={jest.fn()} isDarkMode onToggleTheme={jest.fn()} onHide={jest.fn()} />,
  );
}

describe('LeftBar: BUG-FE-47 блок «Закреплено» в режиме проектов', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('use_folders_mode', 'false');
    ctx.chats = [chat('c1', 'Закреплённый чат'), chat('c2', 'Обычный чат')];
    ctx.folders = [{ id: 'f-pin', name: 'Закреплено', chatIds: ['c1'], expanded: true }];
  });

  it('блок «Закреплено» с чатом виден в режиме проектов', () => {
    renderBar();
    expect(screen.getByText('Закреплено')).toBeInTheDocument();
    expect(screen.getByText('Закреплённый чат')).toBeInTheDocument();
  });

  it('закреплённый чат не дублируется в «Все чаты»', () => {
    renderBar();
    expect(screen.getAllByText('Закреплённый чат')).toHaveLength(1);
    expect(screen.getAllByText('Обычный чат')).toHaveLength(1);
  });

  it('без папки «Закреплено» блок не рисуется', () => {
    ctx.folders = [];
    renderBar();
    expect(screen.queryByText('Закреплено')).toBeNull();
    expect(screen.getByText('Закреплённый чат')).toBeInTheDocument();
  });
});

describe('LeftBar: BUG-FE-46 filteredChats зависит от режима папок', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('use_folders_mode', 'false');
    ctx.chats = [chat('c3', 'Чат в папке')];
    ctx.folders = [{ id: 'f-1', name: 'Прочая', chatIds: ['c3'], expanded: false }];
  });

  it('в режиме проектов чат из обычной папки остаётся в «Все чаты»', () => {
    renderBar();
    expect(screen.getByText('Чат в папке')).toBeInTheDocument();
  });

  it('после переключения в режим папок чат исчезает из «Все чаты» без смены списка чатов', () => {
    renderBar();
    expect(screen.getByText('Чат в папке')).toBeInTheDocument();
    act(() => {
      localStorage.setItem('use_folders_mode', 'true');
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });
    expect(screen.queryByText('Чат в папке')).toBeNull();
  });

  it('и обратно: при выключении режима папок чат возвращается в «Все чаты»', () => {
    localStorage.setItem('use_folders_mode', 'true');
    renderBar();
    expect(screen.queryByText('Чат в папке')).toBeNull();
    act(() => {
      localStorage.setItem('use_folders_mode', 'false');
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });
    expect(screen.getByText('Чат в папке')).toBeInTheDocument();
  });
});
