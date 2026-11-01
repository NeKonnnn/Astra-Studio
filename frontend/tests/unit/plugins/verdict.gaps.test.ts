import { extractMarkdownFromResponse } from '@/plugins/verdict';

describe('extractMarkdownFromResponse: прямой verdict_markdown', () => {
  it('берёт verdict_markdown верхнего уровня result, если вложенный result пуст', () => {
    const res: any = { success: true, plugin_id: 'p', result: { verdict_markdown: '  ВЕРДИКТ  ', result: {} } };
    expect(extractMarkdownFromResponse(res)).toBe('ВЕРДИКТ');
  });

  it('пустой verdict_markdown даёт пустую строку', () => {
    const res: any = { success: true, plugin_id: 'p', result: { verdict_markdown: '   ', result: {} } };
    expect(extractMarkdownFromResponse(res)).toBe('');
  });

  it('markdown самого ответа приоритетнее', () => {
    const res: any = { success: true, plugin_id: 'p', markdown: ' # md ', result: {} };
    expect(extractMarkdownFromResponse(res)).toBe('# md');
  });
});
