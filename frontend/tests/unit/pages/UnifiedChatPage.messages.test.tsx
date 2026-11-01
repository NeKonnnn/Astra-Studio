import React from 'react';
import { render, screen, fireEvent, act, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { hx, resetHarness, installFetch, msg, userMsg } from '@/pages/__test_support__/unifiedChatHarness';
import UnifiedChatPage from '@/pages/UnifiedChatPage';

function renderPage(props: Partial<React.ComponentProps<typeof UnifiedChatPage>> = {}) {
  return render(<UnifiedChatPage isDarkMode={false} {...props} />);
}
const cards = (c: HTMLElement) => Array.from(c.querySelectorAll('[data-message-index]')) as HTMLElement[];

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetHarness();
  installFetch();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('UnifiedChatPage: отрисовка сообщений', () => {
  it('рисует карточки пользователя и ассистента с подписями, временем и аватаром', () => {
    hx.messages = [
      userMsg({ id: 'u1', content: 'привет' }),
      msg({ id: 'a1', content: 'здравствуйте' }),
    ];
    const { container } = renderPage();
    const [u, a] = cards(container);
    expect(within(u).getByText('Вы')).toBeInTheDocument();
    expect(within(u).getByText('привет')).toBeInTheDocument();
    expect(within(u).getByTestId('user-avatar')).toHaveTextContent('ivan');
    expect(within(a).getByText('AstraChat')).toBeInTheDocument();
    expect(within(a).getByText('здравствуйте')).toBeInTheDocument();
    expect(within(u).getByText(/^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$/)).toBeInTheDocument();
    // индексы навигации
    expect(u.getAttribute('data-message-index')).toBe('0');
    expect(a.getAttribute('data-message-index')).toBe('1');
    // панель навигации по диалогам получает все сообщения
    expect(screen.getByTestId('nav-bar')).toHaveAttribute('data-count', '2');
  });

  it('при show_user_name=true подпись пользователя — его логин; без логина остаётся «Вы»', () => {
    localStorage.setItem('show_user_name', 'true');
    hx.messages = [userMsg({ id: 'u1' })];
    const { container, unmount } = renderPage();
    // логин в подписи + заглушка аватара
    expect(within(cards(container)[0]).getAllByText('ivan')).toHaveLength(2);
    expect(within(cards(container)[0]).queryByText('Вы')).toBeNull();
    unmount();

    hx.auth.user = null;
    const second = renderPage();
    expect(within(cards(second.container)[0]).getByText('Вы')).toBeInTheDocument();
  });

  it('некорректный timestamp даёт пустую строку времени', () => {
    hx.messages = [userMsg({ id: 'u1', timestamp: 'не дата' })];
    const { container } = renderPage();
    expect(within(cards(container)[0]).queryByText(/\d{2}\.\d{2}\.\d{4}/)).toBeNull();
  });

  it('имя агента цепочки приоритетнее имени последнего шага, иначе AstraChat', () => {
    hx.messages = [
      msg({ id: 'a1', chainCurrentAgent: { agentName: 'Текущий', index: 1, total: 2 } }),
      msg({
        id: 'a2',
        chainSteps: [
          { agentId: 1, agentName: 'Первый', content: 'x' },
          { agentId: 2, agentName: 'Последний', content: 'y' },
        ],
        isStreaming: true,
      }),
    ];
    const { container } = renderPage();
    const [a1, a2] = cards(container);
    expect(within(a1).getByText('Текущий')).toBeInTheDocument();
    // имя последнего шага есть и в подписи карточки (span), и в заголовке шага цепочки (p)
    expect(within(a2).getByText('Последний', { selector: 'span' })).toBeInTheDocument();
  });

  it('user_no_border/assistant_no_border и left_align_messages меняют оформление пузыря', () => {
    localStorage.setItem('user_no_border', 'true');
    localStorage.setItem('assistant_no_border', 'true');
    localStorage.setItem('left_align_messages', 'true');
    localStorage.setItem('widescreen_mode', 'true');
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1' })];
    const { container } = renderPage();
    // без рамки карточка .message-bubble не создаётся
    expect(container.querySelector('.message-bubble')).toBeNull();
    expect(cards(container)).toHaveLength(2);
  });

  it('с рамкой у каждого сообщения есть .message-bubble с темой', () => {
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1' })];
    const { container } = renderPage();
    const bubbles = container.querySelectorAll('.message-bubble');
    expect(bubbles).toHaveLength(2);
    expect(bubbles[0]).toHaveAttribute('data-theme', 'light');
  });

  it('тёмная тема передаётся в карточки', () => {
    hx.messages = [msg({ id: 'a1' })];
    const { container } = renderPage({ isDarkMode: true });
    expect(container.querySelector('.message-bubble')).toHaveAttribute('data-theme', 'dark');
  });

  it('settings из localStorage перечитываются по событию storage', () => {
    hx.messages = [userMsg({ id: 'u1' })];
    const { container } = renderPage();
    expect(container.querySelector('.message-bubble')).not.toBeNull();
    localStorage.setItem('user_no_border', 'true');
    act(() => {
      window.dispatchEvent(new Event('storage'));
    });
    expect(container.querySelector('.message-bubble')).toBeNull();
  });

  it('пустой ассистентский плейсхолдер остаётся видимым во время загрузки (думает внутри карточки)', () => {
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', content: '', isStreaming: true })];
    const { container, rerender } = renderPage();
    expect(cards(container)).toHaveLength(2);

    hx.state = { ...hx.state, loadingChatIds: ['c1'] };
    rerender(<UnifiedChatPage isDarkMode={false} />);
    // Раньше плейсхолдер скрывали и рисовали отдельный пузырь «думает» —
    // теперь одна карточка ответа с inline-индикатором.
    expect(cards(container)).toHaveLength(2);
  });

  it('пустой плейсхолдер с одним reasoning не скрывается во время загрузки', () => {
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', content: '<think>раздумья', isStreaming: true })];
    const { container } = renderPage();
    expect(cards(container)).toHaveLength(2);
  });
});

describe('UnifiedChatPage: индикатор «думает» и плейсхолдер ввода', () => {
  it('без ответа ассистента во время загрузки показывает индикатор с таймером и плейсхолдер', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(10_000_000));
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', content: '', generationStartedAtMs: 10_000_000 - 65_000 })];
    renderPage();
    const shimmer = screen
      .getAllByTestId('shimmer')
      .find((n) => (n.textContent || '').startsWith('думает...'));
    expect(shimmer?.textContent).toBe('думает... 1:05');
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-placeholder', 'astrachat думает...');
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-input-disabled', 'true');
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-send-disabled', 'true');
    expect(screen.getByText('AstraChat', { selector: 'span' })).toBeInTheDocument();
  });

  it('индикатор берёт время старта у последнего ассистента-плейсхолдера и имя агента цепочки', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(10_000_000));
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({
        id: 'a1',
        content: '',
        isStreaming: true,
        generationStartedAtMs: 10_000_000 - 3_000,
        chainCurrentAgent: { agentName: ' Аналитик ', index: 1, total: 2 },
      }),
    ];
    renderPage();
    // карточка плейсхолдера с цепочкой остаётся в списке, плюс индикатор «думает» над полем ввода
    expect(screen.getAllByText('Аналитик', { selector: 'span' }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-placeholder', 'Аналитик думает...');
  });

  it('если нет ни одного ассистентского сообщения после пользователя, таймер индикатора не стартует', () => {
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [msg({ id: 'a0', content: 'старый', generationStartedAtMs: 5 }), userMsg({ id: 'u1' })];
    renderPage();
    const shimmer = screen
      .getAllByTestId('shimmer')
      .find((n) => (n.textContent || '').startsWith('думает...'));
    expect(shimmer?.textContent).toBe('думает... 0:00');
  });

  it('генерация изображения: индикатор «думает» скрыт, плейсхолдер говорит о генерации', () => {
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({ id: 'a1', content: '', isStreaming: true, isImageGenerating: true }),
    ];
    renderPage();
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-placeholder', 'Генерация изображения…');
    expect(screen.getByTestId('img-gen')).toBeInTheDocument();
    expect(screen.queryByText(/думает\.\.\./)).toBeNull();
  });

  it('когда ответ уже содержит текст или рассуждение, индикатор не показывается', () => {
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', content: 'частичный', isStreaming: true })];
    const { unmount } = renderPage();
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-placeholder', 'Чем я могу помочь вам сегодня?');
    unmount();

    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', content: '<think>размышляю', isStreaming: true })];
    renderPage();
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-placeholder', 'Чем я могу помочь вам сегодня?');
  });

  it('активный alternativeResponses[index] учитывается при определении «ждём токены»', () => {
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({
        id: 'a1',
        content: 'старый',
        isStreaming: true,
        alternativeResponses: ['старый', ''],
        currentResponseIndex: 1,
      }),
    ];
    renderPage();
    // пустой слот нового варианта -> ждём первый токен (visible = '' -> fallback на content='старый')
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-placeholder', 'Чем я могу помочь вам сегодня?');
  });

  it('запущенный MCP-инструмент отключает индикатор «думает»', () => {
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({
        id: 'a1',
        content: '',
        isStreaming: true,
        mcpToolCalls: [
          { type: 'mcp_tool_start', server_id: 's', tool: 't', qualified_name: 's.t', call_id: 'c-1' },
        ],
      }),
    ];
    renderPage();
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-placeholder', 'Чем я могу помочь вам сегодня?');
  });

  it('завершённый MCP-инструмент и ассистент без вызовов не считаются «running»', () => {
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({
        id: 'a1',
        content: '',
        isStreaming: true,
        mcpToolCalls: [
          { type: 'mcp_tool_start', server_id: 's', tool: 't', qualified_name: 's.t', call_id: 'c-1' },
          { type: 'mcp_tool_end', server_id: 's', tool: 't', qualified_name: 's.t', call_id: 'c-1', success: true },
        ],
      }),
    ];
    renderPage();
    // инструмент завершён, ответа ещё нет -> «думает»
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-placeholder', 'astrachat думает...');
  });

  it('без соединения плейсхолдер сообщает об отсутствии связи или подключении', () => {
    hx.socket.isConnected = false;
    const { unmount } = renderPage();
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-placeholder', 'Нет соединения с сервером');
    unmount();

    hx.socket.isConnecting = true;
    renderPage();
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-placeholder', 'Подключение к серверу...');
  });

  it('блокировка RAG-переиндексации показывает её сообщение (или дефолтное) и блокирует отправку', () => {
    hx.rag.shouldBlockRagSend = jest.fn(() => true);
    hx.rag.blockMessage = 'Идёт переиндексация X';
    const { unmount } = renderPage();
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-placeholder', 'Идёт переиндексация X');
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-send-disabled', 'true');
    unmount();

    hx.rag.blockMessage = '';
    renderPage();
    expect(screen.getByTestId('input-bar')).toHaveAttribute(
      'data-placeholder',
      'Идёт перечанковка документов — дождитесь завершения',
    );
  });

  it('живые MCP-инструменты показываются над полем ввода', () => {
    hx.activeMcpTools = [{ id: 't1' }, { id: 't2' }];
    renderPage();
    expect(screen.getByTestId('mcp-live')).toHaveTextContent('2');
  });
});

