import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

const mockUseAuth = jest.fn();
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));
jest.mock('@/components/UserAvatar', () => ({
  __esModule: true,
  default: () => <div data-testid="avatar" />,
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a), getApiUrl: (p: string) => `http://api.test${p}` }));
jest.mock('@/settings', () => ({
  getSettings: () => ({ api: { baseUrl: 'http://settings.test' } }),
}));
jest.mock('axios', () => ({
  __esModule: true,
  default: { put: jest.fn(), isAxiosError: jest.fn() },
}));

import axios from 'axios';
import ProfileSettings from '@/components/settings/ProfileSettings';

const http: any = axios;
const USER = {
  username: 'alice',
  email: 'a@x.io',
  full_name: 'Alice A',
  is_admin: false,
};
const updateUser = jest.fn();

beforeEach(() => {
  jest.clearAllMocks();
  mockUseAuth.mockReturnValue({ user: USER, updateUser, token: 'tok-1' });
  http.isAxiosError.mockImplementation((e: any) => Boolean(e && e.isAxiosError));
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => {
  jest.restoreAllMocks();
});

describe('ProfileSettings', () => {
  it('asks to log in when there is no user', () => {
    mockUseAuth.mockReturnValue({ user: null, updateUser, token: null });
    render(<ProfileSettings />);

    expect(screen.getByText('Необходимо авторизоваться')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Сохранить/ })).toBeNull();
  });

  it('prefills the form from the user and shows the admin badge only for admins', () => {
    const { rerender } = render(<ProfileSettings />);

    expect((screen.getByLabelText('Полное имя') as HTMLInputElement).value).toBe('Alice A');
    expect((screen.getByLabelText('Email') as HTMLInputElement).value).toBe('a@x.io');
    const username = screen.getByLabelText('Имя пользователя') as HTMLInputElement;
    expect(username.value).toBe('alice');
    expect(username.disabled).toBe(true);
    expect(screen.queryByText('Администратор')).toBeNull();

    mockUseAuth.mockReturnValue({ user: { ...USER, is_admin: true }, updateUser, token: 't' });
    rerender(<ProfileSettings />);
    expect(screen.getByText('Администратор')).toBeTruthy();
  });

  it('falls back to username as the title when the user has no full name', () => {
    mockUseAuth.mockReturnValue({
      user: { ...USER, full_name: null, email: null },
      updateUser,
      token: 't',
    });
    render(<ProfileSettings />);

    expect(screen.getByText('alice', { selector: 'h6' })).toBeTruthy();
    expect((screen.getByLabelText('Email') as HTMLInputElement).value).toBe('');
  });

  it('PUTs the edited profile with a Bearer token, updates the user and auto-hides the success alert', async () => {
    jest.useFakeTimers();
    try {
      http.put.mockResolvedValue({ data: { ...USER, full_name: 'Bob' } });
      render(<ProfileSettings />);

      fireEvent.change(screen.getByLabelText('Полное имя'), { target: { value: 'Bob' } });
      fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'b@x.io' } });
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /Сохранить изменения/ }));
      });

      expect(http.put).toHaveBeenCalledWith(
        'http://settings.test/api/auth/me',
        { email: 'b@x.io', full_name: 'Bob' },
        { headers: { Authorization: 'Bearer tok-1' } },
      );
      expect(updateUser).toHaveBeenCalledWith({ ...USER, full_name: 'Bob' });
      expect(screen.getByText('Профиль успешно обновлен!')).toBeTruthy();

      act(() => {
        jest.advanceTimersByTime(3000);
      });
      expect(screen.queryByText('Профиль успешно обновлен!')).toBeNull();
    } finally {
      jest.useRealTimers();
    }
  });

  it('sends null for cleared email and full name', async () => {
    http.put.mockResolvedValue({ data: USER });
    render(<ProfileSettings />);

    fireEvent.change(screen.getByLabelText('Полное имя'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /Сохранить изменения/ }));

    await waitFor(() => expect(http.put).toHaveBeenCalled());
    expect(http.put.mock.calls[0][1]).toEqual({ email: null, full_name: null });
  });

  it('disables the form while saving', async () => {
    let resolve!: (v: any) => void;
    http.put.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<ProfileSettings />);

    fireEvent.click(screen.getByRole('button', { name: /Сохранить изменения/ }));

    expect((screen.getByRole('button', { name: /Сохранить изменения/ }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText('Email') as HTMLInputElement).disabled).toBe(true);

    await act(async () => {
      resolve({ data: USER });
    });
    expect((screen.getByRole('button', { name: /Сохранить изменения/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('shows the backend "detail" message on an HTTP error and does not update the user', async () => {
    http.put.mockRejectedValue({ isAxiosError: true, response: { data: { detail: 'Email уже занят' } } });
    render(<ProfileSettings />);

    fireEvent.click(screen.getByRole('button', { name: /Сохранить изменения/ }));

    expect(await screen.findByText('Email уже занят')).toBeTruthy();
    expect(updateUser).not.toHaveBeenCalled();
    expect(screen.queryByText('Профиль успешно обновлен!')).toBeNull();
  });

  it('falls back to a generic message when the HTTP error has no detail', async () => {
    http.put.mockRejectedValue({ isAxiosError: true, response: { data: {} } });
    render(<ProfileSettings />);

    fireEvent.click(screen.getByRole('button', { name: /Сохранить изменения/ }));

    expect(await screen.findByText('Ошибка обновления профиля')).toBeTruthy();
  });

  it('shows the unexpected-error message on a network failure and allows dismissing it', async () => {
    http.put.mockRejectedValue(new Error('Network Error'));
    render(<ProfileSettings />);

    fireEvent.click(screen.getByRole('button', { name: /Сохранить изменения/ }));

    expect(await screen.findByText('Произошла непредвиденная ошибка')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(screen.queryByText('Произошла непредвиденная ошибка')).toBeNull();
  });

  it('allows dismissing the success alert manually', async () => {
    http.put.mockResolvedValue({ data: USER });
    render(<ProfileSettings />);

    fireEvent.click(screen.getByRole('button', { name: /Сохранить изменения/ }));
    await screen.findByText('Профиль успешно обновлен!');
    fireEvent.click(screen.getByRole('button', { name: /close/i }));

    expect(screen.queryByText('Профиль успешно обновлен!')).toBeNull();
  });

  it('prefers REACT_APP_API_URL over the settings base URL', async () => {
    const prev = process.env.REACT_APP_API_URL;
    process.env.REACT_APP_API_URL = 'http://env.test';
    try {
      http.put.mockResolvedValue({ data: USER });
      render(<ProfileSettings />);
      fireEvent.click(screen.getByRole('button', { name: /Сохранить изменения/ }));
      await waitFor(() => expect(http.put).toHaveBeenCalled());
      expect(http.put.mock.calls[0][0]).toBe('http://env.test/api/auth/me');
    } finally {
      if (prev === undefined) delete process.env.REACT_APP_API_URL;
      else process.env.REACT_APP_API_URL = prev;
    }
  });
});
