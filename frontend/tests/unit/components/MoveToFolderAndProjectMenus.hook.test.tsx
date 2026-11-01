import React from 'react';
import { act, renderHook } from '@testing-library/react';
import { useMoveToFolderAndProjectMenus } from '@/components/MoveToFolderAndProjectMenus';

/**
 * Хук подменю «Перенести в папку / проект». Логика завязана на DOM (closest, elementFromPoint),
 * поэтому строим минимальную разметку: основное меню с триггерами и отдельные «подменю».
 */

let pointEl: Element | null = null;
/** Если задана очередь, elementFromPoint отдаёт элементы по порядку (последний повторяется). */
let pointSeq: Array<Element | null> | null = null;
let pointCoords: Array<[number, number]> = [];
const originalFromPoint = (document as any).elementFromPoint;
const originalFromPoints = (document as any).elementsFromPoint;

const byId = (id: string) => document.getElementById(id) as HTMLElement;

function buildDom() {
  document.body.innerHTML = `
    <div id="main" role="menu">
      <div id="tf" role="menuitem" data-submenu-trigger="folder"><span id="tf-inner"></span></div>
      <div id="tf2" role="menuitem" data-submenu-trigger="folder"></div>
      <div id="tp" role="menuitem" data-submenu-trigger="project"><span id="tp-inner"></span></div>
      <div id="other" role="menuitem"><span id="other-inner"></span></div>
    </div>
    <div id="subf" role="menu" data-chat-submenu="folder"><span id="subf-inner"></span></div>
    <div id="subp" role="menu" data-chat-submenu="project"><span id="subp-inner"></span></div>
    <div id="orphan"></div>
    <div id="outside"></div>
  `;
}

const ev = (currentTarget: HTMLElement, relatedTarget: HTMLElement | null = null) =>
  ({ currentTarget, relatedTarget } as unknown as React.MouseEvent<HTMLElement>);

const move = (x = 1, y = 1) =>
  act(() => {
    window.dispatchEvent(new MouseEvent('mousemove', { clientX: x, clientY: y }));
  });
const tick = (ms: number) =>
  act(() => {
    jest.advanceTimersByTime(ms);
  });

function setup(chatMenuOpen = true) {
  const moveChatToFolder = jest.fn();
  const handleChatMenuClose = jest.fn();
  const view = renderHook(() =>
    useMoveToFolderAndProjectMenus({ chatMenuOpen, moveChatToFolder, handleChatMenuClose }),
  );
  return { ...view, moveChatToFolder, handleChatMenuClose };
}

beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(new Date('2026-01-01T00:00:00'));
  buildDom();
  pointEl = null;
  pointSeq = null;
  pointCoords = [];
  (document as any).elementFromPoint = (x: number, y: number) => {
    pointCoords.push([x, y]);
    if (pointSeq) return pointSeq.length > 1 ? (pointSeq.shift() as Element | null) : pointSeq[0];
    return pointEl;
  };
  (document as any).elementsFromPoint = () => (pointEl ? [pointEl] : []);
});

afterEach(() => {
  jest.useRealTimers();
  (document as any).elementFromPoint = originalFromPoint;
  (document as any).elementsFromPoint = originalFromPoints;
  document.body.innerHTML = '';
});

