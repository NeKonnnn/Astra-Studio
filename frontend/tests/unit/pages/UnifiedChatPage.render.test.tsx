import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import '@testing-library/jest-dom';
import { hx, resetHarness, installFetch, msg, userMsg } from '@/pages/__test_support__/unifiedChatHarness';
import UnifiedChatPage from '@/pages/UnifiedChatPage';

function renderPage(props: Partial<React.ComponentProps<typeof UnifiedChatPage>> = {}) {
  return render(<UnifiedChatPage isDarkMode={false} {...props} />);
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetHarness();
  installFetch();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('UnifiedChatPage: пустое состояние и приветствие', () => {
  it('рендерит приветствие с именем пользователя и поле ввода для пустого чата', () => {
    renderPage();
    expect(screen.getByTestId('input-bar')).toBeInTheDocument();
    expect(screen.getByText(/Иван Петров/)).toBeInTheDocument();
    // ленты сообщений нет, навигационной панели тоже
    expect(screen.queryByTestId('virtuoso')).toBeNull();
    expect(screen.queryByTestId('nav-bar')).toBeNull();
  });

  it('использует username, если full_name не задан, и обходится без имени вообще', () => {
    hx.auth.user = { username: 'only_login' };
    const { unmount } = renderPage();
    expect(screen.getByText(/, only_login/)).toBeInTheDocument();
    unmount();

    hx.auth.user = null;
    renderPage();
    const heading = screen.getByRole('heading', { level: 4 });
    expect(heading.textContent).not.toContain(',');
  });

  it.each([
    ['2025-03-01T02:00:00Z', 'Доброе утро'], // 05:00 МСК
    ['2025-03-01T09:00:00Z', 'Добрый день'], // 12:00 МСК
    ['2025-03-01T15:00:00Z', 'Добрый вечер'], // 18:00 МСК
    ['2025-03-01T20:00:00Z', 'Доброй ночи'], // 23:00 МСК
  ])('приветствие по московскому времени %s -> %s', (iso, expected) => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(iso));
    renderPage();
    expect(screen.getByRole('heading', { level: 4 }).textContent).toBe(`${expected}, Иван Петров`);
  });

  it('пока стор не инициализирован, приветствие не показывается, а нижнее поле ввода ждёт сообщений', () => {
    hx.state.isInitialized = false;
    renderPage();
    expect(screen.queryByRole('heading', { level: 4 })).toBeNull();
    expect(screen.queryByTestId('input-bar')).toBeNull();
  });

  it('при выгруженном чате показывает спиннер истории только после задержки 350 мс', () => {
    jest.useFakeTimers();
    hx.chat.messagesUnloaded = true;
    renderPage();
    expect(screen.queryByText('Загрузка переписки…')).toBeNull();
    // нижнее поле ввода тоже ещё не отрисовано
    expect(screen.queryByTestId('input-bar')).toBeNull();
    act(() => {
      jest.advanceTimersByTime(360);
    });
    expect(screen.getByText('Загрузка переписки…')).toBeInTheDocument();
    expect(screen.getByTestId('input-bar')).toBeInTheDocument();
  });

  it('если у выгруженного чата уже есть сообщения, спиннер не показывается', () => {
    jest.useFakeTimers();
    hx.chat.messagesUnloaded = true;
    hx.messages = [userMsg({ id: 'u1' })];
    renderPage();
    act(() => {
      jest.advanceTimersByTime(500);
    });
    expect(screen.queryByText('Загрузка переписки…')).toBeNull();
    expect(screen.getByTestId('virtuoso')).toBeInTheDocument();
  });
});

