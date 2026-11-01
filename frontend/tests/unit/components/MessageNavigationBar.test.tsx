import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import MessageNavigationBar from '@/components/MessageNavigationBar';

let mockLayout: { open: boolean; hidden: boolean; expandedWidthPx: number };
jest.mock('@/components/right_bar', () => ({
  useRightBarLayout: () => mockLayout,
}));

type Rect = { top: number; bottom: number };
const created: HTMLElement[] = [];

/** Создаёт в документе элемент сообщения с заданным прямоугольником. */
function addMsgEl(index: number | string, rect: Rect) {
  const el = document.createElement('div');
  el.setAttribute('data-message-index', String(index));
  el.getBoundingClientRect = () =>
    ({ top: rect.top, bottom: rect.bottom, height: rect.bottom - rect.top, left: 0, right: 0, width: 0, x: 0, y: rect.top } as DOMRect);
  document.body.appendChild(el);
  created.push(el);
  return el;
}

const msg = (role: string, content: string) => ({ id: content, role, content, timestamp: new Date() } as any);
const MESSAGES = [msg('user', 'Первый вопрос'), msg('assistant', 'Ответ 1'), msg('user', 'Второй вопрос'), msg('assistant', 'Ответ 2')];

const onNavigate = jest.fn();
function setup(messages: any[] = MESSAGES, isDarkMode = false) {
  return render(<MessageNavigationBar messages={messages} isDarkMode={isDarkMode} onNavigate={onNavigate} />);
}
const navBox = (c: HTMLElement) => c.firstChild as HTMLElement;
const dashes = (c: HTMLElement) => Array.from(navBox(c).lastChild!.childNodes) as HTMLElement[];
const openPanel = (c: HTMLElement) => fireEvent.mouseEnter(navBox(c));
const BLUE = 'rgb(33, 150, 243)';

beforeEach(() => {
  jest.clearAllMocks();
  mockLayout = { open: false, hidden: false, expandedWidthPx: 320 };
  (window as any).innerHeight = 800; // середина viewport = 400
});
afterEach(() => {
  created.splice(0).forEach((e) => e.remove());
  document.querySelectorAll('.chat-messages-area').forEach((e) => e.remove());
  jest.useRealTimers();
});

describe('MessageNavigationBar: список и клики', () => {
  it('рисует по одной черте на каждый вопрос пользователя (ответы ассистента пропускаются)', () => {
    const { container } = setup();
    expect(dashes(container)).toHaveLength(2);
  });

  it('без сообщений пользователя - ни одной черты', () => {
    const { container } = setup([msg('assistant', 'a')]);
    expect(dashes(container)).toHaveLength(0);
  });

  it('чёрточки чередуют длину (чётные длиннее)', () => {
    const { container } = setup();
    const [a, b] = dashes(container);
    expect(a).toHaveStyle({ width: '24px' });
    expect(b).toHaveStyle({ width: '16px' });
  });

  it('наведение на область открывает список с краткими текстами; уход мыши закрывает', () => {
    const { container } = setup();
    expect(screen.queryByText('Первый вопрос')).toBeNull();
    openPanel(container);
    expect(screen.getByText('Первый вопрос')).toBeInTheDocument();
    expect(screen.getByText('Второй вопрос')).toBeInTheDocument();
    fireEvent.mouseLeave(navBox(container));
    expect(screen.queryByText('Первый вопрос')).toBeNull();
  });

  it('краткий текст: убирает #, *, ` и обрезает до 50 символов с многоточием', () => {
    const long = '# **' + 'я'.repeat(60) + '**';
    const { container } = setup([msg('user', long), msg('user', '`код`')]);
    openPanel(container);
    expect(screen.getByText('я'.repeat(50) + '...')).toBeInTheDocument();
    expect(screen.getByText('код')).toBeInTheDocument();
  });

  it('клик (mousedown) по элементу списка переходит к сообщению по исходному индексу и закрывает список', () => {
    const { container } = setup();
    openPanel(container);
    fireEvent.mouseDown(screen.getByText('Второй вопрос'));
    expect(onNavigate).toHaveBeenCalledWith(2);
    expect(screen.queryByText('Второй вопрос')).toBeNull();
  });

  it('click по элементу списка тоже вызывает переход', () => {
    const { container } = setup();
    openPanel(container);
    fireEvent.click(screen.getByText('Первый вопрос'));
    expect(onNavigate).toHaveBeenCalledWith(0);
  });

  it('клик по чёрточке (mousedown и click) вызывает переход', () => {
    const { container } = setup();
    const [first, second] = dashes(container);
    fireEvent.mouseDown(first);
    expect(onNavigate).toHaveBeenLastCalledWith(0);
    fireEvent.click(second);
    expect(onNavigate).toHaveBeenLastCalledWith(2);
  });

  it('после клика выбранный пункт подсвечивается активным', () => {
    const { container } = setup();
    fireEvent.mouseDown(dashes(container)[1]);
    expect(dashes(container)[1]).toHaveStyle({ backgroundColor: BLUE });
    expect(dashes(container)[0]).not.toHaveStyle({ backgroundColor: BLUE });
  });

  it('тёмная тема не ломает рендер списка', () => {
    const { container } = setup(MESSAGES, true);
    openPanel(container);
    expect(screen.getByText('Первый вопрос')).toBeInTheDocument();
  });
});