describe('useMoveToFolderAndProjectMenus: начальное состояние и открытие', () => {
  it('изначально оба подменю закрыты, якорей нет', () => {
    const { result } = setup();
    expect(result.current.showMoveToFolderMenu).toBe(false);
    expect(result.current.showMoveToProjectMenu).toBe(false);
    expect(result.current.folderMenuAnchorForChat).toBeNull();
    expect(result.current.projectMenuAnchorForChat).toBeNull();
    expect(result.current.closeFolderTimerRef.current).toBeNull();
    expect(result.current.closeProjectTimerRef.current).toBeNull();
  });

  it('вход на триггер «папка» открывает подменю папок с этим якорем и закрывает подменю проектов', () => {
    const { result } = setup();
    act(() => result.current.handleProjectSubmenuEnter(ev(byId('tp'))));
    expect(result.current.showMoveToProjectMenu).toBe(true);
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    expect(result.current.showMoveToFolderMenu).toBe(true);
    expect(result.current.folderMenuAnchorForChat).toBe(byId('tf'));
    expect(result.current.showMoveToProjectMenu).toBe(false);
    expect(result.current.projectMenuAnchorForChat).toBeNull();
  });

  it('вход на триггер «проект» открывает подменю проектов и закрывает подменю папок', () => {
    const { result } = setup();
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    act(() => result.current.handleProjectSubmenuEnter(ev(byId('tp'))));
    expect(result.current.showMoveToProjectMenu).toBe(true);
    expect(result.current.projectMenuAnchorForChat).toBe(byId('tp'));
    expect(result.current.showMoveToFolderMenu).toBe(false);
    expect(result.current.folderMenuAnchorForChat).toBeNull();
  });

  it('повторный вход на тот же триггер ничего не меняет, на другой узел — переносит якорь', () => {
    const { result } = setup();
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    expect(result.current.folderMenuAnchorForChat).toBe(byId('tf'));
    // новый DOM-узел триггера (ремонт меню) — якорь обновляется
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf2'))));
    expect(result.current.folderMenuAnchorForChat).toBe(byId('tf2'));
    expect(result.current.showMoveToFolderMenu).toBe(true);

    act(() => result.current.handleProjectSubmenuEnter(ev(byId('tp'))));
    act(() => result.current.handleProjectSubmenuEnter(ev(byId('tp'))));
    expect(result.current.projectMenuAnchorForChat).toBe(byId('tp'));
    act(() => result.current.handleProjectSubmenuEnter(ev(byId('other'))));
    expect(result.current.projectMenuAnchorForChat).toBe(byId('other'));
  });

  it('closeSubmenus закрывает всё, обнуляет якоря и гасит запланированные таймеры', () => {
    const { result } = setup();
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    const folderTimer = jest.fn();
    const projectTimer = jest.fn();
    result.current.closeFolderTimerRef.current = setTimeout(folderTimer, 100);
    result.current.closeProjectTimerRef.current = setTimeout(projectTimer, 100);
    act(() => result.current.closeSubmenus());
    expect(result.current.showMoveToFolderMenu).toBe(false);
    expect(result.current.showMoveToProjectMenu).toBe(false);
    expect(result.current.folderMenuAnchorForChat).toBeNull();
    expect(result.current.projectMenuAnchorForChat).toBeNull();
    expect(result.current.closeFolderTimerRef.current).toBeNull();
    expect(result.current.closeProjectTimerRef.current).toBeNull();
    tick(500);
    expect(folderTimer).not.toHaveBeenCalled();
    expect(projectTimer).not.toHaveBeenCalled();
  });

  it('closeSubmenus без таймеров тоже безопасен', () => {
    const { result } = setup();
    act(() => result.current.closeSubmenus());
    expect(result.current.showMoveToFolderMenu).toBe(false);
  });
});

describe('useMoveToFolderAndProjectMenus: выбор действия', () => {
  it('handleMoveToFolder переносит чат, закрывает подменю и основное меню', () => {
    const { result, moveChatToFolder, handleChatMenuClose } = setup();
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    act(() => result.current.handleMoveToFolder('c1', 'f1'));
    expect(moveChatToFolder).toHaveBeenCalledWith('c1', 'f1');
    expect(handleChatMenuClose).toHaveBeenCalledTimes(1);
    expect(result.current.showMoveToFolderMenu).toBe(false);
  });

  it('handleRemoveFromFolder переносит чат в «Все чаты» (null)', () => {
    const { result, moveChatToFolder, handleChatMenuClose } = setup();
    act(() => result.current.setShowMoveToFolderMenu(true));
    act(() => result.current.handleRemoveFromFolder('c1'));
    expect(moveChatToFolder).toHaveBeenCalledWith('c1', null);
    expect(handleChatMenuClose).toHaveBeenCalledTimes(1);
    expect(result.current.showMoveToFolderMenu).toBe(false);
  });
});

