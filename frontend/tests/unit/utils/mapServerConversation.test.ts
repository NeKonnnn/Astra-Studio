import { mapInlineAttachmentRecords, mapServerConversationToChat } from '@/utils/mapServerConversation';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (path: string) => `http://api.test${path}`,
}));

function conversation(messages: unknown[], extra: Record<string, unknown> = {}) {
  return { conversation_id: 'c1', messages, ...extra };
}

describe('mapInlineAttachmentRecords', () => {
  it.each([undefined, null, 'x', [], {}])('returns undefined for %p', (raw) => {
    expect(mapInlineAttachmentRecords(raw)).toBeUndefined();
  });

  it('maps a text attachment', () => {
    expect(mapInlineAttachmentRecords([{ name: 'a.txt', size: 12, token_estimate: 3.2 }])).toEqual([
      { name: 'a.txt', contentType: 'text', size: 12, tokenEstimate: 4 },
    ]);
  });

  it('prefers camelCase tokenEstimate and ignores non-positive values', () => {
    const [item] = mapInlineAttachmentRecords([{ name: 'a', tokenEstimate: 5, token_estimate: 9, size: 0 }])!;

    expect(item.tokenEstimate).toBe(5);
    expect('size' in item).toBe(false);
  });

  it('uses the data URI as the image preview', () => {
    const [item] = mapInlineAttachmentRecords([
      { name: 'p.png', contentType: 'image', data_uri: 'data:image/png;base64,AAA' },
    ])!;

    expect(item.contentType).toBe('image');
    expect(item.preview).toBe('data:image/png;base64,AAA');
  });

  it('builds an encoded API URL for stored images', () => {
    const [item] = mapInlineAttachmentRecords([
      { name: 'p.png', contentType: 'image', minio_bucket: 'b ucket', minio_object: 'dir/a&b.png' },
    ])!;

    expect(item.preview).toBe(
      'http://api.test/api/documents/inline-file?bucket=b%20ucket&object=dir%2Fa%26b.png',
    );
  });

  it('gives text attachments no preview and defaults the name', () => {
    const [item] = mapInlineAttachmentRecords([{ data_uri: 'data:x', minio_bucket: 'b', minio_object: 'o' }])!;

    expect(item.name).toBe('file');
    expect(item.preview).toBeUndefined();
  });

  it('skips non-object entries', () => {
    expect(mapInlineAttachmentRecords([null, 'x', { name: 'ok' }])).toHaveLength(1);
  });
});

