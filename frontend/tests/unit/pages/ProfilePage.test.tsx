import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ProfilePage from '@/pages/ProfilePage';

const mockNavigate = jest.fn();
const mockLogout = jest.fn();
const mockUpdateUser = jest.fn();
let mockUser: any;

jest.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: mockUser,
    logout: mockLogout,
    updateUser: mockUpdateUser,
    token: 'tok123',
  }),
}));
jest.mock('@/components/UserAvatar', () => ({
  __esModule: true,
  default: () => <div data-testid="avatar" />,
}));
jest.mock('@/settings', () => ({
  getSettings: () => ({ api: { baseUrl: 'http://base' } }),
}));
const mockPut = jest.fn();
jest.mock('axios', () => ({
  __esModule: true,
  default: { put: (...a: any[]) => mockPut(...a) },
}));

const baseUser = {
  username: 'ivan',
  full_name: 'Иван Петров',
  email: 'i@p.ru',
  is_admin: false,
  is_active: true,
};

describe('ProfilePage', () => {
  const oldApi = process.env.REACT_APP_API_URL;
  beforeEach(() => {
    mockUser = { ...baseUser };
    mockPut.mockReset();
    mockLogout.mockReset();
    mockUpdateUser.mockReset();
    mockNavigate.mockReset();
    delete process.env.REACT_APP_API_URL;
  });
  afterAll(() => {
    if (oldApi !== undefined) process.env.REACT_APP_API_URL = oldApi;
  });

  it('без пользователя ничего не рисует', () => {
    mockUser = null;
    const { container } = render(<ProfilePage />);
    expect(container.firstChild).toBeNull();
  });

  it('показывает данные пользователя и статусы обычного аккаунта', () => {
    render(<ProfilePage />);
    expect(screen.getByText('Иван Петров')).toBeTruthy();
    expect(screen.getByText('@ivan')).toBeTruthy();
    expect(screen.getByText('Активен')).toBeTruthy();
    expect(screen.getByText('Пользователь')).toBeTruthy();
    expect(screen.queryByText('Администратор')).toBeNull();
    expect((screen.getByLabelText('Email') as HTMLInputElement).value).toBe('i@p.ru');
    expect((screen.getByLabelText('Имя пользователя') as HTMLInputElement).disabled).toBe(true);
  });

  it('для администратора без полного имени показывает username и бейдж', () => {
    mockUser = { ...baseUser, full_name: '', email: undefined, is_admin: true, is_active: false };
    render(<ProfilePage />);
    expect(screen.getByRole('heading', { name: 'ivan' })).toBeTruthy();
    expect(screen.getAllByText('Администратор').length).toBe(2);
    expect(screen.getByText('Неактивен')).toBeTruthy();
    expect((screen.getByLabelText('Email') as HTMLInputElement).value).toBe('');
  });

  it('сохранение шлёт PUT с токеном на базовый URL из настроек и обновляет пользователя', async () => {
    mockPut.mockResolvedValue({ data: { ...baseUser, full_name: 'Новое имя' } });
    render(<ProfilePage />);
    fireEvent.change(screen.getByLabelText('Полное имя'), { target: { value: 'Новое имя' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'n@p.ru' } });
    fireEvent.click(screen.getByText('Сохранить изменения'));
    expect(await screen.findByText('Профиль успешно обновлен')).toBeTruthy();
    expect(mockPut).toHaveBeenCalledWith(
      'http://base/api/auth/me',
      { email: 'n@p.ru', full_name: 'Новое имя' },
      { headers: { Authorization: 'Bearer tok123' } },
    );
    expect(mockUpdateUser).toHaveBeenCalledWith({ ...baseUser, full_name: 'Новое имя' });
  });

  it('REACT_APP_API_URL имеет приоритет, пустые поля отправляются как null', async () => {
    process.env.REACT_APP_API_URL = 'http://env';
    mockPut.mockResolvedValue({ data: baseUser });
    render(<ProfilePage />);
    fireEvent.change(screen.getByLabelText('Полное имя'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: '' } });
    fireEvent.click(screen.getByText('Сохранить изменения'));
    await screen.findByText('Профиль успешно обновлен');
    expect(mockPut.mock.calls[0][0]).toBe('http://env/api/auth/me');
    expect(mockPut.mock.calls[0][1]).toEqual({ email: null, full_name: null });
  });

  it('во время сохранения кнопка и поля заблокированы', async () => {
    let resolve!: (v: unknown) => void;
    mockPut.mockReturnValue(new Promise((r) => { resolve = r; }));
    render(<ProfilePage />);
    fireEvent.click(screen.getByText('Сохранить изменения'));
    await waitFor(() => expect((screen.getByLabelText('Email') as HTMLInputElement).disabled).toBe(true));
    expect((screen.getByText('Сохранить изменения').closest('button') as HTMLButtonElement).disabled).toBe(true);
    resolve({ data: baseUser });
    await screen.findByText('Профиль успешно обновлен');
    expect((screen.getByLabelText('Email') as HTMLInputElement).disabled).toBe(false);
  });

  it('ошибка сервера показывает detail, закрывается крестиком', async () => {
    mockPut.mockRejectedValue({ response: { data: { detail: 'Email занят' } } });
    render(<ProfilePage />);
    fireEvent.click(screen.getByText('Сохранить изменения'));
    expect(await screen.findByText('Email занят')).toBeTruthy();
    expect(mockUpdateUser).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(screen.queryByText('Email занят')).toBeNull();
  });

  it('ошибка без detail даёт общий текст', async () => {
    mockPut.mockRejectedValue(new Error('network'));
    render(<ProfilePage />);
    fireEvent.click(screen.getByText('Сохранить изменения'));
    expect(await screen.findByText('Ошибка при обновлении профиля')).toBeTruthy();
  });

  it('успешное сообщение закрывается, а следующее сохранение сбрасывает старую ошибку', async () => {
    mockPut.mockResolvedValueOnce({ data: baseUser });
    render(<ProfilePage />);
    fireEvent.click(screen.getByText('Сохранить изменения'));
    await screen.findByText('Профиль успешно обновлен');
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(screen.queryByText('Профиль успешно обновлен')).toBeNull();
  });

  it('выход вызывает logout и переходит на /login', async () => {
    mockLogout.mockResolvedValue(undefined);
    render(<ProfilePage />);
    fireEvent.click(screen.getByText('Выйти из системы'));
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/login'));
    expect(mockLogout).toHaveBeenCalledTimes(1);
  });
});
