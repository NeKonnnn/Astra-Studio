import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import GalleryNavButton from '@/components/right_bar/GalleryNavButton';

const mockNavigate = jest.fn();
let mockPath = '/chat';

jest.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: mockPath }),
}));

describe('GalleryNavButton', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockPath = '/chat';
  });

  it('expanded: показывает подпись и переходит на /gallery', () => {
    render(<GalleryNavButton variant="expanded" isDarkMode />);
    fireEvent.click(screen.getByRole('button', { name: /Галерея/ }));
    expect(mockNavigate).toHaveBeenCalledWith('/gallery');
  });

  it('collapsed: только иконка с подсказкой, клик ведёт на /gallery', () => {
    render(<GalleryNavButton variant="collapsed" isDarkMode={false} panelIsLight />);
    expect(screen.queryByText('Галерея')).toBeNull();
    fireEvent.click(screen.getByRole('button'));
    expect(mockNavigate).toHaveBeenCalledWith('/gallery');
  });

  it('на странице /gallery кнопка помечена выбранной (expanded и collapsed)', () => {
    mockPath = '/gallery';
    const a = render(<GalleryNavButton variant="expanded" isDarkMode panelIsLight />);
    expect(screen.getByRole('button').className).toContain('Mui-selected');
    a.unmount();
    render(<GalleryNavButton variant="collapsed" isDarkMode />);
    expect(screen.getByRole('button').className).toContain('Mui-selected');
  });

  it('на другой странице кнопка не выбрана', () => {
    render(<GalleryNavButton variant="expanded" isDarkMode />);
    expect(screen.getByRole('button').className).not.toContain('Mui-selected');
  });
});
