import React from 'react';
import { render, screen } from '@testing-library/react';
import UserAvatar from '@/components/UserAvatar';

const mockUseAuth = jest.fn();

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

describe('UserAvatar', () => {
  beforeEach(() => {
    mockUseAuth.mockReturnValue({
      user: { username: 'ivanov', full_name: 'Иван Иванов' },
      avatarSrc: undefined,
    });
  });

  it('shows initials of the current user', () => {
    render(<UserAvatar />);

    expect(screen.getByText('ИИ')).toBeTruthy();
  });

  it('uses explicit props over the current user', () => {
    render(<UserAvatar username="petrov" fullName="Пётр Петров" />);

    expect(screen.getByText('ПП')).toBeTruthy();
    expect(screen.queryByText('ИИ')).toBeNull();
  });

  it('falls back to the login when there is no full name', () => {
    mockUseAuth.mockReturnValue({ user: { username: 'ivanov' }, avatarSrc: undefined });

    render(<UserAvatar />);

    expect(screen.getByText('IV')).toBeTruthy();
  });

  it('prefers explicit fallback initials', () => {
    render(<UserAvatar fallbackInitials="Z" />);

    expect(screen.getByText('Z')).toBeTruthy();
  });

  it('shows a question mark for an anonymous visitor', () => {
    mockUseAuth.mockReturnValue({ user: null, avatarSrc: undefined });

    render(<UserAvatar />);

    expect(screen.getByText('?')).toBeTruthy();
  });

  it('shrinks the font for two-letter initials only', () => {
    const { container, rerender } = render(<UserAvatar />);
    const two = getComputedStyle(container.firstElementChild as Element).fontSize;

    rerender(<UserAvatar fallbackInitials="Z" />);
    const one = getComputedStyle(container.firstElementChild as Element).fontSize;

    expect(two).not.toBe(one);
  });

  it('forwards extra avatar props', () => {
    render(<UserAvatar data-testid="avatar" />);

    expect(screen.getByTestId('avatar')).toBeTruthy();
  });
});
