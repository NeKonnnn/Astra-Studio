/* eslint-disable @typescript-eslint/no-explicit-any */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import App from '@/App';

// ---------------------------------------------------------------------------
// Управляемое состояние моков
// ---------------------------------------------------------------------------
const mockPath = { current: '/' };
const mockInitSettings = jest.fn();
const mockInsetHook = jest.fn();
const mockRightBarLayout: { current: any } = { current: null };
const mockLeftProps: { current: any } = { current: null };
const mockRightProps: { current: any } = { current: null };
const mockModalProps: { current: any } = { current: null };

// react-router-dom v7 не резолвится в Jest 27: минимальный роутер на основе mockPath.
jest.mock('react-router-dom', () => {
  const R = require('react');
  const match = (pattern: string | undefined, path: string) => {
    if (pattern === undefined) return false;
    if (pattern === '/*') return true;
    const [pathname] = path.split('?');
    return new RegExp('^' + pattern.replace(/:[^/]+/g, '[^/]+') + '$').test(pathname);
  };
  return {
    BrowserRouter: ({ children }: any) => <div data-testid="router">{children}</div>,
    Routes: ({ children }: any) => {
      const arr = R.Children.toArray(children) as any[];
      const found = arr.find((c) => match(c.props.path, mockPath.current));
      return found ? found.props.element : null;
    },
    Route: () => null,
    Navigate: ({ to, replace }: any) => (
      <div data-testid="navigate" data-to={to} data-replace={String(Boolean(replace))} />
    ),
  };
});

jest.mock('@/components/left_bar', () => ({
  LeftBar: (props: any) => {
    mockLeftProps.current = props;
    return (
      <div data-testid="leftbar" data-open={String(props.open)} data-dark={String(props.isDarkMode)} data-nonce={props.searchFocusNonce}>
        <button onClick={props.onToggle}>left-toggle</button>
        <button onClick={props.onToggleTheme}>left-theme</button>
        <button onClick={props.onHide}>left-hide</button>
      </div>
    );
  },
}));
jest.mock('@/components/right_bar', () => ({
  RightBarProvider: ({ value, children }: any) => {
    mockRightBarLayout.current = value;
    return <div data-testid="rb-provider">{children}</div>;
  },
  RightBar: (props: any) => {
    mockRightProps.current = props;
    return (
      <div data-testid="rightbar">
        <button onClick={props.onToggleOpen}>right-toggle</button>
        <button onClick={props.onHide}>right-hide</button>
        <button onClick={props.onShow}>right-show</button>
      </div>
    );
  },
}));
jest.mock('@/components/GlobalKeyboardShortcuts', () => ({ __esModule: true, default: () => <div data-testid="hotkeys" /> }));
jest.mock('@/components/SettingsModal', () => ({
  __esModule: true,
  default: (props: any) => {
    mockModalProps.current = props;
    return (
      <div data-testid="settings-modal" data-open={String(props.open)} data-section={String(props.initialSection)}>
        <button onClick={props.onClose}>modal-close</button>
        <button onClick={props.onToggleTheme}>modal-theme</button>
      </div>
    );
  },
}));
jest.mock('@/hooks/useRightSidebarInsetCssVar', () => ({
  useRightSidebarInsetCssVar: (...a: any[]) => mockInsetHook(...a),
}));
jest.mock('@/settings', () => ({ initSettings: () => mockInitSettings() }));

