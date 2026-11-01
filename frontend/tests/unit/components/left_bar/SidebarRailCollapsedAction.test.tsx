import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import SidebarRailCollapsedAction, { SidebarRailShortcutTooltip } from '@/components/left_bar/SidebarRailCollapsedAction';

describe('SidebarRailCollapsedAction', () => {
  const baseProps = {
    variant: 'newChat' as const,
    panelIsLight: false,
    enterIndex: 0,
    icon: <svg data-testid="rail-icon" />,
    tooltipTitle: 'Подсказка',
  };

  it('рисует иконку внутри кнопки и вызывает onClick', () => {
    const onClick = jest.fn();
    render(<SidebarRailCollapsedAction {...baseProps} onClick={onClick} />);
    const button = screen.getByRole('button');
    expect(button).toContainElement(screen.getByTestId('rail-icon'));
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('подсказка (строка) становится aria-label кнопки', () => {
    render(<SidebarRailCollapsedAction {...baseProps} onClick={jest.fn()} />);
    expect(screen.getByLabelText('Подсказка')).toBeInTheDocument();
  });

  it('активное состояние подсвечивает кнопку: светлая и тёмная панель дают разный фон', () => {
    const dark = render(
      <SidebarRailCollapsedAction {...baseProps} variant="search" active onClick={jest.fn()} />,
    );
    const darkBg = getComputedStyle(dark.container.querySelector('[role="button"]') as HTMLElement).backgroundColor;
    dark.unmount();
    const light = render(
      <SidebarRailCollapsedAction {...baseProps} variant="search" active panelIsLight onClick={jest.fn()} />,
    );
    const lightBg = getComputedStyle(light.container.querySelector('[role="button"]') as HTMLElement).backgroundColor;
    expect(darkBg).toBe('rgba(255, 255, 255, 0.15)');
    expect(lightBg).toBe('rgba(0, 0, 0, 0.08)');
  });

  it('без active фон подсветкой не задаётся', () => {
    const { container } = render(<SidebarRailCollapsedAction {...baseProps} onClick={jest.fn()} />);
    const bg = getComputedStyle(container.querySelector('[role="button"]') as HTMLElement).backgroundColor;
    expect(bg).not.toBe('rgba(255, 255, 255, 0.15)');
    expect(bg).not.toBe('rgba(0, 0, 0, 0.08)');
  });
});

describe('SidebarRailShortcutTooltip', () => {
  it('без шортката показывает только заголовок', () => {
    const { container } = render(<SidebarRailShortcutTooltip title="Поиск" />);
    expect(screen.getByText('Поиск')).toBeInTheDocument();
    expect(container.querySelectorAll('.MuiTypography-root')).toHaveLength(1);
  });

  it('с шорткатом показывает заголовок и сочетание клавиш отдельными строками', () => {
    const { container } = render(<SidebarRailShortcutTooltip title="Новый чат" shortcut="Ctrl + Shift + K" />);
    expect(screen.getByText('Новый чат')).toBeInTheDocument();
    expect(screen.getByText('Ctrl + Shift + K')).toBeInTheDocument();
    expect(container.querySelectorAll('.MuiTypography-root')).toHaveLength(2);
  });

  it('пустая строка шортката считается отсутствием шортката', () => {
    const { container } = render(<SidebarRailShortcutTooltip title="Поиск" shortcut="" />);
    expect(container.querySelectorAll('.MuiTypography-root')).toHaveLength(1);
  });
});
