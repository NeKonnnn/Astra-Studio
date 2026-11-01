import React from 'react';
import { act, renderHook } from '@testing-library/react';
import {
  ArtifactProvider,
  useArtifactContext,
  useOptionalArtifactContext,
} from '@/contexts/ArtifactContext';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ArtifactProvider>{children}</ArtifactProvider>
);

const art = (over: Record<string, unknown> = {}): any => ({
  id: 'a1',
  identifier: 'doc',
  type: 'text/html',
  title: 'Док',
  content: '<p>x</p>',
  closed: false,
  ...over,
});

describe('ArtifactContext', () => {
  it('начальное состояние: ничего не открыто, вкладка preview', () => {
    const { result } = renderHook(() => useArtifactContext(), { wrapper });
    expect(result.current.current).toBeNull();
    expect(result.current.isOpen).toBe(false);
    expect(result.current.tab).toBe('preview');
  });

  it('openArtifact для незакрытого блока открывает вкладку code', () => {
    const { result } = renderHook(() => useArtifactContext(), { wrapper });
    act(() => result.current.openArtifact(art()));
    expect(result.current.isOpen).toBe(true);
    expect(result.current.current?.id).toBe('a1');
    expect(result.current.tab).toBe('code');
  });

  it('openArtifact для закрытого блока открывает preview', () => {
    const { result } = renderHook(() => useArtifactContext(), { wrapper });
    act(() => result.current.openArtifact(art({ closed: true })));
    expect(result.current.tab).toBe('preview');
  });

  it('явная вкладка из opts приоритетнее эвристики', () => {
    const { result } = renderHook(() => useArtifactContext(), { wrapper });
    act(() => result.current.openArtifact(art({ closed: true }), { tab: 'code' }));
    expect(result.current.tab).toBe('code');
  });

  it('updateArtifact обновляет контент того же артефакта', () => {
    const { result } = renderHook(() => useArtifactContext(), { wrapper });
    act(() => result.current.openArtifact(art()));
    act(() => result.current.updateArtifact(art({ content: '<p>y</p>', closed: true })));
    expect(result.current.current?.content).toBe('<p>y</p>');
    expect(result.current.current?.closed).toBe(true);
  });

  it('updateArtifact игнорирует другой id и отсутствие текущего артефакта', () => {
    const { result } = renderHook(() => useArtifactContext(), { wrapper });
    act(() => result.current.updateArtifact(art()));
    expect(result.current.current).toBeNull();
    act(() => result.current.openArtifact(art()));
    const before = result.current.current;
    act(() => result.current.updateArtifact(art({ id: 'other', content: 'zzz' })));
    expect(result.current.current).toBe(before);
  });

  it('updateArtifact без изменений контента/closed сохраняет ту же ссылку', () => {
    const { result } = renderHook(() => useArtifactContext(), { wrapper });
    act(() => result.current.openArtifact(art()));
    const before = result.current.current;
    act(() => result.current.updateArtifact(art({ title: 'Другой заголовок' })));
    expect(result.current.current).toBe(before);
  });

  it('closeArtifact скрывает панель, но сохраняет артефакт', () => {
    const { result } = renderHook(() => useArtifactContext(), { wrapper });
    act(() => result.current.openArtifact(art()));
    act(() => result.current.closeArtifact());
    expect(result.current.isOpen).toBe(false);
    expect(result.current.current?.id).toBe('a1');
  });

  it('setTab переключает вкладку', () => {
    const { result } = renderHook(() => useArtifactContext(), { wrapper });
    act(() => result.current.setTab('code'));
    expect(result.current.tab).toBe('code');
  });

  it('useArtifactContext вне провайдера бросает понятную ошибку', () => {
    const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => renderHook(() => useArtifactContext())).toThrow(
      'useArtifactContext must be used within ArtifactProvider',
    );
    spy.mockRestore();
  });

  it('useOptionalArtifactContext вне провайдера возвращает null, внутри — значение', () => {
    expect(renderHook(() => useOptionalArtifactContext()).result.current).toBeNull();
    const { result } = renderHook(() => useOptionalArtifactContext(), { wrapper });
    expect(result.current?.isOpen).toBe(false);
  });
});
