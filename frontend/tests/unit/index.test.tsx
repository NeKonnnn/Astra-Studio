const mockRender = jest.fn();
const mockCreateRoot = jest.fn(() => ({ render: mockRender }));
const mockInstallHandlers = jest.fn();
const mockInitSettings = jest.fn();

jest.mock('react-dom/client', () => ({ __esModule: true, default: { createRoot: mockCreateRoot }, createRoot: mockCreateRoot }));
jest.mock('katex/dist/katex.min.css', () => ({}), { virtual: true });
jest.mock('@/index.css', () => ({}), { virtual: true });
jest.mock('@/App', () => ({ __esModule: true, default: () => null }));
jest.mock('@/settings', () => ({ initSettings: (...a: unknown[]) => mockInitSettings(...a) }));
jest.mock('@/utils/debugReact185', () => ({ installAstra185GlobalHandlers: () => mockInstallHandlers() }));

describe('index.tsx (точка входа)', () => {
  beforeEach(() => {
    jest.resetModules();
    mockRender.mockClear();
    mockCreateRoot.mockReturnValue({ render: mockRender });
    mockInstallHandlers.mockClear();
    document.body.innerHTML = '<div id="root"></div>';
  });

  it('задаёт ширину меню, ставит диагностику, грузит настройки и монтирует App в StrictMode', async () => {
    mockInitSettings.mockResolvedValue(undefined);
    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      require('./index');
    });
    expect(document.documentElement.style.getPropertyValue('--menu-min-width')).toMatch(/^\d+px$/);
    expect(mockInstallHandlers).toHaveBeenCalledTimes(1);
    expect(mockInitSettings).toHaveBeenCalledTimes(1);
    expect(mockCreateRoot).toHaveBeenCalledWith(document.getElementById('root'));
    expect(mockRender).toHaveBeenCalledTimes(1);
    const tree: any = mockRender.mock.calls[0][0];
    expect(String(tree.type.toString())).toContain('react.strict_mode');
  });

  it('ошибка initSettings не приводит к необработанному отклонению', async () => {
    mockInitSettings.mockRejectedValue(new Error('config down'));
    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      require('./index');
    });
    await Promise.resolve();
    await Promise.resolve();
    expect(mockRender).toHaveBeenCalledTimes(1);
  });
});
