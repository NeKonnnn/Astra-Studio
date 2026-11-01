import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import {
  MoveToFolderAndProjectSubmenus,
  MoveToMenusHookResult,
  MoveToSubmenusProps,
} from '@/components/MoveToFolderAndProjectMenus';

/**
 * Основное «меню чата» (.MuiPopover-paper с якорем) рисуем средствами React: React не умеет
 * находить фибры у чужих DOM-узлов, и relatedTarget тогда приходит как window.
 */
function Harness({ build }: { build: (anchor: HTMLElement | null) => MoveToSubmenusProps }) {
  const [anchor, setAnchor] = React.useState<HTMLElement | null>(null);
  return (
    <>
      <div className="MuiPopover-paper">
        <div ref={setAnchor} data-testid="anchor" />
        <span data-testid="inside" />
      </div>
      <div data-testid="outside" />
      <MoveToFolderAndProjectSubmenus {...build(anchor)} />
    </>
  );
}

function makeMoveTo(over: Partial<MoveToMenusHookResult> = {}): MoveToMenusHookResult {
  return {
    closeSubmenus: jest.fn(),
    handleFolderSubmenuEnter: jest.fn(),
    handleFolderSubmenuLeave: jest.fn(),
    handleProjectSubmenuEnter: jest.fn(),
    handleProjectSubmenuLeave: jest.fn(),
    handleMoveToFolder: jest.fn(),
    handleRemoveFromFolder: jest.fn(),
    showMoveToFolderMenu: false,
    setShowMoveToFolderMenu: jest.fn(),
    showMoveToProjectMenu: false,
    setShowMoveToProjectMenu: jest.fn(),
    projectMenuAnchorForChat: null,
    setProjectMenuAnchorForChat: jest.fn(),
    folderMenuAnchorForChat: null,
    setFolderMenuAnchorForChat: jest.fn(),
    closeFolderTimerRef: { current: null },
    closeProjectTimerRef: { current: null },
    logRef: { current: jest.fn() },
    ...over,
  };
}

function makeProps(over: Partial<MoveToSubmenusProps> = {}): MoveToSubmenusProps {
  return {
    moveTo: makeMoveTo(),
    menuBg: '#111',
    menuBorder: '#222',
    menuItemColor: '#eee',
    menuItemHover: '#333',
    menuDisabledColor: '#777',
    folders: [],
    projects: [],
    selectedChatId: 'c1',
    getChatFolder: () => undefined,
    chats: [{ id: 'c1' }],
    isDarkMode: true,
    useFoldersMode: true,
    setShowCreateFolderDialog: jest.fn(),
    setPendingChatIdForProject: jest.fn(),
    setShowNewProjectModal: jest.fn(),
    handleChatMenuClose: jest.fn(),
    moveChatToProject: jest.fn(),
    ...over,
  };
}

