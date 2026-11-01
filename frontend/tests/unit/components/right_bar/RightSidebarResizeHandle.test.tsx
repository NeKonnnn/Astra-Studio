import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import RightSidebarResizeHandle from '@/components/right_bar/RightSidebarResizeHandle';

function setup(props: Partial<React.ComponentProps<typeof RightSidebarResizeHandle>> = {}, paperWidth = 400) {
  const onResize = jest.fn();
  const onResizeEnd = jest.fn();
  const onResizeStart = jest.fn();
  const utils = render(
    <div className="astra-right-rail" data-testid="paper">
      <RightSidebarResizeHandle
        onResize={onResize}
        onResizeEnd={onResizeEnd}
        onResizeStart={onResizeStart}
        {...props}
      />
    </div>,
  );
  const paper = screen.getByTestId('paper');
  paper.getBoundingClientRect = () => ({ width: paperWidth } as DOMRect);
  return { ...utils, onResize, onResizeEnd, onResizeStart, handle: screen.getByRole('separator') };
}

describe('RightSidebarResizeHandle', () => {
  afterEach(() => {
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });

  it('имеет доступную роль separator', () => {
    const { handle } = setup();
    expect(handle.getAttribute('aria-label')).toBe('Изменить ширину панели');
    expect(handle.getAttribute('aria-orientation')).toBe('vertical');
  });

  it('перетаскивание влево увеличивает ширину и завершается onResizeEnd', () => {
    const { handle, onResize, onResizeEnd, onResizeStart } = setup();
    fireEvent.mouseDown(handle, { button: 0, clientX: 500 });
    expect(onResizeStart).toHaveBeenCalledTimes(1);
    expect(document.body.style.cursor).toBe('col-resize');
    expect(document.body.style.userSelect).toBe('none');

    fireEvent.mouseMove(document, { clientX: 450 });
    expect(onResize).toHaveBeenLastCalledWith(450);

    fireEvent.mouseUp(document, { clientX: 400 });
    expect(onResizeEnd).toHaveBeenCalledWith(500);
    expect(document.body.style.cursor).toBe('');
    expect(document.body.style.userSelect).toBe('');

    // после отпускания движение игнорируется
    onResize.mockClear();
    fireEvent.mouseMove(document, { clientX: 100 });
    expect(onResize).not.toHaveBeenCalled();
  });

  it('ширина зажимается в допустимый диапазон 240..720', () => {
    const { handle, onResize, onResizeEnd } = setup();
    fireEvent.mouseDown(handle, { button: 0, clientX: 500 });
    fireEvent.mouseMove(document, { clientX: 5000 });
    expect(onResize).toHaveBeenLastCalledWith(240);
    fireEvent.mouseMove(document, { clientX: -5000 });
    expect(onResize).toHaveBeenLastCalledWith(720);
    fireEvent.mouseUp(document, { clientX: -5000 });
    expect(onResizeEnd).toHaveBeenCalledWith(720);
  });

  it('восстанавливает прежние стили body после перетаскивания', () => {
    document.body.style.cursor = 'help';
    document.body.style.userSelect = 'text';
    const { handle } = setup();
    fireEvent.mouseDown(handle, { button: 0, clientX: 100 });
    fireEvent.mouseUp(document, { clientX: 100 });
    expect(document.body.style.cursor).toBe('help');
    expect(document.body.style.userSelect).toBe('text');
  });

  it('disabled и не-левая кнопка не запускают перетаскивание', () => {
    const a = setup({ disabled: true });
    fireEvent.mouseDown(a.handle, { button: 0, clientX: 10 });
    expect(a.onResizeStart).not.toHaveBeenCalled();
    a.unmount();

    const b = setup();
    fireEvent.mouseDown(b.handle, { button: 2, clientX: 10 });
    expect(b.onResizeStart).not.toHaveBeenCalled();
  });

  it('нулевая ширина панели — перетаскивание не начинается', () => {
    const { handle, onResizeStart } = setup({}, 0);
    fireEvent.mouseDown(handle, { button: 0, clientX: 10 });
    expect(onResizeStart).not.toHaveBeenCalled();
    expect(document.body.style.cursor).toBe('');
  });

  it('без родительской панели ширина равна 0 и перетаскивания нет', () => {
    const onResizeStart = jest.fn();
    render(<RightSidebarResizeHandle onResize={jest.fn()} onResizeStart={onResizeStart} />);
    fireEvent.mouseDown(screen.getByRole('separator'), { button: 0, clientX: 10 });
    expect(onResizeStart).not.toHaveBeenCalled();
  });

  it('находит панель по .MuiDrawer-paper, колбэки onResizeEnd/onResizeStart необязательны', () => {
    const onResize = jest.fn();
    render(
      <div className="MuiDrawer-paper" data-testid="paper2">
        <RightSidebarResizeHandle onResize={onResize} />
      </div>,
    );
    screen.getByTestId('paper2').getBoundingClientRect = () => ({ width: 300 } as DOMRect);
    const h = screen.getByRole('separator');
    fireEvent.mouseDown(h, { button: 0, clientX: 100 });
    fireEvent.mouseMove(document, { clientX: 50 });
    expect(onResize).toHaveBeenCalledWith(350);
    fireEvent.mouseUp(document, { clientX: 50 });
  });
});
