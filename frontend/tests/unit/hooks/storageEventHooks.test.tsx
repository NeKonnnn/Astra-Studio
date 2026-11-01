import { act, cleanup, renderHook } from '@testing-library/react';

// AgentSelector тянет за собой MUI и пол-приложения; нужен только чтение агента из localStorage.
jest.mock('@/components/AgentSelector', () => ({
  getActiveAgentFromStorage: () => {
    const id = (globalThis as any).localStorage.getItem('active_agent_id');
    const name = (globalThis as any).localStorage.getItem('active_agent_name');
    if (!id || !name) return null;
    return { id: parseInt(id, 10), name, system_prompt: (globalThis as any).localStorage.getItem('active_agent_prompt') || '' };
  },
}));

import { useActiveSkillIndicators } from '@/hooks/useActiveSkillIndicators';
import { useArtifactsEnabled } from '@/hooks/useArtifactsEnabled';
import { dispatchAgentStatusChanged, useMyAgentSelection } from '@/hooks/useChatInputAgentIndicators';
import { useChatInputArtifactsIndicator } from '@/hooks/useChatInputArtifactsIndicator';
import { useHotkeyBindings } from '@/hooks/useHotkeyBindings';
import { useWorkZoneBgMode } from '@/hooks/useWorkZoneBgMode';
import {
  RIGHT_SIDEBAR_COLLAPSED_WIDTH_PX,
  RIGHT_SIDEBAR_EXPANDED_WIDTH_PX,
  RIGHT_SIDEBAR_INSET_CSS_VAR,
  RIGHT_SIDEBAR_RAIL_OVERLAP_CSS_VAR,
  getRightSidebarInsetPx,
  getRightSidebarRailOverlapPx,
  useRightRailOffsetPx,
  useRightSidebarInsetCssVar,
} from '@/hooks/useRightSidebarInsetCssVar';
import { RIGHT_SIDEBAR_MAX_WIDTH_PX } from '@/hooks/useRightSidebarWidth';
import { setActiveSkillRefs, SKILL_SELECTION_CHANGED_EVENT } from '@/utils/skillSelectionStorage';
import {
  AGENT_ARTIFACTS_CHANGED_EVENT,
  AGENT_ARTIFACTS_ENABLED_KEY,
  AGENT_SHADCN_ENABLED_KEY,
} from '@/utils/agentArtifactsEnabled';
import { setChatArtifactsToolMode } from '@/utils/artifactsSelectionStorage';
import { ASTRA_HOTKEYS_CHANGED, DEFAULT_HOTKEY_BINDINGS } from '@/constants/hotkeys';
import { WORK_ZONE_BG_MODE_KEY } from '@/constants/workZoneBackground';

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe('useActiveSkillIndicators', () => {
  it('без chatId возвращает пустой список', () => {
    const { result } = renderHook(() => useActiveSkillIndicators(null));
    expect(result.current).toEqual([]);
  });

  it('читает skills чата из localStorage при монтировании', () => {
    setActiveSkillRefs('c1', [{ slug: 'pptx', name: 'Презентации' }, { slug: 'mermaid' }]);
    const { result } = renderHook(() => useActiveSkillIndicators('c1'));
    expect(result.current).toEqual([{ slug: 'pptx', name: 'Презентации' }, { slug: 'mermaid' }]);
  });

  it('обновляется по событию выбора skill в своём чате', () => {
    const { result } = renderHook(() => useActiveSkillIndicators('c1'));
    expect(result.current).toEqual([]);
    act(() => setActiveSkillRefs('c1', [{ slug: 'a' }]));
    expect(result.current).toEqual([{ slug: 'a' }]);
  });

  it('игнорирует событие другого чата', () => {
    localStorage.setItem('chat:c2:active_skill_ids', JSON.stringify(['x']));
    const { result } = renderHook(() => useActiveSkillIndicators('c1'));
    act(() => {
      window.dispatchEvent(new CustomEvent(SKILL_SELECTION_CHANGED_EVENT, { detail: { chatId: 'c2' } }));
    });
    expect(result.current).toEqual([]);
  });

  it('реагирует на storage-событие (другая вкладка)', () => {
    const { result } = renderHook(() => useActiveSkillIndicators('c1'));
    localStorage.setItem('chat:c1:active_skill_ids', JSON.stringify(['z']));
    act(() => {
      window.dispatchEvent(new Event('storage'));
    });
    expect(result.current).toEqual([{ slug: 'z' }]);
  });

  it('при смене chatId перечитывает список', () => {
    setActiveSkillRefs('c1', [{ slug: 'one' }]);
    setActiveSkillRefs('c2', [{ slug: 'two' }]);
    const { result, rerender } = renderHook(({ id }) => useActiveSkillIndicators(id), {
      initialProps: { id: 'c1' },
    });
    expect(result.current).toEqual([{ slug: 'one' }]);
    rerender({ id: 'c2' });
    expect(result.current).toEqual([{ slug: 'two' }]);
  });

  it('снимает подписки при размонтировании', () => {
    const removeSpy = jest.spyOn(window, 'removeEventListener');
    const { unmount } = renderHook(() => useActiveSkillIndicators('c1'));
    unmount();
    const removed = removeSpy.mock.calls.map((c) => c[0]);
    expect(removed).toEqual(expect.arrayContaining([SKILL_SELECTION_CHANGED_EVENT, 'storage']));
    removeSpy.mockRestore();
  });
});