describe('UnifiedChatPage: блок рассуждений', () => {
  const think = '<think>мысли модели</think>итоговый ответ';

  it('завершённое сообщение: заголовок «Цепочка рассуждений», раскрытие по клику, видимая часть без тегов', () => {
    hx.messages = [msg({ id: 'a1', content: think })];
    const { container } = renderPage();
    const card = cards(container)[0];
    const header = within(card).getByRole('button', { name: /Цепочка рассуждений/ });
    expect(header).toHaveAttribute('aria-expanded', 'false');
    expect(within(card).getByText('итоговый ответ')).toBeInTheDocument();

    const paused = jest.fn();
    window.addEventListener('astra_pause_chat_autoscroll', paused);
    fireEvent.click(header);
    window.removeEventListener('astra_pause_chat_autoscroll', paused);
    expect(paused).toHaveBeenCalled();
    expect(header).toHaveAttribute('aria-expanded', 'true');
    expect(header.getAttribute('aria-label')).toContain('Свернуть');
    fireEvent.click(header);
    expect(header).toHaveAttribute('aria-expanded', 'false');
  });

  it('mouseDown/touchStart на заголовке ставит автоскролл на паузу и показывает кнопку «вниз»', () => {
    hx.messages = [msg({ id: 'a1', content: think })];
    const { container } = renderPage();
    const header = within(cards(container)[0]).getByRole('button', { name: /Цепочка рассуждений/ });
    expect(screen.queryByLabelText('Прокрутить к концу чата')).toBeNull();
    fireEvent.mouseDown(header);
    expect(screen.getByLabelText('Прокрутить к концу чата')).toBeInTheDocument();
    fireEvent.touchStart(header);
    expect(screen.getByLabelText('Прокрутить к концу чата')).toBeInTheDocument();
  });

  it('стриминг рассуждения: «Думает…» с живым таймером, автораскрытие, затем «Думала N сек»', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(1_000_000));
    // сообщение монтируется пустым, рассуждение приходит потом (как в реальном стриме)
    hx.messages = [msg({ id: 'a1', content: '', isStreaming: true })];
    const { container, rerender } = renderPage();
    hx.messages = [msg({ id: 'a1', content: '<think>раз', isStreaming: true })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    let card = cards(container)[0];
    expect(within(card).getByRole('button', { name: /Думает…/ })).toHaveAttribute('aria-expanded', 'true');

    act(() => {
      jest.advanceTimersByTime(3000);
    });
    card = cards(container)[0];
    expect(within(card).getByRole('button', { name: /Думает…\s+3\s+сек/ })).toBeInTheDocument();

    // рассуждение закончилось
    hx.messages = [msg({ id: 'a1', content: '<think>раз</think>отв', isStreaming: false })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    card = cards(container)[0];
    expect(within(card).getByRole('button', { name: /Думала\s+3\s+сек/ })).toBeInTheDocument();
  });

  it('очень короткое рассуждение округляется до 1 сек', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(2_000_000));
    hx.messages = [msg({ id: 'a1', content: '', isStreaming: true })];
    const { container, rerender } = renderPage();
    hx.messages = [msg({ id: 'a1', content: '<think>раз', isStreaming: true })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    hx.messages = [msg({ id: 'a1', content: '<think>раз</think>готово', isStreaming: false })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    expect(within(cards(container)[0]).getByRole('button', { name: /Думала\s+1\s+сек/ })).toBeInTheDocument();
  });

  // Эффект «сброса таймера при старте генерации» (по message.id) объявлен после эффекта
  // старта таймера и на монтировании обнуляет thinkingStartRef: если карточка с уже идущим
  // рассуждением монтируется заново (переключение чата, remount элемента Virtuoso),
  // счётчик секунд «Думает… N сек» не запускается. Ожидаемое поведение: секунды идут.
  it('карточка, смонтированная посреди рассуждения, не показывает секунды (known issue)', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(3_000_000));
    hx.messages = [msg({ id: 'a1', content: '<think>раз', isStreaming: true })];
    const { container } = renderPage();
    act(() => {
      jest.advanceTimersByTime(3000);
    });
    const header = within(cards(container)[0]).getByRole('button', { name: /Думает…/ });
    expect(header.getAttribute('aria-label')).not.toMatch(/сек/);
  });

  it('после ручного сворачивания последующие чанки блок не раскрывают повторно', () => {
    hx.messages = [msg({ id: 'a1', content: '<think>раз', isStreaming: true })];
    const { container, rerender } = renderPage();
    let header = within(cards(container)[0]).getByRole('button', { name: /Думает/ });
    expect(header).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(header);
    expect(header).toHaveAttribute('aria-expanded', 'false');

    hx.messages = [msg({ id: 'a1', content: '<think>раз два', isStreaming: true })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    header = within(cards(container)[0]).getByRole('button', { name: /Думает/ });
    expect(header).toHaveAttribute('aria-expanded', 'false');
  });

  it('при генерации изображения рассуждения не считаются «живыми»', () => {
    hx.messages = [
      msg({ id: 'a1', content: '<think>раз', isStreaming: true, isImageGenerating: true }),
    ];
    const { container } = renderPage();
    expect(within(cards(container)[0]).getByRole('button', { name: /Цепочка рассуждений/ })).toBeInTheDocument();
  });
});

