import { applySpeakerMapping, extractSpeakers } from '@/utils/speakerNames';

describe('extractSpeakers', () => {
  it('returns an empty list for empty text', () => {
    expect(extractSpeakers('')).toEqual([]);
  });

  it('finds unique speakers of all supported formats, sorted', () => {
    const text = '00:01 SPEAKER_01: a\n00:02 SPEAKER_00: b\n00:03 SPEAKER_01: c\nSpeaker_A: d\nСпикер_2: e';
    expect(extractSpeakers(text)).toEqual(['SPEAKER_00', 'SPEAKER_01', 'Speaker_A', 'Спикер_2'].sort());
  });

  it('treats SPEAKER_SPEAKER_01 as a single speaker (no nested SPEAKER_01 / SPEAKER_SPEAKER)', () => {
    expect(extractSpeakers('SPEAKER_SPEAKER_01: привет')).toEqual(['SPEAKER_SPEAKER_01']);
  });

  it('keeps SPEAKER_1 and SPEAKER_10 as different speakers', () => {
    expect(extractSpeakers('SPEAKER_1: a\nSPEAKER_10: b')).toEqual(['SPEAKER_1', 'SPEAKER_10']);
  });

  it('does not take a speaker token from the middle of another word', () => {
    expect(extractSpeakers('xSPEAKER_01 and SPEAKER_01y')).toEqual(['SPEAKER_01y']);
  });
});

describe('applySpeakerMapping', () => {
  it('replaces SPEAKER_1 without touching SPEAKER_10', () => {
    expect(applySpeakerMapping('SPEAKER_1: а\nSPEAKER_10: б', { SPEAKER_1: 'Аня' })).toBe('Аня: а\nSPEAKER_10: б');
  });

  it('replaces both when both are mapped, regardless of key order', () => {
    const text = 'SPEAKER_1: а\nSPEAKER_10: б';
    expect(applySpeakerMapping(text, { SPEAKER_1: 'Аня', SPEAKER_10: 'Борис' })).toBe('Аня: а\nБорис: б');
    expect(applySpeakerMapping(text, { SPEAKER_10: 'Борис', SPEAKER_1: 'Аня' })).toBe('Аня: а\nБорис: б');
  });

  it('replaces SPEAKER_SPEAKER_01 as a whole and leaves SPEAKER_01 alone', () => {
    expect(
      applySpeakerMapping('SPEAKER_SPEAKER_01: a\nSPEAKER_01: b', { SPEAKER_SPEAKER_01: 'Вера' }),
    ).toBe('Вера: a\nSPEAKER_01: b');
  });

  it('skips empty names and does not interpret $ patterns in names', () => {
    expect(applySpeakerMapping('SPEAKER_00: a', { SPEAKER_00: '   ' })).toBe('SPEAKER_00: a');
    expect(applySpeakerMapping('SPEAKER_00: a', { SPEAKER_00: 'A$&B' })).toBe('A$&B: a');
  });

  it('does not re-map a name that looks like another speaker (single pass)', () => {
    expect(applySpeakerMapping('SPEAKER_1: a', { SPEAKER_1: 'SPEAKER_2', SPEAKER_2: 'X' })).toBe('SPEAKER_2: a');
  });
});