describe('useArtifactsEnabled', () => {
  it('начальное значение берётся из localStorage', () => {
    localStorage.setItem(AGENT_ARTIFACTS_ENABLED_KEY, '1');
    const { result } = renderHook(() => useArtifactsEnabled());
    expect(result.current).toBe(true);
  });

  it('agentSelected с config включает флаг и сохраняет его', () => {
    const { result } = renderHook(() => useArtifactsEnabled());
    expect(result.current).toBe(false);
    act(() => {
      window.dispatchEvent(
        new CustomEvent('agentSelected', { detail: { config: { artifacts_enabled: true } } }),
      );
    });
    expect(result.current).toBe(true);
    expect(localStorage.getItem(AGENT_ARTIFACTS_ENABLED_KEY)).toBe('1');
  });

  it('agentSelected с config без artifacts_enabled выключает флаг', () => {
    localStorage.setItem(AGENT_ARTIFACTS_ENABLED_KEY, '1');
    const { result } = renderHook(() => useArtifactsEnabled());
    act(() => {
      window.dispatchEvent(new CustomEvent('agentSelected', { detail: { config: {} } }));
    });
    expect(result.current).toBe(false);
    expect(localStorage.getItem(AGENT_ARTIFACTS_ENABLED_KEY)).toBeNull();
  });

  it('agentSelected с detail=null (агент снят) сбрасывает флаг и хранилище', () => {
    localStorage.setItem(AGENT_ARTIFACTS_ENABLED_KEY, '1');
    localStorage.setItem(AGENT_SHADCN_ENABLED_KEY, '1');
    const { result } = renderHook(() => useArtifactsEnabled());
    expect(result.current).toBe(true);
    act(() => {
      window.dispatchEvent(new CustomEvent('agentSelected', { detail: null }));
    });
    expect(result.current).toBe(false);
    expect(localStorage.getItem(AGENT_ARTIFACTS_ENABLED_KEY)).toBeNull();
    expect(localStorage.getItem(AGENT_SHADCN_ENABLED_KEY)).toBeNull();
  });

  it('agentSelected без config не затирает уже сохранённый флаг', () => {
    localStorage.setItem(AGENT_ARTIFACTS_ENABLED_KEY, '1');
    const { result } = renderHook(() => useArtifactsEnabled());
    act(() => {
      window.dispatchEvent(new CustomEvent('agentSelected', { detail: { id: 5 } }));
    });
    expect(result.current).toBe(true);
    expect(localStorage.getItem(AGENT_ARTIFACTS_ENABLED_KEY)).toBe('1');
  });

  it('синхронизируется по AGENT_ARTIFACTS_CHANGED_EVENT и storage', () => {
    const { result } = renderHook(() => useArtifactsEnabled());
    localStorage.setItem(AGENT_ARTIFACTS_ENABLED_KEY, '1');
    act(() => {
      window.dispatchEvent(new Event(AGENT_ARTIFACTS_CHANGED_EVENT));
    });
    expect(result.current).toBe(true);
    localStorage.removeItem(AGENT_ARTIFACTS_ENABLED_KEY);
    act(() => {
      window.dispatchEvent(new Event('storage'));
    });
    expect(result.current).toBe(false);
  });

  it('после размонтирования события больше не обрабатываются', () => {
    const { result, unmount } = renderHook(() => useArtifactsEnabled());
    unmount();
    localStorage.setItem(AGENT_ARTIFACTS_ENABLED_KEY, '1');
    act(() => {
      window.dispatchEvent(new Event(AGENT_ARTIFACTS_CHANGED_EVENT));
    });
    expect(result.current).toBe(false);
  });
});

