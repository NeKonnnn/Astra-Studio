import React from 'react';
import { render, screen } from '@testing-library/react';
import { RightBarProvider, useRightBarLayout, RightBarLayout } from '@/components/right_bar/RightBarContext';

function Probe() {
  const l = useRightBarLayout();
  return (
    <div data-testid="probe">
      {JSON.stringify({ open: l.open, hidden: l.hidden, w: l.expandedWidthPx, pinned: l.widthPinned })}
    </div>
  );
}

const makeLayout = (over: Partial<RightBarLayout> = {}): RightBarLayout => ({
  open: false,
  hidden: true,
  expandedWidthPx: 500,
  widthPinned: true,
  setOpen: jest.fn(),
  setHidden: jest.fn(),
  setExpandedWidthPx: jest.fn(),
  setWidthPinned: jest.fn(),
  ...over,
});

describe('RightBarContext', () => {
  it('без провайдера отдаёт безопасные значения по умолчанию', () => {
    render(<Probe />);
    expect(JSON.parse(screen.getByTestId('probe').textContent!)).toEqual({
      open: true,
      hidden: false,
      w: 240,
      pinned: false,
    });
  });

  it('дефолтные сеттеры без провайдера — безопасные no-op', () => {
    let layout!: RightBarLayout;
    function Grab() {
      layout = useRightBarLayout();
      return null;
    }
    render(<Grab />);
    expect(layout.setOpen(true)).toBeUndefined();
    expect(layout.setHidden(true)).toBeUndefined();
    expect(layout.setExpandedWidthPx(300)).toBeUndefined();
    expect(layout.setWidthPinned(true)).toBeUndefined();
  });

  it('провайдер передаёт значения потребителю', () => {
    render(
      <RightBarProvider value={makeLayout()}>
        <Probe />
      </RightBarProvider>,
    );
    expect(JSON.parse(screen.getByTestId('probe').textContent!)).toEqual({
      open: false,
      hidden: true,
      w: 500,
      pinned: true,
    });
  });

  it('при смене значения провайдер обновляет потребителей', () => {
    const base = makeLayout();
    const { rerender } = render(
      <RightBarProvider value={base}>
        <Probe />
      </RightBarProvider>,
    );
    rerender(
      <RightBarProvider value={{ ...base, expandedWidthPx: 640 }}>
        <Probe />
      </RightBarProvider>,
    );
    expect(JSON.parse(screen.getByTestId('probe').textContent!).w).toBe(640);
  });
});
