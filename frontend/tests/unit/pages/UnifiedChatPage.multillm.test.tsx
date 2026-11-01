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
} from '@/pages/__test_support__/unifiedChatHarness';
import UnifiedChatPage from '@/pages/UnifiedChatPage';

function renderPage() {
  return render(<UnifiedChatPage isDarkMode={false} />);
}
/** Карточка колонки по названию модели. */
const col = (model: string): HTMLElement => {
  const el = screen.getAllByText(model).find((n) => n.closest('.MuiCard-root') && n.tagName === 'SPAN');
  return (el as HTMLElement).closest('.MuiCard-root') as HTMLElement;
};
function btn(card: HTMLElement, label: string): HTMLElement {
  const el = within(card).getByLabelText(label);
  return (el.tagName === 'SPAN' ? el.querySelector('button') : el) as HTMLElement;
}

const columns = () => [
  {
    model: 'm/a',
    content: '<think>r0</think>ans0',
    generationDurationSec: 65,
    alternativeResponses: ['alt0', '<think>r0</think>ans0'],
    currentResponseIndex: 1,
  },
  { model: 'b:c/d', content: 'ошибка модели', error: true },
  { model: 'strm', content: 'часть', isStreaming: true, generationStartedAtMs: 10_000_000 - 7_000 },
  { model: 'n', content: 'x', alternativeResponses: ['x', 'y'], currentResponseIndex: 0 },
];

function setup(extra: Record<string, unknown> = {}, cols = columns()) {
  hx.messages = [
    userMsg({ id: 'u1', content: 'вопрос' }),
    msg({ id: 'a1', content: '', multiLLMResponses: cols, ...extra }),
  ];
  return renderPage();
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetHarness();
  installFetch();
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: jest.fn().mockResolvedValue(undefined), write: jest.fn() },
  });
});

afterEach(() => {
  jest.useRealTimers();
});

describe('UnifiedChatPage: колонки multi-LLM — отрисовка', () => {
  it('рисует колонку на каждую модель, ошибку, чип генерации, длительность и рассуждение', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(10_000_000));
    setup();
    for (const m of ['m/a', 'b:c/d', 'strm', 'n']) {
      expect(screen.getAllByText(m).length).toBeGreaterThan(0);
    }
    // ошибка
    expect(screen.getByText('Ошибка')).toBeInTheDocument();
    expect(screen.getByText('ошибка модели')).toBeInTheDocument();
    // чип генерации с живым таймером
    expect(screen.getByText('Генерируется… 0:07')).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(3000);
    });
    expect(screen.getByText('Генерируется… 0:10')).toBeInTheDocument();
    // длительность завершённой колонки
    expect(within(col('m/a')).getByText(/думал 1:05/)).toBeInTheDocument();
    // рассуждение внутри колонки: заголовок и текст ответа
    const header = within(col('m/a')).getByRole('button', { name: /Цепочка рассуждений/ });
    expect(within(col('m/a')).getByText('ans0')).toBeInTheDocument();
    fireEvent.click(header);
    expect(header).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(header);
    expect(header).toHaveAttribute('aria-expanded', 'false');
    // внешней панели действий (одной на сообщение) нет — действия у каждой колонки
    expect(within(col('m/a')).getByLabelText('Копировать')).toBeInTheDocument();
    // у колонки с ошибкой панели действий нет
    expect(within(col('b:c/d')).queryByLabelText('Копировать')).toBeNull();
  });

  it('потоковая колонка стримит без включённого стрима, если идёт рассуждение; дальше isStreaming=true для текста', () => {
    setup(undefined, [
      { model: 'think', content: '<think>раз', isStreaming: true },
      { model: 'text', content: 'привет', isStreaming: true },
    ]);
    const r = screen.getAllByTestId('renderer');
    expect(r[0]).toHaveAttribute('data-streaming', 'false');
    expect(r[1]).toHaveAttribute('data-streaming', 'true');
  });

  it('MCP-вызовы раскладываются по колонкам: свои + без модели; внешней панели нет', () => {
    setup({
      mcpToolCalls: [
        { type: 'mcp_tool_start', server_id: 's', tool: 't', qualified_name: 's.t', model: 'm/a' },
        { type: 'mcp_tool_start', server_id: 's', tool: 't2', qualified_name: 's.t2', model: 'other' },
        { type: 'mcp_tool_start', server_id: 's', tool: 't3', qualified_name: 's.t3' },
      ],
    });
    expect(within(col('m/a')).getByTestId('mcp-panel')).toHaveTextContent('2');
    expect(within(col('n')).getByTestId('mcp-panel')).toHaveTextContent('1');
    expect(within(col('b:c/d')).queryByTestId('mcp-panel')).toBeNull();
    expect(screen.getAllByTestId('mcp-panel')).toHaveLength(3);
  });

  it('если вызовы относятся только к другой модели, панель в колонке не рисуется', () => {
    setup({
      mcpToolCalls: [
        { type: 'mcp_tool_start', server_id: 's', tool: 't', qualified_name: 's.t', model: 'other' },
      ],
    });
    expect(screen.queryByTestId('mcp-panel')).toBeNull();
  });

  it('наведение мыши на колонку не ломает отрисовку', () => {
    setup();
    const c = col('m/a');
    fireEvent.mouseEnter(c);
    fireEvent.mouseLeave(c);
    fireEvent.mouseEnter(c);
    fireEvent.mouseEnter(col('n'));
    fireEvent.mouseLeave(c); // уход со «старой» колонки не сбрасывает hover новой
    expect(c).toBeInTheDocument();
  });

  it('onSendMessage из колонки использует messageId с индексом колонки', () => {
    localStorage.setItem('last_selected_model_path', 'p/m');
    setup();
    fireEvent.click(screen.getByTestId('renderer-send-a1-m0'));
    expect(hx.socket.sendMessage).toHaveBeenCalledWith('prompt-from-renderer', 'c1');
  });
});

