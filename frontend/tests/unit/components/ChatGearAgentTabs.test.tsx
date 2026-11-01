import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import ChatGearMyAgentsTab from '@/components/ChatGearMyAgentsTab';
import ChatGearGalleryAgentsTab from '@/components/ChatGearGalleryAgentsTab';

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: '/', search: '', hash: '', state: null, key: 'k' }),
}));

let mockToken: string | null = 'tok';
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ token: mockToken }) }));

const mockNotify = jest.fn();
jest.mock('@/contexts/AppContext', () => ({ useAppActions: () => ({ showNotification: mockNotify }) }));

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a), getApiUrl: (p: string) => `http://api.test${p}` }));

jest.mock('@/components/AgentSelector', () => ({
  getActiveAgentFromStorage: () => {
    const id = (globalThis as any).localStorage.getItem('active_agent_id');
    const name = (globalThis as any).localStorage.getItem('active_agent_name');
    if (!id || !name) return null;
    return { id: Number(id), name, system_prompt: (globalThis as any).localStorage.getItem('active_agent_prompt') || '' };
  },
}));

const mockLoadModel = jest.fn();
jest.mock('@/utils/applyAgentServer', () => ({ loadAgentModelOnly: (...a: unknown[]) => mockLoadModel(...a) }));
const mockPersistMcp = jest.fn();
jest.mock('@/utils/applyAgentMcp', () => ({ persistAgentMcpConfig: (...a: unknown[]) => mockPersistMcp(...a) }));
const mockPersistArtifacts = jest.fn();
jest.mock('@/utils/agentArtifactsEnabled', () => ({
  persistAgentArtifactsEnabled: (...a: unknown[]) => mockPersistArtifacts(...a),
}));
const mockClearAgent = jest.fn();
jest.mock('@/utils/clearActiveAgent', () => ({
  clearActiveAgent: () => {
    mockClearAgent();
    (globalThis as any).localStorage.removeItem('active_agent_id');
    (globalThis as any).localStorage.removeItem('active_agent_name');
  },
}));
jest.mock('@/components/ChatGearAgentReindexHint', () => ({
  __esModule: true,
  default: ({ agentId }: { agentId: number }) => <span data-testid={`hint-${agentId}`} />,
}));

const flush = async () => {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
};

const resp = (body: unknown, ok = true) => ({ ok, json: async () => body });

let fetchMock: jest.Mock;

beforeEach(() => {
  localStorage.clear();
  mockToken = 'tok';
  mockNotify.mockReset();
  mockLoadModel.mockReset().mockResolvedValue({ ok: true });
  mockPersistMcp.mockReset();
  mockPersistArtifacts.mockReset();
  mockClearAgent.mockReset();
  fetchMock = jest.fn();
  (global as any).fetch = fetchMock;
});
afterEach(() => jest.restoreAllMocks());

const AGENTS = [
  { id: 1, name: 'Аналитик', system_prompt: 'p1', config: { model_path: ' 1lm-svc://m 1 ' }, author_id: 'u1' },
  { id: 2, name: 'Редактор', system_prompt: 'p2', author_id: 'u2', author_name: 'bob', author_full_name: 'Боб Иванов' },
];

type Variant = {
  title: string;
  Tab: React.ComponentType<any>;
  setupList: (agents: any[]) => void;
};

const variants: Variant[] = [
  {
    title: 'ChatGearMyAgentsTab',
    Tab: ChatGearMyAgentsTab,
    setupList: (agents) => {
      fetchMock.mockImplementation(async (url: string) => {
        if (String(url).includes('/api/agents/my/agents')) return resp({ agents });
        if (String(url).includes('/api/agents/my/shared')) return resp({ agents: [] });
        if (/\/api\/agents\/\d+$/.test(String(url))) return resp({}, false);
        return resp({});
      });
    },
  },
  {
    title: 'ChatGearGalleryAgentsTab',
    Tab: ChatGearGalleryAgentsTab,
    setupList: (agents) => {
      fetchMock.mockImplementation(async (url: string) => {
        if (String(url).includes('/api/agents/my/bookmarks')) return resp({ agents });
        if (/\/api\/agents\/\d+$/.test(String(url))) return resp({}, false);
        return resp({});
      });
    },
  },
];