describe('UnifiedChatPage: заголовок, селекторы модели и режим сравнения', () => {
  it('по умолчанию (режим workspace_agent) показывает AgentSelector и кнопку сравнения моделей', () => {
    renderPage();
    expect(screen.getByTestId('agent-selector')).toBeInTheDocument();
    expect(screen.queryByTestId('model-selector')).toBeNull();
    expect(screen.getByLabelText('Сравнение моделей')).toBeInTheDocument();
    // колбэк-заглушка AgentSelector не должен падать
    fireEvent.click(screen.getByTestId('agent-selector-pick'));
  });

  it('режим workspace показывает ModelSelector без проекта', () => {
    localStorage.setItem('model_selector_mode', 'workspace');
    renderPage();
    expect(screen.getByTestId('model-selector')).toBeInTheDocument();
    expect(screen.queryByTestId('agent-selector')).toBeNull();
    fireEvent.click(screen.getByTestId('model-selector-pick'));
  });

  it('старый флаг show_model_selector_in_settings=true переводит в режим settings: селекторов нет, кнопка сравнения в поле ввода', () => {
    localStorage.setItem('show_model_selector_in_settings', 'true');
    renderPage();
    expect(screen.queryByTestId('agent-selector')).toBeNull();
    expect(screen.queryByTestId('model-selector')).toBeNull();
    expect(screen.getByTestId('ib-extra').querySelector('button')).not.toBeNull();
  });

  it('в режиме workspace_agent кнопки сравнения в поле ввода нет', () => {
    renderPage();
    expect(screen.getByTestId('ib-extra').querySelector('button')).toBeNull();
  });

  it('событие interfaceSettingsChanged перечитывает режим селектора и видимость панели диалогов', () => {
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1' })];
    renderPage();
    expect(screen.getByTestId('agent-selector')).toBeInTheDocument();
    expect(screen.getByTestId('nav-bar')).toBeInTheDocument();

    localStorage.setItem('model_selector_mode', 'workspace');
    localStorage.setItem('show_dialogues_panel', 'false');
    act(() => {
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });
    expect(screen.getByTestId('model-selector')).toBeInTheDocument();
    expect(screen.queryByTestId('nav-bar')).toBeNull();
  });

  it('show_dialogues_panel=false на старте скрывает панель навигации по сообщениям', () => {
    localStorage.setItem('show_dialogues_panel', 'false');
    hx.messages = [userMsg({ id: 'u1' })];
    renderPage();
    expect(screen.queryByTestId('nav-bar')).toBeNull();
  });

  it('для чата с проектом показывает имя проекта, разделитель и переходит на страницу проекта по клику', () => {
    hx.chat.projectId = 'p7';
    hx.project = { id: 'p7', name: 'Проект Омега', instructions: 'инструкции' };
    renderPage();
    const name = screen.getByText('Проект Омега');
    fireEvent.click(name);
    expect(hx.navigate).toHaveBeenCalledWith('/project/p7');
    expect(hx.actions.getProjectById).toHaveBeenCalledWith('p7');
    expect(screen.getByText('/')).toBeInTheDocument();
    expect(screen.getByTestId('agent-selector')).toBeInTheDocument();
    // инструкции проекта пробрасываются в расчёт контекста
    expect(hx.lastContextUsageArgs.projectInstructions).toBe('инструкции');
  });

  it('для чата с проектом в режиме workspace показывается ModelSelector рядом с именем проекта', () => {
    localStorage.setItem('model_selector_mode', 'workspace');
    hx.chat.projectId = 'p7';
    hx.project = { id: 'p7', name: 'Проект Омега' };
    renderPage();
    expect(screen.getByTestId('model-selector')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('model-selector-pick'));
    expect(hx.lastContextUsageArgs.projectInstructions).toBeNull();
  });

  it('в режиме settings для чата с проектом нет ни селекторов, ни кнопки сравнения в заголовке', () => {
    localStorage.setItem('model_selector_mode', 'settings');
    hx.chat.projectId = 'p7';
    hx.project = { id: 'p7', name: 'Проект' };
    renderPage();
    expect(screen.queryByTestId('agent-selector')).toBeNull();
    // единственная кнопка сравнения — в поле ввода (extraActions), а не в заголовке
    const buttons = screen.getAllByLabelText('Сравнение моделей');
    expect(buttons).toHaveLength(1);
    expect(screen.getByTestId('ib-extra')).toContainElement(buttons[0]);
  });

  it('кнопка сравнения моделей в заголовке чата с проектом переключает режим', () => {
    hx.chat.projectId = 'p7';
    hx.project = { id: 'p7', name: 'Проект' };
    renderPage();
    fireEvent.click(screen.getByLabelText('Сравнение моделей').querySelector('button') as HTMLElement);
    expect(localStorage.getItem('model_comparison_enabled')).toBe('true');
    expect(hx.actions.showNotification).toHaveBeenCalledWith('info', 'Режим: сравнение моделей');
  });

  it('позиция заголовка зависит от sidebarHidden/sidebarOpen', () => {
    const { container, rerender } = renderPage({ sidebarOpen: false, sidebarHidden: false });
    const header = () => container.querySelector('[class*="MuiBox-root"]') as HTMLElement;
    expect(header()).not.toBeNull();
    rerender(<UnifiedChatPage isDarkMode={false} sidebarOpen={false} sidebarHidden />);
    rerender(<UnifiedChatPage isDarkMode />);
    expect(screen.getByTestId('input-bar')).toBeInTheDocument();
  });
});

