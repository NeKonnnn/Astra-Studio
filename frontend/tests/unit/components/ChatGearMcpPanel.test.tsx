import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import ChatGearMcpPanel from '@/components/ChatGearMcpPanel';

let mockCanUseMcp = true;
jest.mock('@/hooks/useFeatureAccess', () => ({
  useFeatureAccess: () => ({ canUseMcp: mockCanUseMcp }),
}));

const mockFetchServers = jest.fn();
const mockFetchStatus = jest.fn();
const mockFetchTools = jest.fn();
jest.mock('@/mcp/api', () => ({
  fetchMcpServers: () => mockFetchServers(),
  fetchMcpStatus: () => mockFetchStatus(),
  fetchMcpTools: (id: string) => mockFetchTools(id),
}));

jest.mock('@/mcp/plugins/registry', () => ({
  getMcpServerPlugin: (id: string) =>
    id === 'atl'
      ? (p: any) => <div data-testid="plugin">{`plugin:${p.serverId}:${p.authMode}:${p.compact}`}</div>
      : null,
}));

const SERVERS = [
  { id: 'atl', display_name: 'Atlassian', enabled: true, transport: 'streamable-http', auth_mode: 'per_user' },
  { id: 'web', display_name: '', enabled: true, transport: 'stdio' },
  { id: 'off', display_name: 'Disabled', enabled: false, transport: 'stdio' },
];
const STATUS = {
  servers: [
    { id: 'atl', connected: true, tools: 5, transport: 'http2' },
    { id: 'web', connected: false },
  ],
};

const flush = async () => {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
};

beforeEach(() => {
  localStorage.clear();
  mockCanUseMcp = true;
  mockFetchServers.mockReset().mockResolvedValue(SERVERS);
  mockFetchStatus.mockReset().mockResolvedValue(STATUS);
  mockFetchTools.mockReset().mockResolvedValue([
    { name: 'search', qualified_name: 'atl.search', description: 'Поиск' },
    { name: 'same', qualified_name: 'same' },
  ]);
});

