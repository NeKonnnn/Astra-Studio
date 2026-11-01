import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import '@testing-library/jest-dom';
import { hx, resetHarness, installFetch, msg, userMsg } from '@/pages/__test_support__/unifiedChatHarness';
import UnifiedChatPage from '@/pages/UnifiedChatPage';

function renderPage() {
  return render(<UnifiedChatPage isDarkMode={false} />);
}
const input = () => screen.getByTestId('input-field') as HTMLInputElement;
const type = (v: string) => fireEvent.change(input(), { target: { value: v } });

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetHarness();
  installFetch();
  localStorage.setItem('last_selected_model_path', 'prov/model-a');
});

afterEach(() => {
  jest.useRealTimers();
  delete (window as any).AudioContext;
});

describe('UnifiedChatPage: отправка сообщения', () => {
  it('кнопка отправки отключена для пустого ввода и включена при тексте', () => {
    renderPage();
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-send-disabled', 'true');
    type('привет');
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-send-disabled', 'false');
    type('   ');
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-send-disabled', 'true');
  });

  it('отправляет текст в текущий чат, очищает поле и возвращает фокус', () => {
    jest.useFakeTimers();
    renderPage();
    type('  привет  ');
    fireEvent.click(screen.getByTestId('ib-send'));
    expect(hx.socket.sendMessage).toHaveBeenCalledWith('привет', 'c1', true, undefined, undefined, undefined);
    expect(input()).toHaveValue('');
    input().blur();
    act(() => {
      jest.advanceTimersByTime(20);
    });
    expect(input()).toHaveFocus();
  });

  it('Enter отправляет, Shift+Enter и прочие клавиши — нет', () => {
    renderPage();
    type('текст');
    fireEvent.keyPress(input(), { key: 'a', charCode: 97 });
    fireEvent.keyPress(input(), { key: 'Enter', charCode: 13, shiftKey: true });
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
    fireEvent.keyPress(input(), { key: 'Enter', charCode: 13 });
    expect(hx.socket.sendMessage).toHaveBeenCalledTimes(1);
  });

  it('пустое сообщение без вложений не отправляется', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('ib-send'));
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
  });

  it('пока любой чат генерирует, новая отправка блокируется', () => {
    hx.state.loadingChatIds = ['c1']; // генерация в текущем чате (чаты генерируют параллельно)
    renderPage();
    type('привет');
    fireEvent.click(screen.getByTestId('ib-send'));
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-show-stop', 'true');
  });

  it('без выбранной модели показывает баннер и не отправляет', () => {
    localStorage.removeItem('last_selected_model_path');
    renderPage();
    type('привет');
    fireEvent.click(screen.getByTestId('ib-send'));
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
    expect(screen.getByTestId('top-banner-msg')).toHaveTextContent('Модель не выбрана! Пожалуйста, выберите модель');
  });

  it('заглушка провайдера (llm-svc) не считается выбранной моделью', () => {
    localStorage.setItem('last_selected_model_path', 'llm-svc');
    renderPage();
    type('привет');
    fireEvent.click(screen.getByTestId('ib-send'));
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
  });

  it('активный агент позволяет отправлять без выбранной модели', () => {
    localStorage.removeItem('last_selected_model_path');
    localStorage.setItem('active_agent_id', '7');
    renderPage();
    type('привет');
    fireEvent.click(screen.getByTestId('ib-send'));
    expect(hx.socket.sendMessage).toHaveBeenCalled();
  });

  it('нет соединения и нет токена — ошибка и блокировка поля ввода', () => {
    hx.socket.isConnected = false;
    hx.socket.isConnecting = false;
    hx.auth.token = null;
    renderPage();
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-input-disabled', 'true');
    type('привет');
    fireEvent.click(screen.getByTestId('ib-send'));
    expect(hx.actions.showNotification).toHaveBeenCalledWith(
      'error',
      'Нет соединения с сервером. Попробуйте переподключиться.',
    );
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
  });

  it('нет соединения, но есть токен — сообщение ставится в очередь через sendMessage', () => {
    hx.socket.isConnected = false;
    hx.socket.isConnecting = false;
    renderPage();
    type('привет');
    fireEvent.click(screen.getByTestId('ib-send'));
    expect(hx.socket.sendMessage).toHaveBeenCalledWith('привет', 'c1', true, undefined, undefined, undefined);
  });

  it('без текущего чата создаёт новый, отправляет в него с задержкой и очищает вложения', () => {
    jest.useFakeTimers();
    hx.chat = null;
    renderPage();
    type('первое');
    fireEvent.click(screen.getByTestId('ib-send'));
    expect(hx.actions.createChat).toHaveBeenCalledWith('Новый чат');
    expect(hx.actions.setCurrentChat).toHaveBeenCalledWith('new-chat');
    expect(input()).toHaveValue('');
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
    act(() => {
      jest.advanceTimersByTime(60);
    });
    expect(hx.socket.sendMessage).toHaveBeenCalledWith('первое', 'new-chat', true, undefined, undefined, undefined);
  });

  it('отправка во время загрузки файла показывает баннер «подождите»', async () => {
    let release: (v: unknown) => void = () => {};
    installFetch({
      '/documents/attach': () =>
        new Promise((r) => {
          release = r;
        }),
    });
    renderPage();
    const file = new File(['abc'], 'a.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByTestId('input-file'), { target: { files: [file] } });
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-uploading', 'true');
    fireEvent.click(screen.getByTestId('ib-send'));
    expect(screen.getByTestId('top-banner-msg').textContent).toMatch(/подождите/i);
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
    await act(async () => {
      release({ ok: false, status: 500, headers: { get: () => null }, text: async () => '' });
    });
  });
});

