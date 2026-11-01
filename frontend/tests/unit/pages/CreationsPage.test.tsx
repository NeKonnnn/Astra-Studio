import React from 'react';
import { render, screen } from '@testing-library/react';
import { createTheme, ThemeProvider } from '@mui/material';
import CreationsPage from '@/pages/CreationsPage';

jest.mock('@/components/CreationsGallery', () => ({
  __esModule: true,
  default: ({ isDarkMode }: { isDarkMode: boolean }) => (
    <div data-testid="gallery" data-dark={String(isDarkMode)} />
  ),
}));

describe('CreationsPage', () => {
  beforeEach(() => localStorage.clear());

  it('в тёмной теме передаёт isDarkMode=true и красит зону в тёмный цвет', () => {
    const { container } = render(
      <ThemeProvider theme={createTheme({ palette: { mode: 'dark' } })}>
        <CreationsPage />
      </ThemeProvider>,
    );
    expect(screen.getByTestId('gallery').getAttribute('data-dark')).toBe('true');
    expect(getComputedStyle(container.firstChild as Element).backgroundColor).toBe('rgb(35, 35, 38)');
  });

  it('в светлой теме передаёт isDarkMode=false', () => {
    const { container } = render(
      <ThemeProvider theme={createTheme({ palette: { mode: 'light' } })}>
        <CreationsPage />
      </ThemeProvider>,
    );
    expect(screen.getByTestId('gallery').getAttribute('data-dark')).toBe('false');
    expect(getComputedStyle(container.firstChild as Element).backgroundColor).toBe('rgb(250, 250, 250)');
  });
});