const folder = (id: string, name: string) => ({ id, name, chatIds: [], expanded: true });
const project = (id: string, name: string, over: Record<string, unknown> = {}) => ({
  id,
  name,
  memory: 'default' as const,
  instructions: '',
  createdAt: '',
  updatedAt: '',
  ...over,
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('MoveToFolderAndProjectSubmenus: подменю проектов', () => {
  const openProjects = (over: Partial<MoveToSubmenusProps> = {}, moveOver: Partial<MoveToMenusHookResult> = {}) => {
    const moveTo = makeMoveTo(moveOver);
    const props = makeProps({ moveTo, ...over });
    render(
      <Harness
        build={(a) => ({
          ...props,
          moveTo: { ...moveTo, showMoveToProjectMenu: !!a, projectMenuAnchorForChat: a },
        })}
      />,
    );
    return { props, moveTo, inside: screen.getByTestId('inside'), outside: screen.getByTestId('outside') };
  };

  it('закрытые подменю ничего не рисуют', () => {
    render(<MoveToFolderAndProjectSubmenus {...makeProps()} />);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('«Новый проект» запоминает чат, открывает модалку и закрывает меню', () => {
    const { props, moveTo } = openProjects();
    fireEvent.click(screen.getByText('Новый проект'));
    expect(props.setPendingChatIdForProject).toHaveBeenCalledWith('c1');
    expect(props.setShowNewProjectModal).toHaveBeenCalledWith(true);
    expect(moveTo.setShowMoveToProjectMenu).toHaveBeenCalledWith(false);
    expect(moveTo.setProjectMenuAnchorForChat).toHaveBeenCalledWith(null);
    expect(props.handleChatMenuClose).toHaveBeenCalledTimes(1);
  });

  it('«Новый проект» без выбранного чата ничего не делает', () => {
    const { props } = openProjects({ selectedChatId: null });
    fireEvent.click(screen.getByText('Новый проект'));
    expect(props.setShowNewProjectModal).not.toHaveBeenCalled();
    expect(props.handleChatMenuClose).not.toHaveBeenCalled();
  });

  it('иконки проектов: emoji — текстом, известная — своей иконкой, неизвестная и без иконки — папкой', () => {
    openProjects({
      projects: [
        project('p1', 'Эмодзи', { iconType: 'emoji', icon: '🚀' }),
        project('p2', 'Деньги', { iconType: 'icon', icon: 'money', iconColor: '#00ff00' }),
        project('p3', 'Неизвестная', { iconType: 'icon', icon: 'nope' }),
        project('p4', 'Без иконки'),
      ],
    });
    const item = (n: string) => screen.getByText(n).closest('li') as HTMLElement;
    expect(within(item('Эмодзи')).getByText('🚀')).toBeInTheDocument();
    expect(within(item('Деньги')).getByTestId('AttachMoneyOutlinedIcon')).toBeInTheDocument();
    expect(within(item('Неизвестная')).getByTestId('FolderOutlinedIcon')).toBeInTheDocument();
    expect(within(item('Без иконки')).getByTestId('FolderOutlinedIcon')).toBeInTheDocument();
  });

  it('клик по проекту переносит выбранный чат и закрывает меню', () => {
    const { props, moveTo } = openProjects({
      projects: [project('p1', 'Первый'), project('p2', 'Второй')],
      chats: [{ id: 'c1', projectId: 'p1' }],
    });
    fireEvent.click(screen.getByText('Второй'));
    expect(props.moveChatToProject).toHaveBeenCalledWith('c1', 'p2');
    expect(moveTo.setShowMoveToProjectMenu).toHaveBeenCalledWith(false);
    expect(props.handleChatMenuClose).toHaveBeenCalledTimes(1);
  });

  it('текущий проект чата недоступен (aria-disabled)', () => {
    openProjects({
      projects: [project('p1', 'Первый'), project('p2', 'Второй')],
      chats: [{ id: 'c1', projectId: 'p1' }],
    });
    expect(screen.getByText('Первый').closest('li')).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('Второй').closest('li')).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('если обойти pointer-events и кликнуть по текущему проекту, чат снимается с проекта (null)', () => {
    const { props } = openProjects({
      projects: [project('p1', 'Первый')],
      chats: [{ id: 'c1', projectId: 'p1' }],
    });
    fireEvent.click(screen.getByText('Первый'));
    expect(props.moveChatToProject).toHaveBeenCalledWith('c1', null);
  });

  it('клик по проекту без выбранного чата игнорируется', () => {
    const { props } = openProjects({ projects: [project('p1', 'Первый')], selectedChatId: null });
    fireEvent.click(screen.getByText('Первый'));
    expect(props.moveChatToProject).not.toHaveBeenCalled();
  });

  it('закрытие меню (Escape) сбрасывает флаг и якорь', () => {
    const { moveTo } = openProjects();
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    expect(moveTo.setShowMoveToProjectMenu).toHaveBeenCalledWith(false);
    expect(moveTo.setProjectMenuAnchorForChat).toHaveBeenCalledWith(null);
  });

  it('вход курсора в панель отменяет таймер закрытия и пишет в лог', () => {
    const timer = setTimeout(() => {}, 5000);
    const { moveTo } = openProjects({}, { closeProjectTimerRef: { current: timer } });
    const paper = document.querySelector('[data-chat-submenu="project"]') as HTMLElement;
    fireEvent.mouseEnter(paper);
    expect(moveTo.closeProjectTimerRef.current).toBeNull();
    expect(moveTo.logRef.current).toHaveBeenCalledWith('paper project enter — отмена таймера закрытия');
  });

  it('вход курсора в панель без таймера ничего не логирует', () => {
    const { moveTo } = openProjects();
    fireEvent.mouseEnter(document.querySelector('[data-chat-submenu="project"]') as HTMLElement);
    expect(moveTo.logRef.current).not.toHaveBeenCalled();
  });

  it('уход курсора в основное меню не закрывает подменю', () => {
    const { moveTo, inside } = openProjects();
    const paper = document.querySelector('[data-chat-submenu="project"]') as HTMLElement;
    fireEvent.mouseLeave(paper, { relatedTarget: inside });
    expect(moveTo.setShowMoveToProjectMenu).not.toHaveBeenCalled();
    expect(moveTo.logRef.current).toHaveBeenCalledWith('paper project leave — уход в основное меню, не закрываем');
  });

  it('уход курсора в сторону закрывает подменю', () => {
    const { moveTo, outside } = openProjects();
    const paper = document.querySelector('[data-chat-submenu="project"]') as HTMLElement;
    fireEvent.mouseLeave(paper, { relatedTarget: outside });
    expect(moveTo.setShowMoveToProjectMenu).toHaveBeenCalledWith(false);
    expect(moveTo.setProjectMenuAnchorForChat).toHaveBeenCalledWith(null);
    expect(moveTo.logRef.current).toHaveBeenCalledWith('paper project leave — закрываем подменю', { to: 'DIV' });
  });

  it('(known issue) уход курсора за пределы окна бросает TypeError и не закрывает подменю', () => {
    // Правильное поведение: подменю закрывается. React отдаёт relatedTarget = window, если у цели
    // нет фибры (выход из окна, чужой DOM-узел), а mainPaper.contains(window) бросает TypeError.
    const { moveTo } = openProjects();
    const errors: unknown[] = [];
    const onError = (e: ErrorEvent) => {
      errors.push(e.error);
      e.preventDefault();
    };
    window.addEventListener('error', onError);
    const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
    fireEvent.mouseLeave(document.querySelector('[data-chat-submenu="project"]') as HTMLElement);
    spy.mockRestore();
    window.removeEventListener('error', onError);
    expect(errors).toHaveLength(1);
    expect((errors[0] as Error).name).toBe('TypeError');
    expect(moveTo.setShowMoveToProjectMenu).not.toHaveBeenCalled();
  });
});

describe('MoveToFolderAndProjectSubmenus: подменю папок', () => {
  const openFolders = (over: Partial<MoveToSubmenusProps> = {}, moveOver: Partial<MoveToMenusHookResult> = {}) => {
    const moveTo = makeMoveTo(moveOver);
    const props = makeProps({ moveTo, ...over });
    render(
      <Harness
        build={(a) => ({
          ...props,
          moveTo: { ...moveTo, showMoveToFolderMenu: !!a, folderMenuAnchorForChat: a },
        })}
      />,
    );
    return { props, moveTo, inside: screen.getByTestId('inside'), outside: screen.getByTestId('outside') };
  };

  it('«Создать папку» открывает диалог создания и закрывает меню', () => {
    const { props, moveTo } = openFolders();
    fireEvent.click(screen.getByText('Создать папку'));
    expect(props.setShowCreateFolderDialog).toHaveBeenCalledWith(true);
    expect(moveTo.setShowMoveToFolderMenu).toHaveBeenCalledWith(false);
    expect(moveTo.setFolderMenuAnchorForChat).toHaveBeenCalledWith(null);
    expect(props.handleChatMenuClose).toHaveBeenCalledTimes(1);
  });

  it('«Все чаты» недоступно, если чат не лежит в папке', () => {
    openFolders();
    expect(screen.getByText('Все чаты').closest('li')).toHaveAttribute('aria-disabled', 'true');
  });

  it('«Все чаты» для чата в папке возвращает его из папки', () => {
    const { props, moveTo } = openFolders({ getChatFolder: () => folder('f1', 'Работа') });
    const item = screen.getByText('Все чаты').closest('li') as HTMLElement;
    expect(item).not.toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(item);
    expect(moveTo.handleRemoveFromFolder).toHaveBeenCalledWith('c1');
    expect(moveTo.setShowMoveToFolderMenu).toHaveBeenCalledWith(false);
    expect(props.handleChatMenuClose).toHaveBeenCalledTimes(1);
  });

  it('«Все чаты» без выбранного чата не отключён и клик ничего не делает', () => {
    const { moveTo, props } = openFolders({ selectedChatId: null });
    const item = screen.getByText('Все чаты').closest('li') as HTMLElement;
    expect(item).not.toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(item);
    expect(moveTo.handleRemoveFromFolder).not.toHaveBeenCalled();
    expect(props.handleChatMenuClose).not.toHaveBeenCalled();
  });

  it('список папок исключает текущую папку чата; клик переносит чат', () => {
    const { props, moveTo } = openFolders({
      folders: [folder('f1', 'Работа'), folder('f2', 'Дом')],
      getChatFolder: () => folder('f1', 'Работа'),
    });
    expect(screen.queryByText('Работа')).toBeNull();
    fireEvent.click(screen.getByText('Дом'));
    expect(moveTo.handleMoveToFolder).toHaveBeenCalledWith('c1', 'f2');
    expect(moveTo.setFolderMenuAnchorForChat).toHaveBeenCalledWith(null);
    expect(props.handleChatMenuClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Нет доступных папок')).toBeNull();
  });

  it('клик по папке без выбранного чата игнорируется', () => {
    const { moveTo } = openFolders({ folders: [folder('f2', 'Дом')], selectedChatId: null });
    fireEvent.click(screen.getByText('Дом'));
    expect(moveTo.handleMoveToFolder).not.toHaveBeenCalled();
  });

  it('если подходящих папок нет, показывается неактивная строка «Нет доступных папок»', () => {
    openFolders({ folders: [folder('f1', 'Работа')], getChatFolder: () => folder('f1', 'Работа') });
    expect(screen.getByText('Нет доступных папок').closest('li')).toHaveAttribute('aria-disabled', 'true');
  });

  it('без выбранного чата в списке показываются все папки', () => {
    openFolders({ folders: [folder('f1', 'Работа')], selectedChatId: null });
    expect(screen.getByText('Работа')).toBeInTheDocument();
  });

  it('вне режима папок пункты «Создать папку», «Все чаты» и список папок скрыты', () => {
    openFolders({ useFoldersMode: false, folders: [] });
    expect(screen.queryByText('Создать папку')).toBeNull();
    expect(screen.queryByText('Все чаты')).toBeNull();
    expect(screen.getByText('Нет доступных папок')).toBeInTheDocument();
  });

  it('(known issue) вне режима папок при наличии папок меню пустое: нет ни пунктов, ни «Нет доступных папок»', () => {
    // Правильное поведение: показать «Нет доступных папок» (или не открывать меню) — пользователь
    // видит пустую рамку. Условие «пусто» считается по folders, а пункты скрыты по useFoldersMode.
    openFolders({ useFoldersMode: false, folders: [folder('f1', 'Работа')] });
    expect(screen.getByRole('menu').children).toHaveLength(0);
    expect(screen.queryByText('Нет доступных папок')).toBeNull();
  });

  it('закрытие меню (Escape) сбрасывает флаг и якорь', () => {
    const { moveTo } = openFolders();
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    expect(moveTo.setShowMoveToFolderMenu).toHaveBeenCalledWith(false);
    expect(moveTo.setFolderMenuAnchorForChat).toHaveBeenCalledWith(null);
  });

  it('вход курсора в панель отменяет таймер закрытия и пишет в лог', () => {
    const timer = setTimeout(() => {}, 5000);
    const { moveTo } = openFolders({}, { closeFolderTimerRef: { current: timer } });
    fireEvent.mouseEnter(document.querySelector('[data-chat-submenu="folder"]') as HTMLElement);
    expect(moveTo.closeFolderTimerRef.current).toBeNull();
    expect(moveTo.logRef.current).toHaveBeenCalledWith('paper folder enter — отмена таймера закрытия');
  });

  it('вход курсора без таймера ничего не логирует', () => {
    const { moveTo } = openFolders();
    fireEvent.mouseEnter(document.querySelector('[data-chat-submenu="folder"]') as HTMLElement);
    expect(moveTo.logRef.current).not.toHaveBeenCalled();
  });

  it('уход курсора в основное меню не закрывает подменю, в сторону — закрывает', () => {
    const { moveTo, inside, outside } = openFolders();
    const paper = document.querySelector('[data-chat-submenu="folder"]') as HTMLElement;
    fireEvent.mouseLeave(paper, { relatedTarget: inside });
    expect(moveTo.setShowMoveToFolderMenu).not.toHaveBeenCalled();
    expect(moveTo.logRef.current).toHaveBeenCalledWith('paper folder leave — уход в основное меню, не закрываем');

    fireEvent.mouseLeave(paper, { relatedTarget: outside });
    expect(moveTo.setShowMoveToFolderMenu).toHaveBeenCalledWith(false);
    expect(moveTo.setFolderMenuAnchorForChat).toHaveBeenCalledWith(null);
    expect(moveTo.logRef.current).toHaveBeenCalledWith('paper folder leave — закрываем подменю', { to: 'DIV' });
  });

  it('(known issue) уход курсора за пределы окна бросает TypeError и не закрывает подменю папок', () => {
    // Правильное поведение: подменю закрывается (см. такой же случай у подменю проектов).
    const { moveTo } = openFolders();
    const errors: unknown[] = [];
    const onError = (e: ErrorEvent) => {
      errors.push(e.error);
      e.preventDefault();
    };
    window.addEventListener('error', onError);
    const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
    fireEvent.mouseLeave(document.querySelector('[data-chat-submenu="folder"]') as HTMLElement);
    spy.mockRestore();
    window.removeEventListener('error', onError);
    expect(errors).toHaveLength(1);
    expect((errors[0] as Error).name).toBe('TypeError');
    expect(moveTo.setShowMoveToFolderMenu).not.toHaveBeenCalled();
  });
});
