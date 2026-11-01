import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import GalleryHubPage from '@/pages/GalleryHubPage';

const mockNavigate = jest.fn();
const mockSetParams = jest.fn();
let mockParams = new URLSearchParams();

jest.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  useSearchParams: () => [mockParams, mockSetParams],
}));
jest.mock('@/pages/AgentGalleryPage', () => ({
  AgentGalleryContent: ({ embedded }: { embedded?: boolean }) => (
    <div data-testid="agents" data-embedded={String(embedded)} />
  ),
}));
jest.mock('@/pages/SkillsPage', () => ({
  SkillsGalleryContent: () => <div data-testid="skills" />,
}));
jest.mock('@/pages/PluginGalleryPage', () => ({
  PluginGalleryContent: () => <div data-testid="plugins" />,
}));

describe('GalleryHubPage', () => {
  beforeEach(() => {
    mockNavigate.mockClear();
    mockSetParams.mockClear();
    mockParams = new URLSearchParams();
    localStorage.clear();
  });

  it('по умолчанию показывает вкладку агентов во встроенном режиме', () => {
    render(<GalleryHubPage />);
    expect(screen.getByTestId('agents').getAttribute('data-embedded')).toBe('true');
    expect(screen.queryByTestId('skills')).toBeNull();
    expect(screen.queryByTestId('plugins')).toBeNull();
  });

  it.each([
    ['skills', 'skills'],
    ['plugins', 'plugins'],
  ])('?tab=%s показывает соответствующую вкладку', (tab, id) => {
    mockParams = new URLSearchParams(`tab=${tab}`);
    render(<GalleryHubPage />);
    expect(screen.getByTestId(id)).toBeTruthy();
    expect(screen.queryByTestId('agents')).toBeNull();
  });

  it('смена вкладки пишет tab, сбрасывает create и заменяет историю', () => {
    mockParams = new URLSearchParams('tab=skills&create=1&x=y');
    render(<GalleryHubPage />);
    fireEvent.click(screen.getByText('Плагины'));
    expect(mockSetParams).toHaveBeenCalledTimes(1);
    const [params, opts] = mockSetParams.mock.calls[0];
    expect(params.get('tab')).toBe('plugins');
    expect(params.has('create')).toBe(false);
    expect(params.get('x')).toBe('y');
    expect(opts).toEqual({ replace: true });
  });

  it('кнопка «К чату» ведёт на корень', () => {
    render(<GalleryHubPage />);
    fireEvent.click(screen.getByText('К чату'));
    expect(mockNavigate).toHaveBeenCalledWith('/');
  });
});
