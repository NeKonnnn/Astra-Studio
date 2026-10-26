/**
 * @jest-environment jsdom
 */
import {
  clearActiveSkills,
  getActiveSkillIds,
  getActiveSkillRefs,
  setActiveSkillRefs,
  toggleActiveSkill,
} from '@/utils/skillSelectionStorage';

describe('skillSelectionStorage', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('stores skills per chatId (no global leak)', () => {
    expect(getActiveSkillIds('c1')).toEqual([]);
    setActiveSkillRefs('c1', [{ slug: 'summarize', name: 'Summary' }]);
    expect(getActiveSkillIds('c1')).toEqual(['summarize']);
    expect(getActiveSkillRefs('c1')[0]).toEqual({ slug: 'summarize', name: 'Summary' });
    expect(getActiveSkillIds('c2')).toEqual([]);
  });

  it('toggles selection like knowledge bases (click again disables)', () => {
    toggleActiveSkill('c1', 'a', true, 'A');
    toggleActiveSkill('c1', 'b', true, 'B');
    expect(getActiveSkillIds('c1')).toEqual(['a', 'b']);
    toggleActiveSkill('c1', 'a', false);
    expect(getActiveSkillIds('c1')).toEqual(['b']);
    clearActiveSkills('c1');
    expect(getActiveSkillIds('c1')).toEqual([]);
  });

  it('does not write legacy global active_skill_ids', () => {
    toggleActiveSkill('chat-x', 'slug-1', true, 'One');
    expect(localStorage.getItem('active_skill_ids')).toBeNull();
    expect(localStorage.getItem('chat:chat-x:active_skill_ids')).toBeTruthy();
  });
});
