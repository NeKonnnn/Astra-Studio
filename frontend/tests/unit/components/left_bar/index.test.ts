import * as barrel from '@/components/left_bar/index';
import LeftBar from '@/components/left_bar/LeftBar';
import SidebarRailCollapsedAction, { SidebarRailShortcutTooltip } from '@/components/left_bar/SidebarRailCollapsedAction';
import UpdatesDialog from '@/components/left_bar/UpdatesDialog';
import { RELEASE_NOTES_SHORT } from '@/components/left_bar/releaseNotesShort';
import SidebarRailMenuGlyph from '@/icons/SidebarRailMenuGlyph';

jest.mock('react-router-dom', () => require('../__test_support__/sidebarHarness').routerMock);
jest.mock('@/contexts/AppContext', () => require('../__test_support__/sidebarHarness').appContextMock);
jest.mock('@/contexts/SocketContext', () => require('../__test_support__/sidebarHarness').socketContextMock);
jest.mock('@/contexts/AuthContext', () => require('../__test_support__/sidebarHarness').authContextMock);

describe('left_bar/index', () => {
  it('реэкспортирует компоненты и данные тех же самых модулей', () => {
    expect(barrel.LeftBar).toBe(LeftBar);
    expect(barrel.default).toBe(LeftBar);
    expect(barrel.SidebarRailCollapsedAction).toBe(SidebarRailCollapsedAction);
    expect(barrel.SidebarRailShortcutTooltip).toBe(SidebarRailShortcutTooltip);
    expect(barrel.UpdatesDialog).toBe(UpdatesDialog);
    expect(barrel.RELEASE_NOTES_SHORT).toBe(RELEASE_NOTES_SHORT);
    expect(barrel.SidebarRailMenuGlyph).toBe(SidebarRailMenuGlyph);
  });
});