describe('UnifiedChatPage: фон рабочей зоны', () => {
  it('starry показывает звёздное небо', () => {
    hx.workZoneMode = 'starry';
    renderPage();
    expect(screen.getByTestId('starry')).toBeInTheDocument();
    expect(screen.queryByTestId('snowfall')).toBeNull();
  });

  it('snowfall показывает снегопад', () => {
    hx.workZoneMode = 'snowfall';
    renderPage();
    expect(screen.getByTestId('snowfall')).toBeInTheDocument();
    expect(screen.queryByTestId('starry')).toBeNull();
  });

  it('custom с картинкой подставляет её как фон рабочей зоны', () => {
    localStorage.setItem('work_zone_bg_custom_image', 'data:image/png;base64,AAA');
    hx.workZoneMode = 'custom';
    const { container } = renderPage();
    const zone = container.querySelector('.fullscreen-chat') as HTMLElement;
    expect(getComputedStyle(zone).backgroundImage).toContain('data:image/png;base64,AAA');
  });
});

describe('UnifiedChatPage: события окна', () => {
  it('ASTRA_INSERT_CHAT_TEXT вставляет текст в поле ввода и фокусирует его', () => {
    renderPage();
    act(() => {
      window.dispatchEvent(new CustomEvent('astra_insert_chat_text', { detail: { text: 'вставка' } }));
    });
    expect(screen.getByTestId('input-field')).toHaveValue('вставка');
    expect(screen.getByTestId('input-field')).toHaveFocus();
  });

  it('ASTRA_INSERT_CHAT_TEXT с пустым/нестроковым text игнорируется', () => {
    renderPage();
    act(() => {
      window.dispatchEvent(new CustomEvent('astra_insert_chat_text', { detail: { text: 5 } }));
      window.dispatchEvent(new CustomEvent('astra_insert_chat_text'));
    });
    expect(screen.getByTestId('input-field')).toHaveValue('');
  });

  it('текст, отложенный в sessionStorage до монтирования, удаляется из хранилища при монтировании', () => {
    sessionStorage.setItem('astra_insert_chat_text_pending', 'отложенный текст');
    renderPage();
    expect(sessionStorage.getItem('astra_insert_chat_text_pending')).toBeNull();
  });

  // BUG-FE-21: эффект сброса черновика не срабатывает на монтировании, поэтому отложенный текст
  // из sessionStorage остаётся в поле ввода.
  it('отложенный в sessionStorage текст не затирается сбросом черновика при монтировании', () => {
    sessionStorage.setItem('astra_insert_chat_text_pending', 'отложенный текст');
    renderPage();
    expect(screen.getByTestId('input-field')).toHaveValue('отложенный текст');
  });

  it('если sessionStorage недоступен, монтирование не падает', () => {
    const spy = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, k: string) {
      if (k === 'astra_insert_chat_text_pending') throw new Error('denied');
      return null;
    });
    try {
      renderPage();
      expect(screen.getByTestId('input-bar')).toBeInTheDocument();
    } finally {
      spy.mockRestore();
    }
  });

  it('ASTRA_TRIGGER_ATTACH кликает по скрытому input[type=file]', () => {
    renderPage();
    const fileInput = screen.getByTestId('input-file') as HTMLInputElement;
    const click = jest.spyOn(fileInput, 'click').mockImplementation(() => {});
    act(() => {
      window.dispatchEvent(new Event('astrachat:trigger-attach'));
    });
    expect(click).toHaveBeenCalledTimes(1);
  });

  it('agentSelected(null) ничего не делает, с config применяет MCP агента, без config берёт сохранённый', () => {
    renderPage();
    hx.applyAgent.applyStoredAgentMcpToChat.mockClear();
    act(() => {
      window.dispatchEvent(new CustomEvent('agentSelected', { detail: null }));
    });
    expect(hx.applyAgent.applyAgentMcpToChat).not.toHaveBeenCalled();
    expect(hx.applyAgent.applyStoredAgentMcpToChat).not.toHaveBeenCalled();

    const cfg = { mcp_enabled: true };
    act(() => {
      window.dispatchEvent(new CustomEvent('agentSelected', { detail: { config: cfg } }));
    });
    expect(hx.applyAgent.applyAgentMcpToChat).toHaveBeenCalledWith('c1', cfg);

    act(() => {
      window.dispatchEvent(new CustomEvent('agentSelected', { detail: {} }));
    });
    expect(hx.applyAgent.applyStoredAgentMcpToChat).toHaveBeenCalledWith('c1');
  });

  it('при монтировании применяет сохранённый MCP агента для текущего чата', () => {
    renderPage();
    expect(hx.applyAgent.applyStoredAgentMcpToChat).toHaveBeenCalledWith('c1');
  });

  it('смена текущего чата очищает черновик в поле ввода', () => {
    const { rerender } = renderPage();
    fireEvent.change(screen.getByTestId('input-field'), { target: { value: 'черновик' } });
    expect(screen.getByTestId('input-field')).toHaveValue('черновик');
    hx.state = { ...hx.state, currentChatId: 'c2' };
    rerender(<UnifiedChatPage isDarkMode={false} />);
    expect(screen.getByTestId('input-field')).toHaveValue('');
  });
});

