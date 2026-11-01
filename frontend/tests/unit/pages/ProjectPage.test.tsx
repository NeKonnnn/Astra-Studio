/* eslint-disable @typescript-eslint/no-explicit-any */
import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import ProjectPage from '@/pages/ProjectPage';

// ---------------------------------------------------------------------------
// Управляемое состояние моков
// ---------------------------------------------------------------------------
const mock: any = {};
function reset() {
  mock.projectId = 'p1';
  mock.navigate = jest.fn();
  mock.project = { id: 'p1', name: 'Мой проект', icon: '', iconType: undefined, iconColor: undefined };
  mock.chats = [];
  mock.currentChatId = null;
  mock.chatsById = {} as Record<string, any>;
  mock.actions = {
    getProjectById: jest.fn(() => mock.project),
    setCurrentChat: jest.fn(),
    createChat: jest.fn(() => 'new-chat'),
    moveChatToProject: jest.fn(),
    updateChatTitle: jest.fn(),
    deleteChat: jest.fn(),
    archiveChat: jest.fn(),
    getChatById: jest.fn((id: string) => mock.chatsById[id]),
    moveChatToFolder: jest.fn(),
    togglePinInProject: jest.fn(),
    showNotification: jest.fn(),
  };
  mock.socket = { sendMessage: jest.fn(async () => undefined), isConnected: true, isConnecting: false };
  mock.canUseAgents = true;
  mock.myAgent = null;
  mock.rag = { shouldBlockRagSend: jest.fn(() => false), blockMessage: '', memoryRagEnabled: true };
  mock.mcpServers = [];
  mock.mcpTools = [];
  mock.skills = [];
  mock.artifacts = { active: false, tooltip: '' };
  mock.autoSuggest = true;
  mock.insetArgs = null;
  mock.clearActiveAgent = jest.fn();
  mock.clearActiveSkills = jest.fn();
  mock.disableArtifacts = jest.fn();
  mock.copyMcp = jest.fn();
  mock.copySkills = jest.fn();
  mock.suggestionsCatalogArgs = null;
  mock.ro = null;
  mock.noAnchor = false;
}
reset();

