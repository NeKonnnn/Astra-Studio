import { act, renderHook } from '@testing-library/react';
import {
  contentImpliesLegacyArtifactsViewer,
  useArtifactsViewerAllowed,
  useArtifactsViewerLiveGate,
  usePresentationViewerExpected,
} from '@/hooks/useArtifactsViewerGate';
import {
  AGENT_ARTIFACTS_CHANGED_EVENT,
  AGENT_ARTIFACTS_ENABLED_KEY,
  AGENT_PRESENTATION_SKILLS_KEY,
} from '@/utils/agentArtifactsEnabled';
import { setActiveSkillRefs } from '@/utils/skillSelectionStorage';
import { setChatArtifactsToolMode } from '@/utils/artifactsSelectionStorage';
import { pinMessageArtifactsViewer } from '@/utils/messageArtifactsViewerStorage';

beforeEach(() => {
  localStorage.clear();
});

describe('contentImpliesLegacyArtifactsViewer', () => {
  it('пусто / null → false', () => {
    expect(contentImpliesLegacyArtifactsViewer('')).toBe(false);
    expect(contentImpliesLegacyArtifactsViewer(null)).toBe(false);
    expect(contentImpliesLegacyArtifactsViewer(undefined)).toBe(false);
  });

  it('распознаёт :::artifact{ и slide-класс', () => {
    expect(contentImpliesLegacyArtifactsViewer(':::artifact{id="1"}\n<div/>\n:::')).toBe(true);
    expect(contentImpliesLegacyArtifactsViewer('<section class="slide">x</section>')).toBe(true);
  });

  it('обычный текст → false', () => {
    expect(contentImpliesLegacyArtifactsViewer('просто текст')).toBe(false);
  });
});

describe('useArtifactsViewerAllowed', () => {
  it('по умолчанию viewer запрещён', () => {
    const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1', messageId: 'm1' }));
    expect(result.current).toBe(false);
  });

  it('вызов без аргументов тоже безопасен', () => {
    const { result } = renderHook(() => useArtifactsViewerAllowed());
    expect(result.current).toBe(false);
  });

  it('force всегда разрешает', () => {
    const { result } = renderHook(() => useArtifactsViewerAllowed({ force: true }));
    expect(result.current).toBe(true);
  });

  it('разрешает по artifacts_enabled агента (начальное значение)', () => {
    localStorage.setItem(AGENT_ARTIFACTS_ENABLED_KEY, '1');
    const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1' }));
    expect(result.current).toBe(true);
  });

  it('разрешает по presentation-skill агента', () => {
    localStorage.setItem(AGENT_PRESENTATION_SKILLS_KEY, '1');
    const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1' }));
    expect(result.current).toBe(true);
  });

  it('разрешает по skill чата (mermaid) и реагирует на смену skills', () => {
    const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1' }));
    expect(result.current).toBe(false);
    act(() => setActiveSkillRefs('c1', [{ slug: 'mermaid-diagrams' }]));
    expect(result.current).toBe(true);
    act(() => setActiveSkillRefs('c1', []));
    expect(result.current).toBe(false);
  });

  it('skill другого чата не влияет', () => {
    const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1' }));
    localStorage.setItem('chat:c2:active_skill_ids', JSON.stringify(['mermaid']));
    act(() => {
      window.dispatchEvent(
        new CustomEvent('astrachatSkillSelectionChanged', { detail: { chatId: 'c2' } }),
      );
    });
    expect(result.current).toBe(false);
  });

  it('разрешает по режиму артефактов в инструментах чата', () => {
    const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1' }));
    act(() => {
      setChatArtifactsToolMode('c1', 'shadcn_enabled', true);
    });
    expect(result.current).toBe(true);
  });

  it('без chatId режим артефактов чата не включается', () => {
    localStorage.setItem('active_agent_shadcn_enabled', '1');
    const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: null }));
    expect(result.current).toBe(false);
  });

  it('событие артефактов другого чата игнорируется', () => {
    const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1' }));
    localStorage.setItem('chat:c1:artifacts_settings', JSON.stringify({ shadcn_enabled: true }));
    act(() => {
      window.dispatchEvent(
        new CustomEvent('astrachatArtifactsSelectionChanged', { detail: { chatId: 'zzz' } }),
      );
    });
    expect(result.current).toBe(false);
  });

  it('закреплённое (pinned) сообщение остаётся с viewer', () => {
    const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1', messageId: 'm1' }));
    expect(result.current).toBe(false);
    act(() => pinMessageArtifactsViewer('m1'));
    expect(result.current).toBe(true);
  });

  it('повторный storage не меняет pinned, если значение то же', () => {
    pinMessageArtifactsViewer('m1');
    const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1', messageId: 'm1' }));
    act(() => {
      window.dispatchEvent(new Event('storage'));
    });
    expect(result.current).toBe(true);
  });

  it('контент с :::artifact даёт viewer без остальных условий', () => {
    const { result, rerender } = renderHook(
      ({ content }) => useArtifactsViewerAllowed({ chatId: 'c1', content }),
      { initialProps: { content: 'текст' } },
    );
    expect(result.current).toBe(false);
    rerender({ content: ':::artifact{id="a"}\nx\n:::' });
    expect(result.current).toBe(true);
  });

  describe('agentSelected', () => {
    it('detail=null сбрасывает хранилище агента и запрещает viewer', () => {
      localStorage.setItem(AGENT_ARTIFACTS_ENABLED_KEY, '1');
      const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1' }));
      expect(result.current).toBe(true);
      act(() => {
        window.dispatchEvent(new CustomEvent('agentSelected', { detail: null }));
      });
      expect(result.current).toBe(false);
      expect(localStorage.getItem(AGENT_ARTIFACTS_ENABLED_KEY)).toBeNull();
    });

    it('detail с config.artifacts_enabled включает viewer', () => {
      const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1' }));
      act(() => {
        window.dispatchEvent(
          new CustomEvent('agentSelected', { detail: { config: { artifacts_enabled: true } } }),
        );
      });
      expect(result.current).toBe(true);
    });

    it('config с presentation skill_ids включает viewer', () => {
      const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1' }));
      act(() => {
        window.dispatchEvent(
          new CustomEvent('agentSelected', { detail: { config: { skill_ids: ['pptx-maker'] } } }),
        );
      });
      expect(result.current).toBe(true);
      expect(localStorage.getItem(AGENT_PRESENTATION_SKILLS_KEY)).toBe('1');
    });

    it('detail без config берёт состояние из хранилища', () => {
      const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1' }));
      localStorage.setItem(AGENT_ARTIFACTS_ENABLED_KEY, '1');
      act(() => {
        window.dispatchEvent(new CustomEvent('agentSelected', { detail: { id: 3 } }));
      });
      expect(result.current).toBe(true);
    });
  });

  it('AGENT_ARTIFACTS_CHANGED_EVENT и storage синхронизируют агента', () => {
    const { result } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1' }));
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

  it('снимает подписки при размонтировании', () => {
    const removeSpy = jest.spyOn(window, 'removeEventListener');
    const { unmount } = renderHook(() => useArtifactsViewerAllowed({ chatId: 'c1', messageId: 'm' }));
    unmount();
    const removed = removeSpy.mock.calls.map((c) => c[0]);
    expect(removed).toEqual(
      expect.arrayContaining([
        'agentSelected',
        AGENT_ARTIFACTS_CHANGED_EVENT,
        'storage',
        'astrachatSkillSelectionChanged',
        'astrachatArtifactsSelectionChanged',
        'astrachatMessageArtifactsViewerChanged',
      ]),
    );
    removeSpy.mockRestore();
  });
});