describe.each(variants)('$title: общее поведение выбора агента', ({ Tab, setupList }) => {
  const renderTab = async (over: Record<string, unknown> = {}) => {
    setupList(AGENTS);
    const utils = render(<Tab isDarkMode searchQuery="" visible {...over} />);
    await flush();
    return utils;
  };

  it('список агентов показан; строки «Без агента» нет', async () => {
    await renderTab();
    expect(screen.queryByText('Без агента')).not.toBeInTheDocument();
    expect(screen.getByText('Аналитик')).toBeInTheDocument();
    expect(screen.getByText('Редактор')).toBeInTheDocument();
    expect(document.querySelectorAll('[data-testid="CheckIcon"]')).toHaveLength(0);
    expect(screen.getByTestId('hint-1')).toBeInTheDocument();
  });

  it('поиск по имени без учёта регистра; пустая выдача — «Ничего не найдено»', async () => {
    const { rerender } = await renderTab({ searchQuery: 'АНАЛ' });
    expect(screen.getByText('Аналитик')).toBeInTheDocument();
    expect(screen.queryByText('Редактор')).toBeNull();
    rerender(<Tab isDarkMode={false} searchQuery="нет такого" visible />);
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
  });

  it('выбор агента с токеном: детали с сервера, загрузка модели, уведомления и сохранение выбора', async () => {
    await renderTab();
    fetchMock.mockImplementation(async (url: string) => {
      if (/\/api\/agents\/1$/.test(url)) return resp({ system_prompt: 'новый промпт', config: { model_path: ' 1lm-svc://m 1 ', mcp: true } });
      return resp({});
    });
    const selected: any[] = [];
    const h = (e: Event) => selected.push((e as CustomEvent).detail);
    window.addEventListener('agentSelected', h);
    await act(async () => {
      fireEvent.click(screen.getByText('Аналитик'));
    });
    await flush();
    window.removeEventListener('agentSelected', h);
    expect(mockLoadModel).toHaveBeenCalledWith('tok', 'llm-svc://m1');
    expect(localStorage.getItem('active_agent_id')).toBe('1');
    expect(localStorage.getItem('active_agent_name')).toBe('Аналитик');
    expect(localStorage.getItem('active_agent_prompt')).toBe('новый промпт');
    expect(mockPersistMcp).toHaveBeenCalledWith({ model_path: ' 1lm-svc://m 1 ', mcp: true });
    expect(mockPersistArtifacts).toHaveBeenCalled();
    expect(selected[0].system_prompt).toBe('новый промпт');
    expect(mockNotify).toHaveBeenCalledWith('info', 'Загрузка модели агента «Аналитик»…');
    expect(mockNotify).toHaveBeenLastCalledWith('success', 'Агент «Аналитик»: модель загружена, промпт и настройки применятся в чате');
    expect(document.querySelectorAll('[data-testid="CheckIcon"]')).toHaveLength(1);
  });

  it('агент без модели: сообщение про применение настроек', async () => {
    await renderTab();
    await act(async () => {
      fireEvent.click(screen.getByText('Редактор'));
    });
    await flush();
    expect(mockLoadModel).toHaveBeenCalledWith('tok', null);
    expect(mockNotify).toHaveBeenCalledWith('info', 'Применение настроек агента «Редактор»…');
    expect(mockNotify).toHaveBeenLastCalledWith('success', 'Агент «Редактор» активирован — промпт и настройки из карточки агента');
  });

  it('ошибка загрузки деталей игнорируется, используется карточка из списка', async () => {
    await renderTab();
    fetchMock.mockImplementation(async (url: string) => {
      if (/\/api\/agents\/2$/.test(url)) throw new Error('net');
      return resp({});
    });
    await act(async () => {
      fireEvent.click(screen.getByText('Редактор'));
    });
    await flush();
    expect(localStorage.getItem('active_agent_prompt')).toBe('p2');
  });

  it('модель в статусе pending: информационное уведомление', async () => {
    await renderTab();
    mockLoadModel.mockResolvedValue({ ok: true, pending: true, message: 'позже' });
    await act(async () => {
      fireEvent.click(screen.getByText('Редактор'));
    });
    await flush();
    expect(mockNotify).toHaveBeenLastCalledWith('info', 'Агент «Редактор» выбран. Модель подтянется при первом сообщении.');
  });

  it('ошибка загрузки модели: предупреждение, выбор всё равно сохраняется', async () => {
    await renderTab();
    mockLoadModel.mockResolvedValue({ ok: false, message: 'OOM' });
    await act(async () => {
      fireEvent.click(screen.getByText('Редактор'));
    });
    await flush();
    expect(mockNotify).toHaveBeenLastCalledWith('warning', 'Агент «Редактор» выбран, но модель не загрузилась: OOM');
    expect(localStorage.getItem('active_agent_id')).toBe('2');
  });

  it('исключение при загрузке модели: предупреждение с текстом ошибки', async () => {
    await renderTab();
    mockLoadModel.mockRejectedValueOnce(new Error('boom'));
    await act(async () => {
      fireEvent.click(screen.getByText('Редактор'));
    });
    await flush();
    expect(mockNotify).toHaveBeenLastCalledWith('warning', 'Агент «Редактор» выбран, но модель не загрузилась: boom');
    mockLoadModel.mockRejectedValueOnce('строка');
    await act(async () => {
      fireEvent.click(screen.getByText('Аналитик'));
    });
    await flush();
    expect(mockNotify).toHaveBeenLastCalledWith('warning', 'Агент «Аналитик» выбран, но модель не загрузилась: строка');
  });

  it('во время загрузки модели клики блокируются и виден индикатор у агента', async () => {
    await renderTab();
    let finish!: (v: unknown) => void;
    mockLoadModel.mockReturnValue(new Promise((r) => (finish = r)));
    await act(async () => {
      fireEvent.click(screen.getByText('Редактор'));
    });
    await flush();
    expect(screen.getAllByRole('progressbar')).toHaveLength(1);
    fireEvent.click(screen.getByText('Аналитик'));
    expect(mockLoadModel).toHaveBeenCalledTimes(1);
    expect(mockClearAgent).not.toHaveBeenCalled();
    await act(async () => finish({ ok: true }));
    await flush();
    expect(screen.queryByRole('progressbar')).toBeNull();
  });

  it('повторный клик по активному агенту снимает выбор и уведомляет', async () => {
    localStorage.setItem('active_agent_id', '1');
    localStorage.setItem('active_agent_name', 'Аналитик');
    await renderTab();
    fireEvent.click(screen.getByText('Аналитик'));
    expect(mockClearAgent).toHaveBeenCalledTimes(1);
    expect(mockNotify).toHaveBeenCalledWith('info', 'Агент снят');
  });

  it('внешнее событие agentSelected обновляет отметку', async () => {
    await renderTab();
    act(() => {
      localStorage.setItem('active_agent_id', '2');
      localStorage.setItem('active_agent_name', 'Редактор');
      window.dispatchEvent(new CustomEvent('agentSelected'));
    });
    const row = screen.getByText('Редактор').closest('div[class*="MuiBox"]')!.parentElement!;
    expect(row.querySelector('[data-testid="CheckIcon"]')).toBeTruthy();
  });

  it('невидимая вкладка ничего не грузит', () => {
    setupList(AGENTS);
    render(<Tab isDarkMode searchQuery="" visible={false} />);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('пустой список без поиска: подсказка и ссылка в галерею', async () => {
    setupList([]);
    render(<Tab isDarkMode searchQuery="" visible />);
    await flush();
    expect(
      screen.getByText(/Нет своих агентов|В галерее пока нет агентов/),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Открыть галерею' })).toBeInTheDocument();
  });

  it('индикатор первичной загрузки списка', async () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    render(<Tab isDarkMode searchQuery="" visible />);
    await flush();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });
});

describe('ChatGearMyAgentsTab: загрузка и общие агенты', () => {
  it('без токена список пуст и запросов нет', async () => {
    mockToken = null;
    render(<ChatGearMyAgentsTab isDarkMode={false} searchQuery="" visible />);
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText('Нет своих агентов')).toBeInTheDocument();
  });

  it('объединяет свои и расшаренные агенты без дублей и подписывает автора', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/api/agents/my/agents')) return resp({ agents: [AGENTS[0]] });
      if (String(url).includes('/api/agents/my/shared'))
        return resp({ agents: [{ ...AGENTS[0] }, AGENTS[1], { id: 3, name: 'Безымянный автор', system_prompt: '', author_id: '' }] });
      return resp({});
    });
    render(<ChatGearMyAgentsTab isDarkMode searchQuery="" visible />);
    await flush();
    expect(screen.getAllByText('Аналитик')).toHaveLength(1);
    expect(screen.getByText('от Боб Иванов · bob')).toBeInTheDocument();
    expect(screen.getByText('от коллеги')).toBeInTheDocument();
    // Авторизацию (JWT + cookie) добавляет authFetch, а не компонент.
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/agents/my/');
  });

  it('ошибки HTTP и сбой сети дают пустой список', async () => {
    fetchMock.mockResolvedValue(resp({}, false));
    const first = render(<ChatGearMyAgentsTab isDarkMode searchQuery="" visible />);
    await flush();
    expect(screen.getByText('Нет своих агентов')).toBeInTheDocument();
    first.unmount();
    fetchMock.mockRejectedValue(new Error('net'));
    render(<ChatGearMyAgentsTab isDarkMode searchQuery="" visible />);
    await flush();
    expect(screen.getByText('Нет своих агентов')).toBeInTheDocument();
  });
});

