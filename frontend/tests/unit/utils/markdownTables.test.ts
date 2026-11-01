import {
  extractFirstMarkdownTable,
  isMarkdownTableRowLine,
  isMarkdownTableSeparator,
  looksLikeAsciiArtTable,
  parseMarkdownTableBlock,
  splitMarkdownTableRow,
  splitTextWithMarkdownTables,
} from '@/utils/markdownTables';

describe('splitMarkdownTableRow', () => {
  it('splits cells and drops the edge pipes', () => {
    expect(splitMarkdownTableRow('| a | b | c |')).toEqual(['a', 'b', 'c']);
  });

  it('works without edge pipes', () => {
    expect(splitMarkdownTableRow('a | b')).toEqual(['a', 'b']);
  });

  it('keeps empty cells to preserve column alignment', () => {
    expect(splitMarkdownTableRow('| a |  | c |')).toEqual(['a', '', 'c']);
  });

  it('does not split on an escaped pipe', () => {
    expect(splitMarkdownTableRow('| a \\| b | c |')).toEqual(['a | b', 'c']);
  });

  it('returns a line without pipes as one cell', () => {
    expect(splitMarkdownTableRow('  просто текст ')).toEqual(['просто текст']);
    expect(splitMarkdownTableRow('   ')).toEqual([]);
  });
});

describe('isMarkdownTableSeparator', () => {
  it.each(['|---|---|', '| --- | :---: |', '|:--|--:|', '--|--', '+----+----+', '|===|===|', '---', ':---:'])(
    'accepts %p',
    (line) => {
      expect(isMarkdownTableSeparator(line)).toBe(true);
    },
  );

  it('accepts unicode dashes', () => {
    expect(isMarkdownTableSeparator('|──|──|')).toBe(true);
    expect(isMarkdownTableSeparator('|—–|—–|')).toBe(true);
  });

  it.each(['', '   ', '| a | b |', '-', '|-|-|', 'текст --- текст'])('rejects %p', (line) => {
    expect(isMarkdownTableSeparator(line)).toBe(false);
  });
});

describe('isMarkdownTableRowLine', () => {
  it('accepts lines with pipes', () => {
    expect(isMarkdownTableRowLine('| a | b |')).toBe(true);
  });

  it('rejects separators, empty lines and plain text', () => {
    expect(isMarkdownTableRowLine('|---|---|')).toBe(false);
    expect(isMarkdownTableRowLine('')).toBe(false);
    expect(isMarkdownTableRowLine('обычный текст')).toBe(false);
  });
});

describe('parseMarkdownTableBlock', () => {
  it('parses a standard GFM table', () => {
    const table = '| Имя | Сумма |\n|---|---|\n| Анна | 10 |\n| Борис | 20 |';

    expect(parseMarkdownTableBlock(table)).toEqual({
      headers: ['Имя', 'Сумма'],
      rows: [
        ['Анна', '10'],
        ['Борис', '20'],
      ],
    });
  });

  it('accepts a table without a separator row', () => {
    const parsed = parseMarkdownTableBlock('| a | b |\n| 1 | 2 |');

    expect(parsed?.headers).toEqual(['a', 'b']);
    expect(parsed?.rows).toEqual([['1', '2']]);
  });

  it('pads short rows', () => {
    const parsed = parseMarkdownTableBlock('| a | b |\n|---|---|\n| 1 |\n| 3 | 4 |');

    expect(parsed?.rows).toEqual([
      ['1', ''],
      ['3', '4'],
    ]);
  });

  it('widens the table when one of the first five rows has extra cells', () => {
    const parsed = parseMarkdownTableBlock('| a | b |\n|---|---|\n| 1 | 2 | 3 |');

    expect(parsed?.headers).toEqual(['a', 'b', '']);
    expect(parsed?.rows).toEqual([['1', '2', '3']]);
  });

  it('merges extra cells into the last column only for rows after the first five', () => {
    const rows = Array.from({ length: 5 }, (_, i) => `| ${i} | x |`).join('\n');
    const parsed = parseMarkdownTableBlock(`| a | b |\n|---|---|\n${rows}\n| 9 | y | z |`);

    expect(parsed?.headers).toEqual(['a', 'b']);
    expect(parsed?.rows[5]).toEqual(['9', 'y | z']);
  });

  it('tolerates CRLF line endings', () => {
    const parsed = parseMarkdownTableBlock('| a | b |\r\n|---|---|\r\n| 1 | 2 |');

    expect(parsed?.rows).toEqual([['1', '2']]);
  });

  it('returns null for a single line or plain text', () => {
    expect(parseMarkdownTableBlock('| a | b |')).toBeNull();
    expect(parseMarkdownTableBlock('просто\nтекст')).toBeNull();
    expect(parseMarkdownTableBlock('')).toBeNull();
  });
});

