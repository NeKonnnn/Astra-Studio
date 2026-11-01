import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import CodeSelectionMenu from '@/components/CodeSelectionMenu';

function anchor(rect = { bottom: 100, left: 40, width: 60 }) {
  const el = document.createElement('div');
  el.getBoundingClientRect = () => ({ ...rect, top: 0, right: 0, height: 0 } as DOMRect);
  document.body.appendChild(el);
  return el;
}

function setup(over: Record<string, any> = {}) {
  const props = {
    anchorEl: anchor(),
    open: true,
    onClose: jest.fn(),
    selectedText: 'const a = 1;',
    onCopy: jest.fn(),
    onAsk: jest.fn(),
    onExplain: jest.fn(),
    onTranslate: jest.fn(),
    ...over,
  };
  const utils = render(<CodeSelectionMenu {...props} />);
  return { props, ...utils };
}

function menu(): HTMLElement {
  return document.querySelector('[data-menu="code-selection"]') as HTMLElement;
}

describe('CodeSelectionMenu', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('закрытое меню ничего не рендерит', () => {
    const { container } = setup({ open: false });
    expect(container).toBeEmptyDOMElement();
  });

  it('без anchorEl и position меню не показывается', () => {
    const { container } = setup({ anchorEl: null });
    expect(container).toBeEmptyDOMElement();
  });

  it('позиция вычисляется от якоря: под ним, по центру', () => {
    setup();
    const st = getComputedStyle(menu());
    expect(st.top).toBe('108px');
    expect(st.left).toBe('70px');
    expect(st.position).toBe('fixed');
  });

  it('явный position приоритетнее якоря и работает без anchorEl', () => {
    setup({ anchorEl: null, position: { top: 5, left: 7 } });
    expect(getComputedStyle(menu()).top).toBe('5px');
    expect(getComputedStyle(menu()).left).toBe('7px');
  });

  it('показывает действия: Копировать, Спросить, Объяснить, Перевести', () => {
    setup();
    ['Копировать', 'Спросить', 'Объяснить', 'Перевести'].forEach((t) =>
      expect(screen.getByText(t)).toBeInTheDocument(),
    );
  });

  it('«Копировать» вызывает onCopy, затем onClose', () => {
    const { props } = setup();
    fireEvent.click(screen.getByText('Копировать'));
    expect(props.onCopy).toHaveBeenCalledTimes(1);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('«Объяснить» формирует промпт с выделенным кодом в fence', () => {
    const { props } = setup();
    fireEvent.click(screen.getByText('Объяснить'));
    expect(props.onExplain).toHaveBeenCalledWith(
      'Объясни мне эту секцию более подробно\n\n```\nconst a = 1;\n```',
    );
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  describe('«Спросить»', () => {
    function openAsk() {
      const s = setup();
      fireEvent.click(screen.getByText('Спросить'));
      return s;
    }

    it('заменяет панель полем ввода и ставит на него фокус через 10 мс', () => {
      openAsk();
      const input = screen.getByPlaceholderText('Введите ваш вопрос...');
      expect(screen.queryByText('Копировать')).toBeNull();
      act(() => {
        jest.advanceTimersByTime(10);
      });
      expect(input).toHaveFocus();
    });

    it('кнопка отправки неактивна для пустого/пробельного вопроса', () => {
      openAsk();
      const buttons = screen.getAllByRole('button');
      const send = buttons[0];
      expect(send).toBeDisabled();
      fireEvent.change(screen.getByPlaceholderText('Введите ваш вопрос...'), { target: { value: '   ' } });
      expect(send).toBeDisabled();
      fireEvent.change(screen.getByPlaceholderText('Введите ваш вопрос...'), { target: { value: 'что это?' } });
      expect(send).toBeEnabled();
    });

    it('кнопка отправки: промпт = код в fence + обрезанный вопрос; закрывает меню', () => {
      const { props } = openAsk();
      fireEvent.change(screen.getByPlaceholderText('Введите ваш вопрос...'), { target: { value: '  что делает?  ' } });
      fireEvent.click(screen.getAllByRole('button')[0]);
      expect(props.onAsk).toHaveBeenCalledWith('```\nconst a = 1;\n```\n\nчто делает?');
      expect(props.onClose).toHaveBeenCalledTimes(1);
    });

    it('Enter отправляет вопрос, Shift+Enter — нет', () => {
      const { props } = openAsk();
      const input = screen.getByPlaceholderText('Введите ваш вопрос...');
      fireEvent.change(input, { target: { value: 'вопрос' } });
      fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
      expect(props.onAsk).not.toHaveBeenCalled();
      fireEvent.keyDown(input, { key: 'Enter' });
      expect(props.onAsk).toHaveBeenCalledTimes(1);
    });

    it('Enter с пустым вопросом ничего не отправляет', () => {
      const { props } = openAsk();
      fireEvent.keyDown(screen.getByPlaceholderText('Введите ваш вопрос...'), { key: 'Enter' });
      expect(props.onAsk).not.toHaveBeenCalled();
      expect(props.onClose).not.toHaveBeenCalled();
    });

    it('другие клавиши не вызывают отправку и отмену', () => {
      const { props } = openAsk();
      fireEvent.keyDown(screen.getByPlaceholderText('Введите ваш вопрос...'), { key: 'a' });
      expect(props.onAsk).not.toHaveBeenCalled();
      expect(screen.getByPlaceholderText('Введите ваш вопрос...')).toBeInTheDocument();
    });

    it('кнопка «Закрыть» возвращает к панели действий и очищает вопрос', () => {
      openAsk();
      fireEvent.change(screen.getByPlaceholderText('Введите ваш вопрос...'), { target: { value: 'черновик' } });
      fireEvent.click(screen.getAllByRole('button')[1]);
      expect(screen.getByText('Копировать')).toBeInTheDocument();
      fireEvent.click(screen.getByText('Спросить'));
      expect(screen.getByPlaceholderText('Введите ваш вопрос...')).toHaveValue('');
    });

    it('Escape в режиме вопроса отменяет ввод (возврат к панели), но не закрывает меню', () => {
      const { props } = openAsk();
      fireEvent.keyDown(screen.getByPlaceholderText('Введите ваш вопрос...'), { key: 'Escape' });
      expect(screen.getByText('Копировать')).toBeInTheDocument();
      expect(props.onClose).not.toHaveBeenCalled();
    });
  });

  it('Escape на панели действий закрывает меню и не всплывает дальше', () => {
    const { props } = setup();
    const bubbled = jest.fn();
    document.body.addEventListener('keydown', bubbled);
    fireEvent.keyDown(document.body, { key: 'Escape' });
    document.body.removeEventListener('keydown', bubbled);
    expect(props.onClose).toHaveBeenCalledTimes(1);
    expect(bubbled).not.toHaveBeenCalled();
  });

  it('другие клавиши и закрытое состояние не обрабатываются глобальным слушателем', () => {
    const { props, rerender } = setup();
    fireEvent.keyDown(document.body, { key: 'a' });
    expect(props.onClose).not.toHaveBeenCalled();
    rerender(<CodeSelectionMenu {...props} open={false} />);
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it('при размонтировании слушатель Escape снимается', () => {
    const { props, unmount } = setup();
    unmount();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(props.onClose).not.toHaveBeenCalled();
  });

  describe('перевод', () => {
    it('клик по «Перевести» открывает список из 10 языков, повторный клик закрывает', async () => {
      setup();
      fireEvent.click(screen.getByText('Перевести'));
      expect(screen.getAllByRole('menuitem')).toHaveLength(10);
      expect(screen.getByText('Russian (Russia)')).toBeInTheDocument();
      fireEvent.click(screen.getByText('Перевести'));
      await act(async () => {
        jest.advanceTimersByTime(500);
      });
      expect(screen.queryAllByRole('menuitem')).toHaveLength(0);
    });

    it('выбор языка: промпт с названием языка и выделенным текстом, код языка в onTranslate, меню закрыто', () => {
      const { props } = setup();
      fireEvent.click(screen.getByText('Перевести'));
      fireEvent.click(screen.getByText('German (Germany)'));
      expect(props.onTranslate).toHaveBeenCalledWith(
        'Переведите следующий текст на German (Germany):\n\n"const a = 1;"',
        'de-DE',
      );
      expect(props.onClose).toHaveBeenCalledTimes(1);
    });

    it('по умолчанию выбран русский, после выбора пункт помечается selected', () => {
      setup();
      fireEvent.click(screen.getByText('Перевести'));
      expect(screen.getByText('Russian (Russia)').closest('li')).toHaveClass('Mui-selected');
      fireEvent.click(screen.getByText('Japanese (Japan)'));
      fireEvent.click(screen.getByText('Перевести'));
      expect(screen.getByText('Japanese (Japan)').closest('li')).toHaveClass('Mui-selected');
      expect(screen.getByText('Russian (Russia)').closest('li')).not.toHaveClass('Mui-selected');
    });

    it('Escape при открытом списке перехватывается глобальным слушателем и закрывает всё меню через onClose', () => {
      const { props } = setup();
      fireEvent.click(screen.getByText('Перевести'));
      fireEvent.keyDown(screen.getAllByRole('menuitem')[0], { key: 'Escape' });
      expect(props.onClose).toHaveBeenCalledTimes(1);
    });

    it('Tab в списке языков закрывает только список (меню остаётся)', async () => {
      const { props } = setup();
      fireEvent.click(screen.getByText('Перевести'));
      fireEvent.keyDown(screen.getAllByRole('menuitem')[0], { key: 'Tab' });
      await act(async () => {
        jest.advanceTimersByTime(500);
      });
      expect(screen.queryAllByRole('menuitem')).toHaveLength(0);
      expect(screen.getByText('Копировать')).toBeInTheDocument();
      expect(props.onClose).not.toHaveBeenCalled();
    });

    it('клик по backdrop не закрывает список', () => {
      setup();
      fireEvent.click(screen.getByText('Перевести'));
      const backdrop = document.querySelector('.MuiBackdrop-root') as HTMLElement;
      fireEvent.click(backdrop);
      expect(screen.getAllByRole('menuitem')).toHaveLength(10);
    });
  });
});