describe('MessageNavigationBar: положение относительно правой панели', () => {
  it('панель скрыта: отступ 50px', () => {
    mockLayout = { open: true, hidden: true, expandedWidthPx: 320 };
    const { container } = setup();
    expect(navBox(container)).toHaveStyle({ right: '50px' });
  });

  it('панель открыта: ширина панели + 16px', () => {
    mockLayout = { open: true, hidden: false, expandedWidthPx: 320 };
    const { container } = setup();
    expect(navBox(container)).toHaveStyle({ right: '336px' });
  });

  it('панель свёрнута: 64 + 16px', () => {
    const { container } = setup();
    expect(navBox(container)).toHaveStyle({ right: '80px' });
  });
});

describe('MessageNavigationBar: активное сообщение при прокрутке', () => {
  it('активна секция вопроса, в которую попадает середина экрана (длинный ответ входит в секцию)', () => {
    addMsgEl(0, { top: 0, bottom: 100 });
    addMsgEl(1, { top: 100, bottom: 700 });
    addMsgEl(2, { top: 750, bottom: 800 });
    addMsgEl(3, { top: 800, bottom: 900 });
    const { container } = setup();
    expect(dashes(container)[0]).toHaveStyle({ backgroundColor: BLUE });
    expect(dashes(container)[1]).not.toHaveStyle({ backgroundColor: BLUE });
  });

  it('вторая секция активна, когда середина экрана лежит между вопросом и концом ответа', () => {
    addMsgEl(0, { top: -900, bottom: -800 });
    addMsgEl(1, { top: -800, bottom: -100 });
    addMsgEl(2, { top: 300, bottom: 350 });
    addMsgEl(3, { top: 350, bottom: 600 });
    const { container } = setup();
    expect(dashes(container)[1]).toHaveStyle({ backgroundColor: BLUE });
  });

  it('если середина экрана вне секций, выбирается ближайшая по расстоянию; секция без DOM-элемента пропускается', () => {
    addMsgEl(0, { top: 0, bottom: 50 }); // секция 0..50 (ответа нет в DOM), середина 25, расстояние 375
    addMsgEl(2, { top: 300, bottom: 350 }); // секция 300..350, середина 325, расстояние 75
    const { container } = setup([...MESSAGES, msg('user', 'Третий без DOM')]);
    expect(dashes(container)[1]).toHaveStyle({ backgroundColor: BLUE });
  });

  it('следующий вопрос без DOM-элемента: нижняя граница секции берётся по ответам', () => {
    addMsgEl(0, { top: 300, bottom: 350 });
    addMsgEl(1, { top: 350, bottom: 500 });
    const { container } = setup();
    expect(dashes(container)[0]).toHaveStyle({ backgroundColor: BLUE });
  });

  it('прокрутка контейнера .chat-messages-area пересчитывает активное сообщение', () => {
    const area = document.createElement('div');
    area.className = 'chat-messages-area';
    document.body.appendChild(area);
    let rect2: Rect = { top: 900, bottom: 950 };
    const u0 = addMsgEl(0, { top: 0, bottom: 50 });
    const u2 = addMsgEl(2, rect2);
    u2.getBoundingClientRect = () => ({ top: rect2.top, bottom: rect2.bottom, height: 50 } as DOMRect);
    const { container } = setup();
    expect(u0).toBeTruthy();
    // сейчас ближайшая к середине (400) - секция 0 (расстояние 375 против 525)
    expect(dashes(container)[0]).toHaveStyle({ backgroundColor: BLUE });
    rect2 = { top: 380, bottom: 430 };
    act(() => {
      area.dispatchEvent(new Event('scroll'));
    });
    expect(dashes(container)[1]).toHaveStyle({ backgroundColor: BLUE });
  });

  it('прокрутка окна (capture) тоже пересчитывает', () => {
    let rect2: Rect = { top: 900, bottom: 950 };
    addMsgEl(0, { top: 0, bottom: 50 });
    const u2 = addMsgEl(2, rect2);
    u2.getBoundingClientRect = () => ({ top: rect2.top, bottom: rect2.bottom, height: 50 } as DOMRect);
    const { container } = setup();
    rect2 = { top: 390, bottom: 440 };
    act(() => {
      window.dispatchEvent(new Event('scroll'));
    });
    expect(dashes(container)[1]).toHaveStyle({ backgroundColor: BLUE });
  });

  it('после размонтирования слушатели снимаются (контейнер и окно)', () => {
    const area = document.createElement('div');
    area.className = 'chat-messages-area';
    document.body.appendChild(area);
    const addSpy = jest.spyOn(area, 'removeEventListener');
    const winSpy = jest.spyOn(window, 'removeEventListener');
    const { unmount } = setup();
    unmount();
    expect(addSpy).toHaveBeenCalledWith('scroll', expect.any(Function));
    expect(winSpy).toHaveBeenCalledWith('scroll', expect.any(Function), true);
    winSpy.mockRestore();
  });

  describe('резервный путь (нет DOM-элементов вопросов)', () => {
    it('ближайший к середине элемент ответа сопоставляется с вопросом выше него', () => {
      addMsgEl(1, { top: 350, bottom: 450 });
      const { container } = setup();
      expect(dashes(container)[0]).toHaveStyle({ backgroundColor: BLUE });
    });

    it('элементы вне экрана и с некорректным индексом игнорируются', () => {
      addMsgEl(1, { top: 900, bottom: 1000 }); // ниже экрана
      addMsgEl(3, { top: -200, bottom: -10 }); // выше экрана
      addMsgEl('', { top: 350, bottom: 450 }); // нет индекса -> -1
      const { container } = setup();
      dashes(container).forEach((d) => expect(d).not.toHaveStyle({ backgroundColor: BLUE }));
    });

    it('из двух видимых ответов выбирается ближайший к середине', () => {
      addMsgEl(1, { top: 0, bottom: 100 });
      addMsgEl(3, { top: 380, bottom: 420 });
      const { container } = setup();
      expect(dashes(container)[1]).toHaveStyle({ backgroundColor: BLUE });
    });

    it('индекс вне диапазона сообщений - активного нет', () => {
      addMsgEl(9, { top: 350, bottom: 450 });
      const { container } = setup();
      dashes(container).forEach((d) => expect(d).not.toHaveStyle({ backgroundColor: BLUE }));
    });

    it('ответ ассистента без вопроса выше - активного нет', () => {
      addMsgEl(0, { top: 350, bottom: 450 });
      const { container } = setup([msg('assistant', 'приветствие'), msg('user', 'вопрос')]);
      dashes(container).forEach((d) => expect(d).not.toHaveStyle({ backgroundColor: BLUE }));
    });

    it('элемент самого вопроса в резервном пути сопоставляется сам с собой', () => {
      // messages: сообщение 0 - ассистент (секций нет у него), вопрос 1 имеет DOM, но поиск секций его находит
      addMsgEl(1, { top: 350, bottom: 450 });
      const { container } = setup([msg('assistant', 'a'), msg('user', 'q')]);
      expect(dashes(container)[0]).toHaveStyle({ backgroundColor: BLUE });
    });
  });
});

