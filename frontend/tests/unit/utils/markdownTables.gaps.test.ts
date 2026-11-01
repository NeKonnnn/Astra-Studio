import {
  extractFirstMarkdownTable,
  parseMarkdownTableBlock,
  splitTextWithMarkdownTables,
} from '@/utils/markdownTables';

describe('markdownTables: недостающие ветки', () => {
  it('parseMarkdownTableBlock: вторая строка не таблица -> null', () => {
    expect(parseMarkdownTableBlock('a | b\nпросто текст')).toBeNull();
    expect(parseMarkdownTableBlock('одна строка | a')).toBeNull();
    expect(parseMarkdownTableBlock('нет труб\n| a |')).toBeNull();
  });

  it('parseMarkdownTableBlock: допускает таблицу без разделителя и выравнивает колонки', () => {
    const r = parseMarkdownTableBlock('a | b\n1 | 2 | 3');
    expect(r).toEqual({ headers: ['a', 'b', ''], rows: [['1', '2', '3']] });
  });

  it('extractFirstMarkdownTable: случайные одиночные колонки с | не таблица', () => {
    expect(extractFirstMarkdownTable('| a |\n| b |')).toBeNull();
  });

  it('extractFirstMarkdownTable: склеивает перенос внутри ячейки и возвращает границы', () => {
    const r = extractFirstMarkdownTable('до\n| a | b |\n|---|---|\n| x | длинный\nхвост ячейки\n\nпосле');
    expect(r).not.toBeNull();
    expect(r!.before).toBe('до');
    expect(r!.table).toContain('| x | длинный хвост ячейки');
    expect(r!.after).toBe('\nпосле');
    expect(r!.start).toBe(1);
  });

  it('extractFirstMarkdownTable: пропускает ложную пару и находит настоящую таблицу ниже', () => {
    const r = extractFirstMarkdownTable('| a |\n| b |\n\n| x | y |\n|--|--|\n| 1 | 2 |');
    expect(r!.start).toBe(3);
  });

  it('splitTextWithMarkdownTables: чередует текст и таблицы', () => {
    const blocks = splitTextWithMarkdownTables('вступление\n| a | b |\n|---|---|\n| 1 | 2 |\n\nконец');
    expect(blocks.map((b) => b.kind)).toEqual(['text', 'table', 'text']);
    expect((blocks[1] as any).rows).toEqual([['1', '2']]);
  });

  it('splitTextWithMarkdownTables: текст без таблиц и пустой ввод', () => {
    expect(splitTextWithMarkdownTables('просто текст')).toEqual([{ kind: 'text', text: 'просто текст' }]);
    expect(splitTextWithMarkdownTables('')).toEqual([]);
  });
});
