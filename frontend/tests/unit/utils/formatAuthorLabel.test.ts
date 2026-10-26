/**
 * @jest-environment jsdom
 */
import { formatAuthorLabel } from '@/utils/formatAuthorLabel';

describe('formatAuthorLabel', () => {
  it('shows FIO · gpbu when both differ', () => {
    expect(formatAuthorLabel('gpbu', 'Иван Иванов', 'uid')).toBe('Иван Иванов · gpbu');
  });

  it('falls back to gpbu / authorId', () => {
    expect(formatAuthorLabel('gpbu', null, 'uid')).toBe('gpbu');
    expect(formatAuthorLabel(null, null, 'uid-1')).toBe('uid-1');
  });

  it('returns empty when nothing provided', () => {
    expect(formatAuthorLabel()).toBe('');
  });
});
