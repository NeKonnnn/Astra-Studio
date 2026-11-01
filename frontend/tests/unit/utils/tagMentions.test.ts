import {
  extractTagIds,
  getTagHashQuery,
  serializeTagMention,
  stripTagMentions,
} from '@/utils/tagMentions';

describe('extractTagIds', () => {
  it('returns nothing for empty text', () => {
    expect(extractTagIds('')).toEqual([]);
  });

  it('extracts unique positive ids in order', () => {
    expect(extractTagIds('<#5|А> и <#2|Б> и <#5|А>')).toEqual([5, 2]);
  });

  it('truncates fractional ids', () => {
    expect(extractTagIds('<#7.9|X>')).toEqual([7]);
  });

  it('skips non-numeric, zero and negative ids', () => {
    expect(extractTagIds('<#abc|X> <#0|X> <#-3|X> <#4|X>')).toEqual([4]);
  });

  it('works without a display name and across repeated calls', () => {
    expect(extractTagIds('<#9>')).toEqual([9]);
    expect(extractTagIds('<#9>')).toEqual([9]);
  });
});

describe('serializeTagMention', () => {
  it('formats id and name', () => {
    expect(serializeTagMention(3, 'Финансы')).toBe('<#3|Финансы>');
  });

  it('falls back to the id when the name is empty', () => {
    expect(serializeTagMention(3, '')).toBe('<#3|3>');
  });

  it('truncates the id', () => {
    expect(serializeTagMention(3.7, 'x')).toBe('<#3|x>');
  });

  it('round-trips with extractTagIds', () => {
    expect(extractTagIds(serializeTagMention(12, 'Имя'))).toEqual([12]);
  });
});

describe('stripTagMentions', () => {
  it('removes mentions and trims', () => {
    expect(stripTagMentions('<#1|A> спроси <#2|B>')).toBe('спроси');
  });

  it('handles empty values', () => {
    expect(stripTagMentions('')).toBe('');
    expect(stripTagMentions(undefined as unknown as string)).toBe('');
  });
});

describe('getTagHashQuery', () => {
  it('detects # at the start and after whitespace', () => {
    expect(getTagHashQuery('#фин', 4)).toEqual({ start: 0, query: 'фин' });
    expect(getTagHashQuery('вопрос #a', 9)).toEqual({ start: 7, query: 'a' });
  });

  it('returns an empty query right after #', () => {
    expect(getTagHashQuery('x #', 3)).toEqual({ start: 2, query: '' });
  });

  it('does not trigger inside a word or after a space in the query', () => {
    expect(getTagHashQuery('abc#d', 5)).toBeNull();
    expect(getTagHashQuery('#abc d', 6)).toBeNull();
  });

  it('only looks at text before the cursor', () => {
    expect(getTagHashQuery('#abc def', 2)).toEqual({ start: 0, query: 'a' });
  });
});
