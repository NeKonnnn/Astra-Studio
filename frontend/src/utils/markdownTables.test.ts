import {
  extractFirstMarkdownTable,
  isMarkdownTableRowLine,
  isMarkdownTableSeparator,
  looksLikeAsciiArtTable,
  parseMarkdownTableBlock,
  splitMarkdownTableRow,
  splitTextWithMarkdownTables,
} from './markdownTables';

describe('splitMarkdownTableRow', () => {
  it('делит по | и убирает крайние трубы', () => {
    expect(splitMarkdownTableRow('| a | b | c |')).toEqual(['a', 'b', 'c']);
  });

  it('работает без крайних труб', () => {
    expect(splitMarkdownTableRow('a | b')).toEqual(['a', 'b']);
  });

  it('экранированный \\| остаётся в ячейке', () => {
    expect(splitMarkdownTableRow('| a \\| b | c |')).toEqual(['a | b', 'c']);
  });

  it('сохраняет пустые ячейки', () => {
    expect(splitMarkdownTableRow('| a | | c |')).toEqual(['a', '', 'c']);
  });

  it('строка без | — одна ячейка либо пусто', () => {
    expect(splitMarkdownTableRow('просто текст')).toEqual(['просто текст']);
    expect(splitMarkdownTableRow('   ')).toEqual([]);
  });
});

describe('isMarkdownTableSeparator', () => {
  it.each(['|---|---|', '| --- | :---: | ---: |', '|--|--|', '---|---', '+----+----+', '|====|====|'])(
    'считает разделителем: %s',
    (line) => {
      expect(isMarkdownTableSeparator(line)).toBe(true);
    },
  );

  it('понимает unicode-тире', () => {
    expect(isMarkdownTableSeparator('|———|———|')).toBe(true);
  });

  it.each(['', '   ', '| a | b |', 'текст', '-', '|-|', '| - | - |'])('не разделитель: "%s"', (line) => {
    expect(isMarkdownTableSeparator(line)).toBe(false);
  });
});

describe('isMarkdownTableRowLine', () => {
  it('строка с | — строка таблицы', () => {
    expect(isMarkdownTableRowLine('| a | b |')).toBe(true);
  });

  it('разделитель, пустая строка и текст без | — нет', () => {
    expect(isMarkdownTableRowLine('|---|---|')).toBe(false);
    expect(isMarkdownTableRowLine('')).toBe(false);
    expect(isMarkdownTableRowLine('текст')).toBe(false);
  });
});

describe('parseMarkdownTableBlock', () => {
  it('классическая GFM-таблица', () => {
    const md = '| Имя | Возраст |\n|---|---|\n| Аня | 30 |\n| Борис | 25 |';
    expect(parseMarkdownTableBlock(md)).toEqual({
      headers: ['Имя', 'Возраст'],
      rows: [
        ['Аня', '30'],
        ['Борис', '25'],
      ],
    });
  });

  it('таблица без разделителя', () => {
    expect(parseMarkdownTableBlock('| a | b |\n| 1 | 2 |')).toEqual({
      headers: ['a', 'b'],
      rows: [['1', '2']],
    });
  });

  it('короткие строки добиваются пустыми ячейками', () => {
    const parsed = parseMarkdownTableBlock('| a | b | c |\n|---|---|---|\n| 1 |');
    expect(parsed?.rows).toEqual([['1', '', '']]);
  });

  it('лишние ячейки склеиваются в последнюю колонку', () => {
    const parsed = parseMarkdownTableBlock('| a | b |\n|---|---|\n| 1 | 2 | 3 |');
    expect(parsed?.headers).toHaveLength(parsed?.rows[0].length ?? -1);
  });

  it('разделитель с большим числом колонок расширяет таблицу', () => {
    const parsed = parseMarkdownTableBlock('| a | b |\n|---|---|---|\n| 1 | 2 | 3 |');
    expect(parsed?.headers).toEqual(['a', 'b', '']);
    expect(parsed?.rows).toEqual([['1', '2', '3']]);
  });

  it('CRLF-переводы строк', () => {
    expect(parseMarkdownTableBlock('| a |\r\n|---|\r\n| 1 |')?.rows).toEqual([['1']]);
  });

  it.each(['', 'одна строка | с трубой', 'нет\nтаблицы', '| a |\nтекст без трубы'])(
    'не таблица: %j',
    (text) => {
      expect(parseMarkdownTableBlock(text)).toBeNull();
    },
  );
});

describe('extractFirstMarkdownTable', () => {
  it('находит таблицу среди текста', () => {
    const text = 'Введение\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nЗаключение';
    const found = extractFirstMarkdownTable(text);
    expect(found).not.toBeNull();
    expect(found?.table).toBe('| a | b |\n|---|---|\n| 1 | 2 |');
    expect(found?.before).toBe('Введение\n');
    expect(found?.after.trim()).toBe('Заключение');
  });

  it('склеивает перенос внутри ячейки', () => {
    const text = '| a | b |\n|---|---|\n| 1 | длинный текст\nпродолжение ячейки';
    const found = extractFirstMarkdownTable(text);
    expect(found?.table).toContain('длинный текст продолжение ячейки');
  });

  it('текст без таблицы — null', () => {
    expect(extractFirstMarkdownTable('Обычный абзац.\n\nЕщё один.')).toBeNull();
  });

  it('случайные | в тексте — не таблица', () => {
    expect(extractFirstMarkdownTable('команда a | grep b\nещё одна строка')).toBeNull();
  });
});

describe('splitTextWithMarkdownTables', () => {
  it('чередует text и table блоки', () => {
    const text = 'До\n\n| a |\n|---|\n| 1 |\n\nМежду\n\n| b |\n|---|\n| 2 |\n\nПосле';
    const blocks = splitTextWithMarkdownTables(text);
    expect(blocks.map((b) => b.kind)).toEqual(['text', 'table', 'text', 'table', 'text']);
    expect(blocks[1]).toEqual({ kind: 'table', headers: ['a'], rows: [['1']] });
  });

  it('пустой ввод — пустой список', () => {
    expect(splitTextWithMarkdownTables('')).toEqual([]);
  });

  it('текст без таблиц — один text-блок', () => {
    expect(splitTextWithMarkdownTables('просто текст')).toEqual([{ kind: 'text', text: 'просто текст' }]);
  });
});

describe('looksLikeAsciiArtTable', () => {
  it('распознаёт ASCII-таблицу с границами', () => {
    const ascii = [
      '+-----+-----+',
      '| a   | b   |',
      '+-----+-----+',
      '| 1   | 2   |',
      '| 3   | 4   |',
      '| 5   | 6   |',
      '+-----+-----+',
    ].join('\n');
    expect(looksLikeAsciiArtTable(ascii)).toBe(true);
  });

  it('GFM-таблица — не ASCII-art', () => {
    expect(looksLikeAsciiArtTable('| a | b |\n|---|---|\n| 1 | 2 |')).toBe(false);
  });

  it('короткий текст — нет', () => {
    expect(looksLikeAsciiArtTable('a\nb')).toBe(false);
  });
});