describe('extractFirstMarkdownTable', () => {
  it('returns null when there is no table', () => {
    expect(extractFirstMarkdownTable('просто текст\nещё строка')).toBeNull();
  });

  it('separates the text before and after the table', () => {
    const text = 'Вступление\n| a | b |\n|---|---|\n| 1 | 2 |\n\nЗаключение';

    const found = extractFirstMarkdownTable(text);

    expect(found?.before).toBe('Вступление');
    expect(found?.table).toBe('| a | b |\n|---|---|\n| 1 | 2 |');
    expect(found?.after).toBe('\nЗаключение');
  });

  it('joins a wrapped cell when the continuation has no pipe', () => {
    const text = '| a | b |\n|---|---|\n| 1 | длинный\nперенос';

    const found = extractFirstMarkdownTable(text);

    expect(parseMarkdownTableBlock(found!.table)?.rows).toEqual([['1', 'длинный перенос']]);
  });

  it('treats a continuation line that contains a pipe as a new row', () => {
    // См. TESTING_KNOWN_BUGS.md: ветка склейки переноса недостижима, если в
    // продолжении есть `|` (например, закрывающая труба ячейки).
    const text = '| a | b |\n|---|---|\n| 1 | длинный\nперенос |';

    const found = extractFirstMarkdownTable(text);

    expect(parseMarkdownTableBlock(found!.table)?.rows).toEqual([
      ['1', 'длинный'],
      ['перенос', ''],
    ]);
  });

  it('ignores a stray pipe line in prose', () => {
    expect(extractFirstMarkdownTable('команда a | b\nдальше текст')).toBeNull();
  });
});

describe('splitTextWithMarkdownTables', () => {
  it('returns no blocks for empty text', () => {
    expect(splitTextWithMarkdownTables('')).toEqual([]);
  });

  it('returns one text block when there are no tables', () => {
    expect(splitTextWithMarkdownTables('привет')).toEqual([{ kind: 'text', text: 'привет' }]);
  });

  it('alternates text and table blocks for several tables', () => {
    const text = 'до\n| a |\n|---|\n| 1 |\n\nмежду\n| b | c |\n|---|---|\n| 2 | 3 |\n\nпосле';

    const blocks = splitTextWithMarkdownTables(text);

    expect(blocks.map((b) => b.kind)).toEqual(['text', 'table', 'text', 'table', 'text']);
    expect(blocks[1]).toEqual({ kind: 'table', headers: ['a'], rows: [['1']] });
    expect(blocks[3]).toEqual({ kind: 'table', headers: ['b', 'c'], rows: [['2', '3']] });
  });
});

describe('looksLikeAsciiArtTable', () => {
  it('detects +---+ bordered tables with several data rows', () => {
    const rows = ['| 1  | 2  |', '| 3  | 4  |', '| 5  | 6  |', '| 7  | 8  |'].join('\n');
    const ascii = `+----+----+\n| a  | b  |\n+----+----+\n${rows}\n+----+----+`;

    expect(looksLikeAsciiArtTable(ascii)).toBe(true);
  });

  it('misses a small bordered table: border lines have no pipes and dilute the ratio', () => {
    const ascii = '+----+----+\n| a  | b  |\n+----+----+\n| 1  | 2  |\n+----+----+';

    expect(looksLikeAsciiArtTable(ascii)).toBe(false);
  });

  it('does not treat a GFM table as ascii art', () => {
    expect(looksLikeAsciiArtTable('| a | b |\n|---|---|\n| 1 | 2 |')).toBe(false);
  });

  it('needs at least three non-empty lines', () => {
    expect(looksLikeAsciiArtTable('+---+\n| a |')).toBe(false);
  });

  it('rejects text without borders', () => {
    expect(looksLikeAsciiArtTable('раз\nдва\nтри')).toBe(false);
  });
});
