import {
  extractSkillIds,
  getSkillDollarQuery,
  serializeSkillMention,
  splitSkillMentions,
  stripSkillMentions,
} from '@/utils/skillMentions';

describe('serializeSkillMention', () => {
  it('formats slug and name', () => {
    expect(serializeSkillMention('pdf-tools', 'PDF инструменты')).toBe('<$pdf-tools|PDF инструменты>');
  });

  it('falls back to the slug when the name is empty', () => {
    expect(serializeSkillMention('pdf', '')).toBe('<$pdf|pdf>');
  });

  it('trims both parts', () => {
    expect(serializeSkillMention('  a ', ' B ')).toBe('<$a|B>');
  });
});

describe('extractSkillIds', () => {
  it('returns nothing for empty text', () => {
    expect(extractSkillIds('')).toEqual([]);
  });

  it('extracts unique ids preserving order', () => {
    const text = 'Привет <$a|A> и <$b|B>, ещё раз <$a|A>';

    expect(extractSkillIds(text)).toEqual(['a', 'b']);
  });

  it('supports mentions without a display name', () => {
    expect(extractSkillIds('<$solo>')).toEqual(['solo']);
  });

  it('is stable across repeated calls (no sticky regex state)', () => {
    const text = '<$a|A>';

    expect(extractSkillIds(text)).toEqual(['a']);
    expect(extractSkillIds(text)).toEqual(['a']);
  });

  it('ignores plain text with a dollar sign', () => {
    expect(extractSkillIds('цена $5 и <b>тег</b>')).toEqual([]);
  });
});

describe('stripSkillMentions', () => {
  it('removes mentions and trims', () => {
    expect(stripSkillMentions('<$a|A> сделай отчёт <$b|B>')).toBe('сделай отчёт');
  });

  it('handles empty values', () => {
    expect(stripSkillMentions('')).toBe('');
    expect(stripSkillMentions(undefined as unknown as string)).toBe('');
  });
});

describe('splitSkillMentions', () => {
  it('returns no parts for empty text', () => {
    expect(splitSkillMentions('')).toEqual([]);
  });

  it('returns one text part when there are no mentions', () => {
    expect(splitSkillMentions('просто текст')).toEqual([{ type: 'text', value: 'просто текст' }]);
  });

  it('splits text around mentions', () => {
    expect(splitSkillMentions('до <$a|Навык А> после')).toEqual([
      { type: 'text', value: 'до ' },
      { type: 'skill', slug: 'a', name: 'Навык А' },
      { type: 'text', value: ' после' },
    ]);
  });

  it('handles adjacent mentions and a missing name', () => {
    expect(splitSkillMentions('<$a|A><$b>')).toEqual([
      { type: 'skill', slug: 'a', name: 'A' },
      { type: 'skill', slug: 'b', name: 'b' },
    ]);
  });

  it('round-trips with serializeSkillMention', () => {
    const text = `x ${serializeSkillMention('s', 'Имя')} y`;

    expect(splitSkillMentions(text)[1]).toEqual({ type: 'skill', slug: 's', name: 'Имя' });
  });
});

describe('getSkillDollarQuery', () => {
  it('detects a $ at the start of the text', () => {
    expect(getSkillDollarQuery('$pdf', 4)).toEqual({ start: 0, query: 'pdf' });
  });

  it('detects a $ after whitespace', () => {
    expect(getSkillDollarQuery('сделай $ex', 10)).toEqual({ start: 7, query: 'ex' });
  });

  it('returns an empty query right after the $', () => {
    expect(getSkillDollarQuery('привет $', 8)).toEqual({ start: 7, query: '' });
  });

  it('uses only the text before the cursor', () => {
    expect(getSkillDollarQuery('$abc def', 2)).toEqual({ start: 0, query: 'a' });
  });

  it('does not trigger in the middle of a word', () => {
    expect(getSkillDollarQuery('цена5$ab', 8)).toBeNull();
  });

  it('does not trigger when the query already contains a space', () => {
    expect(getSkillDollarQuery('$abc def', 8)).toBeNull();
  });

  it('handles a $ at the start of a new line', () => {
    expect(getSkillDollarQuery('строка\n$ab', 10)).toEqual({ start: 7, query: 'ab' });
  });
});