type Kind = 'folder' | 'project';
const kinds: Array<{
  kind: Kind;
  trigger: string;
  triggerInner: string;
  sub: string;
  subInner: string;
  enter: 'handleFolderSubmenuEnter' | 'handleProjectSubmenuEnter';
  leave: 'handleFolderSubmenuLeave' | 'handleProjectSubmenuLeave';
  show: 'showMoveToFolderMenu' | 'showMoveToProjectMenu';
  anchor: 'folderMenuAnchorForChat' | 'projectMenuAnchorForChat';
  timer: 'closeFolderTimerRef' | 'closeProjectTimerRef';
}> = [
  {
    kind: 'folder',
    trigger: 'tf',
    triggerInner: 'tf-inner',
    sub: 'subf',
    subInner: 'subf-inner',
    enter: 'handleFolderSubmenuEnter',
    leave: 'handleFolderSubmenuLeave',
    show: 'showMoveToFolderMenu',
    anchor: 'folderMenuAnchorForChat',
    timer: 'closeFolderTimerRef',
  },
  {
    kind: 'project',
    trigger: 'tp',
    triggerInner: 'tp-inner',
    sub: 'subp',
    subInner: 'subp-inner',
    enter: 'handleProjectSubmenuEnter',
    leave: 'handleProjectSubmenuLeave',
    show: 'showMoveToProjectMenu',
    anchor: 'projectMenuAnchorForChat',
    timer: 'closeProjectTimerRef',
  },
];

describe.each(kinds)('useMoveToFolderAndProjectMenus: уход курсора ($kind)', (k) => {
  /** Открываем подменю на «сиротском» якоре, чтобы интервальная проверка не вмешивалась в логику leave. */
  const open = (anchorId = 'orphan') => {
    const view = setup();
    act(() => view.result.current[k.enter](ev(byId(anchorId))));
    tick(30); // grace period (10 мс) прошёл
    return view;
  };
  const leave = (view: ReturnType<typeof setup>, related: HTMLElement | null, current = byId(k.trigger)) =>
    act(() => view.result.current[k.leave](ev(current, related)));
  const settle = () => tick(60);

  it('leave до открытия подменю сбрасывает состояние и якорь', () => {
    const view = setup();
    leave(view, null);
    expect(view.result.current[k.show]).toBe(false);
    expect(view.result.current[k.anchor]).toBeNull();
  });

  it('leave сразу после открытия (grace period) игнорируется', () => {
    const view = setup();
    act(() => view.result.current[k.enter](ev(byId('orphan'))));
    leave(view, null);
    settle();
    expect(view.result.current[k.show]).toBe(true);
  });

  it('переход на другой пункт основного меню закрывает подменю сразу', () => {
    const view = open();
    leave(view, byId('other-inner'));
    expect(view.result.current[k.show]).toBe(false);
    expect(view.result.current[k.anchor]).toBeNull();
  });

  it('переход курсора в само подменю (другое меню) не закрывает его', () => {
    const view = open();
    leave(view, byId(k.subInner));
    settle();
    expect(view.result.current[k.show]).toBe(true);
  });

  it('переход на собственный триггер оставляет подменю открытым, если курсор над текущим триггером', () => {
    const view = open();
    pointEl = byId(k.triggerInner);
    move();
    leave(view, byId(k.triggerInner));
    settle();
    expect(view.result.current[k.show]).toBe(true);
  });

  it('уход на собственный триггер, но под курсором уже другой пункт-триггер — подменю закрывается', () => {
    const view = open();
    // под курсором триггер другого вида (для папки — проект и наоборот): быстрая проверка закрывает
    pointEl = byId(k.kind === 'folder' ? 'tp-inner' : 'tf');
    leave(view, byId(k.triggerInner));
    settle();
    expect(view.result.current[k.show]).toBe(false);
  });

  it('курсор вышел за пределы окна (нет элемента под курсором) — подменю закрывается по таймеру', () => {
    const view = open();
    pointEl = null;
    leave(view, null);
    settle(); // rAF + таймер проверки позиции
    expect(view.result.current[k.show]).toBe(false);
    expect(view.result.current[k.timer].current).toBeNull();
  });

  it('курсор над самим подменю — не закрывать', () => {
    const view = open();
    pointEl = byId(k.subInner);
    move();
    leave(view, null);
    settle();
    expect(view.result.current[k.show]).toBe(true);
  });

  it('быстрая проверка: курсор уже над другим пунктом основного меню — закрывается без ожидания таймера', () => {
    const view = open();
    pointEl = byId('other-inner');
    move();
    leave(view, null);
    settle();
    expect(view.result.current[k.show]).toBe(false);
    // таймер отложенной проверки не понадобился
    expect(view.result.current[k.timer].current).toBeNull();
  });

  it('отложенная проверка: курсор успел перейти на другой пункт меню — закрывается', () => {
    const view = open();
    // быстрая проверка видит свой триггер, отложенная — уже другой пункт меню
    pointSeq = [byId(k.triggerInner), byId('other-inner')];
    leave(view, null);
    settle();
    expect(view.result.current[k.show]).toBe(false);
  });

  it('отложенная проверка: курсор над текущим триггером — остаётся открытым', () => {
    const view = open();
    pointEl = byId(k.triggerInner);
    leave(view, null);
    settle();
    expect(view.result.current[k.show]).toBe(true);
  });

  it('отложенная проверка: курсор вне любых меню — закрывается', () => {
    const view = open();
    // быстрая проверка пропускает (элемент не в меню), итоговая — закрывает
    pointEl = byId('outside');
    leave(view, null);
    settle();
    expect(view.result.current[k.show]).toBe(false);
  });

  it('повторный leave сбрасывает прежний таймер проверки', () => {
    const view = open();
    pointEl = byId(k.triggerInner);
    leave(view, null);
    view.result.current[k.timer].current = setTimeout(() => {}, 10_000);
    leave(view, null);
    expect(view.result.current[k.timer].current).toBeNull();
    settle();
    expect(view.result.current[k.show]).toBe(true);
  });
});

