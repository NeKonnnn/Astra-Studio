import React from 'react';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { hx, resetHarness, installFetch } from '@/pages/__test_support__/unifiedChatHarness';
import UnifiedChatPage from '@/pages/UnifiedChatPage';

function renderPage() {
  return render(<UnifiedChatPage isDarkMode={false} />);
}
const openMenu = () => fireEvent.click(screen.getByTestId('ib-settings'));

let roInstances: Array<{ cb: () => void; observe: jest.Mock; disconnect: jest.Mock }>;

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetHarness();
  installFetch();
  roInstances = [];
  (global as any).ResizeObserver = class {
    cb: () => void;
    observe = jest.fn();
    disconnect = jest.fn();
    constructor(cb: () => void) {
      this.cb = cb;
      roInstances.push(this as any);
    }
  };
});

afterEach(() => {
  delete (global as any).ResizeObserver;
});

describe('UnifiedChatPage: меню «Инструменты»', () => {
  it('открывается по кнопке настроек и показывает пункты левой колонки', () => {
    renderPage();
    expect(screen.queryByText('Агенты')).toBeNull();
    openMenu();
    for (const label of ['Агенты', 'Артефакты', 'Skills', 'MCP', 'Базы знаний', 'Режим модели']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByTestId('gear-agents')).toBeNull();
  });

  it('следит за размером пилюли через ResizeObserver и отключает наблюдателя при закрытии', async () => {
    renderPage();
    openMenu();
    expect(roInstances).toHaveLength(1);
    expect(roInstances[0].observe).toHaveBeenCalled();
    await act(async () => {
      roInstances[0].cb();
      await Promise.resolve();
    });
    fireEvent.keyDown(screen.getByText('Агенты'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('Агенты')).toBeNull());
    expect(roInstances[0].disconnect).toHaveBeenCalled();
  });

  it('без ResizeObserver меню всё равно работает', () => {
    delete (global as any).ResizeObserver;
    renderPage();
    openMenu();
    expect(screen.getByText('Агенты')).toBeInTheDocument();
  });

  it.each([
    ['Агенты', 'gear-agents'],
    ['Артефакты', 'gear-artifacts'],
    ['Skills', 'gear-skills'],
    ['Базы знаний', 'gear-kb'],
    ['MCP', 'gear-mcp'],
  ])('пункт «%s» раскрывает правую панель и сворачивает её повторным кликом', (label, testId) => {
    renderPage();
    openMenu();
    fireEvent.click(screen.getByText(label));
    expect(screen.getByTestId(testId)).toBeInTheDocument();
    fireEvent.click(screen.getByText(label));
    expect(screen.queryByTestId(testId)).toBeNull();
  });

  it('панели артефактов/skills/MCP получают id чата', () => {
    renderPage();
    openMenu();
    fireEvent.click(screen.getByText('Артефакты'));
    expect(screen.getByTestId('gear-artifacts')).toHaveAttribute('data-chat', 'c1');
    fireEvent.click(screen.getByText('Skills'));
    expect(screen.getByTestId('gear-skills')).toHaveAttribute('data-chat', 'c1');
    fireEvent.click(screen.getByText('MCP'));
    expect(screen.getByTestId('gear-mcp')).toHaveAttribute('data-chat', 'c1');
  });

  it('«Режим модели» выбирает режим, сохраняет в localStorage и уведомляет', () => {
    renderPage();
    openMenu();
    fireEvent.click(screen.getByText('Режим модели'));
    expect(localStorage.getItem('model_thinking_mode')).toBe('fast');
    fireEvent.click(screen.getByText('Мышление'));
    expect(localStorage.getItem('model_thinking_mode')).toBe('thinking');
    expect(hx.actions.showNotification).toHaveBeenCalledWith('info', 'Режим модели: Мышление');
    fireEvent.click(screen.getByText('Автоматический'));
    expect(localStorage.getItem('model_thinking_mode')).toBe('auto');
    fireEvent.click(screen.getByText('Быстрый'));
    expect(localStorage.getItem('model_thinking_mode')).toBe('fast');
    expect(hx.actions.showNotification).toHaveBeenLastCalledWith('info', 'Режим модели: Быстрый');
  });

  it('режим модели восстанавливается из localStorage, мусор заменяется на fast', () => {
    localStorage.setItem('model_thinking_mode', 'thinking');
    const { unmount } = renderPage();
    expect(localStorage.getItem('model_thinking_mode')).toBe('thinking');
    unmount();
    localStorage.setItem('model_thinking_mode', 'bogus');
    renderPage();
    expect(localStorage.getItem('model_thinking_mode')).toBe('fast');
  });

  it('миграция на базы знаний: пункта «Общий RAG» в меню нет, флаг библиотеки не меняется', () => {
    renderPage();
    openMenu();
    expect(screen.queryByText('Общий RAG')).toBeNull();
    expect(screen.queryByText('Отключить общий RAG')).toBeNull();
    expect(localStorage.getItem('use_kb_rag')).toBeNull();
    expect(screen.getByTestId('cluster')).toHaveAttribute('data-library', 'false');
  });

  it('панель «Базы знаний» получает id чата', () => {
    renderPage();
    openMenu();
    fireEvent.click(screen.getByText('Базы знаний'));
    expect(screen.getByTestId('gear-kb')).toHaveAttribute('data-chat', 'c1');
  });

  it('клик вне меню (Escape) закрывает его и сбрасывает раскрытую панель', async () => {
    renderPage();
    openMenu();
    fireEvent.click(screen.getByText('Агенты'));
    expect(screen.getByTestId('gear-agents')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByText('Агенты'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('Агенты')).toBeNull());
    openMenu();
    expect(screen.queryByTestId('gear-agents')).toBeNull();
  });

  it('кнопка меню отключена, пока ждём первый токен', () => {
    hx.state.loadingChatIds = ['c1'];
    renderPage();
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-settings-disabled', 'true');
  });
});