describe('useChatInputAgentIndicators', () => {
  it('dispatchAgentStatusChanged с boolean кладёт anyActive в detail', () => {
    const handler = jest.fn();
    window.addEventListener('astrachatAgentStatusChanged', handler);
    dispatchAgentStatusChanged(true);
    dispatchAgentStatusChanged(false);
    window.removeEventListener('astrachatAgentStatusChanged', handler);
    expect(handler).toHaveBeenCalledTimes(2);
    expect((handler.mock.calls[0][0] as CustomEvent).detail).toEqual({ anyActive: true });
    expect((handler.mock.calls[1][0] as CustomEvent).detail).toEqual({ anyActive: false });
  });

  it('dispatchAgentStatusChanged без аргумента отправляет событие без detail', () => {
    const handler = jest.fn();
    window.addEventListener('astrachatAgentStatusChanged', handler);
    dispatchAgentStatusChanged();
    window.removeEventListener('astrachatAgentStatusChanged', handler);
    expect(handler).toHaveBeenCalledTimes(1);
    expect((handler.mock.calls[0][0] as CustomEvent).detail).toBeNull();
  });

  it('useMyAgentSelection читает агента и обновляется по agentSelected', () => {
    const { result } = renderHook(() => useMyAgentSelection());
    expect(result.current).toBeNull();
    localStorage.setItem('active_agent_id', '7');
    localStorage.setItem('active_agent_name', 'Бот');
    localStorage.setItem('active_agent_prompt', 'Ты бот');
    act(() => {
      window.dispatchEvent(new Event('agentSelected'));
    });
    expect(result.current).toEqual({ id: 7, name: 'Бот', system_prompt: 'Ты бот' });
  });

  it('useMyAgentSelection отписывается при размонтировании', () => {
    const { result, unmount } = renderHook(() => useMyAgentSelection());
    unmount();
    localStorage.setItem('active_agent_id', '1');
    localStorage.setItem('active_agent_name', 'X');
    act(() => {
      window.dispatchEvent(new Event('agentSelected'));
    });
    expect(result.current).toBeNull();
  });
});

