import { groupChatsBySidebarTime } from '@/utils/chatListTimeGroups';

// Локальное время, чтобы тест не зависел от часового пояса машины.
const NOW = new Date(2026, 5, 15, 12, 0, 0); // 15 июня 2026

function chat(id: string, date: Date) {
  return { id, updatedAt: date.toISOString() };
}

function daysAgo(n: number, hour = 10) {
  return new Date(2026, 5, 15 - n, hour, 0, 0);
}

describe('groupChatsBySidebarTime', () => {
  it('returns no sections for no chats', () => {
    expect(groupChatsBySidebarTime([], NOW)).toEqual([]);
  });

  it('puts today and yesterday into their own sections', () => {
    const sections = groupChatsBySidebarTime(
      [chat('y', daysAgo(1)), chat('t', daysAgo(0))],
      NOW,
    );

    expect(sections.map((s) => [s.key, s.label])).toEqual([
      ['today', 'Сегодня'],
      ['yesterday', 'Вчера'],
    ]);
  });

  it('keeps the last-7-days window free of today and yesterday', () => {
    const sections = groupChatsBySidebarTime(
      [chat('a', daysAgo(2)), chat('b', daysAgo(7)), chat('c', daysAgo(8))],
      NOW,
    );

    const last7 = sections.find((s) => s.key === 'last7');
    expect(last7?.chats.map((c) => c.id)).toEqual(['a', 'b']);
    expect(sections.find((s) => s.key === 'last30')?.chats.map((c) => c.id)).toEqual(['c']);
  });

  it('puts 8..30 days into the last-30-days section', () => {
    const sections = groupChatsBySidebarTime([chat('a', daysAgo(30)), chat('b', daysAgo(31))], NOW);

    expect(sections.find((s) => s.key === 'last30')?.chats.map((c) => c.id)).toEqual(['a']);
    expect(sections.find((s) => s.key === 'last30')).toBeDefined();
  });

  it('groups older chats of the current year by month, newest month first', () => {
    const sections = groupChatsBySidebarTime(
      [
        chat('jan', new Date(2026, 0, 10)),
        chat('mar', new Date(2026, 2, 10)),
        chat('feb', new Date(2026, 1, 10)),
      ],
      NOW,
    );

    expect(sections.map((s) => s.label)).toEqual(['Март 2026', 'Февраль 2026', 'Январь 2026']);
  });

  it('groups previous years by year, newest year first, after months', () => {
    const sections = groupChatsBySidebarTime(
      [
        chat('2024', new Date(2024, 4, 1)),
        chat('2025', new Date(2025, 4, 1)),
        chat('jan', new Date(2026, 0, 10)),
      ],
      NOW,
    );

    expect(sections.map((s) => s.label)).toEqual(['Январь 2026', '2025', '2024']);
  });

  it('orders chats inside a section from newest to oldest', () => {
    const sections = groupChatsBySidebarTime(
      [chat('old', daysAgo(0, 8)), chat('new', daysAgo(0, 11)), chat('mid', daysAgo(0, 9))],
      NOW,
    );

    expect(sections[0].chats.map((c) => c.id)).toEqual(['new', 'mid', 'old']);
  });

  it('orders sections: fixed windows, then months, then years', () => {
    const sections = groupChatsBySidebarTime(
      [
        chat('year', new Date(2023, 1, 1)),
        chat('month', new Date(2026, 0, 5)),
        chat('last30', daysAgo(20)),
        chat('today', daysAgo(0)),
        chat('last7', daysAgo(3)),
        chat('yesterday', daysAgo(1)),
      ],
      NOW,
    );

    expect(sections.map((s) => s.key)).toEqual([
      'today',
      'yesterday',
      'last7',
      'last30',
      'month-2026-0',
      'year-2023',
    ]);
  });

  it('puts chats with an invalid date into a fallback section', () => {
    const sections = groupChatsBySidebarTime([{ id: 'x', updatedAt: 'not-a-date' }], NOW);

    expect(sections).toEqual([{ key: 'unknown', label: 'Ранее', chats: [{ id: 'x', updatedAt: 'not-a-date' }] }]);
  });

  it('does not mutate the input array', () => {
    const input = [chat('a', daysAgo(5)), chat('b', daysAgo(0))];
    const snapshot = [...input];

    groupChatsBySidebarTime(input, NOW);

    expect(input).toEqual(snapshot);
  });

  it('preserves extra fields of the chat objects', () => {
    const sections = groupChatsBySidebarTime([{ ...chat('a', daysAgo(0)), title: 'Мой чат' }], NOW);

    expect(sections[0].chats[0]).toMatchObject({ id: 'a', title: 'Мой чат' });
  });
});