jest.mock('@/pages/UnifiedChatPage', () => {
  const R = require('react');
  const mui = require('@mui/material');
  return {
    __esModule: true,
    default: (props: any) => {
      const theme = mui.useTheme();
      return (
        <div
          data-testid="unified"
          data-dark={String(props.isDarkMode)}
          data-open={String(props.sidebarOpen)}
          data-hidden={String(props.sidebarHidden)}
          data-mode={theme.palette.mode}
          data-primary={theme.palette.primary.main}
          data-radius={String(theme.shape.borderRadius)}
        >
          {/* рендер компонентов, к которым применяются styleOverrides темы */}
          <mui.MenuItem data-testid="menu-item">пункт</mui.MenuItem>
          <mui.ListItemButton data-testid="list-btn">кнопка</mui.ListItemButton>
          <mui.Autocomplete
            open
            options={['один']}
            renderInput={(p: any) => <mui.TextField {...p} />}
          />
        </div>
      );
    },
  };
});
const page = (name: string) => ({ __esModule: true, default: (p: any) => <div data-testid={name} data-props={JSON.stringify(p)} /> });
jest.mock('@/pages/HistoryPage', () => page('history'));
jest.mock('@/pages/ReleaseNotesPage', () => ({
  __esModule: true,
  default: ({ version }: any) => <div data-testid={`release-${version}`} />,
}));
jest.mock('@/pages/UserGuidePage', () => page('user-guide'));
jest.mock('@/pages/AgentGalleryPage', () => page('agents-gallery'));
jest.mock('@/pages/PluginGalleryPage', () => page('plugins-gallery'));
jest.mock('@/pages/GalleryHubPage', () => page('gallery-hub'));
jest.mock('@/pages/SkillsPage', () => page('skills'));
jest.mock('@/pages/ProjectPage', () => page('project-page'));
jest.mock('@/pages/LoginPage', () => page('login'));
jest.mock('@/pages/SsoCallbackPage', () => page('sso-callback'));
jest.mock('@/pages/ProfilePage', () => page('profile'));
jest.mock('@/pages/ShareViewPage', () => page('share'));

const passthrough = (name: string) => ({ children }: any) => <div data-testid={name}>{children}</div>;
jest.mock('@/contexts/SocketContext', () => ({ SocketProvider: passthrough('socket-provider') }));
jest.mock('@/contexts/AppContext', () => ({ AppProvider: passthrough('app-provider') }));
jest.mock('@/contexts/AuthContext', () => ({ AuthProvider: passthrough('auth-provider') }));
jest.mock('@/contexts/RagReindexStatusContext', () => ({ RagReindexStatusProvider: passthrough('rag-provider') }));
jest.mock('@/contexts/PluginRunContext', () => ({ PluginRunProvider: passthrough('plugin-provider') }));
jest.mock('@/components/PrivateRoute', () => ({ __esModule: true, default: passthrough('private-route') }));
jest.mock('@/components/SessionTimeoutWatcher', () => ({ __esModule: true, default: () => <div data-testid="timeout-watcher" /> }));
jest.mock('@/components/SessionValidityWatcher', () => ({ __esModule: true, default: () => <div data-testid="validity-watcher" /> }));
jest.mock('@/components/LlmStatusBanner', () => ({ __esModule: true, default: () => <div data-testid="llm-banner" /> }));
jest.mock('@/components/RagReindexStatusBanner', () => ({ __esModule: true, default: () => <div data-testid="rag-banner" /> }));
jest.mock('@/components/GenerationCompleteToastHost', () => ({ __esModule: true, default: () => <div data-testid="generation-toast-host" /> }));
jest.mock('@/components/StatusToastHost', () => ({ __esModule: true, default: () => <div data-testid="toast-host" /> }));
jest.mock('@/components/PluginRunBanner', () => ({ __esModule: true, default: () => <div data-testid="plugin-banner" /> }));
jest.mock('@/components/TabNotificationWatcher', () => ({ __esModule: true, default: () => <div data-testid="tab-watcher" /> }));
jest.mock('@/support_assistant', () => ({ __esModule: true, default: () => <div data-testid="support-widget" /> }));

const SETTINGS_EVT = 'astrachat:open-settings';
const SECTION_EVT = 'astrachat:open-settings-section';
const FOCUS_EVT = 'astrachat:focus-chat-search';

function renderApp(path = '/') {
  mockPath.current = path;
  return render(<App />);
}