describe('mapServerConversationToChat', () => {
  it('maps conversation fields', () => {
    const chat = mapServerConversationToChat(
      conversation([], {
        title: ' Мой чат ',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-02T00:00:00Z',
        project_id: 42,
        metadata: { archived: true, hidden_from_sidebar_until_user_message: true },
      }),
    );

    expect(chat).toEqual({
      id: 'c1',
      title: 'Мой чат',
      messages: [],
      createdAt: '2026-01-01T00:00:00Z',
      updatedAt: '2026-01-02T00:00:00Z',
      projectId: '42',
      isArchived: true,
      hiddenFromSidebarUntilUserMessage: true,
    });
  });

  it('survives garbage input', () => {
    const chat = mapServerConversationToChat(null);

    expect(chat.messages).toEqual([]);
    expect(chat.title).toBe('Новый чат');
    expect(chat.isArchived).toBe(false);
    expect(chat.projectId).toBeUndefined();
    expect('hiddenFromSidebarUntilUserMessage' in chat).toBe(false);
  });

  it('titles an untitled chat by the first user message (60 chars)', () => {
    const chat = mapServerConversationToChat(
      conversation([
        { role: 'assistant', content: 'привет' },
        { role: 'user', content: 'в'.repeat(100) },
      ]),
    );

    expect(chat.title).toBe('в'.repeat(60));
  });

  it('maps basic messages', () => {
    const [m] = mapServerConversationToChat(
      conversation([{ message_id: 'm1', role: 'assistant', content: 'Ответ', timestamp: 't1' }]),
    ).messages;

    expect(m).toMatchObject({ id: 'm1', role: 'assistant', content: 'Ответ', timestamp: 't1', isStreaming: false });
  });

  it('treats unknown roles as user and generates ids and timestamps', () => {
    const [m] = mapServerConversationToChat(conversation([{ role: 'system', content: 'x' }])).messages;

    expect(m.role).toBe('user');
    expect(m.id).toMatch(/^msg_/);
    expect(typeof m.timestamp).toBe('string');
  });

  it('folds stored reasoning into a think block', () => {
    const [m] = mapServerConversationToChat(
      conversation([{ role: 'assistant', content: 'Ответ', metadata: { reasoning_content: ' мысли ' } }]),
    ).messages;

    expect(m.content).toBe('<think>мысли</think>\n\nОтвет');
    expect(m.reasoningContent).toBe('мысли');
  });

  it('does not duplicate reasoning when the content already has a think tag', () => {
    const [m] = mapServerConversationToChat(
      conversation([{ role: 'assistant', content: '<think>a</think>b', metadata: { reasoning: 'a' } }]),
    ).messages;

    expect(m.content).toBe('<think>a</think>b');
  });

  it('reads alternative responses and defaults the current index to the last one', () => {
    const [m] = mapServerConversationToChat(
      conversation([{ role: 'assistant', content: 'x', metadata: { alternative_responses: ['a', 'b', 3] } }]),
    ).messages;

    expect(m.alternativeResponses).toEqual(['a', 'b', '3']);
    expect(m.currentResponseIndex).toBe(2);
  });

  it('prefers the stored current response index', () => {
    const [m] = mapServerConversationToChat(
      conversation([
        { role: 'assistant', content: 'x', metadata: { alternativeResponses: ['a', 'b'], currentResponseIndex: 0 } },
      ]),
    ).messages;

    expect(m.currentResponseIndex).toBe(0);
  });

  it('maps multi-LLM slots and falls back to the message duration', () => {
    const [m] = mapServerConversationToChat(
      conversation([
        {
          role: 'assistant',
          content: '',
          metadata: {
            generation_duration_sec: 12.4,
            multi_llm_responses: [
              { model: 'a', content: 'A', generation_duration_sec: 3.6 },
              { content: 'B', error: 1, alternative_responses: ['b0'], current_response_index: 0 },
              null,
            ],
          },
        },
      ]),
    ).messages;

    expect(m.multiLLMResponses).toEqual([
      { model: 'a', content: 'A', isStreaming: false, error: false, generationDurationSec: 4 },
      {
        model: 'unknown',
        content: 'B',
        isStreaming: false,
        error: true,
        alternativeResponses: ['b0'],
        currentResponseIndex: 0,
        generationDurationSec: 12,
      },
    ]);
    expect(m.generationDurationSec).toBe(12);
  });

  it('maps valid feedback only', () => {
    const [liked, bad] = mapServerConversationToChat(
      conversation([
        {
          role: 'assistant',
          content: 'a',
          metadata: { feedback: { rating: 'like', tags: ['t', 1], comment: 'ок', updated_at: 'ts' } },
        },
        { role: 'assistant', content: 'b', metadata: { feedback: { rating: 'meh' } } },
      ]),
    ).messages;

    expect(liked.feedback).toEqual({ rating: 'like', tags: ['t', '1'], comment: 'ок', updatedAt: 'ts' });
    expect(bad.feedback).toBeUndefined();
  });

  it('maps document search hits with snake_case fallbacks', () => {
    const [m] = mapServerConversationToChat(
      conversation([
        {
          role: 'assistant',
          content: 'a',
          metadata: {
            document_search: {
              query: 'q',
              hits: [{ file: 'f.pdf', chunk_index: 3, document_id: 9, relevance: 80, store: 'kb' }, null],
            },
          },
        },
      ]),
    ).messages;

    expect(m.documentSearch).toEqual({
      query: 'q',
      sourceFiles: ['f.pdf'],
      hits: [{ file: 'f.pdf', anchor: '', relevance: 80, content: '', chunkIndex: 3, documentId: 9, store: 'kb' }],
    });
  });

  it('drops an empty document search block', () => {
    const [m] = mapServerConversationToChat(
      conversation([{ role: 'assistant', content: 'a', metadata: { document_search: { hits: [] } } }]),
    ).messages;

    expect(m.documentSearch).toBeUndefined();
  });

  it('maps inline attachments and image variants', () => {
    const [m] = mapServerConversationToChat(
      conversation([
        {
          role: 'user',
          content: 'смотри',
          metadata: {
            inline_attachments: [{ name: 'a.txt' }],
            image_variants: [{ inline_attachments: [{ name: 'v1.png', contentType: 'image', data_uri: 'data:x' }] }, {}],
            current_image_variant_index: 0,
          },
        },
      ]),
    ).messages;

    expect(m.inlineAttachments).toHaveLength(1);
    expect(m.inlineAttachmentVariants).toHaveLength(1);
    expect(m.currentResponseIndex).toBe(0);
  });

  it('flags hidden sequential outputs', () => {
    const [a, b] = mapServerConversationToChat(
      conversation([
        { role: 'assistant', content: 'a', metadata: { hide_sequential_outputs: true } },
        { role: 'assistant', content: 'b', metadata: { hideSequentialOutputs: true } },
      ]),
    ).messages;

    expect(a.hideSequentialOutputs).toBe(true);
    expect(b.hideSequentialOutputs).toBe(true);
  });

  it('adds no optional fields to a plain message', () => {
    const [m] = mapServerConversationToChat(conversation([{ message_id: 'm', role: 'user', content: 'x' }])).messages;

    expect(Object.keys(m).sort()).toEqual(['content', 'id', 'isStreaming', 'role', 'timestamp']);
  });
});
