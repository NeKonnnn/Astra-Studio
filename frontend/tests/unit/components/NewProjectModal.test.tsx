/**
 * @jest-environment jsdom
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import NewProjectModal from '@/components/NewProjectModal';

jest.mock('@/components/ProjectKnowledgeBasesField', () => ({
  __esModule: true,
  default: (p: {
    enabled: boolean;
    selectedIds: number[];
    onEnabledChange: (v: boolean) => void;
    onSelectedIdsChange: (ids: number[]) => void;
  }) => (
    <div data-testid="kb-field">
      <button type="button" onClick={() => p.onEnabledChange(true)}>
        enable-kb
      </button>
      <button type="button" onClick={() => p.onSelectedIdsChange([3, 9])}>
        pick-kbs
      </button>
      <span data-testid="kb-enabled">{String(p.enabled)}</span>
      <span data-testid="kb-ids">{JSON.stringify(p.selectedIds)}</span>
    </div>
  ),
}));

const onClose = jest.fn();
const onCreate = jest.fn();

function setup(props: Partial<React.ComponentProps<typeof NewProjectModal>> = {}) {
  return render(<NewProjectModal open onClose={onClose} onCreateProject={onCreate} {...props} />);
}
const nameInput = () => screen.getByPlaceholderText('Название проекта') as HTMLInputElement;
const createBtn = () => screen.getByRole('button', { name: 'Создать проект' });
const typeName = (v: string) => fireEvent.change(nameInput(), { target: { value: v } });

beforeEach(() => {
  jest.clearAllMocks();
  onCreate.mockReturnValue('p-new');
  jest.spyOn(window, 'alert').mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

describe('NewProjectModal (KB-only)', () => {
  it('кнопка «Создать проект» заблокирована при пустом названии', () => {
    setup();
    expect(createBtn()).toBeDisabled();
    typeName('   ');
    expect(createBtn()).toBeDisabled();
    typeName('Альфа');
    expect(createBtn()).toBeEnabled();
  });

  it('закрытый диалог ничего не рендерит', () => {
    setup({ open: false });
    expect(screen.queryByText('Новый проект')).toBeNull();
  });

  it('показывает поле БЗ, без заливки файлов и RAG-панели проекта', () => {
    setup();
    expect(screen.getByTestId('kb-field')).toBeInTheDocument();
    expect(screen.queryByText(/Настройки РАГ/i)).toBeNull();
    expect(screen.queryByTestId('lib')).toBeNull();
  });

  it('создание передаёт knowledgeBaseIds при включённых БЗ', async () => {
    setup();
    typeName('  Проект X  ');
    fireEvent.click(screen.getByRole('button', { name: 'enable-kb' }));
    fireEvent.click(screen.getByRole('button', { name: 'pick-kbs' }));
    fireEvent.click(createBtn());
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Проект X',
        memory: 'default',
        knowledgeBasesEnabled: true,
        knowledgeBaseIds: [3, 9],
      }),
    );
  });

  it('по умолчанию БЗ выключены и список пуст', async () => {
    setup();
    typeName('Без БЗ');
    fireEvent.click(createBtn());
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        knowledgeBasesEnabled: false,
        knowledgeBaseIds: [],
      }),
    );
  });

  it('крестик и «Отменить» закрывают форму', () => {
    setup();
    typeName('Тест');
    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(nameInput().value).toBe('');
  });

  it('вызов handleCreate с пустым названием ничего не делает', async () => {
    setup();
    fireEvent.click(createBtn());
    await act(async () => {});
    expect(onCreate).not.toHaveBeenCalled();
  });
});