describe('App', () => {
  beforeEach(() => {
    localStorage.clear();
    mockPath.current = '/';
    mockInitSettings.mockReset().mockResolvedValue(undefined);
    mockInsetHook.mockReset();
    mockRightBarLayout.current = null;
    mockLeftProps.current = null;
    mockRightProps.current = null;
    mockModalProps.current = null;
    document.documentElement.removeAttribute('style');
  });
  afterEach(() => jest.restoreAllMocks());

  describe('инициализация', () => {
    it('вызывает initSettings при монтировании; сбой конфигурации логируется и не ломает приложение', async () => {
      const err = new Error('cfg');
      mockInitSettings.mockRejectedValue(err);
      const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
      renderApp('/');
      await act(async () => {});
      expect(mockInitSettings).toHaveBeenCalledTimes(1);
      expect(spy).toHaveBeenCalledWith('Ошибка загрузки конфигурации:', err);
      expect(screen.getByTestId('unified')).toBeTruthy();
    });

    it('оборачивает приложение в провайдеры и показывает баннеры и наблюдатели', () => {
      renderApp('/');
      for (const id of [
        'llm-banner', 'auth-provider', 'app-provider', 'router', 'rag-provider', 'rag-banner',
        'toast-host', 'plugin-provider', 'plugin-banner', 'socket-provider', 'tab-watcher',
        'timeout-watcher', 'validity-watcher',
      ]) {
        expect(screen.getByTestId(id)).toBeTruthy();
      }
    });

    it('без сохранённых настроек: светлая тема, обе панели открыты и не скрыты', () => {
      renderApp('/');
      expect(screen.getByTestId('unified').getAttribute('data-mode')).toBe('light');
      expect(screen.getByTestId('leftbar').getAttribute('data-open')).toBe('true');
      expect(mockRightProps.current.open).toBe(true);
      expect(mockRightProps.current.hidden).toBe(false);
      expect(mockRightProps.current.expandedWidthPx).toBe(240);
      expect(mockRightProps.current.widthPinned).toBe(false);
    });

    it('восстанавливает тему и состояние панелей из localStorage', () => {
      localStorage.setItem('gazikii-dark-mode', 'true');
      localStorage.setItem('sidebarOpen', 'false');
      localStorage.setItem('rightSidebarOpen', 'false');
      localStorage.setItem('rightSidebarHidden', 'true');
      localStorage.setItem('rightSidebarWidth', '400');
      localStorage.setItem('rightSidebarWidthPinned', 'true');
      renderApp('/');
      expect(screen.getByTestId('unified').getAttribute('data-mode')).toBe('dark');
      expect(screen.getByTestId('leftbar').getAttribute('data-open')).toBe('false');
      expect(mockRightProps.current).toMatchObject({
        open: false, hidden: true, expandedWidthPx: 400, widthPinned: true,
      });
      expect(mockInsetHook).toHaveBeenLastCalledWith(false, true, 400);
    });

    it('скрытая левая панель из localStorage: LeftBar не рендерится, есть кнопка показа', () => {
      localStorage.setItem('sidebarHidden', 'true');
      renderApp('/');
      expect(screen.queryByTestId('leftbar')).toBeNull();
      expect(screen.getByTestId('ChevronRightIcon')).toBeTruthy();
      expect(screen.getByTestId('unified').getAttribute('data-hidden')).toBe('true');
    });

    it('сохраняет состояние в localStorage и публикует CSS-переменные меню', () => {
      renderApp('/');
      expect(localStorage.getItem('gazikii-dark-mode')).toBe('false');
      expect(localStorage.getItem('sidebarOpen')).toBe('true');
      expect(localStorage.getItem('sidebarHidden')).toBe('false');
      expect(localStorage.getItem('rightSidebarOpen')).toBe('true');
      expect(localStorage.getItem('rightSidebarHidden')).toBe('false');
      expect(localStorage.getItem('rightSidebarWidth')).toBe('240');
      expect(localStorage.getItem('rightSidebarWidthPinned')).toBe('false');
      const root = document.documentElement.style;
      expect(root.getPropertyValue('--menu-item-hover')).not.toBe('');
      expect(root.getPropertyValue('--menu-border-radius')).toMatch(/px$/);
      expect(root.getPropertyValue('--menu-item-hover-radius')).toMatch(/px$/);
      expect(root.getPropertyValue('--menu-item-hover-margin')).toMatch(/px$/);
      expect(root.getPropertyValue('--menu-min-width')).toMatch(/px$/);
      expect(root.getPropertyValue('--menu-icon-min-width')).toMatch(/px$/);
      expect(root.getPropertyValue('--menu-icon-to-text-gap')).toMatch(/px$/);
      expect(root.getPropertyValue('--menu-icon-font-size')).toMatch(/px$/);
    });
  });

  describe('тема', () => {
    it('переключение темы: сохраняется синхронно, рассылается sidebarColorChanged, меняется цвет hover-переменной', () => {
      const events: any[] = [];
      const on = (e: Event) => events.push((e as CustomEvent).detail);
      window.addEventListener('sidebarColorChanged', on);
      renderApp('/');
      const lightHover = document.documentElement.style.getPropertyValue('--menu-item-hover');
      fireEvent.click(screen.getByText('left-theme'));
      window.removeEventListener('sidebarColorChanged', on);
      expect(localStorage.getItem('gazikii-dark-mode')).toBe('true');
      expect(screen.getByTestId('unified').getAttribute('data-mode')).toBe('dark');
      expect(screen.getByTestId('leftbar').getAttribute('data-dark')).toBe('true');
      expect(events).toEqual([{ isDarkMode: false }, { isDarkMode: true }]);
      expect(document.documentElement.style.getPropertyValue('--menu-item-hover')).not.toBe(lightHover);
      // из модального окна настроек переключатель делает то же самое
      fireEvent.click(screen.getByText('modal-theme'));
      expect(localStorage.getItem('gazikii-dark-mode')).toBe('false');
    });

    it('тема приложения: фирменные цвета и скругление, стили меню применяются к MUI-компонентам', () => {
      renderApp('/');
      const el = screen.getByTestId('unified');
      expect(el.getAttribute('data-primary')).toBe('#2196f3');
      expect(el.getAttribute('data-radius')).toBe('12');
      const item = screen.getByTestId('menu-item');
      expect(getComputedStyle(item).borderRadius).not.toBe('');
      expect(getComputedStyle(screen.getByTestId('list-btn')).marginLeft).toMatch(/px$/);
    });
  });

  describe('левая панель', () => {
    it('переключение раскрытия сохраняется и сдвигает main (-64px у свёрнутой)', () => {
      renderApp('/');
      const main = document.querySelector('main') as HTMLElement;
      expect(getComputedStyle(main).marginLeft).toBe('0px');
      fireEvent.click(screen.getByText('left-toggle'));
      expect(localStorage.getItem('sidebarOpen')).toBe('false');
      expect(screen.getByTestId('leftbar').getAttribute('data-open')).toBe('false');
      expect(getComputedStyle(main).marginLeft).toBe('-64px');
    });

    it('скрытие панели → кнопка «Показать панель» возвращает свёрнутую панель', () => {
      renderApp('/');
      fireEvent.click(screen.getByText('left-hide'));
      expect(screen.queryByTestId('leftbar')).toBeNull();
      expect(localStorage.getItem('sidebarHidden')).toBe('true');
      expect(getComputedStyle(document.querySelector('main') as HTMLElement).marginLeft).toBe('0px');
      fireEvent.click(screen.getByTestId('ChevronRightIcon').closest('button')!);
      expect(screen.getByTestId('leftbar').getAttribute('data-open')).toBe('false');
      expect(localStorage.getItem('sidebarHidden')).toBe('false');
      expect(localStorage.getItem('sidebarOpen')).toBe('false');
    });
  });

  describe('правая панель', () => {
    it('toggle / hide / show обновляют состояние, main и CSS-переменную отступа', () => {
      renderApp('/');
      const main = document.querySelector('main') as HTMLElement;
      fireEvent.click(screen.getByText('right-toggle'));
      expect(mockRightProps.current.open).toBe(false);
      expect(getComputedStyle(main).marginRight).toBe('-64px');
      expect(mockInsetHook).toHaveBeenLastCalledWith(false, false, 240);
      fireEvent.click(screen.getByText('right-hide'));
      expect(mockRightProps.current.hidden).toBe(true);
      expect(getComputedStyle(main).marginRight).toBe('0px');
      expect(localStorage.getItem('rightSidebarHidden')).toBe('true');
      fireEvent.click(screen.getByText('right-show'));
      expect(mockRightProps.current).toMatchObject({ hidden: false, open: false });
      expect(localStorage.getItem('rightSidebarOpen')).toBe('false');
    });

    it('сеттеры из пропсов и из RightBarProvider меняют ширину, закрепление, open и hidden', () => {
      renderApp('/');
      act(() => {
        mockRightProps.current.setExpandedWidthPx(500);
        mockRightProps.current.setWidthPinned(true);
        mockRightProps.current.setOpen(false);
        mockRightProps.current.setHidden(true);
      });
      expect(localStorage.getItem('rightSidebarWidth')).toBe('500');
      expect(localStorage.getItem('rightSidebarWidthPinned')).toBe('true');
      expect(mockRightProps.current).toMatchObject({ open: false, hidden: true, expandedWidthPx: 500, widthPinned: true });
      const layout = mockRightBarLayout.current;
      expect(layout).toMatchObject({ open: false, hidden: true, expandedWidthPx: 500, widthPinned: true });
      act(() => {
        layout.setOpen(true);
        layout.setHidden(false);
        layout.setExpandedWidthPx((w: number) => w + 10);
        layout.setWidthPinned((p: boolean) => !p);
      });
      expect(mockRightProps.current).toMatchObject({ open: true, hidden: false, expandedWidthPx: 510, widthPinned: false });
    });

    it('сеттеры стабильны между рендерами (не пересоздаются)', () => {
      renderApp('/');
      const first = mockRightProps.current;
      fireEvent.click(screen.getByText('left-toggle'));
      const second = mockRightProps.current;
      expect(second.setOpen).toBe(first.setOpen);
      expect(second.setHidden).toBe(first.setHidden);
      expect(second.setExpandedWidthPx).toBe(first.setExpandedWidthPx);
      expect(second.setWidthPinned).toBe(first.setWidthPinned);
    });
  });

  describe('глобальные события', () => {
    it('открытие настроек по событию и закрытие сбрасывает раздел', () => {
      renderApp('/');
      const modal = () => screen.getByTestId('settings-modal');
      expect(modal().getAttribute('data-open')).toBe('false');
      act(() => { window.dispatchEvent(new Event(SETTINGS_EVT)); });
      expect(modal().getAttribute('data-open')).toBe('true');
      expect(modal().getAttribute('data-section')).toBe('undefined');
      fireEvent.click(screen.getByText('modal-close'));
      expect(modal().getAttribute('data-open')).toBe('false');
    });

    it('событие с разделом открывает настройки сразу на нужной вкладке; закрытие сбрасывает раздел', () => {
      renderApp('/');
      act(() => {
        window.dispatchEvent(new CustomEvent(SECTION_EVT, { detail: { section: 'models' } }));
      });
      const modal = screen.getByTestId('settings-modal');
      expect(modal.getAttribute('data-open')).toBe('true');
      expect(modal.getAttribute('data-section')).toBe('models');
      fireEvent.click(screen.getByText('modal-close'));
      expect(screen.getByTestId('settings-modal').getAttribute('data-section')).toBe('undefined');
    });

    it('событие раздела без section игнорируется', () => {
      renderApp('/');
      act(() => {
        window.dispatchEvent(new CustomEvent(SECTION_EVT, { detail: {} }));
        window.dispatchEvent(new CustomEvent(SECTION_EVT));
      });
      expect(screen.getByTestId('settings-modal').getAttribute('data-open')).toBe('false');
    });

    it('фокус на поиск чатов показывает скрытую панель, раскрывает её и увеличивает счётчик', () => {
      localStorage.setItem('sidebarHidden', 'true');
      localStorage.setItem('sidebarOpen', 'false');
      renderApp('/');
      expect(screen.queryByTestId('leftbar')).toBeNull();
      act(() => { window.dispatchEvent(new Event(FOCUS_EVT)); });
      const bar = screen.getByTestId('leftbar');
      expect(bar.getAttribute('data-open')).toBe('true');
      expect(bar.getAttribute('data-nonce')).toBe('1');
      act(() => { window.dispatchEvent(new Event(FOCUS_EVT)); });
      expect(screen.getByTestId('leftbar').getAttribute('data-nonce')).toBe('2');
    });

    it('при размонтировании слушатели событий снимаются', () => {
      const { unmount } = renderApp('/');
      const remove = jest.spyOn(window, 'removeEventListener');
      unmount();
      const names = remove.mock.calls.map((c) => c[0]);
      expect(names).toEqual(expect.arrayContaining([SETTINGS_EVT, SECTION_EVT, FOCUS_EVT]));
    });
  });

  describe('маршруты', () => {
    it.each([
      ['/login', 'login'],
      ['/login/sso-callback', 'sso-callback'],
      ['/share/abc123', 'share'],
    ])('публичный маршрут %s не оборачивается в PrivateRoute и не показывает панели', (path, id) => {
      renderApp(path);
      expect(screen.getByTestId(id)).toBeTruthy();
      expect(screen.queryByTestId('private-route')).toBeNull();
      expect(screen.queryByTestId('leftbar')).toBeNull();
      expect(screen.queryByTestId('rightbar')).toBeNull();
    });

    it.each([
      ['/', 'unified'],
      ['/project', 'unified'],
      ['/voice', 'unified'],
      ['/documents', 'unified'],
      ['/search', 'unified'],
      ['/project/p1', 'project-page'],
      ['/gallery', 'gallery-hub'],
      ['/agents-gallery', 'agents-gallery'],
      ['/plugins-gallery', 'plugins-gallery'],
      ['/skills', 'skills'],
      ['/profile', 'profile'],
      ['/history', 'history'],
      ['/docs/astrachat-release-1.0', 'release-1.0'],
      ['/docs/astrachat-release-2.0', 'release-2.0'],
      ['/docs/user-guide', 'user-guide'],
    ])('защищённый маршрут %s рендерит %s внутри PrivateRoute с панелями', (path, id) => {
      renderApp(path);
      expect(screen.getByTestId('private-route')).toBeTruthy();
      expect(screen.getByTestId(id)).toBeTruthy();
      expect(screen.getByTestId('leftbar')).toBeTruthy();
      expect(screen.getByTestId('rightbar')).toBeTruthy();
      expect(screen.getByTestId('hotkeys')).toBeTruthy();
      expect(screen.getByTestId('support-widget')).toBeTruthy();
    });

    it('UnifiedChatPage получает тему и состояние левой панели', () => {
      localStorage.setItem('gazikii-dark-mode', 'true');
      localStorage.setItem('sidebarOpen', 'false');
      renderApp('/voice');
      const el = screen.getByTestId('unified');
      expect(el.getAttribute('data-dark')).toBe('true');
      expect(el.getAttribute('data-open')).toBe('false');
      expect(el.getAttribute('data-hidden')).toBe('false');
    });

    it('страница проекта получает sidebarOpen/sidebarHidden', () => {
      localStorage.setItem('sidebarOpen', 'false');
      renderApp('/project/p9');
      expect(JSON.parse(screen.getByTestId('project-page').getAttribute('data-props')!)).toEqual({
        sidebarOpen: false,
        sidebarHidden: false,
      });
    });

    it('старый путь /prompts перенаправляется на вкладку агентов галереи', () => {
      renderApp('/prompts');
      const nav = screen.getByTestId('navigate');
      expect(nav.getAttribute('data-to')).toBe('/gallery?tab=agents');
      expect(nav.getAttribute('data-replace')).toBe('true');
    });

    it('неизвестный защищённый путь показывает каркас без страницы', () => {
      renderApp('/no-such-page');
      expect(screen.getByTestId('private-route')).toBeTruthy();
      expect(screen.getByTestId('leftbar')).toBeTruthy();
      expect(document.querySelector('main')!.querySelector('[data-testid="unified"]')).toBeNull();
    });
  });
});
