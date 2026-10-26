/**
 * @jest-environment jsdom
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import ChatTitleWithGenerating from '@/components/left_bar/ChatTitleWithGenerating';

describe('ChatTitleWithGenerating', () => {
  it('renders title without spinner when idle', () => {
    render(<ChatTitleWithGenerating title="Чат A" isGenerating={false} />);
    expect(screen.getByText('Чат A')).toBeInTheDocument();
    expect(screen.queryByLabelText('Идёт генерация')).not.toBeInTheDocument();
  });

  it('shows upload-style spinner next to title while generating', () => {
    render(<ChatTitleWithGenerating title="Чат B" isGenerating />);
    expect(screen.getByText('Чат B')).toBeInTheDocument();
    expect(screen.getByLabelText('Идёт генерация')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Загрузка файла' })).toBeInTheDocument();
  });
});
