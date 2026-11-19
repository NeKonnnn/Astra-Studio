import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';

jest.mock('@/config/api', () => ({
  authFetch: (...a: any[]) => (global as any).__aboutAuthFetch(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
}));

import AboutSettings from '@/components/settings/AboutSettings';

beforeEach(() => {
  (global as any).__aboutAuthFetch = jest.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      local_version: '1.0.0',
      remote_version: '1.0.0',
      update_available: false,
      enabled: true,
      can_apply: false,
    }),
    text: async () => '',
  });
});

describe('AboutSettings', () => {
  it('renders updates block, technical requirements and license — without app info card', async () => {
    render(<AboutSettings />);

    await waitFor(() => {
      expect(screen.getByText('Разработчик Константин Некрасов')).toBeTruthy();
    });

    expect(screen.getByText(/GitHub версия/)).toBeTruthy();
    expect(screen.queryByText('Информация о приложении')).toBeNull();

    expect(screen.getByText('Техническая информация')).toBeTruthy();
    expect(screen.getByText('Платформы')).toBeTruthy();
    expect(screen.getByText(/Windows 10\/11 \(Docker Desktop\)/)).toBeTruthy();
    expect(screen.getByText('Диск')).toBeTruthy();
    expect(screen.getByText('Оперативная память (RAM)')).toBeTruthy();
    expect(screen.getByText('CPU')).toBeTruthy();
    expect(screen.getByText('GPU (опционально)')).toBeTruthy();

    expect(screen.getByText('MIT License')).toBeTruthy();
    expect(screen.queryByText('FastAPI + Python')).toBeNull();
    expect(screen.queryByText('React + TypeScript')).toBeNull();
    expect(screen.queryByText('Socket.IO')).toBeNull();
  });

  it('does not show browser detection or export/refresh controls', async () => {
    render(<AboutSettings />);

    await waitFor(() => {
      expect(screen.getByText('Разработчик Константин Некрасов')).toBeTruthy();
    });

    expect(screen.queryByRole('button', { name: /Обновить информацию/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Экспорт информации/ })).toBeNull();
    expect(screen.queryByText('Системные ресурсы')).toBeNull();
  });
});