describe('ChatGearMcpPanel', () => {
  it('без доступа к MCP показывает сообщение и не грузит данные', async () => {
    mockCanUseMcp = false;
    render(<ChatGearMcpPanel isDarkMode chatId="c1" />);
    await flush();
    expect(screen.getByText('MCP недоступен для вашей роли.')).toBeInTheDocument();
    expect(mockFetchServers).not.toHaveBeenCalled();
  });

  it('потеря и возврат доступа во время работы не ломают порядок хуков (BUG-FE-73)', async () => {
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    const { rerender } = render(<ChatGearMcpPanel isDarkMode chatId="c1" />);
    await flush();
    mockCanUseMcp = false;
    expect(() => rerender(<ChatGearMcpPanel isDarkMode chatId="c1" />)).not.toThrow();
    expect(screen.getByText('MCP недоступен для вашей роли.')).toBeInTheDocument();
    mockCanUseMcp = true;
    expect(() => rerender(<ChatGearMcpPanel isDarkMode chatId="c1" />)).not.toThrow();
    await flush();
    expect(screen.queryByText('MCP недоступен для вашей роли.')).toBeNull();
    err.mockRestore();
  });

  it('без чата показывается подсказка', async () => {
    render(<ChatGearMcpPanel isDarkMode={false} chatId={null} />);
    await flush();
    expect(screen.getByText(/Выберите или создайте чат/)).toBeInTheDocument();
  });

  it('показывает включённые серверы со статусом, транспортом и числом инструментов', async () => {
    render(<ChatGearMcpPanel isDarkMode chatId="c1" />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    await flush();
    expect(screen.getByText('Atlassian')).toBeInTheDocument();
    expect(screen.getByText('web')).toBeInTheDocument(); // имя пустое -> id
    expect(screen.queryByText('Disabled')).toBeNull();
    expect(screen.getByText('Transport: http2')).toBeInTheDocument();
    expect(screen.getByText('Transport: stdio')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('ok')).toBeInTheDocument();
    expect(screen.getByText('offline')).toBeInTheDocument();
  });

  it('транспорт «—», если нигде не указан; сервер без статуса без бейджей', async () => {
    mockFetchServers.mockResolvedValue([{ id: 'x', display_name: 'X', enabled: true, transport: '' }]);
    mockFetchStatus.mockResolvedValue({});
    render(<ChatGearMcpPanel isDarkMode chatId="c1" />);
    await flush();
    expect(screen.getByText('Transport: —')).toBeInTheDocument();
    expect(screen.queryByText('ok')).toBeNull();
    expect(screen.queryByText('offline')).toBeNull();
  });

  it('переключатель сервера сохраняет выбор для чата', async () => {
    render(<ChatGearMcpPanel isDarkMode chatId="c1" />);
    await flush();
    const switches = screen.getAllByRole('switch');
    fireEvent.click(switches[0]);
    expect(JSON.parse(localStorage.getItem('chat:c1:mcp_tool_ids')!)).toEqual(['server:mcp:atl']);
    expect(screen.getAllByRole('switch')[0]).toBeChecked();
    fireEvent.click(screen.getAllByRole('switch')[0]);
    expect(JSON.parse(localStorage.getItem('chat:c1:mcp_tool_ids')!)).toEqual([]);
  });

  it('разворачивание загружает инструменты и показывает плагин сервера; повторный клик сворачивает', async () => {
    render(<ChatGearMcpPanel isDarkMode chatId="c1" />);
    await flush();
    fireEvent.click(screen.getByText('Atlassian'));
    await flush();
    expect(mockFetchTools).toHaveBeenCalledWith('atl');
    expect(screen.getByText('search')).toBeInTheDocument();
    expect(screen.getByText('atl.search')).toBeInTheDocument();
    expect(screen.getByText('Поиск')).toBeInTheDocument();
    expect(screen.getAllByText('same')).toHaveLength(1); // qualified_name совпадает с name - не дублируется
    expect(screen.getByTestId('plugin')).toHaveTextContent('plugin:atl:per_user:true');
    fireEvent.click(screen.getByText('Atlassian'));
    expect(mockFetchTools).toHaveBeenCalledTimes(1);
  });

  it('раскрытие с клавиатуры: Enter и пробел', async () => {
    render(<ChatGearMcpPanel isDarkMode chatId="c1" />);
    await flush();
    const header = screen.getByText('Atlassian').closest('[role="button"]') as HTMLElement;
    fireEvent.keyDown(header, { key: 'Enter' });
    await flush();
    expect(mockFetchTools).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(header, { key: ' ' });
    fireEvent.keyDown(header, { key: 'a' });
    expect(mockFetchTools).toHaveBeenCalledTimes(1);
  });

  it('ошибка загрузки инструментов показывает сообщение; пустой список — заглушку', async () => {
    mockFetchTools.mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce([]);
    render(<ChatGearMcpPanel isDarkMode chatId="c1" />);
    await flush();
    fireEvent.click(screen.getByText('Atlassian'));
    await flush();
    expect(screen.getByText('Не удалось загрузить инструменты')).toBeInTheDocument();
    fireEvent.click(screen.getByText('web'));
    await flush();
    expect(screen.getByText('Инструменты не найдены или сервер недоступен.')).toBeInTheDocument();
  });

  it('индикатор загрузки инструментов пока идёт запрос', async () => {
    let resolve!: (v: unknown) => void;
    mockFetchTools.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<ChatGearMcpPanel isDarkMode chatId="c1" />);
    await flush();
    fireEvent.click(screen.getByText('web'));
    await flush();
    expect(screen.getAllByRole('progressbar').length).toBeGreaterThan(0);
    await act(async () => resolve([]));
  });

  it('поиск по id и названию; «Нет доступных MCP-серверов»', async () => {
    render(<ChatGearMcpPanel isDarkMode chatId="c1" />);
    await flush();
    const input = screen.getByPlaceholderText('Поиск MCP...');
    fireEvent.change(input, { target: { value: 'ATLAS' } });
    expect(screen.getByText('Atlassian')).toBeInTheDocument();
    expect(screen.queryByText('web')).toBeNull();
    fireEvent.change(input, { target: { value: 'zzz' } });
    expect(screen.getByText('Нет доступных MCP-серверов.')).toBeInTheDocument();
  });

  it('ошибка загрузки списка серверов даёт пустой список', async () => {
    mockFetchServers.mockRejectedValue(new Error('down'));
    render(<ChatGearMcpPanel isDarkMode chatId="c1" />);
    await flush();
    expect(screen.getByText('Нет доступных MCP-серверов.')).toBeInTheDocument();
  });

  it('ответ статуса без servers не ломает отображение', async () => {
    mockFetchStatus.mockResolvedValue({ servers: undefined });
    render(<ChatGearMcpPanel isDarkMode={false} chatId="c1" />);
    await flush();
    expect(screen.getByText('Atlassian')).toBeInTheDocument();
  });
});
