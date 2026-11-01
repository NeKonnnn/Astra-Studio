import * as barrel from '@/components/right_bar/index';
import RightBarDefault from '@/components/right_bar/RightBar';

jest.mock('react-router-dom', () => ({
  useNavigate: () => jest.fn(),
  useLocation: () => ({ pathname: '/', state: null }),
}));

describe('right_bar/index', () => {
  it('реэкспортирует компоненты, контекст и константы панели', () => {
    expect(barrel.default).toBe(RightBarDefault);
    expect(barrel.RightBar).toBe(RightBarDefault);
    for (const name of [
      'RightBarProvider',
      'useRightBarLayout',
      'GalleryNavButton',
      'SkillsSidebarPanel',
      'AgentConstructorPanel',
      'TranscriptionSidebarSection',
      'dispatchInsertChatText',
      'SidebarRailMenuGlyph',
    ] as const) {
      expect(barrel[name]).toBeTruthy();
    }
    expect(barrel.ASTRA_INSERT_CHAT_TEXT).toBe('astrachat:insert-chat-text');
    expect(barrel.ASTRA_INSERT_CHAT_TEXT_KEY).toBe('astrachat_insert_chat_text');
  });
});
