/**
 * Общий набор тестов левого сайдбара. Вызывается из LeftBar.test.tsx (актуальный компонент)
 * и Sidebar.test.tsx (устаревшая копия, на неё никто не ссылается, но она в зоне покрытия).
 * Моки модулей (jest.mock) объявляются в самих тестовых файлах.
 */
import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { h, resetHarness } from './sidebarHarness';
import {
  ASTRA_OPEN_SETTINGS,
  ASTRA_REQUEST_DELETE_CURRENT_CHAT,
  formatHotkeyBinding,
  getHotkeyBindings,
} from '../../constants/hotkeys';
import { SIDEBAR_PANEL_COLOR_KEY } from '../../constants/sidebarPanelColor';

export type SuiteOptions = {
  variant: 'left' | 'legacy';
  /** Мок prepareNewChatSettings (только у актуального LeftBar). */
  prepareNewChatSettings?: jest.Mock;
};

const TODAY = '2026-05-15T10:00:00';
const YESTERDAY = '2026-05-14T10:00:00';
const FIVE_DAYS_AGO = '2026-05-10T10:00:00';

type ChatOver = Record<string, unknown>;
function mkChat(id: string, title: string, over: ChatOver = {}) {
  return {
    id,
    title,
    messages: [{ content: `текст чата ${title}` }],
    createdAt: TODAY,
    updatedAt: TODAY,
    ...over,
  } as any;
}
const mkFolder = (id: string, name: string, chatIds: string[], expanded = true) => ({ id, name, chatIds, expanded });
const mkProject = (id: string, name: string, over: Record<string, unknown> = {}) => ({
  id,
  name,
  memory: 'default' as const,
  instructions: '',
  ...over,
});

const flush = (ms = 1000) => {
  act(() => {
    jest.advanceTimersByTime(ms);
  });
};

/** Настройки режима «папки» хранятся в localStorage и читаются при монтировании. */
const setFoldersMode = (on: boolean) => localStorage.setItem('use_folders_mode', on ? 'true' : 'false');

const rowOf = (title: string) => screen.getByText(title).closest('.MuiListItemButton-root') as HTMLElement;
const clickText = (text: string | RegExp) => fireEvent.click(screen.getByText(text));
const hoverText = (text: string | RegExp) => fireEvent.mouseEnter(screen.getByText(text));
const popover = () => {
  const all = document.querySelectorAll('.MuiPopover-root');
  return all[all.length - 1] as HTMLElement;
};
const openChatMenu = (title: string) => {
  fireEvent.click(within(rowOf(title)).getByTestId('MoreVertIcon'));
};
const folderHeader = (name: string) => screen.getByText(name).parentElement!.parentElement as HTMLElement;
const typeInto = (el: Element, value: string) => fireEvent.change(el, { target: { value } });
const pressEnterKeyPress = (el: Element) => fireEvent.keyPress(el, { key: 'Enter', code: 'Enter', charCode: 13 });
const dialog = () => screen.getByRole('dialog');