describe('UnifiedChatPage: колонки multi-LLM — варианты, копирование, оценка', () => {
  it('«Предыдущий вариант» меняет индекс и содержимое только выбранной колонки', () => {
    setup();
    fireEvent.click(btn(col('m/a'), 'Предыдущий вариант'));
    const call = hx.actions.updateMessage.mock.calls[0];
    expect(call.slice(0, 4)).toEqual(['c1', 'a1', undefined, false]);
    expect(call[4][0]).toMatchObject({ model: 'm/a', currentResponseIndex: 0, content: 'alt0' });
    expect(call[4][3]).toMatchObject({ model: 'n', currentResponseIndex: 0, content: 'x' });
    expect(within(col('m/a')).getByText('2/2')).toBeInTheDocument();
  });

  it('«Следующий вариант» переключает вперёд, а на последнем — отключена', () => {
    setup();
    expect(btn(col('m/a'), 'Следующий вариант')).toBeDisabled();
    expect(btn(col('n'), 'Предыдущий вариант')).toBeDisabled();
    fireEvent.click(btn(col('n'), 'Следующий вариант'));
    const call = hx.actions.updateMessage.mock.calls[0];
    expect(call[4][3]).toMatchObject({ model: 'n', currentResponseIndex: 1, content: 'y' });
  });

  it('без текущего чата переключение вариантов ничего не делает', () => {
    hx.chat = null;
    setup();
    fireEvent.click(btn(col('m/a'), 'Предыдущий вариант'));
    fireEvent.click(btn(col('n'), 'Следующий вариант'));
    expect(hx.actions.updateMessage).not.toHaveBeenCalled();
  });

  it('копирование колонки берёт отображаемый вариант ответа', async () => {
    setup();
    fireEvent.click(within(col('m/a')).getByLabelText('Копировать'));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalled());
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('plain:<think>r0</think>ans0');
  });

  it('копирование колонки без вариантов берёт content', async () => {
    setup(undefined, [{ model: 'solo', content: 'один' }]);
    fireEvent.click(within(col('solo')).getByLabelText('Копировать'));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('plain:один'));
  });

  it('индекс варианта вне диапазона — отображается content колонки', async () => {
    setup(undefined, [{ model: 'odd', content: 'контент', alternativeResponses: ['а', 'б'], currentResponseIndex: 9 }]);
    fireEvent.click(within(col('odd')).getByLabelText('Копировать'));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('plain:контент'));
    expect(within(col('odd')).getByText('контент')).toBeInTheDocument();
  });

  it('оценка колонки отправляет multi_llm_slot_index и обновляет только её feedback', async () => {
    const fetchMock = installFetch({
      '/feedback': () => fakeResponse({ feedback: { rating: 'like', tags: [] } }),
    });
    setup();
    fireEvent.click(within(col('m/a')).getByTestId('fb-like'));
    await waitFor(() => expect(hx.actions.updateMessage).toHaveBeenCalled());
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/feedback'))!;
    expect(JSON.parse(call[1].body)).toEqual({ rating: 'like', tags: [], comment: '', multi_llm_slot_index: 0 });
    const args = hx.actions.updateMessage.mock.calls[0];
    expect(args.slice(0, 4)).toEqual(['c1', 'a1', undefined, false]);
    expect(args[4][0].feedback).toMatchObject({ rating: 'like' });
    expect(args[4][3].feedback).toBeUndefined();
  });

  it('дизлайк и снятие оценки колонки передают слот', async () => {
    const fetchMock = installFetch({ '/feedback': () => fakeResponse({ feedback: null }) });
    setup();
    fireEvent.click(within(col('n')).getByTestId('fb-dislike'));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(0));
    const bodies = () =>
      fetchMock.mock.calls.filter((c) => String(c[0]).includes('/feedback')).map((c) => JSON.parse(c[1].body));
    await waitFor(() => expect(bodies()).toHaveLength(1));
    expect(bodies()[0]).toEqual({ rating: 'dislike', tags: ['t1'], comment: 'cm', multi_llm_slot_index: 3 });
    fireEvent.click(within(col('n')).getByTestId('fb-clear'));
    await waitFor(() => expect(bodies()).toHaveLength(2));
    expect(bodies()[1]).toMatchObject({ rating: null, multi_llm_slot_index: 3 });
  });

  it('у потоковой колонки нет панели оценки, у колонок при потоковом сообщении она отключена', () => {
    setup({ isStreaming: true });
    expect(within(col('strm')).queryByTestId('feedback-bar')).toBeNull();
    expect(within(col('n')).getByTestId('feedback-bar')).toHaveAttribute('data-disabled', 'true');
  });
});

