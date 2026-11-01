import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import ChatGearAgentsPanel from '@/components/ChatGearAgentsPanel';
import ChatGearArtifactsPanel from '@/components/ChatGearArtifactsPanel';
import {
  ARTIFACTS_SELECTION_CHANGED_EVENT,
  getChatArtifactsToolsState,
  setChatArtifactsToolMode,
} from '@/utils/artifactsSelectionStorage';
import { AGENT_ARTIFACTS_CHANGED_EVENT } from '@/utils/agentArtifactsEnabled';

let mockCanUseAgents = true;
jest.mock('@/hooks/useFeatureAccess', () => ({
  useFeatureAccess: () => ({ canUseAgents: mockCanUseAgents }),
}));
jest.mock('@/components/ChatGearMyAgentsTab', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="my-tab">{`my|${p.searchQuery}|${p.visible}|${p.isDarkMode}`}</div>,
}));
jest.mock('@/components/ChatGearGalleryAgentsTab', () => ({
  __esModule: true,
  default: (p: any) => <div data-testid="gallery-tab">{`gallery|${p.searchQuery}|${p.visible}`}</div>,
}));

describe('ChatGearAgentsPanel', () => {
  beforeEach(() => {
    mockCanUseAgents = true;
  });

  it('без доступа к агентам показывает сообщение', () => {
    mockCanUseAgents = false;
    render(<ChatGearAgentsPanel isDarkMode={false} />);
    expect(screen.getByText('Агенты недоступны для вашей роли.')).toBeInTheDocument();
    expect(screen.queryByTestId('my-tab')).toBeNull();
  });

  it('по умолчанию открыта вкладка «Мои агенты»; поиск передаётся во вкладку', () => {
    render(<ChatGearAgentsPanel isDarkMode />);
    expect(screen.getByTestId('my-tab')).toHaveTextContent('my||true|true');
    fireEvent.change(screen.getByPlaceholderText('Поиск агентов...'), { target: { value: 'аналитик' } });
    expect(screen.getByTestId('my-tab')).toHaveTextContent('my|аналитик|true|true');
  });

  it('переключение на галерею сохраняет поиск; повторный выбор активной вкладки не сбрасывает её', () => {
    render(<ChatGearAgentsPanel isDarkMode={false} />);
    fireEvent.change(screen.getByPlaceholderText('Поиск агентов...'), { target: { value: 'q' } });
    fireEvent.click(screen.getByText('Агенты из галереи'));
    expect(screen.getByTestId('gallery-tab')).toHaveTextContent('gallery|q|true');
    expect(screen.queryByTestId('my-tab')).toBeNull();
    fireEvent.click(screen.getByText('Агенты из галереи'));
    expect(screen.getByTestId('gallery-tab')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Мои агенты'));
    expect(screen.getByTestId('my-tab')).toBeInTheDocument();
  });

  it('потеря и возврат доступа во время работы не ломают порядок хуков (BUG-FE-73)', () => {
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    const { rerender } = render(<ChatGearAgentsPanel isDarkMode />);
    mockCanUseAgents = false;
    expect(() => rerender(<ChatGearAgentsPanel isDarkMode />)).not.toThrow();
    expect(screen.getByText('Агенты недоступны для вашей роли.')).toBeInTheDocument();
    mockCanUseAgents = true;
    expect(() => rerender(<ChatGearAgentsPanel isDarkMode />)).not.toThrow();
    expect(screen.queryByText('Агенты недоступны для вашей роли.')).toBeNull();
    err.mockRestore();
  });
});

describe('ChatGearArtifactsPanel', () => {
  beforeEach(() => localStorage.clear());

  it('без чата показывается подсказка', () => {
    render(<ChatGearArtifactsPanel isDarkMode={false} />);
    expect(screen.getByText(/Выберите или создайте чат/)).toBeInTheDocument();
    expect(screen.queryByRole('switch')).toBeNull();
  });

  it('два переключателя отражают сохранённые режимы чата', () => {
    setChatArtifactsToolMode('c1', 'shadcn_enabled', true);
    render(<ChatGearArtifactsPanel isDarkMode chatId="c1" />);
    const [shadcn, userPrompt] = screen.getAllByRole('switch');
    expect(shadcn).toBeChecked();
    expect(userPrompt).toBeDisabled();
    expect(userPrompt).not.toBeChecked();
  });

  it('включение режима сохраняется для чата и блокирует взаимоисключающий', () => {
    render(<ChatGearArtifactsPanel isDarkMode={false} chatId="c2" />);
    const [shadcn, userPrompt] = screen.getAllByRole('switch');
    expect(shadcn).not.toBeDisabled();
    fireEvent.click(userPrompt);
    expect(getChatArtifactsToolsState('c2').user_prompt_mode).toBe(true);
    expect(screen.getAllByRole('switch')[0]).toBeDisabled();
    fireEvent.click(screen.getAllByRole('switch')[1]);
    expect(getChatArtifactsToolsState('c2').user_prompt_mode).toBe(false);
    expect(screen.getAllByRole('switch')[0]).not.toBeDisabled();
  });

  it('события выбора перечитывают состояние; чужой чат игнорируется', () => {
    render(<ChatGearArtifactsPanel isDarkMode chatId="c3" />);
    act(() => {
      setChatArtifactsToolMode('c3', 'shadcn_enabled', true);
      window.dispatchEvent(new CustomEvent(ARTIFACTS_SELECTION_CHANGED_EVENT, { detail: { chatId: 'c3' } }));
    });
    expect(screen.getAllByRole('switch')[0]).toBeChecked();
    act(() => {
      localStorage.clear();
      window.dispatchEvent(new CustomEvent(ARTIFACTS_SELECTION_CHANGED_EVENT, { detail: { chatId: 'other' } }));
    });
    expect(screen.getAllByRole('switch')[0]).toBeChecked();
    act(() => {
      window.dispatchEvent(new CustomEvent(ARTIFACTS_SELECTION_CHANGED_EVENT));
    });
    expect(screen.getAllByRole('switch')[0]).not.toBeChecked();
  });

  it('события смены агента и storage тоже синхронизируют состояние', () => {
    render(<ChatGearArtifactsPanel isDarkMode chatId="c4" />);
    for (const evt of [AGENT_ARTIFACTS_CHANGED_EVENT, 'agentSelected', 'storage']) {
      act(() => {
        localStorage.clear();
        setChatArtifactsToolMode('c4', 'user_prompt_mode', true);
        window.dispatchEvent(new Event(evt));
      });
      expect(screen.getAllByRole('switch')[1]).toBeChecked();
      act(() => {
        localStorage.clear();
        window.dispatchEvent(new Event(evt));
      });
      expect(screen.getAllByRole('switch')[1]).not.toBeChecked();
    }
  });

  it('после размонтирования слушатели сняты', () => {
    const { unmount } = render(<ChatGearArtifactsPanel isDarkMode chatId="c5" />);
    unmount();
    expect(() => window.dispatchEvent(new Event('storage'))).not.toThrow();
  });
});
