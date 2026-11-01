import React from 'react';
import { defineSidebarSuite } from '@/components/__test_support__/sidebarSuite';
import Sidebar from '@/components/Sidebar';

jest.mock('react-router-dom', () => require('./__test_support__/sidebarHarness').routerMock);
jest.mock('@/contexts/AppContext', () => require('./__test_support__/sidebarHarness').appContextMock);
jest.mock('@/contexts/SocketContext', () => require('./__test_support__/sidebarHarness').socketContextMock);
jest.mock('@/contexts/AuthContext', () => require('./__test_support__/sidebarHarness').authContextMock);
jest.mock('@/components/ArchiveModal', () => ({ __esModule: true, default: require('./__test_support__/sidebarHarness').ArchiveModalStub }));
jest.mock('@/components/NewProjectModal', () => ({ __esModule: true, default: require('./__test_support__/sidebarHarness').NewProjectModalStub }));
jest.mock('@/components/EditProjectModal', () => ({ __esModule: true, default: require('./__test_support__/sidebarHarness').EditProjectModalStub }));
jest.mock('@/components/HotkeysSettingsDialog', () => ({ __esModule: true, default: require('./__test_support__/sidebarHarness').HotkeysDialogStub }));
// Sidebar.tsx импортирует './SidebarRailMenuGlyph', которого в components/ нет (файл лежит в icons/).
jest.mock('@/components/SidebarRailMenuGlyph', () => ({ __esModule: true, default: () => null }), { virtual: true });

defineSidebarSuite('Sidebar (устаревшая копия)', () => Sidebar, { variant: 'legacy' });