describe('useMoveToFolderAndProjectMenus: интервальная проверка положения курсора', () => {
  it('курсор над другим пунктом основного меню закрывает подменю папок и гасит таймеры', () => {
    const { result } = setup();
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    const folderTimer = jest.fn();
    const projectTimer = jest.fn();
    result.current.closeFolderTimerRef.current = setTimeout(folderTimer, 1000);
    result.current.closeProjectTimerRef.current = setTimeout(projectTimer, 1000);
    pointEl = byId('other-inner');
    move();
    tick(10);
    expect(result.current.showMoveToFolderMenu).toBe(false);
    expect(result.current.folderMenuAnchorForChat).toBeNull();
    expect(result.current.closeFolderTimerRef.current).toBeNull();
    expect(result.current.closeProjectTimerRef.current).toBeNull();
    tick(2000);
    expect(folderTimer).not.toHaveBeenCalled();
    expect(projectTimer).not.toHaveBeenCalled();
  });

  it('то же для подменю проектов', () => {
    const { result } = setup();
    act(() => result.current.handleProjectSubmenuEnter(ev(byId('tp'))));
    pointEl = byId('other-inner');
    move();
    tick(10);
    expect(result.current.showMoveToProjectMenu).toBe(false);
    expect(result.current.projectMenuAnchorForChat).toBeNull();
  });

  it('курсор над собственным триггером не закрывает подменю', () => {
    const { result } = setup();
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    pointEl = byId('tf-inner');
    move();
    tick(100);
    expect(result.current.showMoveToFolderMenu).toBe(true);
    act(() => result.current.handleProjectSubmenuEnter(ev(byId('tp'))));
    pointEl = byId('tp-inner');
    move();
    tick(100);
    expect(result.current.showMoveToProjectMenu).toBe(true);
  });

  it('элементы вне меню и сам контейнер меню пропускаются, решает первый пункт меню под курсором', () => {
    const { result } = setup();
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    (document as any).elementsFromPoint = () => [byId('outside'), byId('main'), byId('other-inner')];
    move();
    tick(10);
    expect(result.current.showMoveToFolderMenu).toBe(false);
  });

  it('если под курсором нет пунктов меню, подменю остаётся', () => {
    const { result } = setup();
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    (document as any).elementsFromPoint = () => [byId('outside'), byId('main')];
    move();
    tick(100);
    expect(result.current.showMoveToFolderMenu).toBe(true);
  });

  it('якорь вне элемента role=menu — проверка ничего не делает', () => {
    const { result } = setup();
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('orphan'))));
    pointEl = byId('other-inner');
    move();
    tick(100);
    expect(result.current.showMoveToFolderMenu).toBe(true);
  });

  it('подменю открыто через сеттер без входа на триггер (refs пусты) — проверка ничего не закрывает', () => {
    const { result } = setup();
    act(() => result.current.setShowMoveToFolderMenu(true));
    pointEl = byId('other-inner');
    move();
    tick(100);
    expect(result.current.showMoveToFolderMenu).toBe(true);
  });

  it('координаты мыши запоминаются только пока основное меню чата открыто', () => {
    const run = (chatMenuOpen: boolean) => {
      const { result, unmount } = setup(chatMenuOpen);
      act(() => result.current.handleFolderSubmenuEnter(ev(byId('orphan'))));
      tick(30);
      pointCoords = [];
      pointEl = byId('outside');
      move(50, 60);
      act(() => result.current.handleFolderSubmenuLeave(ev(byId('tf'), null)));
      tick(60);
      const coords = pointCoords.slice();
      unmount();
      return coords;
    };
    const opened = run(true);
    const closed = run(false);
    expect(opened.length).toBeGreaterThan(0);
    expect(opened.every(([x, y]) => x === 50 && y === 60)).toBe(true);
    expect(closed.length).toBeGreaterThan(0);
    expect(closed.every(([x, y]) => x === 0 && y === 0)).toBe(true);
  });

  it('после размонтирования интервал и слушатель mousemove снимаются', () => {
    const { result, unmount } = setup();
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    const clearSpy = jest.spyOn(window, 'removeEventListener');
    unmount();
    expect(clearSpy.mock.calls.some(([type]) => type === 'mousemove')).toBe(true);
    clearSpy.mockRestore();
    expect(jest.getTimerCount()).toBe(0);
  });
});

