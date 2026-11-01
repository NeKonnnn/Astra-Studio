import { act, renderHook } from '@testing-library/react';
import { useActiveSkillIndicators } from '@/utils/useActiveSkillIndicators';
import { SKILL_SELECTION_CHANGED_EVENT } from '@/utils/skillSelectionStorage';

jest.mock('@/utils/skillSelectionStorage', () => ({
  SKILL_SELECTION_CHANGED_EVENT: 'astrachatSkillSelectionChanged',
  getActiveSkillRefs: jest.fn(),
}));

const { getActiveSkillRefs } = jest.requireMock('@/utils/skillSelectionStorage') as {
  getActiveSkillRefs: jest.Mock;
};

describe('useActiveSkillIndicators', () => {
  beforeEach(() => getActiveSkillRefs.mockReset());

  it('читает начальное значение и обновляется по событию выбора и storage', () => {
    getActiveSkillRefs.mockReturnValue([{ slug: 'a' }]);
    const { result } = renderHook(() => useActiveSkillIndicators());
    expect(result.current).toEqual([{ slug: 'a' }]);

    getActiveSkillRefs.mockReturnValue([{ slug: 'a' }, { slug: 'b', name: 'B' }]);
    act(() => {
      window.dispatchEvent(new Event(SKILL_SELECTION_CHANGED_EVENT));
    });
    expect(result.current).toHaveLength(2);

    getActiveSkillRefs.mockReturnValue([]);
    act(() => {
      window.dispatchEvent(new Event('storage'));
    });
    expect(result.current).toEqual([]);
  });

  it('снимает подписки при размонтировании', () => {
    getActiveSkillRefs.mockReturnValue([]);
    const remove = jest.spyOn(window, 'removeEventListener');
    const { unmount } = renderHook(() => useActiveSkillIndicators());
    unmount();
    const names = remove.mock.calls.map((c) => c[0]);
    expect(names).toEqual(expect.arrayContaining([SKILL_SELECTION_CHANGED_EVENT, 'storage']));
    remove.mockRestore();
  });
});
