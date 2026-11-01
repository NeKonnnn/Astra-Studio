/**
 * @jest-environment jsdom
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import EditProjectModal from '@/components/EditProjectModal';

jest.mock('@/components/ProjectKnowledgeBasesField', () => ({
  __esModule: true,
  default: (p: {
    enabled: boolean;
    selectedIds: number[];
    onEnabledChange: (v: boolean) => void;
    onSelectedIdsChange: (ids: number[]) => void;
  }) => (
    <div data-testid="kb-field">
      <button type="button" onClick={() => p.onEnabledChange(!p.enabled)}>
        toggle-kb
      </button>
      <button type="button" onClick={() => p.onSelectedIdsChange([11, 22])}>
        select-kbs
      </button>
      <span data-testid="kb-enabled">{String(p.enabled)}</span>
      <span data-testid="kb-ids">{JSON.stringify(p.selectedIds)}</span>
    </div>
  ),
}));

const onClose = jest.fn();
const onSave = jest.fn();

const project: any = {
  id: 'p1',
  name: 'Старое имя',
  icon: 'lightbulb',
  iconType: 'icon',
  iconColor: '#f44336',
  memory: 'project-only',
  instructions: 'инструкция',
  knowledgeBasesEnabled: true,
  knowledgeBaseIds: [5],
};

function setup(props: Record<string, unknown> = {}) {
  return render(
    <EditProjectModal open onClose={onClose} project={project} onSave={onSave} {...props} />,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('EditProjectModal (KB-only)', () => {
  it('без проекта ничего не рендерится', () => {
    const { container } = setup({ project: null });
    expect(container).toBeEmptyDOMElement();
  });

  it('показывает поле баз знаний, без заливки файлов проекта и RAG-панели', () => {
    setup();
    expect(screen.getByTestId('kb-field')).toBeInTheDocument();
    expect(screen.getByTestId('kb-enabled')).toHaveTextContent('true');
    expect(screen.getByTestId('kb-ids')).toHaveTextContent('[5]');
    expect(screen.queryByText(/Настройки РАГ для этого проекта/i)).toBeNull();
    expect(screen.queryByTestId('lib')).toBeNull();
  });

  it('сохраняет выбранные knowledgeBaseIds', () => {
    setup();
    fireEvent.click(screen.getByRole('button', { name: 'select-kbs' }));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(onSave).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({
        name: 'Старое имя',
        knowledgeBasesEnabled: true,
        knowledgeBaseIds: [11, 22],
      }),
    );
  });

  it('при выключении БЗ сохраняет пустой список', () => {
    setup();
    fireEvent.click(screen.getByRole('button', { name: 'toggle-kb' }));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(onSave).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({
        knowledgeBasesEnabled: false,
        knowledgeBaseIds: [],
      }),
    );
  });
});