jest.mock('react-router-dom', () => ({
  useParams: () => ({ projectId: mock.projectId }),
  useNavigate: () => mock.navigate,
}));
jest.mock('@/icons/AgentIcon', () => ({ __esModule: true, default: () => <span /> }));
jest.mock('@/icons/McpIcon', () => ({ __esModule: true, default: () => <span /> }));
jest.mock('@/icons/SkillsIcon', () => ({ __esModule: true, default: () => <span /> }));
jest.mock('@/icons/ArtifactsIcon', () => ({ __esModule: true, default: () => <span /> }));
jest.mock('@/icons/ThinkingModeIcon', () => ({ __esModule: true, default: () => <span /> }));
jest.mock('@/icons/ThinkingBulbIcon', () => ({ __esModule: true, default: () => <span /> }));
jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: { chats: mock.chats, currentChatId: mock.currentChatId } }),
  useAppActions: () => mock.actions,
  chatIsListedInAllChatsSection: (c: any) => !c.hidden,
}));
jest.mock('@/contexts/SocketContext', () => ({ useSocket: () => mock.socket }));
jest.mock('@/components/VoiceChatDialog', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="voice-dialog" data-open={String(p.open)}>
      <button onClick={p.onClose}>voice-close</button>
    </div>
  ),
}));
jest.mock('@/components/ChatInputBar', () => ({
  __esModule: true,
  default: (p: any) => (
    <div
      data-testid="input-bar"
      data-placeholder={p.placeholder}
      data-style={p.styleVariant}
      data-solid={String(p.solidWorkZoneBackground)}
      ref={(el) => {
        if (el && !mock.noAnchor) {
          el.getBoundingClientRect = () =>
            ({ top: 500, width: 400, height: 50, left: 0, right: 400, bottom: 550, x: 0, y: 500, toJSON() {} } as any);
          p.toolsMenuAnchorRef.current = el;
        }
      }}
    >
      <input
        data-testid="msg-input"
        ref={p.inputRef}
        value={p.value}
        disabled={p.inputDisabled}
        onChange={(e) => p.onChange(e.target.value)}
        onKeyPress={p.onKeyPress}
      />
      <input data-testid="file-input" type="file" ref={p.fileInputRef} onChange={(e) => p.onFileSelect(e.target.files)} />
      <button data-testid="send" disabled={p.sendDisabled} onClick={p.onSendClick}>send</button>
      <button data-testid="settings" disabled={p.settingsDisabled} onClick={p.onSettingsClick}>settings</button>
      <button data-testid="voice" disabled={p.voiceDisabled} onClick={p.onVoiceClick}>voice</button>
      <button data-testid="attach" disabled={p.attachDisabled} onClick={p.onAttachClick}>attach</button>
      <button data-testid="select-empty" onClick={() => p.onFileSelect([])}>select-empty</button>
      <button data-testid="select-files" onClick={() => p.onFileSelect([{ name: 'a.txt', type: 'text/plain' }, { name: 'b.png', type: 'image/png' }])}>select-files</button>
      <div data-testid="uploaded">
        {p.uploadedFiles.map((f: any, i: number) => (
          <span key={i}>
            {f.name}
            <button onClick={() => p.onFileRemove(f, i)}>rm-{f.name}</button>
          </span>
        ))}
      </div>
      <div data-testid="badge">{p.libraryBadge}</div>
      <div data-testid="input-suggestions">{p.inputSuggestions}</div>
    </div>
  ),
}));
jest.mock('@/components/right_bar', () => ({
  ASTRA_INSERT_CHAT_TEXT: 'astra:insert-chat-text',
  ASTRA_INSERT_CHAT_TEXT_KEY: 'astra_insert_chat_text',
}));
jest.mock('@/components/ChatInputStatusCluster', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="cluster" data-lib={String(p.libraryActive)} data-agent={String(p.myAgentName)} data-art={String(p.artifactsActive)}>
      <button onClick={p.onLibraryToggle}>cl-lib</button>
      {p.onAgentToggle && <button onClick={p.onAgentToggle}>cl-agent</button>}
      {p.onSkillsToggle && <button onClick={p.onSkillsToggle}>cl-skills</button>}
      <button onClick={p.onMcpClick}>cl-mcp</button>
      {p.onArtifactsToggle && <button onClick={p.onArtifactsToggle}>cl-art</button>}
    </div>
  ),
}));
jest.mock('@/components/ChatGearAgentsPanel', () => ({ __esModule: true, default: () => <div data-testid="panel-agents" /> }));
jest.mock('@/components/ChatGearArtifactsPanel', () => ({ __esModule: true, default: (p: any) => <div data-testid="panel-artifacts" data-chat={p.chatId} /> }));
jest.mock('@/components/ChatGearMcpPanel', () => ({ __esModule: true, default: (p: any) => <div data-testid="panel-mcp" data-chat={p.chatId} /> }));
jest.mock('@/components/ChatGearSkillsPanel', () => ({ __esModule: true, default: (p: any) => <div data-testid="panel-skills" data-chat={p.chatId} /> }));
jest.mock('@/components/ChatInputSuggestions', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="suggestions" data-input={p.inputValue} data-disabled={String(p.disabled)} data-max={String(p.maxWidth)} data-inset={String(p.contentInset)}>
      <button onClick={() => p.onSelect('Подсказка')}>pick-suggestion</button>
    </div>
  ),
}));
jest.mock('@/components/chatInputLayout', () => ({
  estimateLibraryClusterWidthPx: (...a: any[]) => {
    mock.insetArgs = a;
    return 123;
  },
  getToolsButtonInsetSp: (style: string, w: number) => `${style}:${w}`,
}));
jest.mock('@/chat/getChatInputSuggestions', () => ({
  getChatInputSuggestions: (...a: any[]) => {
    mock.suggestionsCatalogArgs = a;
    return ['s1'];
  },
}));
jest.mock('@/chat/followUpSettings', () => ({
  loadFollowUpSettings: () => ({ followUpAutoGenerate: mock.autoSuggest }),
}));
jest.mock('@/mcp/hooks/useChatInputMcpIndicators', () => ({ useChatInputMcpIndicators: () => mock.mcpServers }));
jest.mock('@/mcp/hooks/useMcpStreamingTools', () => ({ useMcpStreamingTools: () => ({ activeMcpTools: mock.mcpTools }) }));
jest.mock('@/mcp/components/McpLiveToolsIndicator', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="live-tools">{p.tools.length}</div>,
}));
jest.mock('@/mcp/selectionStorage', () => ({
  copyMcpToolIds: (...a: any[]) => mock.copyMcp(...a),
  projectMcpChatKey: (id: string) => `project:${id}`,
}));
jest.mock('@/hooks/useChatInputArtifactsIndicator', () => ({ useChatInputArtifactsIndicator: () => mock.artifacts }));
jest.mock('@/hooks/useActiveSkillIndicators', () => ({ useActiveSkillIndicators: () => mock.skills }));
jest.mock('@/hooks/useFeatureAccess', () => ({ useFeatureAccess: () => ({ canUseAgents: mock.canUseAgents }) }));
jest.mock('@/hooks/useChatInputAgentIndicators', () => ({ useMyAgentSelection: () => mock.myAgent }));
jest.mock('@/hooks/useRagReindexStatus', () => ({ useRagReindexStatus: () => mock.rag }));
jest.mock('@/components/ModelSelector', () => ({ __esModule: true, default: () => <div data-testid="model-selector" /> }));
jest.mock('@/components/AgentSelector', () => ({ __esModule: true, default: () => <div data-testid="agent-selector" /> }));
jest.mock('@/utils/clearActiveAgent', () => ({ clearActiveAgent: (...a: any[]) => mock.clearActiveAgent(...a) }));
jest.mock('@/utils/skillSelectionStorage', () => ({
  clearActiveSkills: (...a: any[]) => mock.clearActiveSkills(...a),
  copyActiveSkills: (...a: any[]) => mock.copySkills(...a),
}));
jest.mock('@/utils/artifactsSelectionStorage', () => ({
  disableChatArtifactsToolsForChat: (...a: any[]) => mock.disableArtifacts(...a),
}));
jest.mock('@/components/WorkZoneStarrySky', () => ({ __esModule: true, default: () => <div data-testid="starry" /> }));
jest.mock('@/components/WorkZoneSnowfall', () => ({ __esModule: true, default: () => <div data-testid="snow" /> }));

const NOW = new Date('2025-06-10T12:00:00');
const chat = (over: any = {}) => ({
  id: 'c1',
  title: 'Чат 1',
  projectId: 'p1',
  isArchived: false,
  isPinnedInProject: false,
  updatedAt: '2025-06-10T10:00:00',
  ...over,
});
const input = () => screen.getByTestId('msg-input') as HTMLInputElement;