describe('ChatGearGalleryAgentsTab: закладки', () => {
  it('без токена список пуст и запросов нет', async () => {
    mockToken = null;
    render(<ChatGearGalleryAgentsTab isDarkMode searchQuery="" visible />);
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText('В галерее пока нет агентов')).toBeInTheDocument();
  });

  it('автор показывается с запасным «автора»; ошибка загрузки даёт пустой список', async () => {
    fetchMock.mockImplementation(async () => resp({ agents: [{ id: 5, name: 'Без автора', system_prompt: '', author_id: '' }] }));
    const first = render(<ChatGearGalleryAgentsTab isDarkMode searchQuery="" visible />);
    await flush();
    expect(screen.getByText('от автора')).toBeInTheDocument();
    first.unmount();
    fetchMock.mockRejectedValue(new Error('net'));
    render(<ChatGearGalleryAgentsTab isDarkMode searchQuery="" visible />);
    await flush();
    expect(screen.getByText('В галерее пока нет агентов')).toBeInTheDocument();
  });

  it('HTTP-ошибка списка даёт пустой список', async () => {
    fetchMock.mockResolvedValue(resp({}, false));
    render(<ChatGearGalleryAgentsTab isDarkMode searchQuery="" visible />);
    await flush();
    expect(screen.getByText('В галерее пока нет агентов')).toBeInTheDocument();
  });

  const setupBookmarks = async () => {
    fetchMock.mockImplementation(async (url: string, init?: any) => {
      if (url.includes('/bookmarks?limit=100')) return resp({ agents: AGENTS });
      if (init?.method === 'DELETE') return resp({}, true);
      return resp({}, false);
    });
    render(<ChatGearGalleryAgentsTab isDarkMode searchQuery="" visible />);
    await flush();
  };

  it('удаление из галереи: DELETE, строка исчезает, клик не выбирает агента', async () => {
    await setupBookmarks();
    const row = screen.getByText('Редактор').closest('div[class*="MuiBox"]')!.parentElement!;
    await act(async () => {
      fireEvent.click(row.querySelector('[data-testid="DeleteOutlineIcon"]')!.closest('button')!);
    });
    await flush();
    const call = fetchMock.mock.calls.find((c) => c[1]?.method === 'DELETE')!;
    expect(call[0]).toBe('http://api.test/api/agents/2/bookmark');
    expect(screen.queryByText('Редактор')).toBeNull();
    expect(mockLoadModel).not.toHaveBeenCalled();
    expect(mockNotify).toHaveBeenCalledWith('success', '«Редактор» убран из «Агенты из галереи»');
  });

  it('удаление активного агента ещё и снимает выбор', async () => {
    localStorage.setItem('active_agent_id', '2');
    localStorage.setItem('active_agent_name', 'Редактор');
    await setupBookmarks();
    const row = screen.getByText('Редактор').closest('div[class*="MuiBox"]')!.parentElement!;
    await act(async () => {
      fireEvent.click(row.querySelector('[data-testid="DeleteOutlineIcon"]')!.closest('button')!);
    });
    await flush();
    expect(mockClearAgent).toHaveBeenCalledTimes(1);
    expect(mockNotify).toHaveBeenCalledWith('info', 'Агент снят');
  });

  it('ошибка удаления: уведомление, строка остаётся', async () => {
    await setupBookmarks();
    fetchMock.mockImplementation(async () => resp({}, false));
    const row = screen.getByText('Редактор').closest('div[class*="MuiBox"]')!.parentElement!;
    await act(async () => {
      fireEvent.click(row.querySelector('[data-testid="DeleteOutlineIcon"]')!.closest('button')!);
    });
    await flush();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось убрать агента');
    expect(screen.getByText('Редактор')).toBeInTheDocument();
  });
});
