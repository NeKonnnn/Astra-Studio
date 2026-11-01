import '@testing-library/jest-dom';
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import { DocumentSearchPanel, type DocumentSearchTrace } from '@/components/DocumentSearchPanel';

const relevances = () =>
  Array.from(document.querySelectorAll('strong'))
    .filter((n) => n.textContent === 'Relevance')
    .map((n) => n.parentElement!.textContent!.replace('Relevance: ', ''));

const hit = (over: Partial<DocumentSearchTrace['hits'][number]> = {}) => ({
  file: 'a.pdf',
  anchor: 'p.1',
  relevance: 0.876,
  content: 'Фрагмент текста',
  chunkIndex: 0,
  documentId: 1,
  store: 'agent',
  ...over,
});

const renderPanel = (trace: DocumentSearchTrace, mode: 'light' | 'dark' = 'light') =>
  render(
    <ThemeProvider theme={createTheme({ palette: { mode } })}>
      <DocumentSearchPanel trace={trace} />
    </ThemeProvider>,
  );

describe('DocumentSearchPanel', () => {
  it('файлы берутся из хитов: уникальные, отсортированные, с типом по расширению', () => {
    renderPanel({
      query: 'q',
      sourceFiles: ['ignored.txt'],
      hits: [
        hit({ file: 'b.docx' }),
        hit({ file: 'a.pdf' }),
        hit({ file: 'a.pdf', chunkIndex: 2 }),
        hit({ file: 'c.xlsx' }),
        hit({ file: 'd.csv' }),
        hit({ file: 'e.txt' }),
        hit({ file: 'f.bin' }),
        hit({ file: '' }),
        hit({ file: 'g.DOC' }),
      ],
    });
    expect(screen.getByText('Исходные документы (7)')).toBeInTheDocument();
    expect(screen.queryByText('ignored.txt')).toBeNull();
    const names = Array.from(document.querySelectorAll('div.MuiTypography-caption')).map((n) => n.textContent);
    expect(names.slice(0, 7)).toEqual(['a.pdf', 'b.docx', 'c.xlsx', 'd.csv', 'e.txt', 'f.bin', 'g.DOC']);
    for (const label of ['PDF', 'Word', 'Excel', 'TXT', 'File']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getAllByText('Excel')).toHaveLength(2);
    expect(screen.getAllByText('Word')).toHaveLength(2);
  });

  it('без хитов используются sourceFiles; совсем без файлов - заглушка и нет счётчика', () => {
    const { unmount } = renderPanel({ query: 'q', sourceFiles: ['z.txt', 'y.pdf'], hits: [] });
    expect(screen.getByText('Исходные документы (2)')).toBeInTheDocument();
    expect(screen.getByText('y.pdf')).toBeInTheDocument();
    unmount();
    renderPanel({ query: 'q', sourceFiles: [], hits: [] });
    expect(screen.getByText('Исходные документы')).toBeInTheDocument();
    expect(screen.getByText('Нет совпадений по файлам')).toBeInTheDocument();
  });

  it('секция документов сворачивается и разворачивается', () => {
    renderPanel({ query: 'q', sourceFiles: ['a.pdf'], hits: [] }, 'dark');
    const toggle = screen.getByLabelText('Свернуть список документов');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(toggle);
    expect(screen.getByLabelText('Показать список документов')).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(screen.getByLabelText('Показать список документов'));
    expect(screen.getByLabelText('Свернуть список документов')).toBeInTheDocument();
  });

  it('детали поиска: запрос и результаты показываются после раскрытия', () => {
    renderPanel({
      query: 'как оформить отпуск',
      sourceFiles: [],
      hits: [hit({ relevance: 0.876, store: 'agent' }), hit({ file: 'b.txt', relevance: 55, store: '', content: 'Второй', chunkIndex: 1 })],
    });
    const toggle = screen.getByText('Выполнен поиск по документам').closest('[role="button"]') as HTMLElement;
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(/"query": "как оформить отпуск"/)).toBeInTheDocument();
    expect(screen.getByText('Фрагмент текста')).toBeInTheDocument();
    expect(screen.getByText(/88% \(agent\)/)).toBeInTheDocument();
    expect(relevances()[1]).toBe('55%');
  });

  it('релевантность ограничивается диапазоном 1..100 и обрабатывает мусор', () => {
    renderPanel({
      query: 'q',
      sourceFiles: [],
      hits: [hit({ relevance: 250 }), hit({ relevance: 0 }), hit({ relevance: NaN as any }), hit({ relevance: -5 }), hit({ relevance: 0.001 })],
    });
    fireEvent.click(screen.getByText('Выполнен поиск по документам'));
    expect(relevances()).toEqual(['100% (agent)', '1% (agent)', '1% (agent)', '1% (agent)', '1% (agent)']);
  });

  it('без результатов показывается «Фрагменты не найдены»', () => {
    renderPanel({ query: 'q', sourceFiles: ['a.pdf'], hits: [] });
    fireEvent.click(screen.getByText('Выполнен поиск по документам'));
    expect(screen.getByText('Фрагменты не найдены.')).toBeInTheDocument();
  });
});
