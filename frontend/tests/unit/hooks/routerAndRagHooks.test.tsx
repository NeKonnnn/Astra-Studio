import React from 'react';
import { act, renderHook } from '@testing-library/react';

let mockLocation: { pathname: string; state: unknown } = { pathname: '/', state: null };
jest.mock('react-router-dom', () => ({
  useLocation: () => mockLocation,
}));

let mockRagStatus: any = null;
jest.mock('@/contexts/RagReindexStatusContext', () => ({
  useRagReindexStatus: () => ({ status: mockRagStatus }),
}));

import { usePendingAgentConstructorOpen } from '@/hooks/usePendingAgentConstructorOpen';
import { usePendingSkillSidebarOpen } from '@/hooks/usePendingSkillSidebarOpen';
import * as ragStatusModule from '@/hooks/useRagReindexStatus';
import { useAgentRagRebuilding } from '@/hooks/useRagReindexStatus';
import { useRagEntityReadyMessage } from '@/hooks/useRagEntityReadyMessage';
import { dispatchRagEntitySettingsApplied } from '@/constants/ragEntityEvents';
import {
  ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY,
  ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY,
} from '@/constants/hotkeys';
import { OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY } from '@/utils/openAgentConstructorNav';
import { OPEN_SKILL_SIDEBAR_NAV_STATE_KEY } from '@/utils/openSkillSidebarNav';

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  mockLocation = { pathname: '/', state: null };
  mockRagStatus = null;
});

const status = (over: Record<string, unknown> = {}) => ({
  memory: { reindexing: false },
  project: { reindexing: false },
  kb: { reindexing: false },
  any_reindexing: false,
  agent_has_kb: false,
  project_has_documents: false,
  memory_rag_enabled: true,
  message: '',
  active: [],
  ...over,
});

describe('usePendingAgentConstructorOpen', () => {
  it('не открывает сайдбар, если нет ни state, ни отложенного id', () => {
    const open = jest.fn();
    renderHook(() => usePendingAgentConstructorOpen(open));
    expect(open).not.toHaveBeenCalled();
  });

  it('открывает сайдбар, если id пришёл в location.state', () => {
    mockLocation = { pathname: '/', state: { [OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY]: 12 } };
    const open = jest.fn();
    renderHook(() => usePendingAgentConstructorOpen(open));
    expect(open).toHaveBeenCalledTimes(1);
  });

  it('открывает сайдбар по id, отложенному в sessionStorage', () => {
    sessionStorage.setItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY, '5');
    const open = jest.fn();
    renderHook(() => usePendingAgentConstructorOpen(open));
    expect(open).toHaveBeenCalledTimes(1);
  });

  it('невалидный id (0 / не число) не открывает сайдбар', () => {
    mockLocation = { pathname: '/', state: { [OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY]: 0 } };
    sessionStorage.setItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY, 'abc');
    const open = jest.fn();
    renderHook(() => usePendingAgentConstructorOpen(open));
    expect(open).not.toHaveBeenCalled();
  });

  it('использует актуальный callback (ref), а не тот, что был при первом рендере', () => {
    const first = jest.fn();
    const second = jest.fn();
    const { rerender } = renderHook(({ cb }) => usePendingAgentConstructorOpen(cb), {
      initialProps: { cb: first },
    });
    mockLocation = { pathname: '/next', state: { [OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY]: 3 } };
    rerender({ cb: second });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it('повторно срабатывает при смене pathname', () => {
    mockLocation = { pathname: '/a', state: { [OPEN_AGENT_CONSTRUCTOR_NAV_STATE_KEY]: 3 } };
    const open = jest.fn();
    const { rerender } = renderHook(() => usePendingAgentConstructorOpen(open));
    expect(open).toHaveBeenCalledTimes(1);
    mockLocation = { pathname: '/b', state: mockLocation.state };
    rerender();
    expect(open).toHaveBeenCalledTimes(2);
  });
});

