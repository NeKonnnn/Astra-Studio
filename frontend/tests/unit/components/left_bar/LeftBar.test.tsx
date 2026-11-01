import React from 'react';
import { defineSidebarSuite } from '@/components/__test_support__/sidebarSuite';
import { prepareNewChatSettings } from '@/utils/newChatSettings';
import LeftBar from '@/components/left_bar/LeftBar';

jest.mock('react-router-dom', () => require('../__test_support__/sidebarHarness').routerMock);
jest.mock('@/contexts/AppContext', () => require('../__test_support__/sidebarHarness').appContextMock);
jest.mock('@/contexts/SocketContext', () => require('../__test_support__/sidebarHarness').socketContextMock);
jest.mock('@/contexts/AuthContext', () => require('../__test_support__/sidebarHarness').authContextMock);
jest.mock('@/components/UserAvatar', () => ({ __esModule: true, default: require('../__test_support__/sidebarHarness').UserAvatarStub }));
jest.mock('@/components/ArchiveModal', () => ({ __esModule: true, default: require('../__test_support__/sidebarHarness').ArchiveModalStub }));
jest.mock('@/components/NewProjectModal', () => ({ __esModule: true, default: require('../__test_support__/sidebarHarness').NewProjectModalStub }));
jest.mock('@/components/EditProjectModal', () => ({ __esModule: true, default: require('../__test_support__/sidebarHarness').EditProjectModalStub }));
jest.mock('@/components/HotkeysSettingsDialog', () => ({ __esModule: true, default: require('../__test_support__/sidebarHarness').HotkeysDialogStub }));
jest.mock('@/utils/newChatSettings', () => ({ prepareNewChatSettings: jest.fn() }));

defineSidebarSuite('LeftBar', () => LeftBar, {
  variant: 'left',
  prepareNewChatSettings: prepareNewChatSettings as unknown as jest.Mock,
});