describe('UnifiedChatPage: права и фокус', () => {
  it('без права canUseAgents сбрасывает активного агента', () => {
    hx.featureAccess = { canUseAgents: false };
    renderPage();
    expect(hx.misc.clearActiveAgent).toHaveBeenCalled();
  });

  it('с правом canUseAgents активного агента не трогает', () => {
    renderPage();
    expect(hx.misc.clearActiveAgent).not.toHaveBeenCalled();
  });

  it('фокусирует поле ввода при монтировании и через 300 мс', () => {
    jest.useFakeTimers();
    renderPage();
    expect(screen.getByTestId('input-field')).toHaveFocus();
    (screen.getByTestId('input-field') as HTMLElement).blur();
    act(() => {
      jest.advanceTimersByTime(350);
    });
    expect(screen.getByTestId('input-field')).toHaveFocus();
  });
});

describe('UnifiedChatPage: перетаскивание файлов', () => {
  const area = (c: HTMLElement) => c.querySelector('.chat-messages-area') as HTMLElement;

  it('dragOver с файлами показывает подсказку, dragLeave убирает', () => {
    const { container } = renderPage();
    const dt = { items: [{ kind: 'file' }], types: ['Files'], files: [], dropEffect: '' };
    fireEvent.dragOver(area(container), { dataTransfer: dt });
    expect(screen.getByText('Отпустите файл для загрузки')).toBeInTheDocument();
    expect(dt.dropEffect).toBe('copy');
    fireEvent.dragLeave(area(container), { dataTransfer: dt });
    expect(screen.queryByText('Отпустите файл для загрузки')).toBeNull();
  });

  it('dragOver с выделенным текстом (без файлов) зону загрузки не показывает', () => {
    const { container } = renderPage();
    fireEvent.dragOver(area(container), {
      dataTransfer: { items: [{ kind: 'string' }], types: ['text/plain'], files: [] },
    });
    expect(screen.queryByText('Отпустите файл для загрузки')).toBeNull();
    // dragLeave без файлов тоже игнорируется
    fireEvent.dragLeave(area(container), {
      dataTransfer: { items: [{ kind: 'string' }], types: ['text/plain'], files: [] },
    });
  });

  it('dragOver без dataTransfer игнорируется', () => {
    const { container } = renderPage();
    fireEvent.dragOver(area(container));
    expect(screen.queryByText('Отпустите файл для загрузки')).toBeNull();
  });

  it('определение файлов по types.contains и по массиву types, а items с ошибкой не ломают проверку', () => {
    const { container } = renderPage();
    // DOMStringList-подобный объект с contains
    fireEvent.dragOver(area(container), {
      dataTransfer: { types: { length: 1, contains: (s: string) => s === 'Files' }, files: [] },
    });
    expect(screen.getByText('Отпустите файл для загрузки')).toBeInTheDocument();
    fireEvent.dragLeave(area(container), {
      dataTransfer: { types: { length: 1, contains: (s: string) => s === 'Files' }, files: [] },
    });
    expect(screen.queryByText('Отпустите файл для загрузки')).toBeNull();

    // items бросает исключение -> переходим к types
    const throwing = {
      get items(): never {
        throw new Error('boom');
      },
      types: ['Files'],
      files: [],
    };
    fireEvent.dragOver(area(container), { dataTransfer: throwing });
    expect(screen.getByText('Отпустите файл для загрузки')).toBeInTheDocument();
    fireEvent.dragLeave(area(container), { dataTransfer: { types: ['Files'], files: [] } });

    // пустые types
    fireEvent.dragOver(area(container), { dataTransfer: { types: [], files: [] } });
    expect(screen.queryByText('Отпустите файл для загрузки')).toBeNull();
    // types отсутствуют
    fireEvent.dragOver(area(container), { dataTransfer: { files: [] } });
    expect(screen.queryByText('Отпустите файл для загрузки')).toBeNull();
    // массив без Files
    fireEvent.dragOver(area(container), { dataTransfer: { types: ['text/plain'], files: [] } });
    expect(screen.queryByText('Отпустите файл для загрузки')).toBeNull();
  });

  it('drop без файлов только снимает подсветку и не запускает загрузку', () => {
    const fetchMock = installFetch();
    const { container } = renderPage();
    const dt = { items: [{ kind: 'file' }], types: ['Files'], files: [] };
    fireEvent.dragOver(area(container), { dataTransfer: dt });
    fireEvent.drop(area(container), { dataTransfer: dt });
    expect(screen.queryByText('Отпустите файл для загрузки')).toBeNull();
    const uploads = fetchMock.mock.calls.filter((c) => String(c[0]).includes('/documents/attach'));
    expect(uploads).toHaveLength(0);
  });
});

describe('UnifiedChatPage: колесо мыши над областью сообщений', () => {
  it('колесо вне ленты прокручивает scroller Virtuoso, а над полями ввода/попапами — нет', () => {
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1' })];
    const { container } = renderPage();
    const scroller = hx.virt.scrollerEl as HTMLDivElement;
    scroller.scrollTop = 10;
    const area = container.querySelector('.chat-messages-area') as HTMLElement;

    // внутри scroller — игнор
    fireEvent.wheel(scroller, { deltaY: 50 });
    expect(scroller.scrollTop).toBe(10);

    // на самой области (вне scroller) — прокручиваем scroller
    fireEvent.wheel(area, { deltaY: 50 });
    expect(scroller.scrollTop).toBe(60);

    // над input внутри области — игнор
    const inner = document.createElement('input');
    area.appendChild(inner);
    fireEvent.wheel(inner, { deltaY: 40 });
    expect(scroller.scrollTop).toBe(60);
  });

  it('без scroller (пустой чат) колесо ничего не делает', () => {
    const { container } = renderPage();
    const area = container.querySelector('.chat-messages-area') as HTMLElement;
    fireEvent.wheel(area, { deltaY: 50 });
    expect(hx.virt.scrollerEl).toBeNull();
  });
});