function renderPage(props: any = {}) {
  return render(<ProjectPage {...props} />);
}

describe('ProjectPage', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
    jest.setSystemTime(NOW);
    reset();
    localStorage.clear();
    sessionStorage.clear();
    (global as any).ResizeObserver = undefined;
    jest.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
    delete (global as any).ResizeObserver;
  });

  describe('шапка и состояния', () => {
    it('проект не найден: сообщение и возврат на главную', () => {
      mock.project = null;
      renderPage();
      expect(screen.getByText('Проект не найден')).toBeTruthy();
      fireEvent.click(screen.getByText('Вернуться на главную'));
      expect(mock.navigate).toHaveBeenCalledWith('/');
    });

    it('показывает название проекта и запрашивает проект по id из URL', () => {
      renderPage();
      expect(screen.getByRole('heading', { name: 'Мой проект' })).toBeTruthy();
      expect(mock.actions.getProjectById).toHaveBeenCalledWith('p1');
    });

    it('без projectId в URL проект не запрашивается', () => {
      mock.projectId = undefined;
      renderPage();
      expect(mock.actions.getProjectById).not.toHaveBeenCalled();
      expect(screen.getByText('Проект не найден')).toBeTruthy();
    });

    it.each([
      ['emoji', { icon: '🚀', iconType: 'emoji', iconColor: '#f00' }, () => expect(screen.getByText('🚀')).toBeTruthy()],
      ['icon-неизвестная', { icon: 'no-such-icon', iconType: 'icon' }, () => expect(screen.getByTestId('FolderOutlinedIcon')).toBeTruthy()],
      ['по умолчанию', { icon: '', iconType: undefined }, () => expect(screen.getByTestId('FolderOutlinedIcon')).toBeTruthy()],
    ])('иконка проекта: %s', (_n, patch, check) => {
      mock.project = { ...mock.project, ...patch };
      renderPage();
      (check as () => void)();
    });

    it('иконка из справочника отрисовывается как svg выбранного типа', () => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { PROJECT_ICON_MAP } = require('../constants/projectIcons');
      const key = Object.keys(PROJECT_ICON_MAP).find((k) => k !== 'FolderOutlined') || Object.keys(PROJECT_ICON_MAP)[0];
      mock.project = { ...mock.project, icon: key, iconType: 'icon', iconColor: '#0f0' };
      renderPage();
      expect(document.querySelector('h4')!.previousElementSibling!.querySelector('svg')).not.toBeNull();
    });

    it('отключённый доступ к агентам сбрасывает активного агента', () => {
      mock.canUseAgents = false;
      renderPage();
      expect(mock.clearActiveAgent).toHaveBeenCalled();
    });

    it('при доступе к агентам активный агент не сбрасывается', () => {
      renderPage();
      expect(mock.clearActiveAgent).not.toHaveBeenCalled();
    });
  });

  describe('режимы селектора и оформление', () => {
    it('по умолчанию — селектор агента; workspace — селектор модели; settings — без селектора', () => {
      const a = renderPage();
      expect(screen.getByTestId('agent-selector')).toBeTruthy();
      a.unmount();
      localStorage.setItem('model_selector_mode', 'workspace');
      const b = renderPage();
      expect(screen.getByTestId('model-selector')).toBeTruthy();
      expect(screen.queryByTestId('agent-selector')).toBeNull();
      b.unmount();
      localStorage.setItem('model_selector_mode', 'settings');
      renderPage();
      expect(screen.queryByTestId('model-selector')).toBeNull();
      expect(screen.queryByTestId('agent-selector')).toBeNull();
    });

    it('устаревший ключ show_model_selector_in_settings=true включает режим settings', () => {
      localStorage.setItem('show_model_selector_in_settings', 'true');
      renderPage();
      expect(screen.queryByTestId('agent-selector')).toBeNull();
    });

    it('смена настроек интерфейса пересчитывает режим селектора, подсказки и стиль поля ввода', () => {
      renderPage();
      expect(screen.getByTestId('suggestions')).toBeTruthy();
      expect(screen.getByTestId('input-bar').getAttribute('data-style')).toBe('compact');
      mock.autoSuggest = false;
      localStorage.setItem('model_selector_mode', 'workspace');
      localStorage.setItem('chat_input_style', 'classic');
      act(() => { window.dispatchEvent(new Event('interfaceSettingsChanged')); });
      expect(screen.queryByTestId('suggestions')).toBeNull();
      expect(screen.getByTestId('model-selector')).toBeTruthy();
      expect(screen.getByTestId('input-bar').getAttribute('data-style')).toBe('classic');
    });

    it('анимированные фоны рабочей зоны', () => {
      localStorage.setItem('work_zone_bg_mode', 'starry');
      const a = renderPage();
      expect(screen.getByTestId('starry')).toBeTruthy();
      expect(screen.getByTestId('input-bar').getAttribute('data-solid')).toBe('true');
      a.unmount();
      localStorage.setItem('work_zone_bg_mode', 'snowfall');
      renderPage();
      expect(screen.getByTestId('snow')).toBeTruthy();
    });

    it('пользовательская картинка фона применяется', () => {
      localStorage.setItem('work_zone_bg_custom_image', 'data:img');
      localStorage.setItem('work_zone_bg_mode', 'custom');
      const { container } = renderPage();
      expect(getComputedStyle(container.firstChild as Element).backgroundImage).toContain('data:img');
    });
  });

  describe('поле ввода и отправка', () => {
    it.each([
      ['обычный', {}, 'Чем я могу помочь вам сегодня?'],
      ['нет соединения', { socket: { isConnected: false, isConnecting: false } }, 'Нет соединения с сервером'],
      ['блок RAG с текстом', { rag: { blockMessage: 'Идёт индексация' } }, 'Идёт индексация'],
      ['блок RAG без текста', { rag: { blockMessage: '' } }, null],
    ])('плейсхолдер: %s', (_n, patch: any, expected) => {
      if (patch.socket) Object.assign(mock.socket, patch.socket);
      if (patch.rag) {
        mock.rag.shouldBlockRagSend = jest.fn(() => true);
        mock.rag.blockMessage = patch.rag.blockMessage;
      }
      renderPage();
      const ph = screen.getByTestId('input-bar').getAttribute('data-placeholder')!;
      if (expected) expect(ph).toBe(expected);
      else expect(ph.length).toBeGreaterThan(0);
      if (patch.rag || patch.socket) expect(input().disabled).toBe(true);
    });

    it('учёт библиотеки при проверке блокировки RAG', () => {
      localStorage.setItem('use_kb_rag', 'true');
      renderPage();
      expect(mock.rag.shouldBlockRagSend).toHaveBeenCalledWith({ libraryEnabled: true });
    });

    it('кнопка отправки заблокирована для пустого ввода и при отсутствии соединения', () => {
      renderPage();
      expect((screen.getByTestId('send') as HTMLButtonElement).disabled).toBe(true);
      fireEvent.change(input(), { target: { value: 'привет' } });
      expect((screen.getByTestId('send') as HTMLButtonElement).disabled).toBe(false);
    });

    it('отправка создаёт чат в проекте, называет его по сообщению, копирует MCP и skills и уходит на главную', async () => {
      renderPage();
      fireEvent.change(input(), { target: { value: '  привет  ' } });
      await act(async () => { fireEvent.click(screen.getByTestId('send')); });
      const a = mock.actions;
      expect(a.createChat).toHaveBeenCalledTimes(1);
      expect(a.moveChatToProject).toHaveBeenCalledWith('new-chat', 'p1');
      expect(a.updateChatTitle).toHaveBeenCalledWith('new-chat', '  привет  ');
      expect(a.setCurrentChat).toHaveBeenCalledWith('new-chat');
      expect(mock.copyMcp).toHaveBeenCalledWith('project:p1', 'new-chat');
      expect(mock.copySkills).toHaveBeenCalledWith('project:p1', 'new-chat');
      expect(mock.socket.sendMessage).toHaveBeenCalledWith('привет', 'new-chat', true, 'p1');
      expect(mock.navigate).toHaveBeenCalledWith('/');
      expect(input().value).toBe('');
    });

    it('длинное сообщение усекается до 50 символов с многоточием в заголовке', async () => {
      renderPage();
      fireEvent.change(input(), { target: { value: 'я'.repeat(80) } });
      await act(async () => { fireEvent.click(screen.getByTestId('send')); });
      expect(mock.actions.updateChatTitle).toHaveBeenCalledWith('new-chat', 'я'.repeat(50) + '...');
    });

    it('Enter отправляет, Shift+Enter нет', async () => {
      renderPage();
      fireEvent.change(input(), { target: { value: 'текст' } });
      fireEvent.keyPress(input(), { key: 'Enter', code: 'Enter', charCode: 13, shiftKey: true });
      expect(mock.socket.sendMessage).not.toHaveBeenCalled();
      await act(async () => { fireEvent.keyPress(input(), { key: 'Enter', code: 'Enter', charCode: 13 }); });
      expect(mock.socket.sendMessage).toHaveBeenCalledTimes(1);
    });

    it('Enter без соединения или с пустым текстом ничего не делает', async () => {
      mock.socket.isConnected = false;
      renderPage();
      // поле заблокировано в UI, но обработчик защищён сам
      fireEvent.change(input(), { target: { value: 'x' } });
      await act(async () => { fireEvent.keyPress(input(), { key: 'Enter', code: 'Enter', charCode: 13 }); });
      expect(mock.actions.createChat).not.toHaveBeenCalled();
    });

    it('отправка при состоянии подключения (isConnecting) разрешена', async () => {
      mock.socket.isConnected = false;
      mock.socket.isConnecting = true;
      renderPage();
      fireEvent.change(input(), { target: { value: 'x' } });
      await act(async () => { fireEvent.click(screen.getByTestId('send')); });
      expect(mock.socket.sendMessage).toHaveBeenCalled();
    });

    it('во время отправки ввод блокируется, плейсхолдер «Отправка сообщения...»', async () => {
      let resolve!: () => void;
      mock.socket.sendMessage.mockReturnValue(new Promise<void>((r) => { resolve = r; }));
      renderPage();
      fireEvent.change(input(), { target: { value: 'x' } });
      await act(async () => { fireEvent.click(screen.getByTestId('send')); });
      expect(screen.getByTestId('input-bar').getAttribute('data-placeholder')).toBe('Отправка сообщения...');
      expect(input().disabled).toBe(true);
      expect((screen.getByTestId('settings') as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByTestId('voice') as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByTestId('attach') as HTMLButtonElement).disabled).toBe(true);
      expect(screen.getByTestId('suggestions').getAttribute('data-disabled')).toBe('true');
      await act(async () => { resolve(); });
      expect(input().disabled).toBe(false);
    });

    it('ошибка отправки логируется, поле очищается и остаётся на странице', async () => {
      mock.socket.sendMessage.mockRejectedValue(new Error('ws down'));
      renderPage();
      fireEvent.change(input(), { target: { value: 'x' } });
      await act(async () => { fireEvent.click(screen.getByTestId('send')); });
      expect(console.error).toHaveBeenCalledWith('Error sending message:', expect.any(Error));
      expect(mock.navigate).not.toHaveBeenCalled();
      expect(input().value).toBe('');
    });

    it('вложения: добавление, удаление по индексу, пустой выбор игнорируется', () => {
      renderPage();
      fireEvent.click(screen.getByTestId('select-empty'));
      expect(screen.getByTestId('uploaded').textContent).toBe('');
      fireEvent.click(screen.getByTestId('select-files'));
      expect(screen.getByText('a.txt')).toBeTruthy();
      fireEvent.click(screen.getByText('rm-a.txt'));
      expect(screen.queryByText('a.txt')).toBeNull();
      expect(screen.getByText('b.png')).toBeTruthy();
    });

    it('кнопка вложения и горячая клавиша открывают выбор файла', () => {
      renderPage();
      const spy = jest.spyOn(screen.getByTestId('file-input') as HTMLInputElement, 'click');
      fireEvent.click(screen.getByTestId('attach'));
      expect(spy).toHaveBeenCalledTimes(1);
      act(() => { window.dispatchEvent(new Event('astrachat:trigger-attach')); });
      expect(spy).toHaveBeenCalledTimes(2);
    });

    it('голосовой ввод открывает и закрывает диалог', () => {
      renderPage();
      const dlg = () => screen.getByTestId('voice-dialog');
      expect(dlg().getAttribute('data-open')).toBe('false');
      fireEvent.click(screen.getByTestId('voice'));
      expect(dlg().getAttribute('data-open')).toBe('true');
      fireEvent.click(screen.getByText('voice-close'));
      expect(dlg().getAttribute('data-open')).toBe('false');
    });
  });

  describe('вставка текста и подсказки', () => {
    it('событие вставки подставляет текст и фокусирует поле; пустой текст и чужой detail игнорируются', () => {
      renderPage();
      act(() => { window.dispatchEvent(new CustomEvent('astra:insert-chat-text', { detail: { text: 'вставка' } })); });
      expect(input().value).toBe('вставка');
      expect(document.activeElement).toBe(input());
      act(() => {
        window.dispatchEvent(new CustomEvent('astra:insert-chat-text', { detail: { text: '' } }));
        window.dispatchEvent(new CustomEvent('astra:insert-chat-text', { detail: { text: 5 } }));
        window.dispatchEvent(new CustomEvent('astra:insert-chat-text'));
      });
      expect(input().value).toBe('вставка');
    });

    it('отложенный текст из sessionStorage вставляется при монтировании и удаляется', () => {
      sessionStorage.setItem('astra_insert_chat_text', 'из хранилища');
      renderPage();
      expect(input().value).toBe('из хранилища');
      expect(sessionStorage.getItem('astra_insert_chat_text')).toBeNull();
    });

    it('если sessionStorage недоступен, страница всё равно рендерится', () => {
      jest.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, k: string) {
        if (k === 'astra_insert_chat_text') throw new Error('denied');
        return null;
      });
      expect(() => renderPage()).not.toThrow();
    });

    it('выбор подсказки подставляет текст в поле', () => {
      renderPage();
      fireEvent.click(screen.getByText('pick-suggestion'));
      expect(input().value).toBe('Подсказка');
      expect(screen.getByTestId('suggestions').getAttribute('data-max')).toBe('800px');
    });

    it('каталог подсказок строится по включённым MCP и состоянию библиотеки', () => {
      mock.mcpServers = [{ id: 'srv1', display_name: 'Один' }, { id: 'srv2', display_name: 'Два' }];
      localStorage.setItem('use_kb_rag', 'true');
      renderPage();
      expect(mock.suggestionsCatalogArgs).toEqual([['srv1', 'srv2'], true]);
    });

    it('живые MCP-инструменты отображаются в поле ввода', () => {
      mock.mcpTools = [{ id: 1 }, { id: 2 }];
      renderPage();
      expect(screen.getByTestId('live-tools').textContent).toBe('2');
    });
  });

  describe('индикаторы и отступ кнопки инструментов', () => {
    const argsLabels = () => ({ mcp: mock.insetArgs[3], skills: mock.insetArgs[5] });

    it('подписи: один MCP/skill — по имени, несколько — по счётчику', () => {
      mock.mcpServers = [{ id: 'a', display_name: 'Сервер' }];
      mock.skills = [{ name: ' Навык ', slug: 's' }];
      renderPage();
      expect(argsLabels()).toEqual({ mcp: 'Сервер', skills: 'Навык' });
    });

    it('несколько MCP и skills — «N MCP» / «N Skills»; skill без имени — slug или «Skill»', () => {
      mock.mcpServers = [{ id: 'a', display_name: 'A' }, { id: 'b', display_name: 'B' }];
      mock.skills = [{ name: '', slug: 'x' }, { name: '', slug: '' }];
      const a = renderPage();
      expect(argsLabels()).toEqual({ mcp: '2 MCP', skills: '2 Skills' });
      a.unmount();
      mock.skills = [{ name: '', slug: 'slug-only' }];
      renderPage();
      expect(argsLabels().skills).toBe('slug-only');
      mock.skills = [{ name: '', slug: '' }];
    });

    it('skill без имени и slug — подпись «Skill»', () => {
      mock.skills = [{ name: '', slug: '' }];
      renderPage();
      expect(argsLabels().skills).toBe('Skill');
    });

    it('без индикаторов подписи пустые, отступ рассчитывается по стилю поля ввода', () => {
      renderPage();
      expect(argsLabels()).toEqual({ mcp: '', skills: '' });
      expect(screen.getByTestId('suggestions').getAttribute('data-inset')).toBe('compact:123');
    });

    it('кластер статуса: legacy «Общий RAG» не подсвечивается (только Knowledge Bases); флаг синхронизируется по событию', () => {
      renderPage();
      expect(screen.getByTestId('cluster').getAttribute('data-lib')).toBe('false');
      fireEvent.click(screen.getByText('cl-lib'));
      expect(localStorage.getItem('use_kb_rag')).toBe('true');
      // KB-миграция: индикатор «общей библиотеки» в кластере всегда выключен.
      expect(screen.getByTestId('cluster').getAttribute('data-lib')).toBe('false');
      act(() => {
        localStorage.setItem('use_kb_rag', 'false');
        localStorage.setItem('use_memory_library_rag', 'false');
        window.dispatchEvent(new Event('astrachat:knowledgeRag'));
      });
      expect(screen.getByTestId('cluster').getAttribute('data-lib')).toBe('false');
    });

    it('снятие агента, skills и артефактов из кластера', () => {
      mock.myAgent = { name: 'Агент' };
      mock.skills = [{ name: 'S', slug: 's' }];
      mock.artifacts = { active: true, tooltip: 'т' };
      renderPage();
      fireEvent.click(screen.getByText('cl-agent'));
      expect(mock.clearActiveAgent).toHaveBeenCalled();
      expect(mock.actions.showNotification).toHaveBeenCalledWith('info', 'Агент снят');
      fireEvent.click(screen.getByText('cl-skills'));
      expect(mock.clearActiveSkills).toHaveBeenCalledWith('project:p1');
      expect(mock.actions.showNotification).toHaveBeenCalledWith('info', 'Skills отключены');
      fireEvent.click(screen.getByText('cl-art'));
      expect(mock.disableArtifacts).toHaveBeenCalledWith('project:p1');
      expect(mock.actions.showNotification).toHaveBeenCalledWith('info', 'Режим артефактов отключён');
    });

    it('без выбранного агента / skills / артефактов обработчики снятия не передаются', () => {
      renderPage();
      expect(screen.queryByText('cl-agent')).toBeNull();
      expect(screen.queryByText('cl-skills')).toBeNull();
      expect(screen.queryByText('cl-art')).toBeNull();
    });

    it('клик по MCP-индикатору открывает меню инструментов на панели MCP', () => {
      renderPage();
      fireEvent.click(screen.getByText('cl-mcp'));
      expect(screen.getByTestId('panel-mcp').getAttribute('data-chat')).toBe('project:p1');
    });
  });

  describe('меню инструментов', () => {
    const openMenu = () => {
      fireEvent.click(screen.getByTestId('settings'));
    };

    it('открывается кнопкой настроек со всеми пунктами', () => {
      renderPage();
      openMenu();
      for (const t of ['Агенты', 'Артефакты', 'Skills', 'MCP', 'Режим модели', 'Очистить поле ввода']) {
        expect(screen.getByText(t)).toBeTruthy();
      }
      // KB-миграция: пункта legacy «Общий RAG» в меню больше нет.
      expect(screen.queryByText('Общий RAG')).toBeNull();
    });

    it.each([
      ['Агенты', 'panel-agents'],
      ['Артефакты', 'panel-artifacts'],
      ['Skills', 'panel-skills'],
      ['MCP', 'panel-mcp'],
    ])('пункт «%s» раскрывает панель и сворачивает её повторным кликом', (label, id) => {
      renderPage();
      openMenu();
      fireEvent.click(screen.getByText(label));
      expect(screen.getByTestId(id)).toBeTruthy();
      fireEvent.click(screen.getByText(label));
      expect(screen.queryByTestId(id)).toBeNull();
    });

    it('режим модели: выбор сохраняется в localStorage; неверное сохранённое значение → fast', () => {
      localStorage.setItem('model_thinking_mode', 'bogus');
      renderPage();
      expect(localStorage.getItem('model_thinking_mode')).toBe('fast');
      openMenu();
      fireEvent.click(screen.getByText('Режим модели'));
      fireEvent.click(screen.getByText('Мышление'));
      expect(localStorage.getItem('model_thinking_mode')).toBe('thinking');
      fireEvent.click(screen.getByText('Автоматический'));
      expect(localStorage.getItem('model_thinking_mode')).toBe('auto');
      fireEvent.click(screen.getByText('Быстрый'));
      expect(localStorage.getItem('model_thinking_mode')).toBe('fast');
    });

    it('сохранённый режим модели восстанавливается', () => {
      localStorage.setItem('model_thinking_mode', 'thinking');
      renderPage();
      expect(localStorage.getItem('model_thinking_mode')).toBe('thinking');
    });

    it('legacy «Общий RAG» убран из меню инструментов (используются Knowledge Bases)', () => {
      renderPage();
      openMenu();
      expect(screen.queryByText('Общий RAG')).toBeNull();
      expect(screen.queryByText('Отключить общий RAG')).toBeNull();
      expect(localStorage.getItem('use_kb_rag')).toBeNull();
    });

    it('если memory RAG выключен, меню по-прежнему без «Общий RAG» и библиотеку не переключает', () => {
      mock.rag.memoryRagEnabled = false;
      renderPage();
      openMenu();
      expect(screen.queryByText('Общий RAG')).toBeNull();
      expect(localStorage.getItem('use_kb_rag')).toBeNull();
      expect(screen.getByText('Очистить поле ввода')).toBeTruthy();
    });

    it('«Очистить поле ввода» очищает текст и закрывает меню', () => {
      renderPage();
      fireEvent.change(input(), { target: { value: 'черновик' } });
      openMenu();
      fireEvent.click(screen.getByText('Очистить поле ввода'));
      expect(input().value).toBe('');
      act(() => { jest.advanceTimersByTime(1000); });
      expect(screen.queryByText('Очистить поле ввода')).toBeNull();
    });

    it('если оболочка поля ввода недоступна, меню якорится на кнопку и без заданной ширины', () => {
      mock.noAnchor = true;
      renderPage();
      openMenu();
      expect(screen.getByText('Очистить поле ввода')).toBeTruthy();
    });

    it('Esc закрывает меню', () => {
      renderPage();
      openMenu();
      fireEvent.keyDown(screen.getByText('Очистить поле ввода'), { key: 'Escape' });
      act(() => { jest.advanceTimersByTime(1000); });
      expect(screen.queryByText('Очистить поле ввода')).toBeNull();
    });

    it('ResizeObserver пересчитывает размеры меню и отключается при закрытии', () => {
      let cb: () => void = () => {};
      const disconnect = jest.fn();
      const observe = jest.fn();
      (global as any).ResizeObserver = function (c: () => void) {
        cb = c;
        return { observe, disconnect, unobserve: jest.fn() };
      };
      renderPage();
      openMenu();
      expect(observe).toHaveBeenCalled();
      expect(() => act(() => { cb(); })).not.toThrow();
      fireEvent.click(screen.getByText('Очистить поле ввода'));
      act(() => { jest.advanceTimersByTime(1000); });
      expect(disconnect).toHaveBeenCalled();
    });
  });

  describe('чаты проекта', () => {
    const setup = () => {
      mock.chats = [
        chat({ id: 'a', title: 'Старый', updatedAt: '2025-06-01T10:00:00' }),
        chat({ id: 'b', title: 'Вчерашний', updatedAt: '2025-06-09T10:00:00' }),
        chat({ id: 'c', title: 'Закреплённый', isPinnedInProject: true, updatedAt: '2025-01-01T10:00:00' }),
        chat({ id: 'd', title: 'Сегодняшний', updatedAt: '2025-06-10T09:00:00' }),
        chat({ id: 'e', title: 'Архивный', isArchived: true }),
        chat({ id: 'f', title: 'Чужой', projectId: 'other' }),
        chat({ id: 'g', title: 'Скрытый', hidden: true }),
      ];
      mock.chatsById = Object.fromEntries(mock.chats.map((c: any) => [c.id, c]));
    };
    const titles = () =>
      Array.from(document.querySelectorAll('.MuiListItemButton-root .MuiTypography-body2')).map((n) => n.textContent);
    const openChatMenu = (title: string) => {
      const row = screen.getByText(title).closest('li')!;
      fireEvent.click(row.querySelector('button.MuiIconButton-root')!);
    };

    it('без чатов список не показывается', () => {
      renderPage();
      expect(screen.queryByText('Чаты')).toBeNull();
    });

    it('показывает только неархивные видимые чаты проекта: закреплённые сверху, затем по дате', () => {
      setup();
      renderPage();
      expect(titles()).toEqual(['Закреплённый', 'Сегодняшний', 'Вчерашний', 'Старый']);
    });

    it('даты: Сегодня / Вчера / день и месяц', () => {
      setup();
      renderPage();
      expect(screen.getByText('Сегодня')).toBeTruthy();
      expect(screen.getByText('Вчера')).toBeTruthy();
      expect(screen.getByText(/^1 июн/)).toBeTruthy();
    });

    it('заголовок «Чаты» сворачивает и разворачивает список', () => {
      setup();
      renderPage();
      fireEvent.click(screen.getByText('Чаты'));
      act(() => { jest.advanceTimersByTime(1000); });
      fireEvent.click(screen.getByText('Чаты'));
      expect(screen.getByText('Старый')).toBeTruthy();
    });

    it('клик по чату делает его текущим и открывает главную', () => {
      setup();
      renderPage();
      fireEvent.click(screen.getByText('Старый'));
      expect(mock.actions.setCurrentChat).toHaveBeenCalledWith('a');
      expect(mock.navigate).toHaveBeenCalledWith('/');
    });

    it('меню чата: закрепить / открепить в зависимости от состояния', () => {
      setup();
      renderPage();
      openChatMenu('Старый');
      expect(screen.getByText('Пин')).toBeTruthy();
      fireEvent.click(screen.getByText('Пин'));
      expect(mock.actions.togglePinInProject).toHaveBeenCalledWith('a');
      act(() => { jest.advanceTimersByTime(1000); });
      openChatMenu('Закреплённый');
      expect(screen.getByText('Открепить')).toBeTruthy();
    });

    it('архивирование и вынос из проекта', () => {
      setup();
      renderPage();
      openChatMenu('Старый');
      fireEvent.click(screen.getByText('Архив'));
      expect(mock.actions.archiveChat).toHaveBeenCalledWith('a');
      act(() => { jest.advanceTimersByTime(1000); });
      openChatMenu('Вчерашний');
      fireEvent.click(screen.getByText('Перенести из проекта'));
      expect(mock.actions.moveChatToProject).toHaveBeenCalledWith('b', null);
    });

    it('пункт «Перенести из проекта» скрыт, если у чата нет projectId', () => {
      setup();
      mock.chatsById.a = { ...mock.chatsById.a, projectId: undefined };
      renderPage();
      openChatMenu('Старый');
      expect(screen.queryByText('Перенести из проекта')).toBeNull();
    });

    it('переименование: Enter сохраняет после защитного окна 250 мс', () => {
      setup();
      renderPage();
      openChatMenu('Старый');
      fireEvent.click(screen.getByText('Переименовать'));
      const box = screen.getByDisplayValue('Старый');
      fireEvent.change(box, { target: { value: '  Новое имя  ' } });
      // мгновенный blur/enter сразу после входа в режим игнорируется
      fireEvent.keyDown(box, { key: 'Enter' });
      expect(mock.actions.updateChatTitle).not.toHaveBeenCalled();
      act(() => { jest.advanceTimersByTime(300); });
      fireEvent.keyDown(box, { key: 'Enter' });
      expect(mock.actions.updateChatTitle).toHaveBeenCalledWith('a', 'Новое имя');
      expect(screen.queryByDisplayValue('Новое имя')).toBeNull();
    });

    it('переименование: blur сохраняет, Esc отменяет, пустое имя не сохраняется', () => {
      setup();
      renderPage();
      openChatMenu('Старый');
      fireEvent.click(screen.getByText('Переименовать'));
      act(() => { jest.advanceTimersByTime(300); });
      let box = screen.getByDisplayValue('Старый');
      fireEvent.change(box, { target: { value: '   ' } });
      fireEvent.blur(box);
      expect(mock.actions.updateChatTitle).not.toHaveBeenCalled();
      fireEvent.change(box, { target: { value: 'Через blur' } });
      fireEvent.blur(box);
      expect(mock.actions.updateChatTitle).toHaveBeenCalledWith('a', 'Через blur');
      act(() => { jest.advanceTimersByTime(1000); });
      openChatMenu('Вчерашний');
      fireEvent.click(screen.getByText('Переименовать'));
      box = screen.getByDisplayValue('Вчерашний');
      fireEvent.keyDown(box, { key: 'Escape' });
      expect(screen.queryByDisplayValue('Вчерашний')).toBeNull();
      expect(screen.getByText('Вчерашний')).toBeTruthy();
    });

    it('клик по полю переименования не открывает чат', () => {
      setup();
      renderPage();
      openChatMenu('Старый');
      fireEvent.click(screen.getByText('Переименовать'));
      const box = screen.getByDisplayValue('Старый');
      fireEvent.click(box);
      expect(mock.navigate).not.toHaveBeenCalled();
      fireEvent.click(box.closest('.MuiListItemButton-root')!);
      expect(mock.navigate).not.toHaveBeenCalled();
    });

    it('удаление: диалог подтверждения; «Отменить» закрывает без удаления', () => {
      setup();
      renderPage();
      openChatMenu('Старый');
      fireEvent.click(screen.getByText('Удалить'));
      const dlg = screen.getByRole('dialog');
      fireEvent.click(within(dlg).getByText('Отменить'));
      expect(mock.actions.deleteChat).not.toHaveBeenCalled();
    });

    it('подтверждённое удаление чата', () => {
      setup();
      renderPage();
      openChatMenu('Старый');
      fireEvent.click(screen.getByText('Удалить'));
      fireEvent.click(within(screen.getByRole('dialog')).getByText('Удалить'));
      expect(mock.actions.deleteChat).toHaveBeenCalledWith('a');
      expect(mock.actions.setCurrentChat).not.toHaveBeenCalled();
    });

    it('удаление текущего чата переключает на первый оставшийся, а если их нет — сбрасывает', () => {
      setup();
      mock.currentChatId = 'a';
      renderPage();
      openChatMenu('Старый');
      fireEvent.click(screen.getByText('Удалить'));
      fireEvent.click(within(screen.getByRole('dialog')).getByText('Удалить'));
      expect(mock.actions.setCurrentChat).toHaveBeenCalledWith('c');
    });

    it('удаление единственного текущего чата сбрасывает текущий в null', () => {
      mock.chats = [chat({ id: 'only', title: 'Один' })];
      mock.chatsById = { only: mock.chats[0] };
      mock.currentChatId = 'only';
      renderPage();
      openChatMenu('Один');
      fireEvent.click(screen.getByText('Удалить'));
      fireEvent.click(within(screen.getByRole('dialog')).getByText('Удалить'));
      expect(mock.actions.setCurrentChat).toHaveBeenCalledWith(null);
    });

    it('Esc закрывает меню чата и диалог удаления', () => {
      setup();
      renderPage();
      openChatMenu('Старый');
      fireEvent.keyDown(screen.getByText('Архив'), { key: 'Escape' });
      act(() => { jest.advanceTimersByTime(1000); });
      expect(screen.queryByText('Архив')).toBeNull();
      openChatMenu('Старый');
      fireEvent.click(screen.getByText('Удалить'));
      fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
      act(() => { jest.advanceTimersByTime(1000); });
      expect(screen.queryByRole('dialog')).toBeNull();
    });
  });
});
