/**
 * @jest-environment jsdom
 */
import { openAgentInConstructor } from '@/utils/openAgentConstructorNav';
import { openKnowledgeBaseInSidebar } from '@/utils/openKnowledgeBaseSidebarNav';
import { openSkillInSidebar } from '@/utils/openSkillSidebarNav';
import {
  ASTRA_OPEN_AGENT_CONSTRUCTOR,
  ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY,
  ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR,
  ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR_ID_KEY,
  ASTRA_OPEN_SKILLS_SIDEBAR,
  ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY,
} from '@/constants/hotkeys';

describe('open* nav helpers (gear settings)', () => {
  const navigate = jest.fn();

  beforeEach(() => {
    navigate.mockReset();
    sessionStorage.clear();
    jest.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      cb(0);
      return 0;
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('openAgentInConstructor stashes id, navigates and emits event', () => {
    const spy = jest.fn();
    window.addEventListener(ASTRA_OPEN_AGENT_CONSTRUCTOR, spy);
    openAgentInConstructor(11, navigate as never);
    expect(sessionStorage.getItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY)).toBe('11');
    expect(navigate).toHaveBeenCalledWith('/', expect.objectContaining({ state: expect.any(Object) }));
    expect(spy).toHaveBeenCalled();
    expect((spy.mock.calls[0][0] as CustomEvent).detail).toEqual(
      expect.objectContaining({ agentId: 11 }),
    );
    window.removeEventListener(ASTRA_OPEN_AGENT_CONSTRUCTOR, spy);
  });

  it('openSkillInSidebar stashes id and emits skills event', () => {
    const spy = jest.fn();
    window.addEventListener(ASTRA_OPEN_SKILLS_SIDEBAR, spy);
    openSkillInSidebar(22, navigate as never);
    expect(sessionStorage.getItem(ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY)).toBe('22');
    expect((spy.mock.calls[0][0] as CustomEvent).detail).toEqual({ skillId: 22 });
    window.removeEventListener(ASTRA_OPEN_SKILLS_SIDEBAR, spy);
  });

  it('openKnowledgeBaseInSidebar stashes id and emits KB event', () => {
    const spy = jest.fn();
    window.addEventListener(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR, spy);
    openKnowledgeBaseInSidebar(33, navigate as never);
    expect(sessionStorage.getItem(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR_ID_KEY)).toBe('33');
    expect((spy.mock.calls[0][0] as CustomEvent).detail).toEqual({ kbId: 33 });
    window.removeEventListener(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR, spy);
  });
});