describe('UnifiedChatPage: вложения, документы, MCP внутри сообщения', () => {
  it('вложения пользователя отображаются списком, клик по разворачиванию открывает лайтбокс', () => {
    hx.messages = [
      userMsg({
        id: 'u1',
        inlineAttachments: [
          { name: 'pic.png', contentType: 'image', preview: 'data:image/png;base64,AA', size: 10 },
          { name: 'doc.txt', contentType: 'text' },
        ],
      }),
    ];
    const { container } = renderPage();
    const card = cards(container)[0];
    expect(within(card).getAllByTestId('inline-file')).toHaveLength(2);
    fireEvent.click(within(card).getByTestId('expand-pic.png'));
    const box = within(card).getByTestId('lightbox');
    expect(box).toHaveAttribute('data-src', 'data:image/png;base64,AA');
    expect(box).toHaveAttribute('data-name', 'pic.png');
    fireEvent.click(within(card).getByTestId('lightbox-close'));
    expect(within(card).queryByTestId('lightbox')).toBeNull();
  });

  it('вложения ассистента берутся из варианта по currentResponseIndex (с ограничением индекса)', () => {
    const v0 = [{ name: 'v0.png', contentType: 'image' as const, preview: 'p0' }];
    const v1 = [{ name: 'v1.png', contentType: 'image' as const, preview: 'p1' }];
    hx.messages = [
      msg({ id: 'a1', content: 'x', inlineAttachmentVariants: [v0, v1], currentResponseIndex: 1 }),
      msg({ id: 'a2', content: 'y', inlineAttachmentVariants: [v0, v1], currentResponseIndex: 99 }),
      msg({ id: 'a3', content: 'z', inlineAttachmentVariants: [v0, v1] }),
      msg({ id: 'a4', content: 'w', inlineAttachmentVariants: [v0, []], currentResponseIndex: 1 }),
      msg({ id: 'a5', content: 'q', inlineAttachments: [{ name: 'plain.txt', contentType: 'text' }] }),
    ];
    const { container } = renderPage();
    const cs = cards(container);
    expect(within(cs[0]).getByText('v1.png')).toBeInTheDocument();
    expect(within(cs[1]).getByText('v1.png')).toBeInTheDocument();
    expect(within(cs[2]).getByText('v1.png')).toBeInTheDocument();
    expect(within(cs[3]).queryByTestId('inline-list')).toBeNull();
    expect(within(cs[4]).getByText('plain.txt')).toBeInTheDocument();
    // лайтбокс из карточки ассистента
    fireEvent.click(within(cs[0]).getByTestId('expand-v1.png'));
    expect(within(cs[0]).getByTestId('lightbox')).toHaveAttribute('data-src', 'p1');
  });

  it('плейсхолдер генерации изображения показывается без вложений, а при готовом изображении скрыт', () => {
    hx.messages = [
      msg({ id: 'a1', content: '', isStreaming: true, isImageGenerating: true }),
      msg({
        id: 'a2',
        content: 'подпись',
        isStreaming: true,
        isImageGenerating: true,
        inlineAttachments: [{ name: 'i.png', contentType: 'image', preview: 'pp' }],
      }),
      msg({ id: 'a3', content: 'есть текст', isStreaming: true, isImageGenerating: true }),
    ];
    const { container } = renderPage();
    const cs = cards(container);
    expect(within(cs[0]).getByTestId('img-gen')).toBeInTheDocument();
    // при пустом тексте рендерер не создаётся
    expect(within(cs[0]).queryByTestId('renderer')).toBeNull();
    expect(within(cs[1]).queryByTestId('img-gen')).toBeNull();
    expect(within(cs[2]).getByTestId('img-gen')).toBeInTheDocument();
    expect(within(cs[2]).getByTestId('renderer')).toBeInTheDocument();
  });

  it('трейс поиска по документам и MCP-вызовы рисуются панелями', () => {
    hx.messages = [
      msg({
        id: 'a1',
        content: 'ответ',
        documentSearch: { query: 'найти X', sourceFiles: [], hits: [] },
        mcpToolCalls: [{ type: 'mcp_tool_start', server_id: 's', tool: 't', qualified_name: 's.t' }],
      }),
    ];
    const { container } = renderPage();
    const card = cards(container)[0];
    expect(within(card).getByTestId('doc-search')).toHaveTextContent('найти X');
    expect(within(card).getByTestId('mcp-panel')).toHaveTextContent('1');
  });

  it('MessageRenderer получает onSendMessage: клик отправляет промпт, если выбрана модель', () => {
    localStorage.setItem('last_selected_model_path', 'prov/model-a');
    hx.messages = [msg({ id: 'a1', content: 'ответ' })];
    renderPage();
    fireEvent.click(screen.getByTestId('renderer-send-a1'));
    expect(hx.socket.sendMessage).toHaveBeenCalledWith('prompt-from-renderer', 'c1');
  });

  it('onSendMessage без выбранной модели показывает баннер ошибки и не отправляет', () => {
    hx.messages = [msg({ id: 'a1', content: 'ответ' })];
    renderPage();
    fireEvent.click(screen.getByTestId('renderer-send-a1'));
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
    expect(screen.getByTestId('top-banner-msg')).toHaveTextContent('Модель не выбрана! Пожалуйста, выберите модель');
    fireEvent.click(screen.getByTestId('top-banner-close'));
    expect(screen.queryByTestId('top-banner')).toBeNull();
  });

  it('onSendMessage с активным агентом (active_agent_id) разрешён без выбранной модели', () => {
    localStorage.setItem('active_agent_id', '42');
    hx.messages = [msg({ id: 'a1', content: 'ответ' })];
    renderPage();
    fireEvent.click(screen.getByTestId('renderer-send-a1'));
    expect(hx.socket.sendMessage).toHaveBeenCalledWith('prompt-from-renderer', 'c1');
  });

  it('onSendMessage не отправляет, если нет соединения или идёт другая генерация', () => {
    localStorage.setItem('last_selected_model_path', 'prov/model-a');
    hx.socket.isConnected = false;
    hx.messages = [msg({ id: 'a1', content: 'ответ' })];
    const { unmount } = renderPage();
    fireEvent.click(screen.getByTestId('renderer-send-a1'));
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
    unmount();

    hx.socket.isConnected = true;
    hx.state.loadingChatIds = ['c1'];
    renderPage();
    fireEvent.click(screen.getByTestId('renderer-send-a1'));
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
  });
});