describe('useArtifactsViewerLiveGate', () => {
  it('по умолчанию false', () => {
    const { result } = renderHook(() => useArtifactsViewerLiveGate('c1'));
    expect(result.current).toBe(false);
  });

  it('agentSelected: config включает, detail=null выключает', () => {
    const { result } = renderHook(() => useArtifactsViewerLiveGate('c1'));
    act(() => {
      window.dispatchEvent(
        new CustomEvent('agentSelected', { detail: { config: { artifacts_enabled: true } } }),
      );
    });
    expect(result.current).toBe(true);
    act(() => {
      window.dispatchEvent(new CustomEvent('agentSelected', { detail: null }));
    });
    expect(result.current).toBe(false);
  });

  it('agentSelected без config синхронизируется с хранилищем', () => {
    const { result } = renderHook(() => useArtifactsViewerLiveGate('c1'));
    localStorage.setItem(AGENT_ARTIFACTS_ENABLED_KEY, '1');
    act(() => {
      window.dispatchEvent(new CustomEvent('agentSelected', { detail: {} }));
    });
    expect(result.current).toBe(true);
  });

  it('учитывает skills чата и игнорирует чужой чат', () => {
    const { result } = renderHook(() => useArtifactsViewerLiveGate('c1'));
    act(() => setActiveSkillRefs('c1', [{ slug: 'visual-art' }]));
    expect(result.current).toBe(true);
    act(() => setActiveSkillRefs('c1', []));
    expect(result.current).toBe(false);
    localStorage.setItem('chat:c1:active_skill_ids', JSON.stringify(['visual-art']));
    act(() => {
      window.dispatchEvent(
        new CustomEvent('astrachatSkillSelectionChanged', { detail: { chatId: 'other' } }),
      );
    });
    expect(result.current).toBe(false);
  });

  it('учитывает режим артефактов чата и игнорирует чужой чат', () => {
    const { result } = renderHook(() => useArtifactsViewerLiveGate('c1'));
    act(() => {
      setChatArtifactsToolMode('c1', 'user_prompt_mode', true);
    });
    expect(result.current).toBe(true);
    act(() => {
      setChatArtifactsToolMode('c1', 'user_prompt_mode', false);
    });
    expect(result.current).toBe(false);
    localStorage.setItem('chat:c1:artifacts_settings', JSON.stringify({ shadcn_enabled: true }));
    act(() => {
      window.dispatchEvent(
        new CustomEvent('astrachatArtifactsSelectionChanged', { detail: { chatId: 'other' } }),
      );
    });
    expect(result.current).toBe(false);
  });

  it('AGENT_ARTIFACTS_CHANGED_EVENT обновляет агентский гейт; размонтирование отписывает', () => {
    const { result, unmount } = renderHook(() => useArtifactsViewerLiveGate('c1'));
    localStorage.setItem(AGENT_ARTIFACTS_ENABLED_KEY, '1');
    act(() => {
      window.dispatchEvent(new Event(AGENT_ARTIFACTS_CHANGED_EVENT));
    });
    expect(result.current).toBe(true);
    unmount();
    localStorage.removeItem(AGENT_ARTIFACTS_ENABLED_KEY);
    act(() => {
      window.dispatchEvent(new Event(AGENT_ARTIFACTS_CHANGED_EVENT));
    });
    expect(result.current).toBe(true); // последнее значение до размонтирования
  });
});

