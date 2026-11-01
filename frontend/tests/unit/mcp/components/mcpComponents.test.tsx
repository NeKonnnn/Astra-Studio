import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import McpToolCallsPanel from '@/mcp/components/McpToolCallsPanel';
import McpLiveToolsIndicator from '@/mcp/components/McpLiveToolsIndicator';
import McpCredentialsSection from '@/mcp/components/McpCredentialsSection';
import AtlassianCredentialsForm from '@/mcp/plugins/AtlassianCredentialsForm';
import AtlassianServerDetails from '@/mcp/plugins/AtlassianServerDetails';
import { AtlassianServerSettings } from '@/mcp/plugins/AtlassianServerSettings';
import { getMcpServerPlugin, MCP_SERVER_PLUGINS } from '@/mcp/plugins/registry';

const mockDownload = jest.fn();
const mockFetchAtlassian = jest.fn();
const mockFetchMeta = jest.fn();
const mockSave = jest.fn();
const mockDelete = jest.fn();

jest.mock('@/mcp/api', () => ({
  downloadMcpFile: (...a: unknown[]) => mockDownload(...a),
  fetchAtlassianConfig: (...a: unknown[]) => mockFetchAtlassian(...a),
  fetchMcpCredentialsMeta: (...a: unknown[]) => mockFetchMeta(...a),
  saveMcpCredentials: (...a: unknown[]) => mockSave(...a),
  deleteMcpCredentials: (...a: unknown[]) => mockDelete(...a),
}));

jest.mock('@/components/DocumentSearchPanel', () => ({
  DocumentSearchPanel: ({ trace }: any) => <div data-testid="doc-panel">{trace.query}</div>,
}));

beforeEach(() => {
  mockDownload.mockReset();
  mockFetchAtlassian.mockReset();
  mockFetchMeta.mockReset();
  mockSave.mockReset();
  mockDelete.mockReset();
});

const start = (over: Record<string, unknown> = {}) =>
  ({ type: 'mcp_tool_start', server_id: 'srv', tool: 'search', qualified_name: 'srv.search', call_id: 'c1', ...over } as any);
const end = (over: Record<string, unknown> = {}) =>
  ({ type: 'mcp_tool_end', server_id: 'srv', tool: 'search', qualified_name: 'srv.search', call_id: 'c1', success: true, ...over } as any);