describe('UnifiedChatPage: цепочка агентов (chainSteps)', () => {
  const steps = [
    {
      agentId: 1,
      agentName: 'Первый',
      content: '<think>план</think>шаг один',
      documentSearch: { query: 'q1', sourceFiles: [], hits: [] },
    },
    { agentId: 2, agentName: '', content: 'шаг два', reasoning: 'явное рассуждение' },
    { agentId: 3, agentName: 'Третий', content: '' },
  ];

  it('показывает все шаги: имя агента, поиск по документам, рассуждение и текст', () => {
    hx.messages = [msg({ id: 'a1', content: 'итог', chainSteps: steps })];
    const { container } = renderPage();
    const card = cards(container)[0];
    expect(within(card).getByText('Первый')).toBeInTheDocument();
    expect(within(card).getByText('Агент')).toBeInTheDocument(); // имя по умолчанию
    // «Третий» — и подпись карточки (последний шаг), и заголовок шага
    expect(within(card).getAllByText('Третий')).toHaveLength(2);
    expect(within(card).getByTestId('doc-search')).toHaveTextContent('q1');
    expect(within(card).getByText('шаг один')).toBeInTheDocument();
    expect(within(card).getByText('шаг два')).toBeInTheDocument();
    const headers = within(card).getAllByRole('button', { name: /Цепочка рассуждений/ });
    expect(headers).toHaveLength(2);
    fireEvent.click(headers[0]);
    expect(headers[0]).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(headers[0]);
    expect(headers[0]).toHaveAttribute('aria-expanded', 'false');
  });

  it('hideSequentialOutputs оставляет только последний шаг', () => {
    hx.messages = [msg({ id: 'a1', content: 'итог', chainSteps: steps, hideSequentialOutputs: true })];
    const { container } = renderPage();
    const card = cards(container)[0];
    expect(within(card).queryByText('Первый')).toBeNull();
    expect(within(card).getAllByText('Третий')).toHaveLength(2);
  });

  it('при стриминге цепочка не раскрывается шагами, а рисуется обычное тело', () => {
    hx.messages = [msg({ id: 'a1', content: 'идёт ответ', chainSteps: steps, isStreaming: true })];
    const { container } = renderPage();
    const card = cards(container)[0];
    expect(within(card).queryByText('Первый')).toBeNull();
    expect(within(card).getByText('идёт ответ')).toBeInTheDocument();
  });
});

