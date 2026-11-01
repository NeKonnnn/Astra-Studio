/**
 * @jest-environment jsdom
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import ProjectKnowledgeBasesField from '@/components/ProjectKnowledgeBasesField';

const mockAuthFetch = jest.fn();
const mockOnEnabledChange = jest.fn();
const mockOnSelectedIdsChange = jest.fn();

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ token: 't' }),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => mockAuthFetch(...a),
  getApiUrl: (p: string) => p,
  API_ENDPOINTS: { KNOWLEDGE_BASES: '/api/knowledge-bases' },
}));

beforeEach(() => {
  jest.clearAllMocks();
  mockAuthFetch.mockResolvedValue({
    ok: true,
    json: async () => ({
      items: [
        { id: 1, name: 'База А' },
        { id: 2, name: 'База Б' },
      ],
    }),
  });
});

function setup(props: Partial<React.ComponentProps<typeof ProjectKnowledgeBasesField>> = {}) {
  return render(
    <ProjectKnowledgeBasesField
      enabled={false}
      selectedIds={[]}
      onEnabledChange={mockOnEnabledChange}
      onSelectedIdsChange={mockOnSelectedIdsChange}
      {...props}
    />,
  );
}

describe('ProjectKnowledgeBasesField', () => {
  it('чекбокс включает БЗ', async () => {
    setup();
    fireEvent.click(screen.getByRole('checkbox'));
    expect(mockOnEnabledChange).toHaveBeenCalledWith(true);
  });

  it('грузит список БЗ и позволяет выбрать несколько', async () => {
    setup({ enabled: true, selectedIds: [1] });
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByDisplayValue('База А')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('Базы знаний'));
    await waitFor(() => expect(screen.getByText('База Б')).toBeInTheDocument());
    fireEvent.click(screen.getByText('База Б'));
    expect(mockOnSelectedIdsChange).toHaveBeenCalledWith([1, 2]);
  });

  it('выключение БЗ чистит выбор', () => {
    setup({ enabled: true, selectedIds: [1, 2] });
    fireEvent.click(screen.getByRole('checkbox'));
    expect(mockOnEnabledChange).toHaveBeenCalledWith(false);
    expect(mockOnSelectedIdsChange).toHaveBeenCalledWith([]);
  });
});