describe('usePresentationViewerExpected', () => {
  it('по умолчанию false', () => {
    const { result } = renderHook(() => usePresentationViewerExpected('c1'));
    expect(result.current).toBe(false);
  });

  it('presentation-skill чата включает ожидание', () => {
    const { result } = renderHook(() => usePresentationViewerExpected('c1'));
    act(() => setActiveSkillRefs('c1', [{ slug: 'html-presentation' }]));
    expect(result.current).toBe(true);
  });

  it('mermaid-skill — это не презентация', () => {
    const { result } = renderHook(() => usePresentationViewerExpected('c1'));
    act(() => setActiveSkillRefs('c1', [{ slug: 'mermaid' }]));
    expect(result.current).toBe(false);
  });

  it('если выбран агент без presentation-skill, skills чата игнорируются', () => {
    localStorage.setItem('active_agent_id', '5');
    setActiveSkillRefs('c1', [{ slug: 'pptx' }]);
    const { result } = renderHook(() => usePresentationViewerExpected('c1'));
    expect(result.current).toBe(false);
  });

  it('если у агента есть presentation-skill, skills чата работают', () => {
    localStorage.setItem('active_agent_id', '5');
    localStorage.setItem(AGENT_PRESENTATION_SKILLS_KEY, '1');
    const { result } = renderHook(() => usePresentationViewerExpected('c1'));
    expect(result.current).toBe(true);
  });

  it('agentSelected: config со skill → true; null → false; без config → из хранилища', () => {
    const { result } = renderHook(() => usePresentationViewerExpected('c1'));
    act(() => {
      window.dispatchEvent(
        new CustomEvent('agentSelected', { detail: { config: { skill_ids: ['slides'] } } }),
      );
    });
    expect(result.current).toBe(true);
    act(() => {
      window.dispatchEvent(new CustomEvent('agentSelected', { detail: null }));
    });
    expect(result.current).toBe(false);
    localStorage.setItem(AGENT_PRESENTATION_SKILLS_KEY, '1');
    act(() => {
      window.dispatchEvent(new CustomEvent('agentSelected', { detail: { id: 1 } }));
    });
    expect(result.current).toBe(true);
  });

  it('AGENT_ARTIFACTS_CHANGED_EVENT обновляет значение агента', () => {
    const { result } = renderHook(() => usePresentationViewerExpected('c1'));
    localStorage.setItem(AGENT_PRESENTATION_SKILLS_KEY, '1');
    act(() => {
      window.dispatchEvent(new Event(AGENT_ARTIFACTS_CHANGED_EVENT));
    });
    expect(result.current).toBe(true);
  });

  it('событие skills другого чата игнорируется', () => {
    const { result } = renderHook(() => usePresentationViewerExpected('c1'));
    localStorage.setItem('chat:c1:active_skill_ids', JSON.stringify(['pptx']));
    act(() => {
      window.dispatchEvent(
        new CustomEvent('astrachatSkillSelectionChanged', { detail: { chatId: 'other' } }),
      );
    });
    expect(result.current).toBe(false);
  });

  it('снимает подписки при размонтировании', () => {
    const removeSpy = jest.spyOn(window, 'removeEventListener');
    const { unmount } = renderHook(() => usePresentationViewerExpected('c1'));
    unmount();
    const removed = removeSpy.mock.calls.map((c) => c[0]);
    expect(removed).toEqual(
      expect.arrayContaining(['agentSelected', AGENT_ARTIFACTS_CHANGED_EVENT, 'astrachatSkillSelectionChanged']),
    );
    removeSpy.mockRestore();
  });
});