describe('MessageNavigationBar: закрепление после клика', () => {
  it('в течение 1.2 с прокрутка не меняет закреплённый пункт, потом пересчитывается', () => {
    jest.useFakeTimers('modern');
    jest.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    let rect0: Rect = { top: 380, bottom: 430 };
    const u0 = addMsgEl(0, rect0);
    u0.getBoundingClientRect = () => ({ top: rect0.top, bottom: rect0.bottom, height: 50 } as DOMRect);
    addMsgEl(2, { top: 2000, bottom: 2050 });
    const { container } = setup();
    expect(dashes(container)[0]).toHaveStyle({ backgroundColor: BLUE });
    fireEvent.mouseDown(dashes(container)[1]); // закрепили второй
    rect0 = { top: 390, bottom: 440 };
    act(() => {
      window.dispatchEvent(new Event('scroll'));
    });
    expect(dashes(container)[1]).toHaveStyle({ backgroundColor: BLUE });
    act(() => {
      jest.setSystemTime(new Date('2026-01-01T00:00:02Z'));
      window.dispatchEvent(new Event('scroll'));
    });
    expect(dashes(container)[0]).toHaveStyle({ backgroundColor: BLUE });
  });
});

describe('MessageNavigationBar: всплывающий полный текст', () => {
  const longText = 'Очень длинный вопрос\n' + 'слово '.repeat(30);

  it('через 2 секунды наведения на пункт показывается полный текст; до этого - нет', () => {
    jest.useFakeTimers('modern');
    const { container } = setup([msg('user', longText), msg('user', 'короткий')]);
    openPanel(container);
    const item = screen.getByText('короткий');
    fireEvent.mouseEnter(item.parentElement!);
    act(() => {
      jest.advanceTimersByTime(1999);
    });
    expect(document.querySelectorAll('.MuiPaper-root')).toHaveLength(1);
    act(() => {
      jest.advanceTimersByTime(2);
    });
    expect(document.querySelectorAll('.MuiPaper-root')).toHaveLength(2);
    expect(screen.getAllByText('короткий')).toHaveLength(2); // краткий в списке + полный в подсказке
  });

  it('при повторном наведении на другой пункт прежний таймер отменяется', () => {
    jest.useFakeTimers('modern');
    const { container } = setup([msg('user', 'первый'), msg('user', 'второй')]);
    openPanel(container);
    fireEvent.mouseEnter(screen.getByText('первый').parentElement!);
    act(() => {
      jest.advanceTimersByTime(1500);
    });
    fireEvent.mouseEnter(screen.getByText('второй').parentElement!);
    act(() => {
      jest.advanceTimersByTime(1000);
    });
    expect(document.querySelectorAll('.MuiPaper-root')).toHaveLength(1); // 2 с для «второй» ещё не прошло
    act(() => {
      jest.advanceTimersByTime(1100);
    });
    expect(screen.getAllByText('второй')).toHaveLength(2);
    expect(screen.getAllByText('первый')).toHaveLength(1);
  });

  it('уход курсора с пункта отменяет показ подсказки', () => {
    jest.useFakeTimers('modern');
    const { container } = setup();
    openPanel(container);
    const item = screen.getByText('Первый вопрос').parentElement!;
    fireEvent.mouseEnter(item);
    fireEvent.mouseLeave(item, { relatedTarget: item.parentElement });
    act(() => {
      jest.advanceTimersByTime(3000);
    });
    expect(document.querySelectorAll('.MuiPaper-root')).toHaveLength(1);
  });

  it('уход курсора с пункта без активного таймера безопасен', () => {
    const { container } = setup();
    openPanel(container);
    const item = screen.getByText('Первый вопрос').parentElement!;
    fireEvent.mouseLeave(item, { relatedTarget: item.parentElement });
    expect(screen.getByText('Первый вопрос')).toBeInTheDocument();
  });

  it('уход мыши с самой подсказки скрывает её', () => {
    jest.useFakeTimers('modern');
    const { container } = setup([msg('user', 'вопрос')]);
    openPanel(container);
    fireEvent.mouseEnter(screen.getByText('вопрос').parentElement!);
    act(() => {
      jest.advanceTimersByTime(2100);
    });
    const papers = document.querySelectorAll('.MuiPaper-root');
    expect(papers).toHaveLength(2);
    fireEvent.mouseLeave(papers[1]);
    expect(document.querySelectorAll('.MuiPaper-root')).toHaveLength(1);
  });

  it('уход мыши с области навигации сбрасывает таймер и подсказку', () => {
    jest.useFakeTimers('modern');
    const { container } = setup([msg('user', 'вопрос')]);
    openPanel(container);
    fireEvent.mouseEnter(screen.getByText('вопрос').parentElement!);
    fireEvent.mouseLeave(navBox(container));
    act(() => {
      jest.advanceTimersByTime(3000);
    });
    expect(document.querySelectorAll('.MuiPaper-root')).toHaveLength(0);
  });

  it('при размонтировании таймер подсказки очищается', () => {
    jest.useFakeTimers('modern');
    const clear = jest.spyOn(window, 'clearTimeout');
    const { container, unmount } = setup([msg('user', 'вопрос')]);
    openPanel(container);
    fireEvent.mouseEnter(screen.getByText('вопрос').parentElement!);
    unmount();
    expect(clear).toHaveBeenCalled();
    clear.mockRestore();
  });

  it('наведение подсвечивает пункт и соответствующую чёрточку', () => {
    const { container } = setup();
    openPanel(container);
    fireEvent.mouseEnter(screen.getByText('Второй вопрос').parentElement!);
    expect(dashes(container)[1]).toHaveStyle({ backgroundColor: 'rgb(100, 181, 246)' });
    const item = screen.getByText('Второй вопрос').parentElement!;
    fireEvent.mouseLeave(item, { relatedTarget: item.parentElement });
  });
});