describe('useChatInputArtifactsIndicator', () => {
  it('по умолчанию индикатор неактивен и без подсказки', () => {
    const { result } = renderHook(() => useChatInputArtifactsIndicator('c1'));
    expect(result.current.active).toBe(false);
    expect(result.current.tooltip).toBe('');
    expect(result.current.settings).toEqual({ shadcn_enabled: false, user_prompt_mode: false });
  });

  it('shadcn в чате включает индикатор и подсказку', () => {
    const { result } = renderHook(() => useChatInputArtifactsIndicator('c1'));
    act(() => {
      setChatArtifactsToolMode('c1', 'shadcn_enabled', true);
    });
    expect(result.current.active).toBe(true);
    expect(result.current.tooltip).toBe('Режим артефактов: shadcn/ui.');
  });

  it('свой промпт даёт подсказку «свой промпт»', () => {
    const { result } = renderHook(() => useChatInputArtifactsIndicator('c1'));
    act(() => {
      setChatArtifactsToolMode('c1', 'user_prompt_mode', true);
    });
    expect(result.current.tooltip).toBe('Режим артефактов: свой промпт.');
  });

  it('значения по умолчанию берутся от агента (shadcn + user prompt взаимоисключаются)', () => {
    localStorage.setItem(AGENT_SHADCN_ENABLED_KEY, '1');
    const { result } = renderHook(() => useChatInputArtifactsIndicator(null));
    expect(result.current.settings).toEqual({ shadcn_enabled: true, user_prompt_mode: false });
    expect(result.current.active).toBe(true);
  });

  it('игнорирует событие выбора артефактов другого чата', () => {
    const { result } = renderHook(() => useChatInputArtifactsIndicator('c1'));
    localStorage.setItem('chat:c1:artifacts_settings', JSON.stringify({ shadcn_enabled: true }));
    act(() => {
      window.dispatchEvent(
        new CustomEvent('astrachatArtifactsSelectionChanged', { detail: { chatId: 'other' } }),
      );
    });
    expect(result.current.active).toBe(false);
  });

  it('реагирует на смену агента и storage', () => {
    const { result } = renderHook(() => useChatInputArtifactsIndicator('c1'));
    localStorage.setItem(AGENT_SHADCN_ENABLED_KEY, '1');
    act(() => {
      window.dispatchEvent(new Event('agentSelected'));
    });
    expect(result.current.active).toBe(true);
    localStorage.removeItem(AGENT_SHADCN_ENABLED_KEY);
    act(() => {
      window.dispatchEvent(new Event('storage'));
    });
    expect(result.current.active).toBe(false);
    localStorage.setItem(AGENT_SHADCN_ENABLED_KEY, '1');
    act(() => {
      window.dispatchEvent(new Event(AGENT_ARTIFACTS_CHANGED_EVENT));
    });
    expect(result.current.active).toBe(true);
  });

  it('при размонтировании снимает все подписки', () => {
    const removeSpy = jest.spyOn(window, 'removeEventListener');
    const { unmount } = renderHook(() => useChatInputArtifactsIndicator('c1'));
    unmount();
    const removed = removeSpy.mock.calls.map((c) => c[0]);
    expect(removed).toEqual(
      expect.arrayContaining([
        'astrachatArtifactsSelectionChanged',
        AGENT_ARTIFACTS_CHANGED_EVENT,
        'agentSelected',
        'storage',
      ]),
    );
    removeSpy.mockRestore();
  });
});

describe('useHotkeyBindings', () => {
  it('отдаёт дефолтные привязки и все действия включёнными', () => {
    const { result } = renderHook(() => useHotkeyBindings());
    expect(result.current.bindings).toEqual(DEFAULT_HOTKEY_BINDINGS);
    expect(result.current.disabled.size).toBe(0);
    expect(result.current.isEnabled('newChat')).toBe(true);
    expect(result.current.isDefault('newChat')).toBe(true);
  });

  it('setBinding сохраняет привязку и ререндерит по ASTRA_HOTKEYS_CHANGED', () => {
    const { result } = renderHook(() => useHotkeyBindings());
    act(() => result.current.setBinding('newChat', { code: 'KeyN', mod: true }));
    expect(result.current.bindings.newChat).toEqual({ code: 'KeyN', mod: true, shift: false, alt: false });
    expect(result.current.isDefault('newChat')).toBe(false);
  });

  it('resetBinding возвращает привязку к дефолту', () => {
    const { result } = renderHook(() => useHotkeyBindings());
    act(() => result.current.setBinding('newChat', { code: 'KeyN', mod: true }));
    act(() => result.current.resetBinding('newChat'));
    expect(result.current.isDefault('newChat')).toBe(true);
    expect(localStorage.getItem('astra_hotkey_bindings')).toBeNull();
  });

  it('setEnabled(false) добавляет действие в disabled и isEnabled отражает это', () => {
    const { result } = renderHook(() => useHotkeyBindings());
    act(() => result.current.setEnabled('searchChats', false));
    expect(result.current.disabled.has('searchChats')).toBe(true);
    expect(result.current.isEnabled('searchChats')).toBe(false);
    act(() => result.current.setEnabled('searchChats', true));
    expect(result.current.isEnabled('searchChats')).toBe(true);
  });

  it('resetAll сбрасывает и привязки, и отключения', () => {
    const { result } = renderHook(() => useHotkeyBindings());
    act(() => {
      result.current.setBinding('openSettings', { code: 'KeyQ', alt: true });
      result.current.setEnabled('deleteChat', false);
    });
    act(() => result.current.resetAll());
    expect(result.current.bindings).toEqual(DEFAULT_HOTKEY_BINDINGS);
    expect(result.current.disabled.size).toBe(0);
  });

  it('format форматирует привязку для UI', () => {
    const { result } = renderHook(() => useHotkeyBindings());
    expect(result.current.format({ code: 'KeyS', alt: true })).toBe('Alt + S');
  });

  it('isDefault учитывает отличие модификаторов', () => {
    const { result } = renderHook(() => useHotkeyBindings());
    act(() => result.current.setBinding('openSettings', { code: 'KeyS', alt: true, shift: true }));
    expect(result.current.isDefault('openSettings')).toBe(false);
  });

  it('снимает подписку на ASTRA_HOTKEYS_CHANGED при размонтировании', () => {
    const { result, unmount } = renderHook(() => useHotkeyBindings());
    unmount();
    localStorage.setItem(
      'astra_hotkey_bindings',
      JSON.stringify({ newChat: { code: 'KeyZ', mod: true } }),
    );
    act(() => {
      window.dispatchEvent(new Event(ASTRA_HOTKEYS_CHANGED));
    });
    expect(result.current.bindings.newChat).toEqual(DEFAULT_HOTKEY_BINDINGS.newChat);
  });
});