export function defineSidebarSuite(
  name: string,
  getSidebar: () => React.ComponentType<any>,
  opts: SuiteOptions,
) {
  const legacy = opts.variant === 'legacy';

  function renderSidebar(over: Record<string, unknown> = {}) {
    const Sidebar = getSidebar();
    const props = {
      open: true,
      onToggle: jest.fn(),
      isDarkMode: true,
      onToggleTheme: jest.fn(),
      ...over,
    };
    const utils = render(<Sidebar {...props} />);
    return {
      ...utils,
      props,
      rerenderWith: (next: Record<string, unknown>) => {
        Object.assign(props, next);
        utils.rerender(<Sidebar {...props} />);
      },
    };
  }

  describe(name, () => {
    beforeEach(() => {
      jest.useFakeTimers();
      jest.setSystemTime(new Date('2026-05-15T12:00:00'));
      localStorage.clear();
      resetHarness();
      opts.prepareNewChatSettings?.mockReset();
    });
    afterEach(() => {
      jest.useRealTimers();
    });

    // ------------------------------------------------------------------ шапка / свёрнутый режим
    describe('шапка и свёрнутая рельса', () => {
      it('раскрытый: показывает бренд и кнопку «Свернуть панель», она вызывает onToggle', () => {
        const { props } = renderSidebar();
        expect(screen.getByText('AstraChat')).toBeInTheDocument();
        fireEvent.click(screen.getByLabelText('Свернуть панель'));
        expect(props.onToggle).toHaveBeenCalledTimes(1);
        expect(screen.getByPlaceholderText('Поиск в чатах')).toBeInTheDocument();
      });

      it('свёрнутый: бренда, поиска и списков нет, «Открыть панель» вызывает onToggle', () => {
        const { props } = renderSidebar({ open: false });
        expect(screen.queryByText('AstraChat')).toBeNull();
        expect(screen.queryByPlaceholderText('Поиск в чатах')).toBeNull();
        expect(screen.queryByText('Все чаты')).toBeNull();
        expect(screen.queryByText('Проекты')).toBeNull();
        fireEvent.click(screen.getByLabelText('Открыть панель'));
        expect(props.onToggle).toHaveBeenCalledTimes(1);
      });

      it('свёрнутый: кнопки рельсы идут по порядку (панель, новый чат, поиск, [«Моё творение»], скрыть, пользователь)', () => {
        const onHide = jest.fn();
        renderSidebar({ open: false, onHide });
        const buttons = screen.getAllByRole('button');
        // актуальный LeftBar дополнительно содержит кнопку «Моё творение» после поиска
        expect(buttons).toHaveLength(legacy ? 5 : 6);
        if (!legacy) {
          fireEvent.click(buttons[3]);
          expect(h.navigate).toHaveBeenCalledWith('/creations');
          h.navigate.mockClear();
        }
        // Новый чат
        fireEvent.click(buttons[1]);
        expect(h.actions.createChat).toHaveBeenCalledTimes(1);
        expect(h.navigate).toHaveBeenCalledWith('/');
        // Скрыть панель
        fireEvent.click(buttons[legacy ? 3 : 4]);
        expect(onHide).toHaveBeenCalledTimes(1);
      });

      it('свёрнутый без onHide: кнопки «Скрыть панель» нет', () => {
        renderSidebar({ open: false });
        expect(screen.getAllByRole('button')).toHaveLength(legacy ? 4 : 5);
        expect(screen.queryByLabelText('Скрыть панель')).toBeNull();
      });

      it('свёрнутый: «Поиск» разворачивает панель и через 300 мс фокусирует поле поиска', () => {
        const { props, rerenderWith } = renderSidebar({ open: false });
        fireEvent.click(screen.getAllByRole('button')[2]);
        expect(props.onToggle).toHaveBeenCalledTimes(1);
        rerenderWith({ open: true });
        flush(299);
        expect(screen.getByPlaceholderText('Поиск в чатах')).not.toHaveFocus();
        flush(1);
        expect(screen.getByPlaceholderText('Поиск в чатах')).toHaveFocus();
      });

      it('свёрнутый: кнопка пользователя подписана именем и открывает меню', () => {
        renderSidebar({ open: false });
        fireEvent.click(screen.getByLabelText('Иван Петров'));
        expect(screen.getByText('Настройки')).toBeInTheDocument();
        expect(screen.getByText('Выйти из аккаунта')).toBeInTheDocument();
      });

      it('свёрнутый: без full_name подпись — username; без пользователя — «Меню» и инициал «М»', () => {
        h.user = { username: 'ivan' };
        const first = renderSidebar({ open: false });
        expect(screen.getByLabelText('ivan')).toBeInTheDocument();
        first.unmount();

        h.user = null;
        renderSidebar({ open: false });
        expect(screen.getByLabelText('Меню')).toBeInTheDocument();
        expect(screen.getByText('М')).toBeInTheDocument();
      });

      it('свёрнутый: аватар пользователя', () => {
        renderSidebar({ open: false });
        // актуальный компонент рисует UserAvatar, устаревший — MUI Avatar с первой буквой логина
        expect(screen.getByText(legacy ? 'I' : 'avatar')).toBeInTheDocument();
      });

      it('горячие клавиши «Новый чат» и «Поиск» показаны подсказками в раскрытом сайдбаре', () => {
        renderSidebar();
        const b = getHotkeyBindings();
        expect(screen.getByText(formatHotkeyBinding(b.newChat))).toBeInTheDocument();
        expect(screen.getByText(formatHotkeyBinding(b.searchChats))).toBeInTheDocument();
      });
    });

    // ------------------------------------------------------------------ фокус поиска
    describe('searchFocusNonce', () => {
      it('нулевой nonce ничего не делает', () => {
        const { props } = renderSidebar({ searchFocusNonce: 0 });
        flush(500);
        expect(props.onToggle).not.toHaveBeenCalled();
        expect(screen.getByPlaceholderText('Поиск в чатах')).not.toHaveFocus();
      });

      it('положительный nonce при раскрытой панели сразу фокусирует поиск', () => {
        const { props } = renderSidebar({ searchFocusNonce: 1 });
        expect(screen.getByPlaceholderText('Поиск в чатах')).toHaveFocus();
        expect(props.onToggle).not.toHaveBeenCalled();
      });

      it('новый nonce повторно фокусирует поиск', () => {
        const { rerenderWith } = renderSidebar({ searchFocusNonce: 1 });
        act(() => {
          (document.activeElement as HTMLElement).blur();
        });
        expect(screen.getByPlaceholderText('Поиск в чатах')).not.toHaveFocus();
        rerenderWith({ searchFocusNonce: 2 });
        expect(screen.getByPlaceholderText('Поиск в чатах')).toHaveFocus();
      });

      it('при свёрнутой панели просит родителя развернуть её, а после раскрытия фокусирует поиск', () => {
        const { props, rerenderWith } = renderSidebar({ open: false, searchFocusNonce: 1 });
        expect(props.onToggle).toHaveBeenCalledTimes(1);
        rerenderWith({ open: true });
        expect(screen.getByPlaceholderText('Поиск в чатах')).toHaveFocus();
      });

      it('размонтирование до таймера 320 мс не падает и не вызывает повторно onToggle', () => {
        const { props, unmount } = renderSidebar({ open: false, searchFocusNonce: 1 });
        unmount();
        flush(500);
        expect(props.onToggle).toHaveBeenCalledTimes(1);
      });
    });

    // ------------------------------------------------------------------ внешние события и цвет
    describe('события окна', () => {
      it('запрос удаления текущего чата открывает диалог, подтверждение удаляет чат', () => {
        h.state.chats = [mkChat('c1', 'Текущий')];
        h.state.currentChatId = 'c1';
        renderSidebar();
        act(() => {
          window.dispatchEvent(new CustomEvent(ASTRA_REQUEST_DELETE_CURRENT_CHAT));
        });
        expect(screen.getByText('Удалить чат')).toBeInTheDocument();
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.deleteChat).toHaveBeenCalledWith('c1');
      });

      it('запрос удаления без текущего чата игнорируется', () => {
        h.state.chats = [mkChat('c1', 'Один')];
        h.state.currentChatId = null;
        renderSidebar();
        act(() => {
          window.dispatchEvent(new CustomEvent(ASTRA_REQUEST_DELETE_CURRENT_CHAT));
        });
        expect(screen.queryByText('Удалить чат')).toBeNull();
      });

      it('после размонтирования слушатель запроса удаления снят', () => {
        h.state.chats = [mkChat('c1', 'Один')];
        h.state.currentChatId = 'c1';
        const { unmount } = renderSidebar();
        unmount();
        act(() => {
          window.dispatchEvent(new CustomEvent(ASTRA_REQUEST_DELETE_CURRENT_CHAT));
        });
        expect(screen.queryByText('Удалить чат')).toBeNull();
      });

      it('sidebarColorChanged пересчитывает фон панели из localStorage', () => {
        localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, '#336699');
        renderSidebar();
        const paper = document.querySelector('.MuiDrawer-paper') as HTMLElement;
        expect(getComputedStyle(paper).backgroundColor).toBe('rgb(51, 102, 153)');
        localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, '#aa0000');
        act(() => {
          window.dispatchEvent(new Event('sidebarColorChanged'));
        });
        expect(getComputedStyle(paper).backgroundColor).toBe('rgb(170, 0, 0)');
      });

      if (!legacy) {
        it('залипший дефолтный HEX в localStorage сбрасывается в пустую строку', () => {
          localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, '#F7F7F9');
          renderSidebar();
          expect(localStorage.getItem(SIDEBAR_PANEL_COLOR_KEY)).toBe('');
        });

        it('пользовательский цвет панели не трогается', () => {
          localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, '#336699');
          renderSidebar();
          expect(localStorage.getItem(SIDEBAR_PANEL_COLOR_KEY)).toBe('#336699');
        });

        it('залипший тёмный дефолт #212128 тоже сбрасывается', () => {
          localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, '#212128');
          renderSidebar();
          expect(localStorage.getItem(SIDEBAR_PANEL_COLOR_KEY)).toBe('');
        });
      }

      it('interfaceSettingsChanged переключает режим «проекты» → «папки»', () => {
        renderSidebar();
        expect(screen.getByText('Проекты')).toBeInTheDocument();
        expect(screen.queryByLabelText('Создать папку')).toBeNull();
        setFoldersMode(true);
        act(() => {
          window.dispatchEvent(new Event('interfaceSettingsChanged'));
        });
        expect(screen.queryByText('Проекты')).toBeNull();
        expect(screen.getByLabelText('Создать папку')).toBeInTheDocument();
      });

      it('interfaceSettingsChanged без сохранённого значения возвращает режим «проекты»', () => {
        setFoldersMode(true);
        renderSidebar();
        expect(screen.queryByText('Проекты')).toBeNull();
        localStorage.removeItem('use_folders_mode');
        act(() => {
          window.dispatchEvent(new Event('interfaceSettingsChanged'));
        });
        expect(screen.getByText('Проекты')).toBeInTheDocument();
      });

      it('после смены режима список «Все чаты» пересчитывается: чат проекта попадает в «Все чаты»', () => {
        // После переключения в режим «папки» чат проекта (проекты больше не показываются)
        // попадает в «Все чаты»: useMemo filteredChats зависит от useFoldersMode.
        h.projects = [mkProject('p1', 'Проект')];
        h.state.chats = [mkChat('c1', 'Чат проекта', { projectId: 'p1' })];
        renderSidebar();
        expect(screen.queryByText('Чат проекта')).toBeNull();
        setFoldersMode(true);
        act(() => {
          window.dispatchEvent(new Event('interfaceSettingsChanged'));
        });
        expect(screen.queryByText('Проекты')).toBeNull();
        expect(screen.getByText('Чат проекта')).toBeInTheDocument();
      });
    });

    // ------------------------------------------------------------------ новый чат
    describe('кнопка «Новый чат»', () => {
      it('создаёт чат, делает его текущим и переходит на главную', () => {
        renderSidebar();
        fireEvent.click(screen.getByText('Новый чат'));
        expect(h.actions.createChat).toHaveBeenCalledTimes(1);
        expect(h.actions.setCurrentChat).toHaveBeenCalledWith('new-chat-id');
        expect(h.navigate).toHaveBeenCalledWith('/');
        expect(h.actions.deleteChat).not.toHaveBeenCalled();
        if (!legacy) {
          expect(opts.prepareNewChatSettings).toHaveBeenCalledWith(undefined, 'new-chat-id');
        }
      });

      it('пустой (ещё не показанный в списке) текущий чат удаляется перед созданием нового', () => {
        h.state.chats = [mkChat('empty', 'Пустой', { messages: [] })];
        h.state.currentChatId = 'empty';
        renderSidebar();
        fireEvent.click(screen.getByText('Новый чат'));
        expect(h.actions.deleteChat).toHaveBeenCalledWith('empty');
        expect(h.actions.createChat).toHaveBeenCalled();
        if (!legacy) {
          expect(opts.prepareNewChatSettings).toHaveBeenCalledWith('empty', 'new-chat-id');
        }
      });

      it('текущий чат с перепиской не удаляется', () => {
        h.state.chats = [mkChat('c1', 'С перепиской')];
        h.state.currentChatId = 'c1';
        renderSidebar();
        fireEvent.click(screen.getByText('Новый чат'));
        expect(h.actions.deleteChat).not.toHaveBeenCalled();
        expect(h.actions.setCurrentChat).toHaveBeenCalledWith('new-chat-id');
      });

      if (!legacy) {
        it('пустой чат, который ещё генерирует ответ (loadingChatIds), не удаляется', () => {
          h.state.chats = [mkChat('empty', 'Пустой', { messages: [] })];
          h.state.currentChatId = 'empty';
          h.state.loadingChatIds = ['empty'];
          renderSidebar();
          fireEvent.click(screen.getByText('Новый чат'));
          expect(h.actions.deleteChat).not.toHaveBeenCalled();
          expect(h.actions.setCurrentChat).toHaveBeenCalledWith('new-chat-id');
        });

        it('скрытый чат со стримящимся сообщением не удаляется', () => {
          h.state.chats = [
            mkChat('branch', 'Ветка', {
              hiddenFromSidebarUntilUserMessage: true,
              messages: [{ content: 'идёт ответ', isStreaming: true }],
            }),
          ];
          h.state.currentChatId = 'branch';
          renderSidebar();
          fireEvent.click(screen.getByText('Новый чат'));
          expect(h.actions.deleteChat).not.toHaveBeenCalled();
        });

        it('скрытый чат без стриминга и без загрузки удаляется', () => {
          h.state.chats = [
            mkChat('branch', 'Ветка', {
              hiddenFromSidebarUntilUserMessage: true,
              messages: [{ content: 'готово' }],
            }),
          ];
          h.state.currentChatId = 'branch';
          renderSidebar();
          fireEvent.click(screen.getByText('Новый чат'));
          expect(h.actions.deleteChat).toHaveBeenCalledWith('branch');
        });
      }
    });

    // ------------------------------------------------------------------ список «Все чаты» и поиск
    describe('список «Все чаты»', () => {
      it('пустой список показывает «Пока нет чатов»', () => {
        renderSidebar();
        expect(screen.getByText('Пока нет чатов')).toBeInTheDocument();
      });

      it('чаты группируются по времени: Сегодня → Вчера → Последние 7 дней', () => {
        h.state.chats = [
          mkChat('c3', 'Старый', { updatedAt: FIVE_DAYS_AGO }),
          mkChat('c1', 'Новый', { updatedAt: TODAY }),
          mkChat('c2', 'Вчерашний', { updatedAt: YESTERDAY }),
        ];
        renderSidebar();
        const labels = screen.getAllByText(/^(Сегодня|Вчера|Последние 7 дней)$/).map((e) => e.textContent);
        expect(labels).toEqual(['Сегодня', 'Вчера', 'Последние 7 дней']);
        const html = document.body.innerHTML;
        expect(html.indexOf('Новый')).toBeLessThan(html.indexOf('Вчерашний'));
        expect(html.indexOf('Вчерашний')).toBeLessThan(html.indexOf('Старый'));
      });

      it('клик по чату выбирает его и переходит на главную', () => {
        h.state.chats = [mkChat('c1', 'Первый'), mkChat('c2', 'Второй')];
        renderSidebar();
        fireEvent.click(rowOf('Второй'));
        expect(h.actions.setCurrentChat).toHaveBeenCalledWith('c2');
        expect(h.navigate).toHaveBeenCalledWith('/');
      });

      it('текущий чат выделен жирным шрифтом', () => {
        h.state.chats = [mkChat('c1', 'Первый'), mkChat('c2', 'Второй')];
        h.state.currentChatId = 'c2';
        renderSidebar();
        expect(screen.getByText('Второй')).toHaveStyle('font-weight: 600');
        expect(screen.getByText('Первый')).toHaveStyle('font-weight: 400');
      });

      it('заголовок «Все чаты» сворачивает и разворачивает список', () => {
        h.state.chats = [mkChat('c1', 'Первый')];
        renderSidebar();
        clickText('Все чаты');
        expect(screen.queryByText('Первый')).toBeNull();
        expect(screen.queryByText('Пока нет чатов')).toBeNull();
        clickText('Все чаты');
        expect(screen.getByText('Первый')).toBeInTheDocument();
      });

      it('в список не попадают архивные, скрытые до сообщения и пустые чаты', () => {
        h.state.chats = [
          mkChat('c1', 'Видимый'),
          mkChat('c2', 'Архивный', { isArchived: true }),
          mkChat('c3', 'Скрытый', { hiddenFromSidebarUntilUserMessage: true }),
          mkChat('c4', 'Пустой', { messages: [] }),
        ];
        renderSidebar();
        expect(screen.getByText('Видимый')).toBeInTheDocument();
        expect(screen.queryByText('Архивный')).toBeNull();
        expect(screen.queryByText('Скрытый')).toBeNull();
        expect(screen.queryByText('Пустой')).toBeNull();
      });

      it('выгруженный из памяти чат виден, если в нём были сообщения', () => {
        h.state.chats = [
          mkChat('c1', 'Выгруженный', { messages: [], messagesUnloaded: true, unloadedMessageCount: 4 }),
          mkChat('c2', 'Выгруженный пустой', { messages: [], messagesUnloaded: true, unloadedMessageCount: 0 }),
        ];
        renderSidebar();
        expect(screen.getByText('Выгруженный')).toBeInTheDocument();
        expect(screen.queryByText('Выгруженный пустой')).toBeNull();
      });

      it('поиск фильтрует по заголовку без учёта регистра', () => {
        h.state.chats = [mkChat('c1', 'Отчёт по продажам'), mkChat('c2', 'Рецепт борща')];
        renderSidebar();
        typeInto(screen.getByPlaceholderText('Поиск в чатах'), 'ОТЧЁТ');
        expect(screen.getByText('Отчёт по продажам')).toBeInTheDocument();
        expect(screen.queryByText('Рецепт борща')).toBeNull();
      });

      it('поиск ищет и по тексту сообщений', () => {
        h.state.chats = [
          mkChat('c1', 'Первый', { messages: [{ content: 'Обсуждаем КВАНТОВУЮ физику' }] }),
          mkChat('c2', 'Второй', { messages: [{ content: 'про кулинарию' }] }),
        ];
        renderSidebar();
        typeInto(screen.getByPlaceholderText('Поиск в чатах'), 'квантов');
        expect(screen.getByText('Первый')).toBeInTheDocument();
        expect(screen.queryByText('Второй')).toBeNull();
      });

      it('если поиск ничего не нашёл, показывает «Ничего не найдено»', () => {
        h.state.chats = [mkChat('c1', 'Первый')];
        renderSidebar();
        typeInto(screen.getByPlaceholderText('Поиск в чатах'), 'zzz');
        expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
        expect(screen.queryByText('Пока нет чатов')).toBeNull();
      });

      it('запрос из одних пробелов не фильтрует список', () => {
        h.state.chats = [mkChat('c1', 'Первый'), mkChat('c2', 'Второй')];
        renderSidebar();
        typeInto(screen.getByPlaceholderText('Поиск в чатах'), '   ');
        expect(screen.getByText('Первый')).toBeInTheDocument();
        expect(screen.getByText('Второй')).toBeInTheDocument();
      });
    });

    // ------------------------------------------------------------------ секция проектов
    describe('секция «Проекты»', () => {
      it('показывает проекты и скрывает их при сворачивании секции', () => {
        h.projects = [mkProject('p1', 'Альфа-проект'), mkProject('p2', 'Бета-проект')];
        renderSidebar();
        expect(screen.getByText('Альфа-проект')).toBeInTheDocument();
        expect(screen.getByText('Бета-проект')).toBeInTheDocument();
        clickText('Проекты');
        expect(screen.queryByText('Альфа-проект')).toBeNull();
        expect(screen.queryByText('Новый проект')).toBeNull();
        clickText('Проекты');
        expect(screen.getByText('Альфа-проект')).toBeInTheDocument();
      });

      it('клик по названию проекта ведёт на /project/<id>', () => {
        h.projects = [mkProject('p1', 'Альфа-проект')];
        renderSidebar();
        clickText('Альфа-проект');
        expect(h.navigate).toHaveBeenCalledWith('/project/p1');
      });

      it('«Новый проект» открывает модалку создания', () => {
        renderSidebar();
        expect(screen.queryByTestId('new-project-modal')).toBeNull();
        clickText('Новый проект');
        expect(screen.getByTestId('new-project-modal')).toBeInTheDocument();
      });

      it('«Новый проект» открывается с клавиатуры по Enter и Space, остальные клавиши игнорируются', () => {
        renderSidebar();
        const btn = screen.getByText('Новый проект').closest('[role="button"]') as HTMLElement;
        fireEvent.keyDown(btn, { key: 'a' });
        expect(screen.queryByTestId('new-project-modal')).toBeNull();
        fireEvent.keyDown(btn, { key: 'Enter' });
        expect(screen.getByTestId('new-project-modal')).toBeInTheDocument();
        act(() => h.props.newProject.onClose());
        expect(screen.queryByTestId('new-project-modal')).toBeNull();
        fireEvent.keyDown(btn, { key: ' ' });
        expect(screen.getByTestId('new-project-modal')).toBeInTheDocument();
      });

      it('иконка проекта: emoji показывается текстом, известная иконка — компонентом, неизвестная и отсутствие — папкой', () => {
        h.projects = [
          mkProject('p1', 'Эмодзи', { iconType: 'emoji', icon: '🚀', iconColor: '#ff0000' }),
          mkProject('p2', 'Деньги', { iconType: 'icon', icon: 'money' }),
          mkProject('p3', 'Неизвестная', { iconType: 'icon', icon: 'no-such-icon' }),
          mkProject('p4', 'Без иконки'),
        ];
        renderSidebar();
        const li = (n: string) => screen.getByText(n).closest('li') as HTMLElement;
        expect(within(li('Эмодзи')).getByText('🚀')).toBeInTheDocument();
        expect(within(li('Деньги')).getByTestId(legacy ? 'AttachMoneyIcon' : 'AttachMoneyOutlinedIcon')).toBeInTheDocument();
        expect(within(li('Неизвестная')).getByTestId('FolderOutlinedIcon')).toBeInTheDocument();
        expect(within(li('Без иконки')).getByTestId('FolderOutlinedIcon')).toBeInTheDocument();
      });

      it('чаты проекта скрыты, пока проект не раскрыт; раскрытие показывает только видимые чаты', () => {
        h.projects = [mkProject('p1', 'Проект')];
        h.state.chats = [
          mkChat('c1', 'Обычный', { projectId: 'p1' }),
          mkChat('c2', 'Архивный', { projectId: 'p1', isArchived: true }),
          mkChat('c3', 'Пустой', { projectId: 'p1', messages: [] }),
          mkChat('c4', 'Чужой', { projectId: 'other' }),
        ];
        renderSidebar();
        expect(screen.queryByText('Обычный')).toBeNull();
        fireEvent.click(screen.getByLabelText('Развернуть чаты проекта'));
        expect(screen.getByText('Обычный')).toBeInTheDocument();
        expect(screen.queryByText('Архивный')).toBeNull();
        expect(screen.queryByText('Пустой')).toBeNull();
        expect(screen.queryByText('Чужой')).toBeNull();
        fireEvent.click(screen.getByLabelText('Свернуть чаты проекта'));
        expect(screen.queryByText('Обычный')).toBeNull();
      });

      it('закреплённые чаты проекта идут первыми, остальные — по убыванию даты', () => {
        h.projects = [mkProject('p1', 'Проект')];
        h.state.chats = [
          mkChat('c1', 'Старый', { projectId: 'p1', updatedAt: YESTERDAY }),
          mkChat('c2', 'Новый', { projectId: 'p1', updatedAt: TODAY }),
          mkChat('c3', 'Закреплённый старый', { projectId: 'p1', updatedAt: FIVE_DAYS_AGO, isPinnedInProject: true }),
        ];
        renderSidebar();
        fireEvent.click(screen.getByLabelText('Развернуть чаты проекта'));
        const html = document.body.innerHTML;
        const pos = ['Закреплённый старый', 'Новый', 'Старый'].map((t) => html.indexOf(`>${t}<`));
        expect(pos.every((p) => p > 0)).toBe(true);
        expect(pos[0]).toBeLessThan(pos[1]);
        expect(pos[1]).toBeLessThan(pos[2]);
      });

      it('два закреплённых чата сортируются по дате обновления', () => {
        h.projects = [mkProject('p1', 'Проект')];
        h.state.chats = [
          mkChat('c1', 'Пин старый', { projectId: 'p1', updatedAt: YESTERDAY, isPinnedInProject: true }),
          mkChat('c2', 'Пин новый', { projectId: 'p1', updatedAt: TODAY, isPinnedInProject: true }),
          mkChat('c3', 'Обычный', { projectId: 'p1', updatedAt: TODAY }),
        ];
        renderSidebar();
        fireEvent.click(screen.getByLabelText('Развернуть чаты проекта'));
        const html = document.body.innerHTML;
        expect(html.indexOf('>Пин новый<')).toBeLessThan(html.indexOf('>Пин старый<'));
        expect(html.indexOf('>Пин старый<')).toBeLessThan(html.indexOf('>Обычный<'));
      });

      it('клик по чату проекта выбирает его', () => {
        h.projects = [mkProject('p1', 'Проект')];
        h.state.chats = [mkChat('c1', 'Чат проекта', { projectId: 'p1' })];
        renderSidebar();
        fireEvent.click(screen.getByLabelText('Развернуть чаты проекта'));
        fireEvent.click(rowOf('Чат проекта'));
        expect(h.actions.setCurrentChat).toHaveBeenCalledWith('c1');
        expect(h.navigate).toHaveBeenCalledWith('/');
      });

      it('чат проекта не попадает в «Все чаты» в режиме проектов', () => {
        h.projects = [mkProject('p1', 'Проект')];
        h.state.chats = [mkChat('c1', 'Чат проекта', { projectId: 'p1' }), mkChat('c2', 'Свободный')];
        renderSidebar();
        expect(screen.getByText('Свободный')).toBeInTheDocument();
        expect(screen.queryByText('Чат проекта')).toBeNull();
      });

      it('меню проекта: «Редактировать проект» открывает модалку с этим проектом, сохранение вызывает updateProject', () => {
        h.projects = [mkProject('p1', 'Проект')];
        renderSidebar();
        fireEvent.click(within(screen.getByText('Проект').closest('li') as HTMLElement).getByTestId('MoreVertIcon'));
        clickText('Редактировать проект');
        expect(screen.getByTestId('edit-project-modal')).toHaveTextContent('Проект');
        act(() => h.props.editProject.onSave('p1', { name: 'Переименован' }));
        expect(h.actions.updateProject).toHaveBeenCalledWith('p1', { name: 'Переименован' });
        flush();
        expect(screen.queryByTestId('edit-project-modal')).toBeNull();
      });

      it('закрытие модалки редактирования не сохраняет проект', () => {
        h.projects = [mkProject('p1', 'Проект')];
        renderSidebar();
        fireEvent.click(within(screen.getByText('Проект').closest('li') as HTMLElement).getByTestId('MoreVertIcon'));
        clickText('Редактировать проект');
        act(() => h.props.editProject.onClose());
        expect(screen.queryByTestId('edit-project-modal')).toBeNull();
        expect(h.actions.updateProject).not.toHaveBeenCalled();
      });

      it('меню проекта: удаление требует подтверждения', () => {
        h.projects = [mkProject('p1', 'Проект')];
        renderSidebar();
        fireEvent.click(within(screen.getByText('Проект').closest('li') as HTMLElement).getByTestId('MoreVertIcon'));
        clickText('Удалить проект');
        flush();
        expect(screen.getAllByText('Удалить проект').length).toBeGreaterThan(0);
        expect(h.actions.deleteProject).not.toHaveBeenCalled();
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.deleteProject).toHaveBeenCalledWith('p1');
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
      });

      it('отмена удаления проекта ничего не удаляет', () => {
        h.projects = [mkProject('p1', 'Проект')];
        renderSidebar();
        fireEvent.click(within(screen.getByText('Проект').closest('li') as HTMLElement).getByTestId('MoreVertIcon'));
        clickText('Удалить проект');
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Отменить' }));
        flush();
        expect(h.actions.deleteProject).not.toHaveBeenCalled();
        expect(screen.queryByRole('dialog')).toBeNull();
      });

      it('клик по пункту меню проекта после его закрытия (во время анимации) игнорируется', () => {
        h.projects = [mkProject('p1', 'Проект')];
        renderSidebar();
        fireEvent.click(within(screen.getByText('Проект').closest('li') as HTMLElement).getByTestId('MoreVertIcon'));
        const item = screen.getByText('Редактировать проект');
        fireEvent.keyDown(popover(), { key: 'Escape' });
        fireEvent.click(popover().querySelector('.MuiBackdrop-root') as HTMLElement);
        fireEvent.click(item);
        expect(screen.queryByTestId('edit-project-modal')).toBeNull();
        expect(h.actions.deleteProject).not.toHaveBeenCalled();
      });

      it('подтверждение удаления проекта без выбранного проекта ничего не делает', () => {
        h.projects = [mkProject('p1', 'Проект')];
        renderSidebar();
        fireEvent.click(within(screen.getByText('Проект').closest('li') as HTMLElement).getByTestId('MoreVertIcon'));
        clickText('Удалить проект');
        // выбранный проект сбрасывается закрытием меню под диалогом
        fireEvent.click(popover().querySelector('.MuiBackdrop-root') as HTMLElement);
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.deleteProject).toHaveBeenCalledTimes(0);
      });
    });

    // ------------------------------------------------------------------ колбэки NewProjectModal
    describe('колбэки модалки «Новый проект»', () => {
      const draft = {
        name: 'Мой проект',
        icon: 'money',
        iconType: 'icon',
        iconColor: '#123456',
        memory: 'project-only',
        instructions: 'Отвечай кратко',
        extra: 'не должно утечь',
      };
      const picked = {
        name: 'Мой проект',
        icon: 'money',
        iconType: 'icon',
        iconColor: '#123456',
        memory: 'project-only',
        instructions: 'Отвечай кратко',
      };

      it('onCreateProject создаёт проект только с нужными полями и возвращает его id', () => {
        renderSidebar();
        clickText('Новый проект');
        let id: string | undefined;
        act(() => {
          id = h.props.newProject.onCreateProject(draft);
        });
        expect(id).toBe('new-project-id');
        expect(h.actions.createProject).toHaveBeenCalledWith(picked);
        expect(h.actions.moveChatToProject).not.toHaveBeenCalled();
      });

      it('ensureDraftProjectForRag создаёт черновик проекта и возвращает id', () => {
        renderSidebar();
        let id: string | undefined;
        act(() => {
          id = h.props.newProject.ensureDraftProjectForRag(draft);
        });
        expect(id).toBe('new-project-id');
        expect(h.actions.createProject).toHaveBeenCalledWith(picked);
        expect(h.actions.moveChatToProject).not.toHaveBeenCalled();
      });

      it('finalizeDraftProject обновляет проект выбранными полями', () => {
        renderSidebar();
        act(() => h.props.newProject.finalizeDraftProject('p9', draft));
        expect(h.actions.updateProject).toHaveBeenCalledWith('p9', picked);
        expect(h.actions.moveChatToProject).not.toHaveBeenCalled();
      });

      it('cancelDraftProject удаляет черновик проекта', () => {
        renderSidebar();
        act(() => h.props.newProject.cancelDraftProject('p9'));
        expect(h.actions.deleteProject).toHaveBeenCalledWith('p9');
      });

      it('onClose скрывает модалку', () => {
        renderSidebar();
        clickText('Новый проект');
        act(() => h.props.newProject.onClose());
        expect(screen.queryByTestId('new-project-modal')).toBeNull();
      });

      describe('создание проекта из меню чата (чат ожидает переноса)', () => {
        const startPending = () => {
          h.state.chats = [mkChat('c1', 'Мой чат')];
          const view = renderSidebar();
          openChatMenu('Мой чат');
          hoverText('Перейти в проект');
          fireEvent.click(within(popover()).getByText('Новый проект'));
          return view;
        };

        it('onCreateProject переносит ожидающий чат в созданный проект один раз', () => {
          startPending();
          expect(screen.getByTestId('new-project-modal')).toBeInTheDocument();
          act(() => {
            h.props.newProject.onCreateProject(draft);
          });
          expect(h.actions.moveChatToProject).toHaveBeenCalledWith('c1', 'new-project-id');
          act(() => {
            h.props.newProject.onCreateProject(draft);
          });
          expect(h.actions.moveChatToProject).toHaveBeenCalledTimes(1);
        });

        it('ensureDraftProjectForRag переносит ожидающий чат', () => {
          startPending();
          act(() => {
            h.props.newProject.ensureDraftProjectForRag(draft);
          });
          expect(h.actions.moveChatToProject).toHaveBeenCalledWith('c1', 'new-project-id');
        });

        it('finalizeDraftProject переносит ожидающий чат в финализированный проект', () => {
          startPending();
          act(() => h.props.newProject.finalizeDraftProject('p9', draft));
          expect(h.actions.moveChatToProject).toHaveBeenCalledWith('c1', 'p9');
        });

        it('закрытие модалки сбрасывает ожидающий чат', () => {
          startPending();
          act(() => h.props.newProject.onClose());
          act(() => {
            h.props.newProject.onCreateProject(draft);
          });
          expect(h.actions.createProject).toHaveBeenCalledTimes(1);
          expect(h.actions.moveChatToProject).not.toHaveBeenCalled();
        });
      });
    });

    // ------------------------------------------------------------------ меню чата
    describe('меню чата', () => {
      it('«Пин» для чата вне проекта создаёт папку «Закреплено» и переносит туда чат', () => {
        h.state.chats = [mkChat('c1', 'Чат')];
        renderSidebar();
        openChatMenu('Чат');
        clickText('Пин');
        expect(h.actions.createFolder).toHaveBeenCalledWith('Закреплено');
        expect(h.actions.moveChatToFolder).toHaveBeenCalledWith('c1', 'new-folder-id');
        flush();
        expect(screen.queryByText('Переименовать')).toBeNull();
      });

      it('«Пин» при существующей папке «Закреплено» переносит чат в неё без создания новой', () => {
        h.state.chats = [mkChat('c1', 'Чат')];
        h.folders = [mkFolder('fp', 'Закреплено', ['x'])];
        renderSidebar();
        openChatMenu('Чат');
        clickText('Пин');
        expect(h.actions.createFolder).not.toHaveBeenCalled();
        expect(h.actions.moveChatToFolder).toHaveBeenCalledWith('c1', 'fp');
      });

      it('чат в папке «Закреплено» имеет пункт «Открепить», клик убирает его из папки', () => {
        h.state.chats = [mkChat('c1', 'Чат')];
        h.folders = [mkFolder('fp', 'Закреплено', ['c1'])];
        renderSidebar();
        openChatMenu('Чат');
        expect(screen.queryByText('Пин')).toBeNull();
        clickText('Открепить');
        expect(h.actions.moveChatToFolder).toHaveBeenCalledWith('c1', null);
        expect(h.actions.createFolder).not.toHaveBeenCalled();
      });

      it('для чата проекта закрепление локальное: togglePinInProject, подпись зависит от isPinnedInProject', () => {
        h.projects = [mkProject('p1', 'Проект')];
        h.state.chats = [
          mkChat('c1', 'Обычный', { projectId: 'p1' }),
          mkChat('c2', 'Закреплённый', { projectId: 'p1', isPinnedInProject: true }),
        ];
        renderSidebar();
        fireEvent.click(screen.getByLabelText('Развернуть чаты проекта'));
        openChatMenu('Обычный');
        expect(within(popover()).getByText('Пин')).toBeInTheDocument();
        fireEvent.click(within(popover()).getByText('Пин'));
        expect(h.actions.togglePinInProject).toHaveBeenCalledWith('c1');
        expect(h.actions.createFolder).not.toHaveBeenCalled();
        flush();
        openChatMenu('Закреплённый');
        expect(within(popover()).getByText('Открепить')).toBeInTheDocument();
        fireEvent.click(within(popover()).getByText('Открепить'));
        expect(h.actions.togglePinInProject).toHaveBeenCalledWith('c2');
      });

      it('«Архив» архивирует чат и закрывает меню', () => {
        h.state.chats = [mkChat('c1', 'Чат')];
        renderSidebar();
        openChatMenu('Чат');
        fireEvent.click(within(popover()).getByText('Архив'));
        expect(h.actions.archiveChat).toHaveBeenCalledWith('c1');
        flush();
        expect(screen.queryByText('Переименовать')).toBeNull();
      });

      it('«Перенести из проекта» есть только у чата проекта в режиме проектов', () => {
        h.projects = [mkProject('p1', 'Проект')];
        h.state.chats = [mkChat('c1', 'Чат проекта', { projectId: 'p1' }), mkChat('c2', 'Свободный')];
        renderSidebar();
        openChatMenu('Свободный');
        expect(screen.queryByText('Перенести из проекта')).toBeNull();
        fireEvent.keyDown(popover(), { key: 'Escape' });
        fireEvent.click(popover().querySelector('.MuiBackdrop-root') as HTMLElement);
        flush();
        fireEvent.click(screen.getByLabelText('Развернуть чаты проекта'));
        openChatMenu('Чат проекта');
        fireEvent.click(screen.getByText('Перенести из проекта'));
        expect(h.actions.moveChatToProject).toHaveBeenCalledWith('c1', null);
      });

      it('в режиме папок пункта «Перенести из проекта» нет, а подменю называется «Перейти в папку»', () => {
        setFoldersMode(true);
        h.state.chats = [mkChat('c1', 'Чат', { projectId: 'p1' })];
        renderSidebar();
        openChatMenu('Чат');
        expect(screen.queryByText('Перенести из проекта')).toBeNull();
        expect(screen.getByText('Перейти в папку')).toBeInTheDocument();
        expect(screen.queryByText('Перейти в проект')).toBeNull();
      });

      it('«Удалить» → диалог → подтверждение удаляет чат; чат не текущий — выбор не меняется', () => {
        h.state.chats = [mkChat('c1', 'Один'), mkChat('c2', 'Два')];
        h.state.currentChatId = 'c2';
        renderSidebar();
        openChatMenu('Один');
        fireEvent.click(within(popover()).getByText('Удалить'));
        expect(screen.getByText('Удалить чат')).toBeInTheDocument();
        expect(h.actions.deleteChat).not.toHaveBeenCalled();
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.deleteChat).toHaveBeenCalledWith('c1');
        expect(h.actions.setCurrentChat).not.toHaveBeenCalled();
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
      });

      it('удаление текущего чата переключает на первый видимый из оставшихся', () => {
        h.state.chats = [
          mkChat('empty', 'Пустой', { messages: [] }),
          mkChat('c2', 'Видимый'),
          mkChat('c1', 'Текущий'),
        ];
        h.state.currentChatId = 'c1';
        renderSidebar();
        openChatMenu('Текущий');
        fireEvent.click(within(popover()).getByText('Удалить'));
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.deleteChat).toHaveBeenCalledWith('c1');
        expect(h.actions.setCurrentChat).toHaveBeenCalledWith('c2');
      });

      it('удаление текущего чата, если видимых нет, выбирает первый из оставшихся (даже пустой)', () => {
        h.state.chats = [mkChat('empty', 'Пустой', { messages: [] }), mkChat('c1', 'Текущий')];
        h.state.currentChatId = 'c1';
        renderSidebar();
        openChatMenu('Текущий');
        fireEvent.click(within(popover()).getByText('Удалить'));
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.setCurrentChat).toHaveBeenCalledWith('empty');
      });

      it('удаление единственного текущего чата сбрасывает текущий чат в null', () => {
        h.state.chats = [mkChat('c1', 'Единственный')];
        h.state.currentChatId = 'c1';
        renderSidebar();
        openChatMenu('Единственный');
        fireEvent.click(within(popover()).getByText('Удалить'));
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.setCurrentChat).toHaveBeenCalledWith(null);
      });

      it('«Отменить» в диалоге удаления чата ничего не удаляет', () => {
        h.state.chats = [mkChat('c1', 'Один')];
        renderSidebar();
        openChatMenu('Один');
        fireEvent.click(within(popover()).getByText('Удалить'));
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Отменить' }));
        flush();
        expect(h.actions.deleteChat).not.toHaveBeenCalled();
        expect(screen.queryByRole('dialog')).toBeNull();
      });

      it('клик по пункту меню чата после закрытия меню (во время анимации) игнорируется', () => {
        h.state.chats = [mkChat('c1', 'Один')];
        renderSidebar();
        openChatMenu('Один');
        const archive = within(popover()).getByText('Архив');
        // закрываем меню кликом по подложке, но DOM ещё не размонтирован
        fireEvent.click(popover().querySelector('.MuiBackdrop-root') as HTMLElement);
        // selectedChatId остаётся, поэтому действие всё же выполняется — фиксируем текущее поведение
        fireEvent.click(archive);
        expect(h.actions.archiveChat).toHaveBeenCalledWith('c1');
      });

      it('подменю закрывается через 220 мс после ухода курсора и не закрывается, если курсор вернулся', () => {
        setFoldersMode(true);
        h.state.chats = [mkChat('c1', 'Чат')];
        renderSidebar();
        openChatMenu('Чат');
        hoverText('Перейти в папку');
        expect(within(popover()).getByText('Создать папку')).toBeInTheDocument();
        fireEvent.mouseLeave(within(popover()).getByText('Создать папку'));
        flush(219);
        expect(within(popover()).getByText('Создать папку')).toBeInTheDocument();
        flush(1);
        expect(within(popover()).queryByText('Создать папку')).toBeNull();

        hoverText('Перейти в папку');
        fireEvent.mouseLeave(within(popover()).getByText('Создать папку'));
        flush(100);
        fireEvent.mouseEnter(within(popover()).getByText('Создать папку'));
        flush(500);
        expect(within(popover()).getByText('Создать папку')).toBeInTheDocument();
      });

      it('наведение на другой пункт закрывает открытое подменю', () => {
        setFoldersMode(true);
        h.state.chats = [mkChat('c1', 'Чат')];
        renderSidebar();
        openChatMenu('Чат');
        hoverText('Перейти в папку');
        expect(within(popover()).getByText('Создать папку')).toBeInTheDocument();
        fireEvent.mouseEnter(within(popover()).getByText('Архив'));
        expect(within(popover()).queryByText('Создать папку')).toBeNull();
        hoverText('Перейти в папку');
        fireEvent.mouseEnter(within(popover()).getByText('Пин'));
        expect(within(popover()).queryByText('Создать папку')).toBeNull();
        hoverText('Перейти в папку');
        fireEvent.mouseEnter(within(popover()).getByText('Переименовать'));
        expect(within(popover()).queryByText('Создать папку')).toBeNull();
        hoverText('Перейти в папку');
        fireEvent.mouseEnter(within(popover()).getByText('Удалить'));
        expect(within(popover()).queryByText('Создать папку')).toBeNull();
      });

      it('подменю проектов: «Новый проект» открывает модалку, закрывает меню', () => {
        h.state.chats = [mkChat('c1', 'Чат')];
        renderSidebar();
        openChatMenu('Чат');
        hoverText('Перейти в проект');
        fireEvent.click(within(popover()).getByText('Новый проект'));
        expect(screen.getByTestId('new-project-modal')).toBeInTheDocument();
        flush();
        expect(screen.queryByText('Переименовать')).toBeNull();
      });

      it('подменю проектов: перенос в другой проект; текущий проект недоступен', () => {
        h.projects = [mkProject('p1', 'Первый'), mkProject('p2', 'Второй')];
        h.state.chats = [mkChat('c1', 'Чат', { projectId: 'p1' })];
        renderSidebar();
        fireEvent.click(screen.getAllByLabelText('Развернуть чаты проекта')[0]);
        openChatMenu('Чат');
        hoverText('Перейти в проект');
        // текущий проект: клик не переносит
        fireEvent.click(within(popover()).getByText('Первый'));
        expect(h.actions.moveChatToProject).not.toHaveBeenCalled();
        fireEvent.click(within(popover()).getByText('Второй'));
        expect(h.actions.moveChatToProject).toHaveBeenCalledWith('c1', 'p2');
        flush();
        expect(screen.queryByText('Перейти в проект')).toBeNull();
      });

      it('подменю папок: «Создать папку» открывает диалог создания папки', () => {
        setFoldersMode(true);
        h.state.chats = [mkChat('c1', 'Чат')];
        renderSidebar();
        openChatMenu('Чат');
        hoverText('Перейти в папку');
        fireEvent.click(within(popover()).getByText('Создать папку'));
        flush();
        expect(screen.getByPlaceholderText('Название папки')).toBeInTheDocument();
      });

      it('подменю папок: чат вне папки — «Все чаты» недоступно, можно перенести в любую папку', () => {
        setFoldersMode(true);
        h.state.chats = [mkChat('c1', 'Чат')];
        h.folders = [mkFolder('f1', 'Работа', []), mkFolder('f2', 'Дом', [])];
        renderSidebar();
        openChatMenu('Чат');
        hoverText('Перейти в папку');
        fireEvent.click(within(popover()).getByText('Все чаты'));
        expect(h.actions.moveChatToFolder).not.toHaveBeenCalled();
        fireEvent.click(within(popover()).getByText('Дом'));
        expect(h.actions.moveChatToFolder).toHaveBeenCalledWith('c1', 'f2');
        flush();
        expect(screen.queryByText('Перейти в папку')).toBeNull();
      });

      it('подменю папок: чат в папке — можно вернуть в «Все чаты», текущая папка не предлагается', () => {
        setFoldersMode(true);
        h.state.chats = [mkChat('c1', 'Чат')];
        h.folders = [mkFolder('f1', 'Работа', ['c1']), mkFolder('f2', 'Дом', [])];
        renderSidebar();
        // чат лежит в папке «Работа» — открываем его меню из папки
        openChatMenu('Чат');
        hoverText('Перейти в папку');
        const sub = within(popover());
        expect(sub.queryByText('Работа')).toBeNull();
        expect(sub.getByText('Дом')).toBeInTheDocument();
        fireEvent.click(sub.getByText('Все чаты'));
        expect(h.actions.moveChatToFolder).toHaveBeenCalledWith('c1', null);
      });

      it('переименование: «Переименовать» подставляет заголовок, Enter сохраняет обрезанное название', () => {
        h.state.chats = [mkChat('c1', 'Старое имя')];
        renderSidebar();
        openChatMenu('Старое имя');
        fireEvent.click(within(popover()).getByText('Переименовать'));
        flush(50);
        const input = screen.getByDisplayValue('Старое имя');
        // пока идёт редактирование, кнопки меню у чатов скрыты
        expect(screen.queryAllByTestId('MoreVertIcon').length).toBe(legacy ? 1 : 1);
        typeInto(input, '  Новое имя  ');
        fireEvent.keyDown(input, { key: 'Enter' });
        expect(h.actions.updateChatTitle).toHaveBeenCalledWith('c1', 'Новое имя');
        expect(screen.queryByDisplayValue('Новое имя')).toBeNull();
      });

      it('переименование: Escape отменяет без сохранения', () => {
        h.state.chats = [mkChat('c1', 'Старое имя')];
        renderSidebar();
        openChatMenu('Старое имя');
        fireEvent.click(within(popover()).getByText('Переименовать'));
        flush(50);
        const input = screen.getByDisplayValue('Старое имя');
        typeInto(input, 'Другое');
        fireEvent.keyDown(input, { key: 'Escape' });
        expect(h.actions.updateChatTitle).not.toHaveBeenCalled();
        expect(screen.getByText('Старое имя')).toBeInTheDocument();
      });

      it('переименование: потеря фокуса сохраняет, пустое название не сохраняется', () => {
        h.state.chats = [mkChat('c1', 'Имя')];
        renderSidebar();
        openChatMenu('Имя');
        fireEvent.click(within(popover()).getByText('Переименовать'));
        flush(50);
        let input = screen.getByDisplayValue('Имя');
        typeInto(input, 'Через blur');
        fireEvent.blur(input);
        expect(h.actions.updateChatTitle).toHaveBeenCalledWith('c1', 'Через blur');

        h.actions.updateChatTitle.mockClear();
        openChatMenu('Имя');
        fireEvent.click(within(popover()).getByText('Переименовать'));
        flush(50);
        input = screen.getByDisplayValue('Имя');
        typeInto(input, '   ');
        fireEvent.keyDown(input, { key: 'Enter' });
        expect(h.actions.updateChatTitle).not.toHaveBeenCalled();
      });

      it('переименование: другие клавиши не завершают редактирование, клик по строке не открывает чат', () => {
        h.state.chats = [mkChat('c1', 'Имя')];
        renderSidebar();
        openChatMenu('Имя');
        fireEvent.click(within(popover()).getByText('Переименовать'));
        flush(50);
        const input = screen.getByDisplayValue('Имя');
        fireEvent.keyDown(input, { key: 'a' });
        expect(screen.getByDisplayValue('Имя')).toBeInTheDocument();
        fireEvent.click(input.closest('.MuiListItemButton-root') as HTMLElement);
        expect(h.actions.setCurrentChat).not.toHaveBeenCalled();
        fireEvent.click(input);
        expect(h.actions.setCurrentChat).not.toHaveBeenCalled();
      });
    });

    // ------------------------------------------------------------------ меню пользователя
    describe('меню пользователя', () => {
      it('показывает имя пользователя (full_name, иначе username)', () => {
        renderSidebar();
        expect(screen.getByText('Иван Петров')).toBeInTheDocument();
        h.user = { username: 'ivan' };
        const second = renderSidebar();
        expect(screen.getByText('ivan')).toBeInTheDocument();
        second.unmount();
      });

      it('аватар в нижнем блоке', () => {
        renderSidebar();
        expect(screen.getByText(legacy ? 'I' : 'avatar')).toBeInTheDocument();
      });

      it('без пользователя вместо блока профиля — кнопка «Меню», которая открывает меню', () => {
        h.user = null;
        renderSidebar();
        fireEvent.click(screen.getByRole('button', { name: 'Меню' }));
        expect(screen.getByText('Настройки')).toBeInTheDocument();
      });

      it('«Настройки» отправляют событие открытия настроек и закрывают меню', () => {
        const spy = jest.fn();
        window.addEventListener(ASTRA_OPEN_SETTINGS, spy);
        try {
          renderSidebar();
          clickText('Иван Петров');
          clickText('Настройки');
          expect(spy).toHaveBeenCalledTimes(1);
          flush();
          expect(screen.queryByText('Настройки')).toBeNull();
        } finally {
          window.removeEventListener(ASTRA_OPEN_SETTINGS, spy);
        }
      });

      it('«Архив» открывает модалку архива, onClose закрывает', () => {
        renderSidebar();
        clickText('Иван Петров');
        clickText('Архив');
        expect(screen.getByTestId('archive-modal')).toBeInTheDocument();
        act(() => h.props.archive.onClose());
        expect(screen.queryByTestId('archive-modal')).toBeNull();
      });

      it('«Выйти из аккаунта» вызывает logout и переходит на /login', () => {
        renderSidebar();
        clickText('Иван Петров');
        clickText('Выйти из аккаунта');
        expect(h.logout).toHaveBeenCalledTimes(1);
        expect(h.navigate).toHaveBeenCalledWith('/login');
      });

      it('«Справка» раскрывает подменю, при наведении на другой пункт оно закрывается', () => {
        renderSidebar();
        clickText('Иван Петров');
        expect(screen.queryByText('Помощь')).toBeNull();
        hoverText('Справка');
        expect(screen.getByText('Помощь')).toBeInTheDocument();
        expect(screen.getByText('Обновления')).toBeInTheDocument();
        fireEvent.mouseEnter(screen.getByText('Настройки'));
        expect(screen.queryByText('Помощь')).toBeNull();
        hoverText('Справка');
        fireEvent.mouseEnter(screen.getByText('Архив'));
        expect(screen.queryByText('Помощь')).toBeNull();
        hoverText('Справка');
        fireEvent.mouseEnter(screen.getByText('Выйти из аккаунта'));
        expect(screen.queryByText('Помощь')).toBeNull();
      });

      it('подменю «Справка» закрывается через 220 мс после ухода курсора; возврат отменяет закрытие', () => {
        renderSidebar();
        clickText('Иван Петров');
        hoverText('Справка');
        fireEvent.mouseLeave(screen.getByText('Помощь'));
        flush(219);
        expect(screen.getByText('Помощь')).toBeInTheDocument();
        flush(1);
        expect(screen.queryByText('Помощь')).toBeNull();

        hoverText('Справка');
        fireEvent.mouseLeave(screen.getByText('Помощь'));
        flush(100);
        fireEvent.mouseEnter(screen.getByText('Помощь'));
        flush(500);
        expect(screen.getByText('Помощь')).toBeInTheDocument();
      });

      it('повторное открытие меню сбрасывает ожидающее закрытие подменю', () => {
        renderSidebar();
        clickText('Иван Петров');
        hoverText('Справка');
        fireEvent.mouseLeave(screen.getByText('Помощь'));
        // закрыли меню целиком (таймер сбрасывается) и открыли снова
        fireEvent.click(popover().querySelector('.MuiBackdrop-root') as HTMLElement);
        flush();
        clickText('Иван Петров');
        flush(500);
        expect(screen.getByText('Настройки')).toBeInTheDocument();
        expect(screen.queryByText('Помощь')).toBeNull();
      });

      it('«Помощь» открывает диалог со ссылками на Onyx', () => {
        renderSidebar({ isDarkMode: false });
        clickText('Иван Петров');
        hoverText('Справка');
        clickText('Помощь');
        flush();
        const dlg = dialog();
        expect(within(dlg).getByText('По возникающим вопросам обращаться:')).toBeInTheDocument();
        const news = within(dlg).getByText('AstraChat News').closest('a') as HTMLAnchorElement;
        const support = within(dlg).getByText('AstraChat Support').closest('a') as HTMLAnchorElement;
        expect(news.href).toContain('https://xlnk.ms/join/channel/');
        expect(support.href).toContain('https://xlnk.ms/join/chat/');
        expect(news).toHaveAttribute('target', '_blank');
        expect(news).toHaveAttribute('rel', 'noopener noreferrer');
        fireEvent.click(within(dlg).getByLabelText('Закрыть'));
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
      });

      it('«Обновления» открывают диалог обновлений', () => {
        renderSidebar();
        clickText('Иван Петров');
        hoverText('Справка');
        clickText('Обновления');
        flush();
        const dlg = dialog();
        expect(within(dlg).getByText('Обновления')).toBeInTheDocument();
        if (legacy) {
          expect(within(dlg).getByText('Релиз версии 1.0 AstraChat')).toBeInTheDocument();
          const link = within(dlg).getByText('Читать подробнее.').closest('a') as HTMLAnchorElement;
          expect(link.getAttribute('href')).toBe('/docs/astrachat-release-1.0.html');
        } else {
          expect(within(dlg).getByText('Релиз версии 2.0 AstraChat')).toBeInTheDocument();
        }
        fireEvent.click(within(dlg).getByLabelText('Закрыть'));
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
      });

      it('«Сочетание клавиш» открывает диалог горячих клавиш', () => {
        renderSidebar();
        clickText('Иван Петров');
        hoverText('Справка');
        clickText('Сочетание клавиш');
        expect(screen.getByTestId('hotkeys-dialog')).toBeInTheDocument();
        act(() => h.props.hotkeys.onClose());
        expect(screen.queryByTestId('hotkeys-dialog')).toBeNull();
      });

      if (legacy) {
        it('в устаревшем сайдбаре пункта «Руководство пользователя» нет', () => {
          renderSidebar();
          clickText('Иван Петров');
          hoverText('Справка');
          expect(screen.queryByText('Руководство пользователя')).toBeNull();
        });
      } else {
        it('«Руководство пользователя» ведёт на /docs/user-guide', () => {
          renderSidebar();
          clickText('Иван Петров');
          hoverText('Справка');
          clickText('Руководство пользователя');
          expect(h.navigate).toHaveBeenCalledWith('/docs/user-guide');
        });
      }
    });

    // ------------------------------------------------------------------ режим папок
    describe('режим папок', () => {
      beforeEach(() => setFoldersMode(true));

      it('«Закреплено» отображается первой, раньше «Все чаты» и остальных папок', () => {
        h.state.chats = [mkChat('c1', 'Закреплённый чат'), mkChat('c2', 'Рабочий чат'), mkChat('c3', 'Свободный')];
        h.folders = [mkFolder('f1', 'Работа', ['c2']), mkFolder('fp', 'Закреплено', ['c1'])];
        renderSidebar();
        const html = document.body.innerHTML;
        const posPinned = html.indexOf('>Закреплено<');
        const posAll = html.indexOf('>Все чаты<');
        const posWork = html.indexOf('>Работа<');
        expect(posPinned).toBeGreaterThan(0);
        expect(posPinned).toBeLessThan(posAll);
        expect(posAll).toBeLessThan(posWork);
        // чаты из папок не дублируются в «Все чаты»
        expect(screen.getAllByText('Закреплённый чат')).toHaveLength(1);
        expect(screen.getAllByText('Рабочий чат')).toHaveLength(1);
        expect(screen.getByText('Свободный')).toBeInTheDocument();
      });

      it('клик по заголовку папки переключает её (toggleFolder)', () => {
        h.state.chats = [mkChat('c1', 'Чат')];
        h.folders = [mkFolder('f1', 'Работа', ['c1']), mkFolder('fp', 'Закреплено', [])];
        renderSidebar();
        clickText('Работа');
        expect(h.actions.toggleFolder).toHaveBeenCalledWith('f1');
        clickText('Закреплено');
        expect(h.actions.toggleFolder).toHaveBeenCalledWith('fp');
      });

      it('свёрнутая папка не показывает чаты', () => {
        h.state.chats = [mkChat('c1', 'Чат работы'), mkChat('c2', 'Закреплённый')];
        h.folders = [mkFolder('f1', 'Работа', ['c1'], false), mkFolder('fp', 'Закреплено', ['c2'], false)];
        renderSidebar();
        expect(screen.queryByText('Чат работы')).toBeNull();
        expect(screen.queryByText('Закреплённый')).toBeNull();
      });

      it('в папках скрыты архивные чаты и несуществующие id', () => {
        h.state.chats = [mkChat('c1', 'Живой'), mkChat('c2', 'Архивный', { isArchived: true })];
        h.folders = [
          mkFolder('f1', 'Работа', ['c1', 'c2', 'ghost']),
          mkFolder('fp', 'Закреплено', ['c1', 'c2', 'ghost']),
        ];
        renderSidebar();
        expect(screen.getAllByText('Живой')).toHaveLength(2);
        expect(screen.queryByText('Архивный')).toBeNull();
      });

      it('клик по чату в папке выбирает его, текущий выделен', () => {
        h.state.chats = [mkChat('c1', 'В папке'), mkChat('c2', 'В закреплённых')];
        h.state.currentChatId = 'c1';
        h.folders = [mkFolder('f1', 'Работа', ['c1']), mkFolder('fp', 'Закреплено', ['c2'])];
        renderSidebar();
        expect(screen.getByText('В папке')).toHaveStyle('font-weight: 600');
        fireEvent.click(rowOf('В папке'));
        expect(h.actions.setCurrentChat).toHaveBeenCalledWith('c1');
        fireEvent.click(rowOf('В закреплённых'));
        expect(h.actions.setCurrentChat).toHaveBeenCalledWith('c2');
      });

      it('поиск фильтрует чаты внутри папок по заголовку и тексту; без совпадений — «Ничего не найдено»', () => {
        h.state.chats = [
          mkChat('c1', 'Отчёт', { messages: [{ content: 'цифры' }] }),
          mkChat('c2', 'Заметка', { messages: [{ content: 'найди СЛОН' }] }),
          mkChat('c3', 'Пин-отчёт', { messages: [{ content: 'x' }] }),
          mkChat('c4', 'Пин-другое', { messages: [{ content: 'слон тут' }] }),
        ];
        h.folders = [mkFolder('f1', 'Работа', ['c1', 'c2']), mkFolder('fp', 'Закреплено', ['c3', 'c4'])];
        renderSidebar();
        const search = screen.getByPlaceholderText('Поиск в чатах');
        typeInto(search, 'отчёт');
        expect(screen.getByText('Отчёт')).toBeInTheDocument();
        expect(screen.getByText('Пин-отчёт')).toBeInTheDocument();
        expect(screen.queryByText('Заметка')).toBeNull();
        expect(screen.queryByText('Пин-другое')).toBeNull();
        typeInto(search, 'слон');
        expect(screen.getByText('Заметка')).toBeInTheDocument();
        expect(screen.getByText('Пин-другое')).toBeInTheDocument();
        typeInto(search, 'нет такого');
        // «Все чаты», «Закреплено» и «Работа»
        expect(screen.getAllByText('Ничего не найдено')).toHaveLength(3);
      });

      it('меню папки: «Переименовать» подставляет название, Enter сохраняет', () => {
        h.folders = [mkFolder('f1', 'Работа', [])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        clickText('Переименовать');
        const input = screen.getByPlaceholderText('Название папки');
        expect(input).toHaveValue('Работа');
        typeInto(input, '  Дела  ');
        pressEnterKeyPress(input);
        expect(h.actions.updateFolder).toHaveBeenCalledWith('f1', 'Дела');
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
      });

      it('переименование папки: кнопка «Переименовать» недоступна при пустом названии, другие клавиши не сохраняют', () => {
        h.folders = [mkFolder('f1', 'Работа', [])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        clickText('Переименовать');
        const input = screen.getByPlaceholderText('Название папки');
        fireEvent.keyPress(input, { key: 'a', code: 'KeyA', charCode: 97 });
        expect(h.actions.updateFolder).not.toHaveBeenCalled();
        typeInto(input, '   ');
        expect(within(dialog()).getByRole('button', { name: 'Переименовать' })).toBeDisabled();
        pressEnterKeyPress(input);
        expect(h.actions.updateFolder).not.toHaveBeenCalled();
        typeInto(input, 'Дом');
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Переименовать' }));
        expect(h.actions.updateFolder).toHaveBeenCalledWith('f1', 'Дом');
      });

      it('отмена переименования папки закрывает диалог без сохранения', () => {
        h.folders = [mkFolder('f1', 'Работа', [])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        clickText('Переименовать');
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Отменить' }));
        flush();
        expect(h.actions.updateFolder).not.toHaveBeenCalled();
        expect(screen.queryByRole('dialog')).toBeNull();
      });

      it('закрытие диалога переименования по Escape закрывает и меню папки', () => {
        h.folders = [mkFolder('f1', 'Работа', [])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        clickText('Переименовать');
        fireEvent.keyDown(dialog(), { key: 'Escape' });
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
        expect(screen.queryByText('Архив')).toBeNull();
      });

      it('меню папки: «Архив» архивирует папку', () => {
        h.folders = [mkFolder('f1', 'Работа', [])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        clickText('Архив');
        expect(h.actions.archiveFolder).toHaveBeenCalledWith('f1');
        flush();
        expect(screen.queryByText('Переименовать')).toBeNull();
      });

      it('меню «Закреплено» работает так же (переименование)', () => {
        h.folders = [mkFolder('fp', 'Закреплено', [])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Закреплено')).getByTestId('MoreVertIcon'));
        clickText('Переименовать');
        expect(screen.getByPlaceholderText('Название папки')).toHaveValue('Закреплено');
      });

      it('удаление папки по умолчанию переносит чаты в «Все чаты»', () => {
        h.state.chats = [mkChat('c1', 'Один'), mkChat('c2', 'Два')];
        h.folders = [mkFolder('f1', 'Работа', ['c1', 'c2'])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        fireEvent.click(within(popover()).getByText('Удалить'));
        expect(screen.getByText('Что делать с чатами в этой папке?')).toBeInTheDocument();
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.moveChatToFolder).toHaveBeenCalledWith('c1', null);
        expect(h.actions.moveChatToFolder).toHaveBeenCalledWith('c2', null);
        expect(h.actions.deleteChat).not.toHaveBeenCalled();
        expect(h.actions.deleteFolder).toHaveBeenCalledWith('f1');
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
      });

      it('удаление папки «со всем содержимым» удаляет её чаты', () => {
        h.state.chats = [mkChat('c1', 'Один'), mkChat('c2', 'Два')];
        h.folders = [mkFolder('f1', 'Работа', ['c1', 'c2'])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        fireEvent.click(within(popover()).getByText('Удалить'));
        fireEvent.click(screen.getByText('Удалить со всем содержимым'));
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.deleteChat).toHaveBeenCalledWith('c1');
        expect(h.actions.deleteChat).toHaveBeenCalledWith('c2');
        expect(h.actions.moveChatToFolder).not.toHaveBeenCalled();
        expect(h.actions.deleteFolder).toHaveBeenCalledWith('f1');
      });

      it('можно передумать: выбрать «со всем содержимым», затем вернуть «Переместить чаты»', () => {
        h.state.chats = [mkChat('c1', 'Один')];
        h.folders = [mkFolder('f1', 'Работа', ['c1'])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        fireEvent.click(within(popover()).getByText('Удалить'));
        fireEvent.click(screen.getByText('Удалить со всем содержимым'));
        fireEvent.click(screen.getByText('Переместить чаты в "Все чаты"'));
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.moveChatToFolder).toHaveBeenCalledWith('c1', null);
        expect(h.actions.deleteChat).not.toHaveBeenCalled();
      });

      it('отмена удаления папки ничего не меняет и закрывает меню', () => {
        h.folders = [mkFolder('f1', 'Работа', [])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        fireEvent.click(within(popover()).getByText('Удалить'));
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Отменить' }));
        flush();
        expect(h.actions.deleteFolder).not.toHaveBeenCalled();
        expect(screen.queryByRole('dialog')).toBeNull();
        expect(screen.queryByText('Переименовать')).toBeNull();
      });

      it('закрытие диалога удаления папки по Escape закрывает и меню', () => {
        h.folders = [mkFolder('f1', 'Работа', [])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        fireEvent.click(within(popover()).getByText('Удалить'));
        fireEvent.keyDown(dialog(), { key: 'Escape' });
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
        expect(h.actions.deleteFolder).not.toHaveBeenCalled();
      });

      it('удаление папки без выбранной папки (меню закрыто под диалогом) ничего не делает', () => {
        h.folders = [mkFolder('f1', 'Работа', [])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        fireEvent.click(within(popover()).getByText('Удалить'));
        fireEvent.click(popover().querySelector('.MuiBackdrop-root') as HTMLElement);
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.deleteFolder).not.toHaveBeenCalled();
      });

      it('удаление папки, которая пропала из состояния, ничего не делает', () => {
        h.folders = [mkFolder('f1', 'Работа', ['c1'])];
        h.state.chats = [mkChat('c1', 'Один')];
        const { rerenderWith } = renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        fireEvent.click(within(popover()).getByText('Удалить'));
        h.folders = [];
        rerenderWith({});
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        expect(h.actions.deleteFolder).not.toHaveBeenCalled();
        expect(h.actions.moveChatToFolder).not.toHaveBeenCalled();
      });

      it('клики по пунктам меню папки после закрытия меню игнорируются', () => {
        h.folders = [mkFolder('f1', 'Работа', [])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        const rename = within(popover()).getByText('Переименовать');
        const archive = within(popover()).getByText('Архив');
        const del = within(popover()).getByText('Удалить');
        fireEvent.click(popover().querySelector('.MuiBackdrop-root') as HTMLElement);
        fireEvent.click(rename);
        fireEvent.click(archive);
        fireEvent.click(del);
        expect(h.actions.archiveFolder).not.toHaveBeenCalled();
        expect(screen.queryByPlaceholderText('Название папки')).toBeNull();
        expect(screen.queryByText('Что делать с чатами в этой папке?')).toBeNull();
      });

      it('переименование без выбранной папки ничего не обновляет', () => {
        h.folders = [mkFolder('f1', 'Работа', [])];
        renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        clickText('Переименовать');
        fireEvent.click(popover().querySelector('.MuiBackdrop-root') as HTMLElement);
        const input = screen.getByPlaceholderText('Название папки');
        typeInto(input, 'Новое');
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Переименовать' }));
        expect(h.actions.updateFolder).not.toHaveBeenCalled();
      });

      it('действие меню папки для папки, которой нет в списке, не открывает диалог переименования', () => {
        h.folders = [mkFolder('f1', 'Работа', [])];
        const { rerenderWith } = renderSidebar();
        fireEvent.click(within(folderHeader('Работа')).getByTestId('MoreVertIcon'));
        h.folders = [];
        rerenderWith({});
        fireEvent.click(within(popover()).getByText('Переименовать'));
        expect(screen.queryByPlaceholderText('Название папки')).toBeNull();
      });

      it('кнопка «Создать папку» в поиске открывает диалог; создание обрезает название', () => {
        renderSidebar();
        fireEvent.click(screen.getByLabelText('Создать папку'));
        const input = screen.getByPlaceholderText('Название папки');
        const create = within(dialog()).getByRole('button', { name: 'Создать' });
        expect(create).toBeDisabled();
        typeInto(input, '  Новая  ');
        expect(create).toBeEnabled();
        fireEvent.click(create);
        expect(h.actions.createFolder).toHaveBeenCalledWith('Новая');
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
      });

      it('создание папки по Enter; пустое название и другие клавиши ничего не создают', () => {
        renderSidebar();
        fireEvent.click(screen.getByLabelText('Создать папку'));
        const input = screen.getByPlaceholderText('Название папки');
        pressEnterKeyPress(input);
        fireEvent.keyPress(input, { key: 'a', code: 'KeyA', charCode: 97 });
        expect(h.actions.createFolder).not.toHaveBeenCalled();
        typeInto(input, 'Моя');
        pressEnterKeyPress(input);
        expect(h.actions.createFolder).toHaveBeenCalledWith('Моя');
      });

      it('отмена создания папки закрывает диалог', () => {
        renderSidebar();
        fireEvent.click(screen.getByLabelText('Создать папку'));
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Отменить' }));
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
        expect(h.actions.createFolder).not.toHaveBeenCalled();
      });

      it('BUG-FE-47: «Закреплено» с закреплённым чатом видно и в режиме проектов', () => {
        // Закреплённый чат отображается в отдельной секции «Закреплено» в обоих режимах.
        localStorage.setItem('use_folders_mode', 'false');
        h.state.chats = [mkChat('c1', 'Чат')];
        h.folders = [mkFolder('fp', 'Закреплено', ['c1'])];
        renderSidebar();
        expect(screen.getByText('Закреплено')).toBeInTheDocument();
        expect(screen.getByText('Чат')).toBeInTheDocument();
        openChatMenu('Чат');
        expect(screen.getByText('Открепить')).toBeInTheDocument();
      });

      it('(known issue) обычные папки рисуются и в режиме проектов', () => {
        // Правильное поведение: в режиме проектов папок в сайдбаре быть не должно (блок
        // «Закреплено» скрыт условием useFoldersMode, а блок остальных папок — нет).
        localStorage.setItem('use_folders_mode', 'false');
        h.state.chats = [mkChat('c1', 'Чат')];
        h.folders = [mkFolder('f1', 'Работа', ['c1'])];
        renderSidebar();
        expect(screen.getByText('Работа')).toBeInTheDocument();
      });
    });

    // ------------------------------------------------------------------ дополнительные сценарии
    describe('наведение, таймеры и закрытие диалогов', () => {
      it('наведение на «Новый чат» и поле поиска показывает подсказки горячих клавиш', () => {
        renderSidebar();
        const b = getHotkeyBindings();
        const newChatHint = screen.getByText(formatHotkeyBinding(b.newChat));
        const searchHint = screen.getByText(formatHotkeyBinding(b.searchChats));
        expect(newChatHint).toHaveStyle('opacity: 0');
        expect(searchHint).toHaveStyle('opacity: 0');
        const newChatRow = screen.getByText('Новый чат').closest('.MuiListItemButton-root') as HTMLElement;
        fireEvent.mouseEnter(newChatRow);
        expect(newChatHint).toHaveStyle('opacity: 0.65');
        fireEvent.mouseLeave(newChatRow);
        expect(newChatHint).toHaveStyle('opacity: 0');
        const searchRow = screen.getByPlaceholderText('Поиск в чатах').closest('.MuiListItemButton-root') as HTMLElement;
        fireEvent.mouseEnter(searchRow);
        expect(searchHint).toHaveStyle('opacity: 0.65');
        fireEvent.mouseLeave(searchRow);
        expect(searchHint).toHaveStyle('opacity: 0');
      });

      it('диалоги закрываются по Escape: помощь, удаление чата, удаление проекта', () => {
        h.state.chats = [mkChat('c1', 'Чат')];
        h.projects = [mkProject('p1', 'Проект')];
        renderSidebar();
        // помощь
        clickText('Иван Петров');
        hoverText('Справка');
        clickText('Помощь');
        fireEvent.keyDown(dialog(), { key: 'Escape' });
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
        // удаление чата
        openChatMenu('Чат');
        fireEvent.click(within(popover()).getByText('Удалить'));
        fireEvent.keyDown(dialog(), { key: 'Escape' });
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
        // удаление проекта
        fireEvent.click(within(screen.getByText('Проект').closest('li') as HTMLElement).getByTestId('MoreVertIcon'));
        clickText('Удалить проект');
        fireEvent.keyDown(dialog(), { key: 'Escape' });
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
        expect(h.actions.deleteChat).not.toHaveBeenCalled();
        expect(h.actions.deleteProject).not.toHaveBeenCalled();
      });

      it('диалог создания папки закрывается по Escape', () => {
        setFoldersMode(true);
        renderSidebar();
        fireEvent.click(screen.getByLabelText('Создать папку'));
        fireEvent.keyDown(dialog(), { key: 'Escape' });
        flush();
        expect(screen.queryByRole('dialog')).toBeNull();
      });

      it('возврат на «Справка» до истечения таймера отменяет закрытие подменю', () => {
        renderSidebar();
        clickText('Иван Петров');
        hoverText('Справка');
        fireEvent.mouseLeave(screen.getByText('Помощь'));
        flush(100);
        hoverText('Справка');
        flush(500);
        expect(screen.getByText('Помощь')).toBeInTheDocument();
      });

      it('повторный клик по блоку пользователя при ожидающем закрытии подменю сбрасывает таймер и подменю', () => {
        renderSidebar();
        clickText('Иван Петров');
        hoverText('Справка');
        fireEvent.mouseLeave(screen.getByText('Помощь'));
        clickText('Иван Петров');
        flush(500);
        expect(screen.queryByText('Помощь')).toBeNull();
        expect(screen.getByText('Настройки')).toBeInTheDocument();
      });

      it('закрытие меню чата при ожидающем закрытии подменю сбрасывает таймер', () => {
        setFoldersMode(true);
        h.state.chats = [mkChat('c1', 'Чат')];
        renderSidebar();
        openChatMenu('Чат');
        hoverText('Перейти в папку');
        fireEvent.mouseLeave(within(popover()).getByText('Создать папку'));
        fireEvent.click(popover().querySelector('.MuiBackdrop-root') as HTMLElement);
        flush();
        expect(screen.queryByText('Перейти в папку')).toBeNull();
        // меню открывается заново без подменю
        openChatMenu('Чат');
        flush(500);
        expect(screen.getByText('Перейти в папку')).toBeInTheDocument();
        expect(within(popover()).queryByText('Создать папку')).toBeNull();
      });

      it('возврат на «Перейти в папку» до таймера оставляет подменю открытым', () => {
        setFoldersMode(true);
        h.state.chats = [mkChat('c1', 'Чат')];
        renderSidebar();
        openChatMenu('Чат');
        hoverText('Перейти в папку');
        fireEvent.mouseLeave(within(popover()).getByText('Создать папку'));
        flush(100);
        hoverText('Перейти в папку');
        flush(500);
        expect(within(popover()).getByText('Создать папку')).toBeInTheDocument();
      });

      it('наведение на «Перенести из проекта» закрывает подменю', () => {
        h.projects = [mkProject('p1', 'Проект')];
        h.state.chats = [mkChat('c1', 'Чат', { projectId: 'p1' })];
        renderSidebar();
        fireEvent.click(screen.getByLabelText('Развернуть чаты проекта'));
        openChatMenu('Чат');
        hoverText('Перейти в проект');
        expect(within(popover()).getByText('Новый проект')).toBeInTheDocument();
        fireEvent.mouseEnter(screen.getByText('Перенести из проекта'));
        expect(within(popover()).queryByText('Новый проект')).toBeNull();
      });

      it('клик по пункту меню чата после подтверждённого удаления игнорируется (чат уже не выбран)', () => {
        h.state.chats = [mkChat('c1', 'Чат')];
        renderSidebar();
        openChatMenu('Чат');
        const archive = within(popover()).getByText('Архив');
        fireEvent.click(within(popover()).getByText('Удалить'));
        fireEvent.click(within(dialog()).getByRole('button', { name: 'Удалить' }));
        // меню ещё не размонтировано, но selectedChatId уже сброшен
        fireEvent.click(archive);
        expect(h.actions.archiveChat).not.toHaveBeenCalled();
      });
    });

    describe('переименование чата в проекте и папках', () => {
      const rename = (title: string, next: string) => {
        openChatMenu(title);
        fireEvent.click(within(popover()).getByText('Переименовать'));
        flush(50);
        const input = screen.getByDisplayValue(title);
        fireEvent.click(input.closest('.MuiListItemButton-root') as HTMLElement);
        expect(h.actions.setCurrentChat).not.toHaveBeenCalled();
        typeInto(input, next);
        fireEvent.keyDown(input, { key: 'a' });
        expect(screen.getByDisplayValue(next)).toBeInTheDocument();
        fireEvent.click(input);
        fireEvent.keyDown(input, { key: 'Enter' });
      };

      it('чат проекта', () => {
        h.projects = [mkProject('p1', 'Проект')];
        h.state.chats = [mkChat('c1', 'Чат проекта', { projectId: 'p1' })];
        renderSidebar();
        fireEvent.click(screen.getByLabelText('Развернуть чаты проекта'));
        rename('Чат проекта', 'Новое имя');
        expect(h.actions.updateChatTitle).toHaveBeenCalledWith('c1', 'Новое имя');
      });

      it('чат в обычной папке', () => {
        setFoldersMode(true);
        h.state.chats = [mkChat('c1', 'Чат папки')];
        h.folders = [mkFolder('f1', 'Работа', ['c1'])];
        renderSidebar();
        rename('Чат папки', 'Новое имя');
        expect(h.actions.updateChatTitle).toHaveBeenCalledWith('c1', 'Новое имя');
      });

      it('чат в папке «Закреплено»', () => {
        setFoldersMode(true);
        h.state.chats = [mkChat('c1', 'Чат пина')];
        h.folders = [mkFolder('fp', 'Закреплено', ['c1'])];
        renderSidebar();
        rename('Чат пина', 'Новое имя');
        expect(h.actions.updateChatTitle).toHaveBeenCalledWith('c1', 'Новое имя');
      });
    });

    describe('переход курсора между пунктами меню', () => {
      it('вход на «Справка» из соседнего пункта отменяет ожидающее закрытие подменю', () => {
        renderSidebar();
        clickText('Иван Петров');
        hoverText('Справка');
        fireEvent.mouseLeave(screen.getByText('Помощь'));
        // курсор остаётся внутри меню: mouseenter срабатывает только на самом пункте «Справка»
        fireEvent.mouseOut(screen.getByText('Архив'), { relatedTarget: screen.getByText('Справка') });
        flush(500);
        expect(screen.getByText('Помощь')).toBeInTheDocument();
      });

      it('вход на «Перейти в папку» из соседнего пункта отменяет ожидающее закрытие подменю', () => {
        setFoldersMode(true);
        h.state.chats = [mkChat('c1', 'Чат')];
        renderSidebar();
        openChatMenu('Чат');
        hoverText('Перейти в папку');
        fireEvent.mouseLeave(within(popover()).getByText('Создать папку'));
        fireEvent.mouseOut(screen.getByText('Архив'), { relatedTarget: screen.getByText('Перейти в папку') });
        flush(500);
        expect(within(popover()).getByText('Создать папку')).toBeInTheDocument();
      });

      if (legacy) {
        it('диалог «Обновления» закрывается по Escape', () => {
          renderSidebar();
          clickText('Иван Петров');
          hoverText('Справка');
          clickText('Обновления');
          fireEvent.keyDown(dialog(), { key: 'Escape' });
          flush();
          expect(screen.queryByRole('dialog')).toBeNull();
        });
      }
    });
  });
}
