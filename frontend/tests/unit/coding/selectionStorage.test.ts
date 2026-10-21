/**
 * @jest-environment jsdom
 */
import {
  isCodingModeEnabled,
  isCodingPlanModeEnabled,
  setCodingModeEnabled,
  setCodingPlanModeEnabled,
} from '@/coding/selectionStorage';

describe('coding selectionStorage (local)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('stores coding mode per chatId', () => {
    expect(isCodingModeEnabled('c1')).toBe(false);
    setCodingModeEnabled('c1', true);
    expect(isCodingModeEnabled('c1')).toBe(true);
    expect(isCodingModeEnabled('c2')).toBe(false);
    expect(localStorage.getItem('astrachat_coding_mode:c1')).toBe('true');
  });

  it('stores plan mode independently', () => {
    setCodingPlanModeEnabled('c1', true);
    expect(isCodingPlanModeEnabled('c1')).toBe(true);
    expect(isCodingModeEnabled('c1')).toBe(false);
  });
});