describe('useWorkZoneBgMode', () => {
  it('по умолчанию режим default', () => {
    const { result } = renderHook(() => useWorkZoneBgMode());
    expect(result.current).toBe('default');
  });

  it('читает сохранённый режим и обновляется по interfaceSettingsChanged', () => {
    localStorage.setItem(WORK_ZONE_BG_MODE_KEY, 'starry');
    const { result } = renderHook(() => useWorkZoneBgMode());
    expect(result.current).toBe('starry');
    localStorage.setItem(WORK_ZONE_BG_MODE_KEY, 'snowfall');
    act(() => {
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });
    expect(result.current).toBe('snowfall');
  });

  it('custom без картинки откатывается к default', () => {
    localStorage.setItem(WORK_ZONE_BG_MODE_KEY, 'custom');
    const { result } = renderHook(() => useWorkZoneBgMode());
    expect(result.current).toBe('default');
  });

  it('отписывается при размонтировании', () => {
    const { result, unmount } = renderHook(() => useWorkZoneBgMode());
    unmount();
    localStorage.setItem(WORK_ZONE_BG_MODE_KEY, 'starry');
    act(() => {
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });
    expect(result.current).toBe('default');
  });
});

describe('useRightSidebarInsetCssVar', () => {
  const rootVar = (name: string) => document.documentElement.style.getPropertyValue(name);

  it('getRightSidebarInsetPx: hidden=0, open=clamp(width), свёрнутая=64', () => {
    expect(getRightSidebarInsetPx(true, true)).toBe(0);
    expect(getRightSidebarInsetPx(true, false)).toBe(RIGHT_SIDEBAR_EXPANDED_WIDTH_PX);
    expect(getRightSidebarInsetPx(true, false, 100000)).toBe(RIGHT_SIDEBAR_MAX_WIDTH_PX);
    expect(getRightSidebarInsetPx(false, false)).toBe(RIGHT_SIDEBAR_COLLAPSED_WIDTH_PX);
  });

  it('getRightSidebarRailOverlapPx: перекрытие только у свёрнутой видимой панели', () => {
    expect(getRightSidebarRailOverlapPx(false, false)).toBe(RIGHT_SIDEBAR_COLLAPSED_WIDTH_PX);
    expect(getRightSidebarRailOverlapPx(true, false)).toBe(0);
    expect(getRightSidebarRailOverlapPx(false, true)).toBe(0);
  });

  it('публикует CSS-переменные и обнуляет их при размонтировании', () => {
    const { unmount } = renderHook(() => useRightSidebarInsetCssVar(false, false));
    expect(rootVar(RIGHT_SIDEBAR_INSET_CSS_VAR)).toBe('64px');
    expect(rootVar(RIGHT_SIDEBAR_RAIL_OVERLAP_CSS_VAR)).toBe('64px');
    unmount();
    expect(rootVar(RIGHT_SIDEBAR_INSET_CSS_VAR)).toBe('0px');
    expect(rootVar(RIGHT_SIDEBAR_RAIL_OVERLAP_CSS_VAR)).toBe('0px');
  });

  it('пересчитывает переменные при смене open/width', () => {
    const { rerender } = renderHook(
      ({ open, width }) => useRightSidebarInsetCssVar(open, false, width),
      { initialProps: { open: false, width: 500 } },
    );
    expect(rootVar(RIGHT_SIDEBAR_INSET_CSS_VAR)).toBe('64px');
    rerender({ open: true, width: 500 });
    expect(rootVar(RIGHT_SIDEBAR_INSET_CSS_VAR)).toBe('500px');
    expect(rootVar(RIGHT_SIDEBAR_RAIL_OVERLAP_CSS_VAR)).toBe('0px');
  });
});

