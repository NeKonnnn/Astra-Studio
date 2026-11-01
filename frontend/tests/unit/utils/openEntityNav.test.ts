import {
  ASTRA_OPEN_AGENT_CONSTRUCTOR,
  ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY,
  ASTRA_OPEN_SKILLS_SIDEBAR,
  ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY,
} from '@/constants/hotkeys';
import {
  OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY,
  OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY,
  consumePendingRelatedAgentIds,
  openAgentInConstructor,
  peekPendingAgentConstructorId,
  resolvePendingAgentConstructorId,
  stashAgentConstructorOpen,
} from '@/utils/openAgentConstructorNav';
import {
  OPEN_SKILL_SIDEBAR_NAV_STATE_KEY,
  openSkillInSidebar,
  peekPendingSkillSidebarId,
  resolvePendingSkillSidebarId,
  stashSkillSidebarOpen,
} from '@/utils/openSkillSidebarNav';

beforeEach(() => {
  sessionStorage.clear();
  jest.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
    cb(0);
    return 1;
  });
});

afterEach(() => jest.restoreAllMocks());

function collect(eventName: string) {
  const details: any[] = [];
  const handler = (e: Event) => details.push((e as CustomEvent).detail);
  window.addEventListener(eventName, handler);
  return { details, stop: () => window.removeEventListener(eventName, handler) };
}

describe('agent constructor navigation', () => {
  it('stash and peek roundtrip without consuming', () => {
    stashAgentConstructorOpen(42);
    expect(peekPendingAgentConstructorId()).toBe(42);
    expect(peekPendingAgentConstructorId()).toBe(42);
    expect(sessionStorage.getItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY)).toBe('42');
  });

  it.each([['', null], ['abc', null], ['0', null], ['-3', null], ['7', 7]])('peek of %j gives %s', (raw, expected) => {
    if (raw) sessionStorage.setItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY, raw);
    expect(peekPendingAgentConstructorId()).toBe(expected);
  });

  it('navigation state wins over storage', () => {
    stashAgentConstructorOpen(5);
    expect(resolvePendingAgentConstructorId({ [OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY]: 9 })).toBe(9);
  });

  it.each([[null], [undefined], [{}], [{ [OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY]: 'x' }], [{ [OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY]: 0 }]])(
    'falls back to storage for state %j',
    (state) => {
      stashAgentConstructorOpen(5);
      expect(resolvePendingAgentConstructorId(state)).toBe(5);
    },
  );

  it('nothing pending gives null', () => {
    expect(resolvePendingAgentConstructorId(null)).toBeNull();
  });

  it('related ids are read once and cleared', () => {
    sessionStorage.setItem(OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY, JSON.stringify([1, '2', 0, -5, 'x', 3]));
    expect(consumePendingRelatedAgentIds()).toEqual([1, 2, 3]);
    expect(consumePendingRelatedAgentIds()).toEqual([]);
  });

  it.each([['{bad'], ['"str"'], ['{"a":1}']])('related ids ignore malformed storage %s', (raw) => {
    sessionStorage.setItem(OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY, raw);
    expect(consumePendingRelatedAgentIds()).toEqual([]);
  });

  it('openAgentInConstructor stashes, navigates home and dispatches twice', () => {
    const navigate = jest.fn();
    const { details, stop } = collect(ASTRA_OPEN_AGENT_CONSTRUCTOR);

    openAgentInConstructor(7, navigate, { relatedAgentIds: [8, 8, 9, -1, 0] });
    stop();

    expect(peekPendingAgentConstructorId()).toBe(7);
    expect(JSON.parse(sessionStorage.getItem(OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY) as string)).toEqual([8, 9]);
    expect(navigate).toHaveBeenCalledWith('/', { state: { [OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY]: 7 } });
    expect(details).toHaveLength(2);
    expect(details[0]).toEqual({ agentId: 7, relatedAgentIds: [8, 8, 9, -1, 0] });
  });

  it('without related ids the stash is cleared', () => {
    sessionStorage.setItem(OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY, '[1]');
    openAgentInConstructor(7, jest.fn());
    expect(sessionStorage.getItem(OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY)).toBeNull();
    sessionStorage.setItem(OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY, '[1]');
    openAgentInConstructor(7, jest.fn(), { relatedAgentIds: [] });
    expect(sessionStorage.getItem(OPEN_AGENT_CONSTRUCTOR_RELATED_IDS_KEY)).toBeNull();
  });

  it('works when sessionStorage throws', () => {
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(() => stashAgentConstructorOpen(1)).not.toThrow();
    expect(peekPendingAgentConstructorId()).toBeNull();
    expect(consumePendingRelatedAgentIds()).toEqual([]);
    expect(() => openAgentInConstructor(1, jest.fn(), { relatedAgentIds: [2] })).not.toThrow();
  });
});

describe('skills sidebar navigation', () => {
  it('stash and peek roundtrip', () => {
    stashSkillSidebarOpen(11);
    expect(peekPendingSkillSidebarId()).toBe(11);
    expect(sessionStorage.getItem(ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY)).toBe('11');
  });

  it.each([['', null], ['x', null], ['0', null], ['-1', null], ['4', 4]])('peek of %j gives %s', (raw, expected) => {
    if (raw) sessionStorage.setItem(ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY, raw);
    expect(peekPendingSkillSidebarId()).toBe(expected);
  });

  it('navigation state wins over storage and falls back to it', () => {
    stashSkillSidebarOpen(3);
    expect(resolvePendingSkillSidebarId({ [OPEN_SKILL_SIDEBAR_NAV_STATE_KEY]: 8 })).toBe(8);
    expect(resolvePendingSkillSidebarId({ [OPEN_SKILL_SIDEBAR_NAV_STATE_KEY]: -2 })).toBe(3);
    expect(resolvePendingSkillSidebarId(null)).toBe(3);
  });

  it('openSkillInSidebar stashes, navigates home and dispatches twice', () => {
    const navigate = jest.fn();
    const { details, stop } = collect(ASTRA_OPEN_SKILLS_SIDEBAR);

    openSkillInSidebar(5, navigate);
    stop();

    expect(peekPendingSkillSidebarId()).toBe(5);
    expect(navigate).toHaveBeenCalledWith('/', { state: { [OPEN_SKILL_SIDEBAR_NAV_STATE_KEY]: 5 } });
    expect(details).toEqual([{ skillId: 5 }, { skillId: 5 }]);
  });

  it('survives a storage that throws', () => {
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(() => stashSkillSidebarOpen(1)).not.toThrow();
    expect(peekPendingSkillSidebarId()).toBeNull();
  });
});