describe('McpToolCallsPanel', () => {
  it('пустой список ничего не рисует', () => {
    const { container } = render(<McpToolCallsPanel toolCalls={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('заголовок «Инструменты», счётчик выполненных и выполняющихся', () => {
    render(
      <McpToolCallsPanel
        toolCalls={[start(), end({ result: 'ok' }), start({ call_id: 'c2', qualified_name: 'srv.b', tool: 'b' })]}
        serverLabels={{ srv: 'Сервер' }}
      />,
    );
    expect(screen.getByText(/Инструменты \(1\/2, выполняется 1\)/)).toBeInTheDocument();
    expect(screen.getByText('Выполнено: search')).toBeInTheDocument();
    expect(screen.getByText('Выполняется: b')).toBeInTheDocument();
    expect(screen.getAllByText(/AstraChat отправил эту информацию Сервер/).length).toBeGreaterThan(0);
  });

  it('только субагенты: заголовок «Субагенты», имя агента из аргументов, ошибка помечается', () => {
    render(
      <McpToolCallsPanel
        toolCalls={[
          start({ tool: 'subagent', qualified_name: 'native.subagent', arguments: { agent_name: ' Аналитик ' } }),
          end({ tool: 'subagent', qualified_name: 'native.subagent', success: false, error: 'boom' }),
        ]}
      />,
    );
    expect(screen.getByText(/Субагенты \(0\/1\)/)).toBeInTheDocument();
    expect(screen.getByText('Ошибка: Аналитик')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  it('subagent без имени агента показывает название инструмента', () => {
    render(<McpToolCallsPanel toolCalls={[start({ tool: 'subagent', arguments: { agent_name: 5 } }), end({ tool: 'subagent' })]} />);
    expect(screen.getByText('Выполнено: subagent')).toBeInTheDocument();
  });

  it('JSON в результате форматируется, невалидный JSON и пустой результат обрабатываются', () => {
    const { rerender } = render(
      <McpToolCallsPanel toolCalls={[start({ arguments: { q: 1 } }), end({ result: '{"a":1}' })]} />,
    );
    expect(screen.getByText(/"a": 1/)).toBeInTheDocument();
    rerender(<McpToolCallsPanel toolCalls={[start(), end({ result: '{bad json' })]} />);
    expect(screen.getByText('{bad json')).toBeInTheDocument();
    rerender(<McpToolCallsPanel toolCalls={[start(), end({ result: '' })]} />);
    expect(screen.getAllByText('(пусто)').length).toBeGreaterThan(0);
  });

  it('аргументы, которые нельзя сериализовать в JSON, показываются как строка', () => {
    const circular: any = { a: 1 };
    circular.self = circular;
    render(<McpToolCallsPanel toolCalls={[start({ arguments: circular }), end({ result: 'r' })]} />);
    expect(screen.getByText('[object Object]')).toBeInTheDocument();
  });

  it('карточка сворачивается кликом и клавишами Enter/пробел; секция сворачивается целиком', () => {
    render(<McpToolCallsPanel toolCalls={[start(), end({ result: 'RESULT_TEXT' })]} />);
    const card = screen.getByText('Выполнено: search').closest('[role="button"]') as HTMLElement;
    const toggleBtn = screen.getByLabelText('Свернуть');
    expect(toggleBtn).toBeInTheDocument();
    fireEvent.keyDown(card, { key: 'Enter' });
    expect(screen.getByLabelText('Развернуть')).toBeInTheDocument();
    fireEvent.keyDown(card, { key: ' ' });
    expect(screen.getByLabelText('Свернуть')).toBeInTheDocument();
    fireEvent.keyDown(card, { key: 'a' });
    expect(screen.getByLabelText('Свернуть')).toBeInTheDocument();
    fireEvent.click(card);
    expect(screen.getByLabelText('Развернуть')).toBeInTheDocument();
    const section = screen.getByText(/Инструменты \(/).closest('[role="button"]') as HTMLElement;
    fireEvent.click(section);
    fireEvent.click(section);
  });

  it('выполняющийся вызов показывает таймер по started_at (секунды и миллисекунды)', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(1_700_000_075_000));
    render(
      <McpToolCallsPanel
        toolCalls={[start({ timestamp: 1_700_000_000 }), start({ call_id: 'c2', qualified_name: 'srv.b', timestamp: 1_700_000_070_000 })]}
      />,
    );
    expect(screen.getByText('1:15')).toBeInTheDocument();
    expect(screen.getByText('0:05')).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(1000);
    });
    expect(screen.getByText('1:16')).toBeInTheDocument();
    jest.useRealTimers();
  });

  it('завершённый вызов показывает duration_ms', () => {
    render(<McpToolCallsPanel toolCalls={[start(), end({ duration_ms: 65_000, result: 'x' })]} />);
    expect(screen.getByText('1:05')).toBeInTheDocument();
  });

  it('панель поиска по документам показывается только при наличии хитов или файлов', () => {
    const trace = { query: 'запрос', sourceFiles: ['a.pdf'], hits: [] };
    const { rerender } = render(<McpToolCallsPanel toolCalls={[start(), end({ document_search: trace, result: 'r' })]} />);
    expect(screen.getByTestId('doc-panel')).toHaveTextContent('запрос');
    rerender(
      <McpToolCallsPanel toolCalls={[start(), end({ document_search: { query: 'q', sourceFiles: [], hits: [] }, result: 'r' })]} />,
    );
    expect(screen.queryByTestId('doc-panel')).toBeNull();
  });

  it('ссылки на скачивание: успех, ошибка с текстом и общий текст, повторные клики во время загрузки игнорируются', async () => {
    let resolveDl!: () => void;
    mockDownload.mockImplementationOnce(() => new Promise<void>((r) => (resolveDl = r)));
    render(
      <McpToolCallsPanel
        toolCalls={[
          start(),
          end({
            result: 'r',
            download_urls: [{ url: '/f1', label: 'отчёт.xlsx' }, { url: '/f2' }, { url: '/f3', label: 'x' }],
          }),
        ]}
      />,
    );
    const first = screen.getByText('Скачать: отчёт.xlsx');
    fireEvent.click(first);
    await screen.findByText('Скачивание…');
    fireEvent.click(screen.getByText('Скачивание…'));
    expect(mockDownload).toHaveBeenCalledTimes(1);
    await act(async () => resolveDl());
    expect(mockDownload).toHaveBeenCalledWith('/f1', 'отчёт.xlsx');

    mockDownload.mockRejectedValueOnce(new Error('403 Forbidden'));
    fireEvent.click(screen.getByText('Скачать: файл'));
    expect(await screen.findByText('403 Forbidden')).toBeInTheDocument();

    mockDownload.mockRejectedValueOnce('str');
    fireEvent.click(screen.getByText('Скачать: x'));
    expect(await screen.findByText('Не удалось скачать файл')).toBeInTheDocument();
  });
});

describe('McpLiveToolsIndicator', () => {
  it('пусто без инструментов', () => {
    const { container } = render(<McpLiveToolsIndicator tools={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('чипы с моделью и именем субагента', () => {
    render(
      <McpLiveToolsIndicator
        tools={[
          start({ tool: 'subagent', arguments: { agent_name: ' Помощник ' }, model: 'gpt' }),
          start({ call_id: 'c2', tool: 'web', qualified_name: 'srv.web' }),
          start({ call_id: 'c3', tool: 'subagent', qualified_name: 'x', arguments: { agent_name: 3 } }),
        ]}
      />,
    );
    expect(screen.getByText('Помощник (gpt)')).toBeInTheDocument();
    expect(screen.getByText('web')).toBeInTheDocument();
    expect(screen.getByText('subagent')).toBeInTheDocument();
  });

  it('без субагентов используется иконка MCP', () => {
    const { container } = render(<McpLiveToolsIndicator tools={[start()]} />);
    expect(container.querySelector('svg')).toBeTruthy();
    expect(screen.getByText('search')).toBeInTheDocument();
  });
});

describe('McpCredentialsSection и плагины Atlassian', () => {
  it('не per_user: информационное сообщение с режимом (по умолчанию service_account)', () => {
    const { rerender } = render(<McpCredentialsSection serverId="x" isDarkMode={false} />);
    expect(screen.getByText('service_account')).toBeInTheDocument();
    rerender(<McpCredentialsSection serverId="x" authMode="Shared" isDarkMode compact />);
    expect(screen.getByText('shared')).toBeInTheDocument();
  });

  it('per_user для неизвестного сервера: подсказка про API', () => {
    render(<McpCredentialsSection serverId="zzz" authMode="per_user" isDarkMode={false} />);
    expect(screen.getByText(/PUT \/api\/mcp\/servers\/zzz\/credentials/)).toBeInTheDocument();
  });

  it('per_user для atlassian рисует форму', async () => {
    mockFetchMeta.mockResolvedValue({ configured: false, storage_available: true });
    render(<McpCredentialsSection serverId="atlassian" authMode="per_user" isDarkMode={false} />);
    expect(await screen.findByLabelText('Jira URL')).toBeInTheDocument();
  });

  it('реестр плагинов', () => {
    expect(getMcpServerPlugin('atlassian')).toBe(AtlassianServerSettings);
    expect(getMcpServerPlugin('other')).toBeNull();
    expect(Object.keys(MCP_SERVER_PLUGINS)).toEqual(['atlassian']);
  });

  it('AtlassianServerSettings: другой сервер -> null, atlassian -> детали и учётные данные', async () => {
    mockFetchAtlassian.mockResolvedValue({});
    const { container, rerender } = render(<AtlassianServerSettings serverId="x" isDarkMode={false} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<AtlassianServerSettings serverId="atlassian" isDarkMode compact authMode="shared" />);
    expect(await screen.findByText('Jira')).toBeInTheDocument();
    expect(screen.getByText('shared')).toBeInTheDocument();
  });

  it('AtlassianServerDetails: тулсеты и read-only; для чужого сервера ничего и запросов нет', async () => {
    mockFetchAtlassian.mockResolvedValue({ toolsets: { jira: false, confluence: true }, read_only: true });
    render(<AtlassianServerDetails serverId="atlassian" isDarkMode={false} />);
    expect(await screen.findByText('Read-only')).toBeInTheDocument();
    expect(screen.getByText('Confluence')).toBeInTheDocument();
    expect(screen.queryByText('Jira')).toBeNull();
  });

  it('AtlassianServerDetails: чужой сервер', () => {
    const { container } = render(<AtlassianServerDetails serverId="x" isDarkMode compact />);
    expect(container).toBeEmptyDOMElement();
    expect(mockFetchAtlassian).not.toHaveBeenCalled();
  });

  it('AtlassianServerDetails: размонтирование до ответа не обновляет состояние', async () => {
    let resolve!: (v: unknown) => void;
    mockFetchAtlassian.mockReturnValue(new Promise((r) => (resolve = r)));
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    const { unmount } = render(<AtlassianServerDetails serverId="atlassian" isDarkMode={false} />);
    unmount();
    await act(async () => resolve({ read_only: true }));
    expect(err).not.toHaveBeenCalled();
    err.mockRestore();
  });
});

describe('AtlassianCredentialsForm', () => {
  it('индикатор загрузки, затем форма; настроенные поля перечислены', async () => {
    mockFetchMeta.mockResolvedValue({ configured: true, storage_available: true, fields: { jira_pat: true, jira_url: false } });
    render(<AtlassianCredentialsForm isDarkMode={false} />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(await screen.findByText(/Настроено:/)).toHaveTextContent('Настроено: jira_pat');
    expect(screen.getByLabelText('Jira PAT')).toHaveAttribute('placeholder', expect.stringContaining('оставьте пустым'));
    expect(screen.getByLabelText('Jira URL')).not.toHaveAttribute('placeholder');
    expect(screen.getByText('Удалить')).toBeInTheDocument();
  });

  it('настроено без перечня полей -> «да»', async () => {
    mockFetchMeta.mockResolvedValue({ configured: true, storage_available: true });
    render(<AtlassianCredentialsForm isDarkMode compact />);
    expect(await screen.findByText(/Настроено:/)).toHaveTextContent('Настроено: да');
  });

  it('хранилище недоступно -> предупреждение', async () => {
    mockFetchMeta.mockResolvedValue({ configured: false, storage_available: false });
    render(<AtlassianCredentialsForm isDarkMode={false} compact />);
    expect(await screen.findByText(/Хранилище credentials недоступно/)).toBeInTheDocument();
  });

  it('ошибка загрузки метаданных трактуется как недоступное хранилище', async () => {
    mockFetchMeta.mockRejectedValue(new Error('x'));
    render(<AtlassianCredentialsForm isDarkMode={false} />);
    expect(await screen.findByText(/Хранилище credentials недоступно/)).toBeInTheDocument();
  });

  it('сохранение отправляет только непустые обрезанные поля, очищает форму и обновляет метаданные', async () => {
    mockFetchMeta.mockResolvedValue({ configured: false, storage_available: true });
    mockSave.mockResolvedValue(undefined);
    render(<AtlassianCredentialsForm isDarkMode={false} />);
    fireEvent.change(await screen.findByLabelText('Jira URL'), { target: { value: '  https://jira  ' } });
    fireEvent.change(screen.getByLabelText('Confluence PAT'), { target: { value: 'tok' } });
    fireEvent.click(screen.getByText('Сохранить'));
    expect(await screen.findByText('Учётные данные сохранены')).toBeInTheDocument();
    expect(mockSave).toHaveBeenCalledWith('atlassian', { jira_url: 'https://jira', confluence_pat: 'tok' });
    expect((screen.getByLabelText('Jira URL') as HTMLInputElement).value).toBe('');
    expect(mockFetchMeta).toHaveBeenCalledTimes(2);
  });

  it('ошибка сохранения показывает сообщение и общий текст для не-Error', async () => {
    mockFetchMeta.mockResolvedValue({ configured: false, storage_available: true });
    mockSave.mockRejectedValueOnce(new Error('нет прав')).mockRejectedValueOnce('str');
    render(<AtlassianCredentialsForm isDarkMode={false} />);
    fireEvent.click(await screen.findByText('Сохранить'));
    expect(await screen.findByText('нет прав')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Сохранить'));
    expect(await screen.findByText('Не удалось сохранить')).toBeInTheDocument();
  });

  it('удаление: успех и ошибки', async () => {
    mockFetchMeta.mockResolvedValue({ configured: true, storage_available: true });
    mockDelete.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('fail')).mockRejectedValueOnce(1);
    render(<AtlassianCredentialsForm isDarkMode={false} />);
    fireEvent.click(await screen.findByText('Удалить'));
    expect(await screen.findByText('Учётные данные удалены')).toBeInTheDocument();
    expect(mockDelete).toHaveBeenCalledWith('atlassian');
    await waitFor(() => expect(mockFetchMeta).toHaveBeenCalledTimes(2));
    fireEvent.click(await screen.findByText('Удалить'));
    expect(await screen.findByText('fail')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Удалить'));
    expect(await screen.findByText('Не удалось удалить')).toBeInTheDocument();
  });
});
