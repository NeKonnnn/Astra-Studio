import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import RagReindexStatusBanner from '@/components/RagReindexStatusBanner';

const mockUseRagReindexStatus = jest.fn();

jest.mock('@/hooks/useRagReindexStatus', () => ({
  useRagReindexStatus: () => mockUseRagReindexStatus(),
}));

function setStatus(anyReindexing: boolean, blockMessage: string) {
  mockUseRagReindexStatus.mockReturnValue({ anyReindexing, blockMessage });
}

describe('RagReindexStatusBanner', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('renders nothing while nothing is being reindexed', () => {
    setStatus(false, 'Идёт пересборка');

    const { container } = render(<RagReindexStatusBanner />);

    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when there is no message to show', () => {
    setStatus(true, '');

    const { container } = render(<RagReindexStatusBanner />);

    expect(container.firstChild).toBeNull();
  });

  it('shows a warning with the server message while reindexing', () => {
    setStatus(true, 'Идёт пересборка агента «Юрист»');

    render(<RagReindexStatusBanner />);

    expect(screen.getByRole('alert').textContent).toContain('Идёт пересборка агента «Юрист»');
  });

  it('stays closed after being dismissed, even across re-renders', () => {
    setStatus(true, 'Идёт пересборка');
    const { rerender } = render(<RagReindexStatusBanner />);

    fireEvent.click(screen.getByRole('button', { name: 'Закрыть уведомление о перечанковке RAG' }));
    rerender(<RagReindexStatusBanner />);

    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows again for the next reindexing after it finished', () => {
    setStatus(true, 'Идёт пересборка');
    const { rerender } = render(<RagReindexStatusBanner />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.queryByRole('alert')).toBeNull();

    setStatus(false, '');
    rerender(<RagReindexStatusBanner />);
    setStatus(true, 'Новая пересборка');
    rerender(<RagReindexStatusBanner />);

    expect(screen.getByRole('alert').textContent).toContain('Новая пересборка');
  });

  it('hides itself automatically after the toast delay', () => {
    setStatus(true, 'Идёт пересборка');
    render(<RagReindexStatusBanner />);

    act(() => {
      jest.advanceTimersByTime(10_000);
    });

    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('disappears when the reindexing ends', () => {
    setStatus(true, 'Идёт пересборка');
    const { rerender } = render(<RagReindexStatusBanner />);
    expect(screen.getByRole('alert')).toBeTruthy();

    setStatus(false, 'Идёт пересборка');
    rerender(<RagReindexStatusBanner />);

    expect(screen.queryByRole('alert')).toBeNull();
  });
});
