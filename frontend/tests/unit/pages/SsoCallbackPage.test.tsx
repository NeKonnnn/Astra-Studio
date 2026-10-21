/**
 * @jest-environment jsdom
 */
import React from 'react';
import { render, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const mockCompleteSsoLogin = jest.fn().mockResolvedValue(undefined);

jest.mock('axios', () => ({
  __esModule: true,
  default: {
    isAxiosError: (err: unknown) => Boolean(err && typeof err === 'object' && (err as { isAxiosError?: boolean }).isAxiosError),
  },
}));

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ completeSsoLogin: mockCompleteSsoLogin }),
}));

import SsoCallbackPage from '@/pages/SsoCallbackPage';

describe('SsoCallbackPage', () => {
  beforeEach(() => {
    mockCompleteSsoLogin.mockClear();
    mockCompleteSsoLogin.mockResolvedValue(undefined);
    sessionStorage.clear();
    window.location.hash = '';
  });

  it('exchanges the ticket from the URL hash once (StrictMode-safe)', async () => {
    window.location.hash = '#ticket=opaque-handoff-code';

    render(
      <MemoryRouter initialEntries={['/login/sso-callback']}>
        <Routes>
          <Route path="/login/sso-callback" element={<SsoCallbackPage />} />
          <Route path="/" element={<div>home</div>} />
        </Routes>
      </MemoryRouter>,
      { reactStrictMode: true },
    );

    await waitFor(() => expect(mockCompleteSsoLogin).toHaveBeenCalledTimes(1));
    expect(mockCompleteSsoLogin).toHaveBeenCalledWith('opaque-handoff-code');
  });

  it('shows an error when the ticket is missing', async () => {
    const { findByText } = render(
      <MemoryRouter initialEntries={['/login/sso-callback']}>
        <Routes>
          <Route path="/login/sso-callback" element={<SsoCallbackPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await findByText('Отсутствует ticket SSO')).toBeInTheDocument();
    expect(mockCompleteSsoLogin).not.toHaveBeenCalled();
  });
});