describe('useRightRailOffsetPx', () => {
  const observers: Array<{ cb: () => void; observe: jest.Mock; disconnect: jest.Mock }> = [];
  const originalRO = (global as any).ResizeObserver;

  beforeEach(() => {
    observers.length = 0;
    (global as any).ResizeObserver = class {
      observe = jest.fn();
      disconnect = jest.fn();
      constructor(public cb: () => void) {
        observers.push(this as any);
      }
    };
    document.body.innerHTML = '';
  });

  afterEach(() => {
    cleanup(); // сначала размонтируем хук, иначе MutationObserver поймает очистку body вне act
    (global as any).ResizeObserver = originalRO;
    document.body.innerHTML = '';
  });

  const addRail = (cls: string, width: number) => {
    const el = document.createElement('div');
    el.className = cls;
    el.getBoundingClientRect = () => ({ width } as DOMRect);
    document.body.appendChild(el);
    return el;
  };

  it('без панели ширина 0', () => {
    const { result } = renderHook(() => useRightRailOffsetPx());
    expect(result.current).toBe(0);
  });

  it('читает ширину .astra-right-rail и наблюдает за ней', () => {
    const rail = addRail('astra-right-rail', 320.4);
    const { result } = renderHook(() => useRightRailOffsetPx());
    expect(result.current).toBe(320);
    expect(observers[0].observe).toHaveBeenCalledWith(rail);
  });

  it('падает на MUI Drawer, если .astra-right-rail нет', () => {
    const el = document.createElement('div');
    el.className = 'MuiDrawer-docked MuiDrawer-anchorRight';
    const paper = document.createElement('div');
    paper.className = 'MuiDrawer-paper';
    paper.getBoundingClientRect = () => ({ width: 280 } as DOMRect);
    el.appendChild(paper);
    document.body.appendChild(el);
    const { result } = renderHook(() => useRightRailOffsetPx());
    expect(result.current).toBe(280);
  });

  it('ширина не больше 8px считается отсутствием панели', () => {
    addRail('astra-right-rail', 8);
    const { result } = renderHook(() => useRightRailOffsetPx());
    expect(result.current).toBe(0);
  });

  it('реагирует на resize окна', () => {
    let width = 300;
    const rail = addRail('astra-right-rail', 300);
    rail.getBoundingClientRect = () => ({ width } as DOMRect);
    const { result } = renderHook(() => useRightRailOffsetPx());
    expect(result.current).toBe(300);
    width = 400;
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    expect(result.current).toBe(400);
  });

  it('реагирует на мутации DOM и переподписывает ResizeObserver', async () => {
    const { result } = renderHook(() => useRightRailOffsetPx());
    expect(result.current).toBe(0);
    await act(async () => {
      addRail('astra-right-rail', 250);
      await Promise.resolve();
    });
    expect(result.current).toBe(250);
    expect(observers[0].disconnect).toHaveBeenCalled();
    expect(observers[0].observe).toHaveBeenCalled();
  });

  it('при размонтировании отключает наблюдателей и снимает resize', () => {
    const removeSpy = jest.spyOn(window, 'removeEventListener');
    const { unmount } = renderHook(() => useRightRailOffsetPx());
    unmount();
    expect(observers[0].disconnect).toHaveBeenCalled();
    expect(removeSpy.mock.calls.map((c) => c[0])).toContain('resize');
    removeSpy.mockRestore();
  });
});