describe('UnifiedChatPage: колонки multi-LLM — перегенерация, меню', () => {
  it('перегенерация колонки добавляет пустой вариант и запускает regenerateMultiLlmSlot', () => {
    setup();
    fireEvent.click(btn(col('n'), 'Перегенерировать'));
    const call = hx.actions.updateMessage.mock.calls[0];
    expect(call.slice(0, 4)).toEqual(['c1', 'a1', undefined, true]);
    expect(call[4][3]).toMatchObject({
      model: 'n',
      alternativeResponses: ['x', 'y', ''],
      currentResponseIndex: 2,
      isStreaming: true,
      content: '',
    });
    // остальные колонки перестают «стримиться»
    expect(call[4][2].isStreaming).toBe(false);
    expect(hx.socket.regenerateMultiLlmSlot).toHaveBeenCalledWith('вопрос', 'a1', 'c1', 'n');
  });

  it('перегенерация колонки без вариантов создаёт массив из текущего текста', () => {
    setup(undefined, [{ model: 'solo', content: 'текст' }]);
    fireEvent.click(btn(col('solo'), 'Перегенерировать'));
    const call = hx.actions.updateMessage.mock.calls[0];
    expect(call[4][0]).toMatchObject({ alternativeResponses: ['текст', ''], currentResponseIndex: 1 });
  });

  it('индекс вне массива вариантов: текущий текст не подменяет существующие элементы', () => {
    setup(undefined, [{ model: 'odd', content: 'c', alternativeResponses: ['a'], currentResponseIndex: 5 }]);
    fireEvent.click(btn(col('odd'), 'Перегенерировать'));
    const call = hx.actions.updateMessage.mock.calls[0];
    expect(call[4][0].alternativeResponses).toEqual(['a', '']);
    expect(call[4][0].currentResponseIndex).toBe(1);
  });

  it('кнопка перегенерации потоковой колонки отключена', () => {
    setup();
    expect(btn(col('strm'), 'Перегенерировать')).toBeDisabled();
  });

  it('без соединения показывает ошибку и не перегенерирует', () => {
    hx.socket.isConnected = false;
    setup();
    fireEvent.click(btn(col('n'), 'Перегенерировать'));
    expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Нет соединения с сервером');
    expect(hx.socket.regenerateMultiLlmSlot).not.toHaveBeenCalled();
  });

  it('без предшествующего сообщения пользователя показывает ошибку', () => {
    hx.messages = [msg({ id: 'a1', content: '', multiLLMResponses: [{ model: 'solo', content: 'т' }] })];
    renderPage();
    fireEvent.click(btn(col('solo'), 'Перегенерировать'));
    expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Не найдено предыдущее сообщение пользователя');
    expect(hx.socket.regenerateMultiLlmSlot).not.toHaveBeenCalled();
  });

  it('меню колонки: озвучивание, ветка со слотом и экспорт с безопасным именем файла', async () => {
    (URL as any).createObjectURL = jest.fn(() => 'blob:a');
    (global as any).Audio = class {
      pause = jest.fn();
      play = jest.fn().mockResolvedValue(undefined);
    };
    const fetchMock = installFetch({ '/api/voice/synthesize': () => fakeResponse({}) });
    setup(undefined, [
      { model: 'm/a:b', content: 'озвучка' },
      { model: '', content: '   ' },
      { model: 'stream', content: 'x', isStreaming: true },
    ]);
    const menu = within(col('m/a:b')).getByTestId('more-menu');
    expect(menu).toHaveAttribute('data-show-branch', 'true');
    expect(menu).toHaveAttribute('data-export-disabled', 'false');

    fireEvent.click(within(menu).getByTestId('mm-read'));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/voice/synthesize'))).toBe(true),
    );

    fireEvent.click(within(menu).getByTestId('mm-branch'));
    await waitFor(() =>
      expect(hx.actions.branchChatAtMessage).toHaveBeenCalledWith('c1', 'a1', { multiLlmSlotIndex: 0 }),
    );

    fireEvent.click(within(menu).getByTestId('mm-export'));
    expect(hx.exports.exportMessageContent).toHaveBeenCalledWith('озвучка', 'md', 'ответ_m_a_b');

    // колонка с пустым названием и пустым текстом: имя «llm», экспорт отключён
    const cards = Array.from(document.querySelectorAll('.MuiCard-root:not(.message-bubble)')).filter((c) =>
      c.querySelector('[data-testid="more-menu"]'),
    ) as HTMLElement[];
    const emptyMenu = within(cards[1]).getByTestId('more-menu');
    expect(emptyMenu).toHaveAttribute('data-export-disabled', 'true');
    fireEvent.click(within(emptyMenu).getByTestId('mm-export'));
    expect(hx.exports.exportMessageContent).toHaveBeenLastCalledWith('   ', 'md', 'ответ_llm');

    // потоковая колонка: ветка отключена, а попытка ветвления даёт информационный тост
    const streamMenu = within(cards[2]).getByTestId('more-menu');
    expect(streamMenu).toHaveAttribute('data-branch-disabled', 'true');
    hx.actions.showNotification.mockClear();
    fireEvent.click(within(streamMenu).getByTestId('mm-branch'));
    expect(hx.actions.showNotification).toHaveBeenCalledWith('info', 'Дождитесь окончания генерации ответа');
    delete (global as any).Audio;
  });

  it('колонка с длинным названием модели усекает имя файла до 40 символов', () => {
    const long = 'x'.repeat(60);
    setup(undefined, [{ model: long, content: 'т' }]);
    fireEvent.click(within(col(long)).getByTestId('mm-export'));
    expect(hx.exports.exportMessageContent).toHaveBeenCalledWith('т', 'md', `ответ_${'x'.repeat(40)}`);
  });

  it('режим «Поделиться» убирает из меню колонки ветку и кнопку шаринга', () => {
    setup();
    fireEvent.click(within(col('m/a')).getByLabelText('Поделиться'));
    expect(within(col('m/a')).queryByLabelText('Поделиться')).toBeNull();
    expect(within(col('m/a')).getByTestId('more-menu')).toHaveAttribute('data-show-branch', 'false');
  });
});