describe('UnifiedChatPage: follow-up подсказки в сообщении', () => {
  const sg = [{ id: 's1', title: 'Подсказка 1', prompt: 'расскажи подробнее' }];

  it('показываются у последнего сообщения и не показываются у предыдущих (scope=last)', () => {
    hx.messages = [
      msg({ id: 'a1', content: 'один', followUpSuggestions: sg }),
      userMsg({ id: 'u2' }),
      msg({ id: 'a2', content: 'два', followUpSuggestions: sg }),
    ];
    const { container } = renderPage();
    const cs = cards(container);
    expect(within(cs[0]).queryByTestId('follow-up')).toBeNull();
    expect(within(cs[2]).getByTestId('follow-up')).toBeInTheDocument();
  });

  it('scope=all показывает подсказки у всех сообщений', () => {
    localStorage.setItem('follow_up_show_scope', 'all');
    hx.messages = [
      msg({ id: 'a1', content: 'один', followUpSuggestions: sg }),
      userMsg({ id: 'u2' }),
      msg({ id: 'a2', content: 'два', followUpSuggestions: sg }),
    ];
    const { container } = renderPage();
    const cs = cards(container);
    expect(within(cs[0]).getByTestId('follow-up')).toBeInTheDocument();
    expect(within(cs[2]).getByTestId('follow-up')).toBeInTheDocument();
  });

  it('следующий по умолчанию клик (insert) подставляет текст подсказки в поле ввода', () => {
    hx.messages = [msg({ id: 'a1', content: 'один', followUpSuggestions: sg })];
    renderPage();
    fireEvent.click(screen.getByTestId('follow-up-0'));
    expect(screen.getByTestId('input-field')).toHaveValue('расскажи подробнее');
  });

  it('действие «send» сразу отправляет подсказку (при выбранной модели)', () => {
    localStorage.setItem('follow_up_click_action', 'send');
    localStorage.setItem('last_selected_model_path', 'prov/model-a');
    hx.messages = [msg({ id: 'a1', content: 'один', followUpSuggestions: sg })];
    renderPage();
    fireEvent.click(screen.getByTestId('follow-up-0'));
    expect(hx.socket.sendMessage).toHaveBeenCalledWith('расскажи подробнее', 'c1');
    // перед отправкой подсказки у сообщений очищаются
    expect(hx.actions.patchMessageFields).toHaveBeenCalledWith('c1', 'a1', { followUpSuggestions: undefined });
  });

  it('при выключенной автогенерации подсказки не рисуются, ни в сообщении, ни у поля ввода', () => {
    localStorage.setItem('follow_up_auto_generate', 'false');
    hx.messages = [msg({ id: 'a1', content: 'один', followUpSuggestions: sg })];
    renderPage();
    expect(screen.queryByTestId('follow-up')).toBeNull();
    expect(screen.queryByTestId('input-suggestions')).toBeNull();
  });

  it('у потокового сообщения подсказки отключены (disabled)', () => {
    hx.messages = [msg({ id: 'a1', content: 'один', isStreaming: true, followUpSuggestions: sg })];
    renderPage();
    expect(screen.getByTestId('follow-up')).toHaveAttribute('data-disabled', 'true');
  });

  it('подсказки поля ввода подставляют текст; они отключены, пока идёт генерация', () => {
    renderPage();
    expect(screen.getByTestId('input-suggestions')).toHaveAttribute('data-disabled', 'false');
    fireEvent.click(screen.getByTestId('sg-pick'));
    expect(screen.getByTestId('input-field')).toHaveValue('подсказка');
    expect(hx.lastSuggestionsProps.suggestions[0].title).toBe('mcp:|rag:false');
  });

  it('каталог подсказок учитывает включённые MCP-серверы и RAG-библиотеку', () => {
    localStorage.setItem('use_kb_rag', 'true');
    hx.activeMcpServers = [{ id: 'srv1', display_name: 'Сервер' }];
    renderPage();
    expect(hx.lastSuggestionsProps.suggestions[0].title).toBe('mcp:srv1|rag:true');
  });

  it('очистка подсказок для сообщений без подсказок ничего не патчит; без чата — тоже', () => {
    localStorage.setItem('follow_up_click_action', 'send');
    localStorage.setItem('last_selected_model_path', 'prov/model-a');
    hx.messages = [
      msg({ id: 'a0', content: 'старый' }),
      userMsg({ id: 'u1' }),
      msg({ id: 'a1', content: 'один', followUpSuggestions: sg }),
    ];
    renderPage();
    fireEvent.click(screen.getByTestId('follow-up-0'));
    expect(hx.actions.patchMessageFields).toHaveBeenCalledTimes(1);
    expect(hx.actions.patchMessageFields).toHaveBeenCalledWith('c1', 'a1', { followUpSuggestions: undefined });
  });
});
