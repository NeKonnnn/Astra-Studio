import React from 'react';
import { render, screen, fireEvent, act, within, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import {
  hx,
  resetHarness,
  installFetch,
  fakeResponse,
  msg,
  userMsg,
  setScrollGeometry,
} from '@/pages/__test_support__/unifiedChatHarness';
import UnifiedChatPage from '@/pages/UnifiedChatPage';

const BTN = 'Прокрутить к концу чата';

function renderPage() {
  return render(<UnifiedChatPage isDarkMode={false} />);
}
const scroller = () => hx.virt.scrollerEl as HTMLDivElement;
const advance = (ms: number) =>
  act(() => {
    jest.advanceTimersByTime(ms);
  });
const cards = (c: HTMLElement) => Array.from(c.querySelectorAll('[data-message-index]')) as HTMLElement[];
const NEAR = { scrollHeight: 1000, scrollTop: 500, clientHeight: 500 };
const FAR = { scrollHeight: 3000, scrollTop: 0, clientHeight: 500 };

beforeEach(() => {
  (HTMLElement.prototype as any).scrollTo = jest.fn();
  jest.useFakeTimers();
  localStorage.clear();
  sessionStorage.clear();
  resetHarness();
  installFetch();
  hx.messages = [userMsg({ id: 'u1', content: 'q' }), msg({ id: 'a1', content: 'a' })];
});

afterEach(() => {
  jest.useRealTimers();
  delete (HTMLElement.prototype as any).scrollTo;
  delete (Element.prototype as any).scrollIntoView;
});

describe('UnifiedChatPage: кнопка «к концу чата» и слушатель прокрутки', () => {
  it('показывается при прокрутке вверх и скрывается у нижнего края', () => {
    renderPage();
    advance(800); // окно первичной доводки к низу
    expect(screen.queryByLabelText(BTN)).toBeNull();
    setScrollGeometry(scroller(), FAR);
    fireEvent.scroll(scroller());
    expect(screen.getByLabelText(BTN)).toBeInTheDocument();
    setScrollGeometry(scroller(), NEAR);
    fireEvent.scroll(scroller());
    expect(screen.queryByLabelText(BTN)).toBeNull();
  });

  it('во время программной прокрутки событие scroll игнорируется', () => {
    renderPage();
    // доводка к низу ещё идёт (isProgrammaticScroll=true)
    setScrollGeometry(scroller(), FAR);
    fireEvent.scroll(scroller());
    expect(screen.queryByLabelText(BTN)).toBeNull();
  });

  it('клик по кнопке плавно прокручивает вниз и пересчитывает положение через 700 мс', () => {
    renderPage();
    advance(800);
    setScrollGeometry(scroller(), FAR);
    fireEvent.scroll(scroller());
    hx.virt.scrollToIndex.mockClear();
    fireEvent.click(screen.getByLabelText(BTN));
    expect(hx.virt.scrollToIndex).toHaveBeenCalledWith({ index: 1, align: 'end', behavior: 'smooth' });
    expect(HTMLElement.prototype.scrollTo).toHaveBeenCalledWith({ top: 3000, behavior: 'smooth' });
    expect(screen.queryByLabelText(BTN)).toBeNull();
    // прокрутка не доехала -> снова «далеко»
    advance(700);
    expect(screen.getByLabelText(BTN)).toBeInTheDocument();
  });

  it('если после прокрутки пользователь у низа, кнопка не возвращается', () => {
    renderPage();
    advance(800);
    setScrollGeometry(scroller(), FAR);
    fireEvent.scroll(scroller());
    fireEvent.click(screen.getByLabelText(BTN));
    setScrollGeometry(scroller(), NEAR);
    advance(700);
    expect(screen.queryByLabelText(BTN)).toBeNull();
  });

  const startStreaming = (rerender: (ui: React.ReactElement) => void) => {
    hx.state = { ...hx.state, loadingChatIds: ['c1'] };
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', content: 'идёт', isStreaming: true })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
  };

  it('во время генерации без автоскролла на кнопке крутится индикатор загрузки', () => {
    const { rerender } = renderPage();
    advance(800);
    setScrollGeometry(scroller(), FAR);
    startStreaming(rerender);
    const button = screen.getByLabelText(BTN);
    expect(within(button).getByRole('progressbar')).toBeInTheDocument();
  });

  it('с включённым автоскроллом индикатора на кнопке нет', () => {
    localStorage.setItem('chat_autoscroll_streaming', 'true');
    const { rerender } = renderPage();
    advance(800);
    setScrollGeometry(scroller(), FAR);
    startStreaming(rerender);
    const button = screen.getByLabelText(BTN);
    expect(within(button).queryByRole('progressbar')).toBeNull();
  });

  it('событие паузы автоскролла (раскрытие рассуждений) тоже показывает кнопку', () => {
    renderPage();
    advance(800);
    act(() => {
      window.dispatchEvent(new Event('astra_pause_chat_autoscroll'));
    });
    expect(screen.getByLabelText(BTN)).toBeInTheDocument();
  });

  it('рост содержимого без scroll-события пересчитывает «у низа ли пользователь»', () => {
    const { rerender } = renderPage();
    advance(800);
    setScrollGeometry(scroller(), FAR);
    hx.messages = [...hx.messages, msg({ id: 'a2', content: 'ещё' })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    // новое ассистентское сообщение: syncAwayFromBottom(false), затем пересчёт по геометрии
    expect(screen.getByLabelText(BTN)).toBeInTheDocument();
  });

  it('новое сообщение: итоговое положение определяется геометрией ленты', () => {
    hx.messages = [msg({ id: 'a1', content: 'a' })];
    const { rerender } = renderPage();
    advance(800);
    setScrollGeometry(scroller(), NEAR);
    hx.messages = [...hx.messages, userMsg({ id: 'u2', content: 'новый' })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    expect(screen.queryByLabelText(BTN)).toBeNull();
    setScrollGeometry(scroller(), FAR);
    hx.messages = [...hx.messages, msg({ id: 'a2', content: 'ответ' })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    expect(screen.getByLabelText(BTN)).toBeInTheDocument();
  });

  it('кнопка не рисуется для пустого чата', () => {
    hx.messages = [];
    renderPage();
    act(() => {
      window.dispatchEvent(new Event('astra_pause_chat_autoscroll'));
    });
    expect(screen.queryByLabelText(BTN)).toBeNull();
  });
});

describe('UnifiedChatPage: автоскролл при стриминге (настройка)', () => {
  beforeEach(() => {
    localStorage.setItem('chat_autoscroll_streaming', 'true');
  });

  const grow = (rerender: (ui: React.ReactElement) => void, id = 'a1', content = 'больше текста') => {
    hx.messages = hx.messages.map((m: any) => (m.id === id ? { ...m, content } : m));
    rerender(<UnifiedChatPage isDarkMode={false} />);
  };

  it('при росте ответа у нижнего края плавно докручивает контейнер', () => {
    const { rerender } = renderPage();
    advance(800);
    setScrollGeometry(scroller(), NEAR);
    (HTMLElement.prototype.scrollTo as jest.Mock).mockClear();
    grow(rerender);
    expect(HTMLElement.prototype.scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: 'smooth' });
    advance(700);
  });

  it('после паузы (пользователь взаимодействует с блоком рассуждений) автоскролл молчит 5 секунд', () => {
    const { rerender } = renderPage();
    advance(800);
    setScrollGeometry(scroller(), NEAR);
    act(() => {
      window.dispatchEvent(new Event('astra_pause_chat_autoscroll'));
    });
    (HTMLElement.prototype.scrollTo as jest.Mock).mockClear();
    grow(rerender);
    expect(HTMLElement.prototype.scrollTo).not.toHaveBeenCalled();
  });

  it('после паузы, если пользователь не у низа, автоскролл не включается', () => {
    const { rerender } = renderPage();
    advance(800);
    act(() => {
      window.dispatchEvent(new Event('astra_pause_chat_autoscroll'));
    });
    advance(6000);
    setScrollGeometry(scroller(), FAR);
    (HTMLElement.prototype.scrollTo as jest.Mock).mockClear();
    grow(rerender);
    expect(HTMLElement.prototype.scrollTo).not.toHaveBeenCalled();
  });

  it('при выключенной настройке автоскролла контейнер не двигается', () => {
    localStorage.setItem('chat_autoscroll_streaming', 'false');
    const { rerender } = renderPage();
    advance(800);
    setScrollGeometry(scroller(), NEAR);
    (HTMLElement.prototype.scrollTo as jest.Mock).mockClear();
    grow(rerender);
    expect(HTMLElement.prototype.scrollTo).not.toHaveBeenCalled();
  });
});

describe('UnifiedChatPage: якорение ленты и режим «вопрос у верха»', () => {
  it('при открытии чата доводит ленту к последнему сообщению несколько раз и затем фиксируется', () => {
    renderPage();
    expect(hx.virt.scrollToIndex).toHaveBeenCalledWith({ index: 1, align: 'end', behavior: 'auto' });
    const initial = hx.virt.scrollToIndex.mock.calls.length;
    advance(800);
    expect(hx.virt.scrollToIndex.mock.calls.length).toBeGreaterThan(initial);
    expect(hx.virt.lastProps.initialTopMostItemIndex).toEqual({ index: 1, align: 'end' });
  });

  it('если пользователь уже прокрутил вверх, доводка не мешает', () => {
    renderPage();
    act(() => {
      window.dispatchEvent(new Event('astra_pause_chat_autoscroll'));
    });
    hx.virt.scrollToIndex.mockClear();
    advance(800);
    expect(hx.virt.scrollToIndex).not.toHaveBeenCalled();
  });

  it('без доступа к Virtuoso-хендлу прокручивает сам контейнер', () => {
    hx.virt.noHandle = true;
    const { unmount } = renderPage();
    unmount();
    // повторный рендер: контейнер появляется, scrollTop выставляется в scrollHeight
    const spy: { top?: number } = {};
    const orig = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollTop');
    Object.defineProperty(HTMLElement.prototype, 'scrollTop', {
      configurable: true,
      get() {
        return 0;
      },
      set(v: number) {
        spy.top = v;
      },
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true, get: () => 777 });
    try {
      renderPage();
      advance(10);
      expect(spy.top).toBe(777);
    } finally {
      if (orig) Object.defineProperty(HTMLElement.prototype, 'scrollTop', orig);
      else delete (HTMLElement.prototype as any).scrollTop;
      delete (HTMLElement.prototype as any).scrollHeight;
    }
  });

  it('если чат уже стримится при монтировании, якорение к низу пропускается', () => {
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', content: 'x', isStreaming: true })];
    renderPage();
    hx.virt.scrollToIndex.mockClear();
    advance(800);
    // только Grok-вызовы с align=start, ни одного align=end
    expect(hx.virt.scrollToIndex.mock.calls.every((c: any[]) => c[0].align === 'start')).toBe(true);
  });

  it('старт генерации ставит вопрос у верха, добавляет нижний спейсер и снимает его по окончании', () => {
    const { container, rerender } = renderPage();
    advance(800);
    setScrollGeometry(scroller(), { scrollHeight: 1000, scrollTop: 500, clientHeight: 600 });
    hx.virt.scrollToIndex.mockClear();

    hx.state = { ...hx.state, loadingChatIds: ['c1'] };
    hx.messages = [userMsg({ id: 'u1', content: 'q' }), msg({ id: 'a1', content: 'частично', isStreaming: true })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    advance(200);
    expect(hx.virt.scrollToIndex).toHaveBeenCalledWith({ index: 0, align: 'start', behavior: 'auto' });
    const spacer = container.querySelector('div[aria-hidden="true"]') as HTMLElement;
    expect(spacer).not.toBeNull();
    expect(spacer.style.height).toBe('576px');
    advance(100);

    // конец хода
    hx.state = { ...hx.state, loadingChatIds: [] };
    hx.messages = [userMsg({ id: 'u1', content: 'q' }), msg({ id: 'a1', content: 'готово', isStreaming: false })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    expect(container.querySelector('div[aria-hidden="true"]')).toBeNull();
  });

  it('смена чата сбрасывает якорь и повторно доводит ленту к низу', () => {
    const { rerender } = renderPage();
    advance(800);
    hx.virt.scrollToIndex.mockClear();
    hx.chat = { id: 'c2', title: 'Другой', messagesUnloaded: false };
    hx.state = { ...hx.state, currentChatId: 'c2' };
    hx.messages = [userMsg({ id: 'x1' }), msg({ id: 'x2' }), msg({ id: 'x3' })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    expect(hx.virt.scrollToIndex).toHaveBeenCalledWith({ index: 2, align: 'end', behavior: 'auto' });
  });

  it('вспомогательные компоненты Virtuoso: стабильный ключ элемента и footer-якорь', () => {
    renderPage();
    const { computeItemKey, components, itemContent } = hx.virt.lastProps;
    expect(computeItemKey(3, { id: 'abc' })).toBe('abc');
    expect(computeItemKey(3, { id: '' })).toBe('3');
    expect(typeof itemContent).toBe('function');
    expect(components.Footer).toBeDefined();
  });
});

describe('UnifiedChatPage: навигация по сообщениям', () => {
  beforeEach(() => {
    (Element.prototype as any).scrollIntoView = jest.fn();
  });

  it('переход к сообщению: scrollToIndex(align=start) и докрутка scrollIntoView', () => {
    const { container } = renderPage();
    advance(800);
    hx.virt.scrollToIndex.mockClear();
    fireEvent.click(screen.getByTestId('nav-1'));
    expect(hx.virt.scrollToIndex).toHaveBeenCalledWith({ index: 1, align: 'start', behavior: 'auto' });
    advance(300);
    expect((Element.prototype as any).scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'start' });
    expect(cards(container)).toHaveLength(2);
    // после перехода лента считается «вдали от низа»
    expect(screen.getByLabelText(BTN)).toBeInTheDocument();
    advance(900);
  });

  it('индекс вне списка использует переданное значение как видимый индекс', () => {
    renderPage();
    advance(800);
    hx.virt.scrollToIndex.mockClear();
    fireEvent.click(screen.getByTestId('nav-99'));
    expect(hx.virt.scrollToIndex).toHaveBeenCalledWith({ index: 99, align: 'start', behavior: 'auto' });
    advance(900);
  });

  it('скрытое (пустое, пока идёт загрузка) сообщение не прокручивается', () => {
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', content: '', isStreaming: true })];
    renderPage();
    advance(800);
    hx.virt.scrollToIndex.mockClear();
    fireEvent.click(screen.getByTestId('nav-1'));
    expect(hx.virt.scrollToIndex).not.toHaveBeenCalled();
  });

  it('без Virtuoso-хендла докручивает элемент напрямую', () => {
    hx.virt.noHandle = true;
    renderPage();
    advance(800);
    (Element.prototype as any).scrollIntoView.mockClear();
    fireEvent.click(screen.getByTestId('nav-0'));
    expect((Element.prototype as any).scrollIntoView).toHaveBeenCalledTimes(1);
    advance(900);
  });
});

describe('UnifiedChatPage: прочее поведение формы', () => {
  it('кнопка сравнения моделей в режиме settings переключает режим из поля ввода', () => {
    localStorage.setItem('show_model_selector_in_settings', 'true');
    renderPage();
    const btn = screen.getByTestId('ib-extra').querySelector('button') as HTMLElement;
    fireEvent.click(btn);
    expect(localStorage.getItem('model_comparison_enabled')).toBe('true');
  });

  it('меню инструментов без якоря-пилюли привязывается к самой кнопке', () => {
    hx.noAnchor = true;
    renderPage();
    fireEvent.click(screen.getByTestId('ib-settings'));
    expect(screen.getByText('Агенты')).toBeInTheDocument();
  });

  it('нижнее поле ввода (когда есть сообщения): прикрепление, удаление вложения, голос', async () => {
    jest.useRealTimers();
    installFetch({
      '/documents/attach': () => fakeResponse({ type: 'text', content: 'T', filename: 'f.txt' }),
    });
    renderPage();
    const fileInput = screen.getByTestId('input-file') as HTMLInputElement;
    const click = jest.spyOn(fileInput, 'click').mockImplementation(() => {});
    fireEvent.click(screen.getByTestId('ib-attach'));
    expect(click).toHaveBeenCalled();
    fireEvent.change(fileInput, { target: { files: [new File(['t'], 'f.txt', { type: 'text/plain' })] } });
    await screen.findByTestId('ib-remove-0');
    fireEvent.click(screen.getByTestId('ib-remove-0'));
    expect(screen.queryByTestId('ib-remove-0')).toBeNull();
    fireEvent.click(screen.getByTestId('ib-voice'));
    expect(screen.getByTestId('voice-dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('ib-file-empty'));
  });

  it('колонка multi-LLM: длительность рассуждения фиксируется после завершения', () => {
    jest.setSystemTime(new Date(5_000_000));
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({ id: 'a1', content: '', multiLLMResponses: [{ model: 'm1', content: '', isStreaming: true }] }),
    ];
    const { rerender, container } = renderPage();
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({ id: 'a1', content: '', multiLLMResponses: [{ model: 'm1', content: '<think>раз', isStreaming: true }] }),
    ];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    advance(4000);
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({
        id: 'a1',
        content: '',
        multiLLMResponses: [{ model: 'm1', content: '<think>раз</think>итог', isStreaming: false }],
      }),
    ];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    const card = cards(container)[1];
    expect(within(card).getByRole('button', { name: /Думала\s+4\s+сек/ })).toBeInTheDocument();
  });

  it('колонка multi-LLM: короткое рассуждение даёт минимум 1 сек, а live-счётчик идёт во время стрима', () => {
    jest.setSystemTime(new Date(6_000_000));
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({ id: 'a1', content: '', multiLLMResponses: [{ model: 'm1', content: '', isStreaming: true }] }),
    ];
    const { rerender, container } = renderPage();
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({ id: 'a1', content: '', multiLLMResponses: [{ model: 'm1', content: '<think>раз', isStreaming: true }] }),
    ];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    advance(2000);
    expect(within(cards(container)[1]).getByRole('button', { name: /Думает…\s+2\s+сек/ })).toBeInTheDocument();
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({
        id: 'a1',
        content: '',
        multiLLMResponses: [{ model: 'm1', content: '<think>раз</think>ок', isStreaming: false }],
      }),
    ];
    jest.setSystemTime(new Date(6_000_100));
    rerender(<UnifiedChatPage isDarkMode={false} />);
    expect(within(cards(container)[1]).getByRole('button', { name: /Думала\s+\d+\s+сек/ })).toBeInTheDocument();
  });

  it('после multi-отправки, создавшей чат, первая смена id чата не сбрасывает окна моделей', async () => {
    jest.useRealTimers();
    localStorage.setItem('model_comparison_enabled', 'true');
    hx.chat = null;
    hx.messages = [];
    installFetch({
      '/api/models/available': () => fakeResponse({ models: [{ name: 'm1', path: 'p/m1', display_name: 'Модель 1' }] }),
      '/api/model-comparison/models': () => fakeResponse({}),
    });
    const { rerender } = renderPage();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    fireEvent.click(document.querySelector('input[id^="multi-llm-model-"]') as HTMLElement);
    fireEvent.click(screen.getByText('Модель 1'));
    fireEvent.change(screen.getByTestId('input-field'), { target: { value: 'привет' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('ib-send'));
    });
    expect(hx.actions.setCurrentChat).toHaveBeenCalledWith('new-chat');
    hx.state = { ...hx.state, currentChatId: 'new-chat' };
    hx.chat = { id: 'new-chat', title: 'Новый чат', messagesUnloaded: false };
    rerender(<UnifiedChatPage isDarkMode={false} />);
    await waitFor(() =>
      expect((document.querySelector('input[id^="multi-llm-model-"]') as HTMLInputElement).value).toBe('Модель 1'),
    );
  });
});