describe('usePendingSkillSidebarOpen', () => {
  it('без state и sessionStorage ничего не открывает', () => {
    const open = jest.fn();
    renderHook(() => usePendingSkillSidebarOpen(open));
    expect(open).not.toHaveBeenCalled();
  });

  it('открывает по id из location.state', () => {
    mockLocation = { pathname: '/', state: { [OPEN_SKILL_SIDEBAR_NAV_STATE_KEY]: 9 } };
    const open = jest.fn();
    renderHook(() => usePendingSkillSidebarOpen(open));
    expect(open).toHaveBeenCalledTimes(1);
  });

  it('открывает по id из sessionStorage', () => {
    sessionStorage.setItem(ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY, '4');
    const open = jest.fn();
    renderHook(() => usePendingSkillSidebarOpen(open));
    expect(open).toHaveBeenCalledTimes(1);
  });

  it('использует свежий callback при перерисовке с новым state', () => {
    const first = jest.fn();
    const second = jest.fn();
    const { rerender } = renderHook(({ cb }) => usePendingSkillSidebarOpen(cb), {
      initialProps: { cb: first },
    });
    mockLocation = { pathname: '/x', state: { [OPEN_SKILL_SIDEBAR_NAV_STATE_KEY]: 2 } };
    rerender({ cb: second });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});

describe('useRagReindexStatus (реэкспорты и useAgentRagRebuilding)', () => {
  it('реэкспортирует утилиты блокировки', () => {
    expect(typeof ragStatusModule.shouldBlockRagSend).toBe('function');
    expect(typeof ragStatusModule.ragReindexBlockMessage).toBe('function');
    expect(typeof ragStatusModule.isRagEntityRebuilding).toBe('function');
    expect(typeof ragStatusModule.isRagReindexBannerVisible).toBe('function');
    expect(typeof ragStatusModule.RAG_REINDEX_BLOCK_PLACEHOLDER).toBe('string');
    expect(typeof ragStatusModule.useRagReindexStatus).toBe('function');
  });

  it('agentId null/NaN → false независимо от статуса', () => {
    mockRagStatus = status({ active: [{ scope: 'agent', entity_id: '1' }] });
    expect(renderHook(() => useAgentRagRebuilding(null)).result.current).toBe(false);
    expect(renderHook(() => useAgentRagRebuilding(undefined)).result.current).toBe(false);
    expect(renderHook(() => useAgentRagRebuilding(NaN)).result.current).toBe(false);
  });

  it('true, если агент есть в active', () => {
    mockRagStatus = status({ active: [{ scope: 'agent', entity_id: '7' }] });
    expect(renderHook(() => useAgentRagRebuilding(7)).result.current).toBe(true);
    expect(renderHook(() => useAgentRagRebuilding(8)).result.current).toBe(false);
  });

  it('статус ещё не загружен → false', () => {
    mockRagStatus = null;
    expect(renderHook(() => useAgentRagRebuilding(7)).result.current).toBe(false);
  });
});

describe('useRagEntityReadyMessage', () => {
  const setup = (
    scope: 'agent' | 'project' = 'agent',
    id: string | number | null | undefined = 5,
    name?: string,
  ) => {
    const hook = renderHook(
      ({ s, i, n }) => useRagEntityReadyMessage(s, i, n),
      { initialProps: { s: scope, i: id, n: name } },
    );
    return hook;
  };

  it('сначала сообщения нет', () => {
    const { result } = setup();
    expect(result.current.readyMessage).toBeNull();
  });

  it('если перечанковки нет — сообщение сразу после применения настроек агента', () => {
    mockRagStatus = status();
    const { result } = setup('agent', 5, 'Юрист');
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: 5 }));
    expect(result.current.readyMessage).toBe(
      'Настройки РАГ применены — можно пользоваться агентом «Юрист» в чате.',
    );
  });

  it('без имени — обезличенный текст для агента и для проекта', () => {
    mockRagStatus = status();
    const agent = setup('agent', 1);
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: 1 }));
    expect(agent.result.current.readyMessage).toBe(
      'Настройки РАГ применены — можно пользоваться этим агентом в чате.',
    );
    const project = setup('project', 'p1');
    act(() => dispatchRagEntitySettingsApplied({ scope: 'project', entityId: 'p1' }));
    expect(project.result.current.readyMessage).toBe(
      'Настройки РАГ применены — можно пользоваться этим проектом.',
    );
  });

  it('для проекта с именем — вариант с «»', () => {
    mockRagStatus = status();
    const { result } = setup('project', 'p1', '  Альфа  ');
    act(() => dispatchRagEntitySettingsApplied({ scope: 'project', entityId: 'p1', entityName: 'Альфа' }));
    expect(result.current.readyMessage).toBe(
      'Настройки РАГ применены — можно пользоваться проектом «Альфа».',
    );
  });

  it('имя из события приоритетнее имени из аргументов', () => {
    mockRagStatus = status();
    const { result } = setup('agent', 5, 'Старое');
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: 5, entityName: 'Новое' }));
    expect(result.current.readyMessage).toContain('«Новое»');
  });

  it('пустое (пробельное) имя трактуется как отсутствие', () => {
    mockRagStatus = status();
    const { result } = setup('agent', 5, '   ');
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: 5 }));
    expect(result.current.readyMessage).toBe(
      'Настройки РАГ применены — можно пользоваться этим агентом в чате.',
    );
  });

  it('игнорирует события другого scope или другой сущности', () => {
    mockRagStatus = status();
    const { result } = setup('agent', 5);
    act(() => dispatchRagEntitySettingsApplied({ scope: 'project', entityId: 5 }));
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: 6 }));
    expect(result.current.readyMessage).toBeNull();
  });

  it('игнорирует событие без detail', () => {
    mockRagStatus = status();
    const { result } = setup('agent', 5);
    act(() => {
      window.dispatchEvent(new CustomEvent('astrachat:rag-entity-settings-applied'));
    });
    expect(result.current.readyMessage).toBeNull();
  });

  it('без entityId событие не обрабатывается', () => {
    mockRagStatus = status();
    const { result } = setup('agent', null);
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: '' }));
    expect(result.current.readyMessage).toBeNull();
    const second = setup('agent', '');
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: 5 }));
    expect(second.result.current.readyMessage).toBeNull();
  });

  it('если идёт перечанковка — ждёт её завершения и только потом показывает сообщение', () => {
    mockRagStatus = status({ active: [{ scope: 'agent', entity_id: '5' }] });
    const { result, rerender } = setup('agent', 5, 'Юрист');
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: 5 }));
    expect(result.current.readyMessage).toBeNull();

    mockRagStatus = status({ active: [] });
    rerender({ s: 'agent', i: 5, n: 'Юрист' });
    expect(result.current.readyMessage).toBe(
      'Настройки РАГ применены — можно пользоваться агентом «Юрист» в чате.',
    );
  });

  it('пока перечанковка идёт, смена статуса на другой «занятый» не показывает сообщение', () => {
    mockRagStatus = status({ active: [{ scope: 'agent', entity_id: '5' }] });
    const { result, rerender } = setup('agent', 5);
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: 5 }));
    mockRagStatus = status({ active: [{ scope: 'agent', entity_id: '5' }, { scope: 'project', entity_id: 'x' }] });
    rerender({ s: 'agent', i: 5, n: undefined });
    expect(result.current.readyMessage).toBeNull();
  });

  it('без ожидающего применения смена статуса сообщение не создаёт', () => {
    mockRagStatus = status({ active: [{ scope: 'agent', entity_id: '5' }] });
    const { result, rerender } = setup('agent', 5);
    mockRagStatus = status();
    rerender({ s: 'agent', i: 5, n: undefined });
    expect(result.current.readyMessage).toBeNull();
  });

  it('clearReadyMessage очищает сообщение', () => {
    mockRagStatus = status();
    const { result } = setup('agent', 5);
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: 5 }));
    expect(result.current.readyMessage).not.toBeNull();
    act(() => result.current.clearReadyMessage());
    expect(result.current.readyMessage).toBeNull();
  });

  it('смена сущности сбрасывает сообщение и ожидание', () => {
    mockRagStatus = status();
    const { result, rerender } = setup('agent', 5);
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: 5 }));
    expect(result.current.readyMessage).not.toBeNull();
    rerender({ s: 'agent', i: 6, n: undefined });
    expect(result.current.readyMessage).toBeNull();
  });

  it('снимает подписку при размонтировании', () => {
    mockRagStatus = status();
    const { result, unmount } = setup('agent', 5);
    unmount();
    act(() => dispatchRagEntitySettingsApplied({ scope: 'agent', entityId: 5 }));
    expect(result.current.readyMessage).toBeNull();
  });

  it('обновление entityName без события не вызывает сообщения', () => {
    mockRagStatus = status();
    const { result, rerender } = setup('agent', 5, 'A');
    rerender({ s: 'agent', i: 5, n: 'B' });
    expect(result.current.readyMessage).toBeNull();
  });
});

// Чтобы React не ругался на неиспользуемый импорт в JSX-режиме.
void React;