describe('UnifiedChatPage: стоп, голос, звук, фокус', () => {
  it('кнопка стоп останавливает генерацию и показывает тост', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('ib-stop'));
    expect(hx.socket.stopGeneration).toHaveBeenCalled();
    expect(hx.actions.showNotification).toHaveBeenCalledWith('info', 'Генерация остановлена');
  });

  it('голосовой диалог открывается и закрывается', () => {
    renderPage();
    expect(screen.queryByTestId('voice-dialog')).toBeNull();
    fireEvent.click(screen.getByTestId('ib-voice'));
    expect(screen.getByTestId('voice-dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('voice-close'));
    expect(screen.queryByTestId('voice-dialog')).toBeNull();
  });

  it('окончание стрима при включённом уведомлении проигрывает звуковой сигнал', () => {
    localStorage.setItem('enable_notification', 'true');
    const osc = { connect: jest.fn(), start: jest.fn(), stop: jest.fn(), frequency: { value: 0 }, type: '' };
    const gain = { connect: jest.fn(), gain: { setValueAtTime: jest.fn(), exponentialRampToValueAtTime: jest.fn() } };
    (window as any).AudioContext = jest.fn(() => ({
      createOscillator: () => osc,
      createGain: () => gain,
      destination: {},
      currentTime: 1,
    }));
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', isStreaming: true, content: 'x' })];
    const { rerender } = renderPage();
    expect((window as any).AudioContext).not.toHaveBeenCalled();
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', isStreaming: false, content: 'x' })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    expect((window as any).AudioContext).toHaveBeenCalledTimes(1);
    expect(osc.frequency.value).toBe(800);
    expect(osc.type).toBe('sine');
    expect(osc.start).toHaveBeenCalledWith(1);
    expect(osc.stop).toHaveBeenCalledWith(1.3);
    expect(gain.gain.setValueAtTime).toHaveBeenCalledWith(0.3, 1);
  });

  it('если AudioContext падает, ошибка проглатывается; при выключенном уведомлении звука нет', () => {
    localStorage.setItem('enable_notification', 'true');
    (window as any).AudioContext = jest.fn(() => {
      throw new Error('no audio');
    });
    hx.messages = [msg({ id: 'a1', isStreaming: true, content: 'x' })];
    const { rerender } = renderPage();
    hx.messages = [msg({ id: 'a1', isStreaming: false, content: 'x' })];
    expect(() => rerender(<UnifiedChatPage isDarkMode={false} />)).not.toThrow();
    expect((window as any).AudioContext).toHaveBeenCalled();
  });

  it('без настройки уведомления звук не создаётся', () => {
    (window as any).AudioContext = jest.fn();
    hx.messages = [msg({ id: 'a1', isStreaming: true, content: 'x' })];
    const { rerender } = renderPage();
    hx.messages = [msg({ id: 'a1', isStreaming: false, content: 'x' })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    expect((window as any).AudioContext).not.toHaveBeenCalled();
  });

  it('после окончания генерации фокус возвращается в поле ввода', async () => {
    hx.state.loadingChatIds = ['c1'];
    const { rerender } = renderPage();
    input().blur();
    hx.state = { ...hx.state, loadingChatIds: [] };
    rerender(<UnifiedChatPage isDarkMode={false} />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(input()).toHaveFocus();
  });
});

describe('UnifiedChatPage: автозаголовок чата', () => {
  it('название берётся из первого сообщения пользователя (до 50 символов)', () => {
    hx.messages = [userMsg({ id: 'u1', content: 'Короткий вопрос' })];
    renderPage();
    expect(hx.actions.updateChatTitle).toHaveBeenCalledWith('c1', 'Короткий вопрос');
  });

  it('длинный текст усекается до 50 символов с многоточием', () => {
    hx.messages = [userMsg({ id: 'u1', content: 'я'.repeat(80) })];
    renderPage();
    expect(hx.actions.updateChatTitle).toHaveBeenCalledWith('c1', `${'я'.repeat(50)}...`);
  });

  it('только вложение — название «Вложение»; пустое сообщение без вложений названия не даёт', () => {
    hx.messages = [
      userMsg({ id: 'u1', content: '', inlineAttachments: [{ name: 'f', contentType: 'text' }] }),
    ];
    const { unmount } = renderPage();
    expect(hx.actions.updateChatTitle).toHaveBeenCalledWith('c1', 'Вложение');
    unmount();
    hx.actions.updateChatTitle.mockClear();
    hx.messages = [userMsg({ id: 'u1', content: '   ' })];
    renderPage();
    expect(hx.actions.updateChatTitle).not.toHaveBeenCalled();
  });

  it('уже переименованный чат и выключенная настройка названия не трогают', () => {
    hx.chat.title = 'Мой чат';
    hx.messages = [userMsg({ id: 'u1', content: 'вопрос' })];
    const { unmount } = renderPage();
    expect(hx.actions.updateChatTitle).not.toHaveBeenCalled();
    unmount();
    hx.chat.title = 'Новый чат';
    localStorage.setItem('auto_generate_titles', 'false');
    renderPage();
    expect(hx.actions.updateChatTitle).not.toHaveBeenCalled();
  });

  it('название задаётся один раз на чат, даже если сообщение перерисовывается', () => {
    hx.messages = [userMsg({ id: 'u1', content: 'вопрос' })];
    const { rerender } = renderPage();
    hx.messages = [...hx.messages, msg({ id: 'a1' })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    expect(hx.actions.updateChatTitle).toHaveBeenCalledTimes(1);
  });

  it('без сообщений пользователя название не меняется', () => {
    hx.messages = [msg({ id: 'a1' })];
    renderPage();
    expect(hx.actions.updateChatTitle).not.toHaveBeenCalled();
  });
});

describe('UnifiedChatPage: индикаторы возле поля ввода (библиотека, агент, skills, MCP, артефакты)', () => {
  it('переключатель библиотеки сохраняет флаг RAG и реагирует на внешнее событие', () => {
    renderPage();
    expect(screen.getByTestId('cluster')).toHaveAttribute('data-library', 'false');
    fireEvent.click(screen.getByTestId('cl-library'));
    expect(localStorage.getItem('use_kb_rag')).toBe('true');
    expect(localStorage.getItem('use_memory_library_rag')).toBe('true');
    // Миграция на базы знаний: индикатор «Общий RAG» скрыт, флаг при этом сохраняется.
    expect(screen.getByTestId('cluster')).toHaveAttribute('data-library', 'false');

    localStorage.setItem('use_kb_rag', 'false');
    localStorage.setItem('use_memory_library_rag', 'false');
    act(() => {
      window.dispatchEvent(new CustomEvent('astrachat:knowledgeRag'));
    });
    expect(screen.getByTestId('cluster')).toHaveAttribute('data-library', 'false');
  });

  it('включённый RAG на старте читается из localStorage (индикатор «Общий RAG» скрыт — миграция на KB)', () => {
    localStorage.setItem('use_kb_rag', 'true');
    renderPage();
    expect(screen.getByTestId('cluster')).toHaveAttribute('data-library', 'false');
  });

  it('снятие агента, skills и артефактов вызывает соответствующие очистки и тосты', () => {
    hx.myAgent = { name: 'Бот' };
    hx.activeSkills = [{ name: 'Навык', slug: 'sk' }];
    hx.artifactsIndicator = { active: true, tooltip: 'арт' };
    renderPage();
    fireEvent.click(screen.getByTestId('cl-agent'));
    expect(hx.misc.clearActiveAgent).toHaveBeenCalled();
    expect(hx.actions.showNotification).toHaveBeenCalledWith('info', 'Агент снят');
    fireEvent.click(screen.getByTestId('cl-skills'));
    expect(hx.misc.clearActiveSkills).toHaveBeenCalledWith('c1');
    expect(hx.actions.showNotification).toHaveBeenCalledWith('info', 'Skills отключены');
    fireEvent.click(screen.getByTestId('cl-artifacts'));
    expect(hx.misc.disableChatArtifactsToolsForChat).toHaveBeenCalledWith('c1');
    expect(hx.actions.showNotification).toHaveBeenCalledWith('info', 'Режим артефактов отключён');
  });

  it('без активного агента/skills/артефактов кнопок снятия нет', () => {
    renderPage();
    expect(screen.queryByTestId('cl-agent')).toBeNull();
    expect(screen.queryByTestId('cl-skills')).toBeNull();
    expect(screen.queryByTestId('cl-artifacts')).toBeNull();
  });

  it('без текущего чата снятие артефактов ничего не делает', () => {
    hx.chat = null;
    hx.artifactsIndicator = { active: true, tooltip: 'арт' };
    renderPage();
    fireEvent.click(screen.getByTestId('cl-artifacts'));
    expect(hx.misc.disableChatArtifactsToolsForChat).not.toHaveBeenCalled();
  });

  it('клик по MCP открывает меню инструментов сразу на панели MCP', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cl-mcp'));
    expect(screen.getByTestId('gear-mcp')).toHaveAttribute('data-chat', 'c1');
  });

  it('метки MCP/skills для подсказок учитывают число активных (1 и больше)', () => {
    hx.activeMcpServers = [{ id: 'a', display_name: 'Один' }];
    hx.activeSkills = [{ name: '', slug: '' }];
    const { unmount } = renderPage();
    expect(screen.getByTestId('input-bar')).toBeInTheDocument();
    unmount();
    hx.activeMcpServers = [{ id: 'a', display_name: 'Один' }, { id: 'b', display_name: 'Два' }];
    hx.activeSkills = [{ name: 'S1' }, { slug: 's2' }];
    renderPage();
    expect(hx.lastSuggestionsProps).toBeTruthy();
  });
});