describe('MoveToFolderAndProjectMenus: отладочный лог', () => {
  afterEach(() => {
    delete (window as any).__SUBMENU_DEBUG__;
  });

  it('при window.__SUBMENU_DEBUG__ хук пишет в консоль с префиксом [Submenu]', () => {
    (window as any).__SUBMENU_DEBUG__ = true;
    const logSpy = jest.spyOn(console, 'log').mockImplementation(() => {});
    let debugMod: any;
    jest.isolateModules(() => {
      // модуль читает флаг при загрузке; React подставляем тот же, что у renderHook
      jest.doMock('react', () => React);
      debugMod = require('./MoveToFolderAndProjectMenus');
    });
    jest.dontMock('react');
    const { result } = renderHook(() =>
      debugMod.useMoveToFolderAndProjectMenus({
        chatMenuOpen: true,
        moveChatToFolder: jest.fn(),
        handleChatMenuClose: jest.fn(),
      }),
    );
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    act(() => result.current.closeSubmenus());
    expect(logSpy).toHaveBeenCalledWith('[Submenu]', 'closeSubmenus');
    expect(logSpy).toHaveBeenCalledWith('[Submenu]', 'folder enter', { isOpening: true });
    logSpy.mockRestore();
  });

  it('без флага консоль не засоряется', () => {
    const logSpy = jest.spyOn(console, 'log').mockImplementation(() => {});
    const { result } = setup();
    act(() => result.current.handleFolderSubmenuEnter(ev(byId('tf'))));
    act(() => result.current.closeSubmenus());
    expect(logSpy).not.toHaveBeenCalled();
    logSpy.mockRestore();
  });
});
