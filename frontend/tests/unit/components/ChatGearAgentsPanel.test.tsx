/**
 * @jest-environment jsdom
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import ChatGearAgentsPanel from '@/components/ChatGearAgentsPanel';

jest.mock('@/components/ChatGearMyAgentsTab', () => ({
  __esModule: true,
  default: ({ visible }: { visible: boolean }) =>
    visible ? <div data-testid="my-agents-tab">my</div> : null,
}));

jest.mock('@/components/ChatGearGalleryAgentsTab', () => ({
  __esModule: true,
  default: ({ visible }: { visible: boolean }) =>
    visible ? <div data-testid="gallery-agents-tab">gallery</div> : null,
}));

jest.mock('@/components/AgentSelector', () => ({
  getActiveAgentFromStorage: () => ({ id: 1, name: 'A', system_prompt: '' }),
}));

jest.mock('@/utils/clearActiveAgent', () => ({
  clearActiveAgent: jest.fn(),
}));

describe('ChatGearAgentsPanel', () => {
  it('has search, my/gallery tabs and reset selection when agent active', () => {
    render(<ChatGearAgentsPanel isDarkMode={false} />);
    expect(screen.getByPlaceholderText('Поиск агентов...')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Мои агенты' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Агенты из галереи' })).toBeInTheDocument();
    expect(screen.getByTestId('my-agents-tab')).toBeInTheDocument();
    expect(screen.getByText('Сбросить выбор (1)')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Агенты из галереи' }));
    expect(screen.getByTestId('gallery-agents-tab')).toBeInTheDocument();
  });
});
