/**
 * Общая оснастка тестов левого сайдбара (left_bar/LeftBar.tsx и устаревшей копии Sidebar.tsx).
 *
 * Не тест (не матчится на *.test.*). Подключается из jest.mock-фабрик в тестовых файлах:
 *   jest.mock('react-router-dom', () => require('../__test_support__/sidebarHarness').routerMock);
 * Контексты (App/Auth/Socket) подменены изменяемым объектом `h`; `resetHarness()` вызывается
 * в beforeEach (CRA включает resetMocks, поэтому реализации jest.fn задаём заново).
 */
import React from 'react';

type AnyChat = {
  id: string;
  title: string;
  messages: Array<{ content: string; isStreaming?: boolean }>;
  createdAt?: string;
  updatedAt: string;
  isArchived?: boolean;
  projectId?: string;
  isPinnedInProject?: boolean;
  hiddenFromSidebarUntilUserMessage?: boolean;
  messagesUnloaded?: boolean;
  unloadedMessageCount?: number;
};

export type HarnessFolder = { id: string; name: string; chatIds: string[]; expanded: boolean };
export type HarnessProject = {
  id: string;
  name: string;
  icon?: string;
  iconType?: 'icon' | 'emoji';
  iconColor?: string;
  memory?: 'default' | 'project-only';
  instructions?: string;
};

export const h = {
  navigate: jest.fn(),
  location: { pathname: '/' } as { pathname: string },
  state: { chats: [] as AnyChat[], currentChatId: null as string | null, loadingChatIds: [] as string[] },
  folders: [] as HarnessFolder[],
  projects: [] as HarnessProject[],
  user: null as null | { username: string; full_name?: string },
  logout: jest.fn(),
  socket: { isConnected: true },
  /** Последние props, с которыми отрисованы заглушки модалок. */
  props: {} as Record<string, any>,
  actions: {
    createChat: jest.fn(),
    setCurrentChat: jest.fn(),
    deleteChat: jest.fn(),
    updateChatTitle: jest.fn(),
    createFolder: jest.fn(),
    updateFolder: jest.fn(),
    deleteFolder: jest.fn(),
    moveChatToFolder: jest.fn(),
    toggleFolder: jest.fn(),
    archiveChat: jest.fn(),
    archiveFolder: jest.fn(),
    createProject: jest.fn(),
    updateProject: jest.fn(),
    deleteProject: jest.fn(),
    moveChatToProject: jest.fn(),
    togglePinInProject: jest.fn(),
  },
};

/** Стабильный объект действий: getCurrentChat/getChatById/getFolders/getProjects читают актуальный `h`. */
const actionsValue = {
  ...h.actions,
  getCurrentChat: () => h.state.chats.find((c) => c.id === h.state.currentChatId) ?? null,
  getChatById: (id: string) => h.state.chats.find((c) => c.id === id) ?? null,
  getFolders: () => h.folders,
  getProjects: () => h.projects,
};

export function resetHarness(): void {
  h.location = { pathname: '/' };
  h.state = { chats: [], currentChatId: null, loadingChatIds: [] };
  h.folders = [];
  h.projects = [];
  h.user = { username: 'ivan', full_name: 'Иван Петров' };
  h.socket = { isConnected: true };
  h.props = {};
  h.navigate.mockReset();
  h.logout.mockReset();
  Object.values(h.actions).forEach((fn) => fn.mockReset());
  h.actions.createChat.mockReturnValue('new-chat-id');
  h.actions.createFolder.mockReturnValue('new-folder-id');
  h.actions.createProject.mockReturnValue('new-project-id');
}

export const routerMock = {
  useNavigate: () => h.navigate,
  useLocation: () => h.location,
};

export const appContextMock = {
  useAppContext: () => ({ state: h.state }),
  useAppActions: () => actionsValue,
  // Копия предиката из contexts/AppContext (реальный модуль тянет весь стор).
  chatIsListedInAllChatsSection: (chat: AnyChat): boolean => {
    if (chat.hiddenFromSidebarUntilUserMessage) return false;
    if (chat.messagesUnloaded) return (chat.unloadedMessageCount ?? 0) > 0;
    return chat.messages.length > 0;
  },
};

export const socketContextMock = { useSocket: () => h.socket };
export const authContextMock = {
  useAuth: () => ({ user: h.user, logout: h.logout, token: h.user ? 'tok' : null }),
};

// ---- Заглушки тяжёлых дочерних компонентов (каждая сохраняет последние props в h.props) ----
export const ArchiveModalStub = (p: { open: boolean; onClose: () => void }) => {
  h.props.archive = p;
  return p.open ? <div data-testid="archive-modal">архив</div> : null;
};
export const NewProjectModalStub = (p: { open: boolean }) => {
  h.props.newProject = p;
  return p.open ? <div data-testid="new-project-modal">новый проект</div> : null;
};
export const EditProjectModalStub = (p: { open: boolean; project: { name: string } | null }) => {
  h.props.editProject = p;
  return p.open ? <div data-testid="edit-project-modal">{p.project ? p.project.name : 'нет проекта'}</div> : null;
};
export const HotkeysDialogStub = (p: { open: boolean }) => {
  h.props.hotkeys = p;
  return p.open ? <div data-testid="hotkeys-dialog">горячие клавиши</div> : null;
};
export const UserAvatarStub = (p: { fallbackInitials?: string }) => (
  <span data-testid="user-avatar">{p.fallbackInitials ?? 'avatar'}</span>
);
