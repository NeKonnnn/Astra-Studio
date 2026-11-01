import React from 'react';
import { render, screen } from '@testing-library/react';
import PrivateRoute from '@/components/PrivateRoute';

const mockUseAuth = jest.fn();

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

// react-router-dom v7 не резолвится в Jest 27 (поле "exports"), а менять конфиг
// запрещено, поэтому подменяем Navigate и проверяем, куда PrivateRoute редиректит.
jest.mock('react-router-dom', () => ({
  Navigate: ({ to, replace }: { to: string; replace?: boolean }) => (
    <div data-testid="navigate" data-to={to} data-replace={String(Boolean(replace))} />
  ),
}));

function renderRoute() {
  return render(
    <PrivateRoute>
      <div>секретное содержимое</div>
    </PrivateRoute>,
  );
}

describe('PrivateRoute', () => {
  it('shows a spinner and nothing protected while the session is loading', () => {
    mockUseAuth.mockReturnValue({ isAuthenticated: false, isLoading: true });

    renderRoute();

    expect(screen.getByRole('progressbar')).toBeTruthy();
    expect(screen.queryByText('секретное содержимое')).toBeNull();
    expect(screen.queryByTestId('navigate')).toBeNull();
  });

  it('does not leak protected content while loading even if a stale session says authenticated', () => {
    mockUseAuth.mockReturnValue({ isAuthenticated: true, isLoading: true });

    renderRoute();

    expect(screen.queryByText('секретное содержимое')).toBeNull();
    expect(screen.getByRole('progressbar')).toBeTruthy();
  });

  it('redirects an anonymous visitor to /login without keeping history', () => {
    mockUseAuth.mockReturnValue({ isAuthenticated: false, isLoading: false });

    renderRoute();

    const navigate = screen.getByTestId('navigate');
    expect(navigate.getAttribute('data-to')).toBe('/login');
    expect(navigate.getAttribute('data-replace')).toBe('true');
    expect(screen.queryByText('секретное содержимое')).toBeNull();
  });

  it('renders the protected content for an authenticated user', () => {
    mockUseAuth.mockReturnValue({ isAuthenticated: true, isLoading: false });

    renderRoute();

    expect(screen.getByText('секретное содержимое')).toBeTruthy();
    expect(screen.queryByTestId('navigate')).toBeNull();
    expect(screen.queryByRole('progressbar')).toBeNull();
  });
});
