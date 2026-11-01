import { getUserInitials } from '@/utils/userInitials';

describe('getUserInitials', () => {
  it.each([
    ['Петр Кашин', 'ПК'],
    ['  анна   мария   петрова ', 'АП'],
    ['Мадонна', 'МА'],
    ['Я', 'Я'],
  ])('uses the full name %p -> %p', (name, expected) => {
    expect(getUserInitials(name, 'login')).toBe(expected);
  });

  it('falls back to the login', () => {
    expect(getUserInitials(null, 'ivanov')).toBe('IV');
    expect(getUserInitials('   ', 'i')).toBe('I');
  });

  it('returns a question mark when nothing is known', () => {
    expect(getUserInitials()).toBe('?');
    expect(getUserInitials('', '  ')).toBe('?');
  });
});
