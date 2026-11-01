import { getApiUrl } from '../config/api';
import type { Chat, Message, MessageFeedback, MultiLLMResponseSlot } from '../contexts/AppContext';
import { mapChainStepsFromMeta } from '../constants/agentChain';
import { normalizeMcpToolCallList } from '../mcp/utils/normalizeToolCall';

function mapFeedbackFromMeta(raw: unknown): MessageFeedback | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const fb = raw as Record<string, unknown>;
  const rating = fb.rating === 'like' || fb.rating === 'dislike' ? fb.rating : null;
  if (!rating) return undefined;
  const tags = Array.isArray(fb.tags) ? fb.tags.map((t) => String(t)) : [];
  const comment = typeof fb.comment === 'string' ? fb.comment : undefined;
  const updatedAt =
    typeof fb.updated_at === 'string'
      ? fb.updated_at
      : typeof fb.updatedAt === 'string'
        ? fb.updatedAt
        : undefined;
  return {
    rating,
    ...(tags.length ? { tags } : {}),
    ...(comment ? { comment } : {}),
    ...(updatedAt ? { updatedAt } : {}),
  };
}

function mapDocumentSearchFromMeta(raw: unknown): Message['documentSearch'] | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const ds = raw as Record<string, unknown>;
  const hitsRaw = Array.isArray(ds.hits) ? ds.hits : [];
  const hits = hitsRaw
    .filter((h): h is Record<string, unknown> => Boolean(h) && typeof h === 'object')
    .map((h) => ({
      file: String(h.file ?? ''),
      anchor: String(h.anchor ?? ''),
      relevance: Number(h.relevance ?? 0),
      content: String(h.content ?? ''),
      chunkIndex: Number(h.chunkIndex ?? h.chunk_index ?? 0),
      documentId: Number(h.documentId ?? h.document_id ?? 0),
      store: String(h.store ?? ''),
    }));
  const sourceFiles = Array.isArray(ds.sourceFiles)
    ? ds.sourceFiles.map(String)
    : Array.from(new Set(hits.map((h) => h.file).filter(Boolean)));
  if (!hits.length && !sourceFiles.length && !String(ds.query ?? '').trim()) {
    return undefined;
  }
  return {
    query: String(ds.query ?? ''),
    sourceFiles,
    hits,
  };
}

export function mapInlineAttachmentRecords(
  raw: unknown,
): NonNullable<Message['inlineAttachments']> | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const items = raw
    .filter((a): a is Record<string, unknown> => Boolean(a) && typeof a === 'object')
    .map((a) => {
      const rawType = a.contentType;
      const contentType =
        rawType === 'video' ? ('video' as const) : rawType === 'image' ? ('image' as const) : ('text' as const);
      const dataUri = a.data_uri ? String(a.data_uri) : undefined;
      const minioObject = a.minio_object ? String(a.minio_object) : undefined;
      const minioBucket = a.minio_bucket ? String(a.minio_bucket) : undefined;
      let preview: string | undefined;
      if ((contentType === 'image' || contentType === 'video') && dataUri) {
        preview = dataUri;
      } else if ((contentType === 'image' || contentType === 'video') && minioObject && minioBucket) {
        preview = getApiUrl(
          `/api/documents/inline-file?bucket=${encodeURIComponent(minioBucket)}&object=${encodeURIComponent(minioObject)}`,
        );
      }
      const tokenEstimate =
        typeof a.tokenEstimate === 'number' && a.tokenEstimate > 0
          ? Math.ceil(a.tokenEstimate)
          : typeof a.token_estimate === 'number' && a.token_estimate > 0
            ? Math.ceil(a.token_estimate)
            : undefined;
      return {
        name: String(a.name || 'file'),
        contentType,
        ...(preview ? { preview } : {}),
        ...(typeof a.size === 'number' && a.size > 0 ? { size: a.size } : {}),
        ...(tokenEstimate ? { tokenEstimate } : {}),
      };
    });
  return items.length > 0 ? items : undefined;
}

export function mapServerConversationToChat(conversation: any): Chat {
  const messages: Message[] = Array.isArray(conversation?.messages)
    ? conversation.messages.map((msg: any) => {
        const rawContent = String(msg?.content || '');
        const metadata = msg?.metadata && typeof msg.metadata === 'object' ? msg.metadata : {};
        const reasoningContent = String(
          metadata?.reasoning_content || metadata?.reasoning || '',
        ).trim();
        const hasThinkTag = /<think>/i.test(rawContent);
        const mergedContent =
          reasoningContent && !hasThinkTag
            ? `<think>${reasoningContent}</think>\n\n${rawContent}`
            : rawContent;

        const rawInline = metadata?.inline_attachments;
        const inlineAttachments = mapInlineAttachmentRecords(rawInline);

        const rawImageVariants = metadata?.image_variants;
        const inlineAttachmentVariants = Array.isArray(rawImageVariants)
          ? rawImageVariants
              .map((block: unknown) =>
                block && typeof block === 'object'
                  ? mapInlineAttachmentRecords((block as Record<string, unknown>).inline_attachments)
                  : undefined,
              )
              .filter((v): v is NonNullable<Message['inlineAttachments']> => Boolean(v?.length))
          : undefined;
        const currentImageVariantIndex =
          typeof metadata?.current_image_variant_index === 'number'
            ? metadata.current_image_variant_index
            : undefined;
        const rawAltResponses = Array.isArray(metadata?.alternative_responses)
          ? metadata.alternative_responses.map((v: unknown) => String(v ?? ''))
          : Array.isArray(metadata?.alternativeResponses)
            ? metadata.alternativeResponses.map((v: unknown) => String(v ?? ''))
            : undefined;
        const currentAltIndex =
          typeof metadata?.current_response_index === 'number'
            ? metadata.current_response_index
            : typeof metadata?.currentResponseIndex === 'number'
              ? metadata.currentResponseIndex
              : undefined;

        const mcpToolCalls = normalizeMcpToolCallList(metadata?.mcp_tool_calls);
        const rawMultiResponses = Array.isArray(metadata?.multi_llm_responses)
          ? metadata.multi_llm_responses
          : Array.isArray(metadata?.multiLLMResponses)
            ? metadata.multiLLMResponses
            : [];
        const multiLLMResponses: MultiLLMResponseSlot[] = rawMultiResponses
          .filter((slot: unknown) => slot && typeof slot === 'object')
          .map((slot: Record<string, unknown>) => {
            const rawAlternatives = Array.isArray(slot.alternativeResponses)
              ? slot.alternativeResponses
              : Array.isArray(slot.alternative_responses)
                ? slot.alternative_responses
                : undefined;
            const currentResponseIndexRaw =
              typeof slot.currentResponseIndex === 'number'
                ? slot.currentResponseIndex
                : typeof slot.current_response_index === 'number'
                  ? slot.current_response_index
                  : undefined;
            const slotFeedback = mapFeedbackFromMeta(slot.feedback);
            return {
              model: String(slot.model || 'unknown'),
              content: String(slot.content || ''),
              isStreaming: false,
              error: Boolean(slot.error),
              ...(rawAlternatives
                ? { alternativeResponses: rawAlternatives.map((v: unknown) => String(v ?? '')) }
                : {}),
              ...(typeof currentResponseIndexRaw === 'number'
                ? { currentResponseIndex: currentResponseIndexRaw }
                : {}),
              ...(slotFeedback ? { feedback: slotFeedback } : {}),
              ...(typeof slot.generation_duration_sec === 'number' && slot.generation_duration_sec > 0
                ? { generationDurationSec: Math.round(slot.generation_duration_sec) }
                : typeof slot.generationDurationSec === 'number' && slot.generationDurationSec > 0
                  ? { generationDurationSec: Math.round(slot.generationDurationSec as number) }
                  : {}),
            };
          });

        const messageFeedback = mapFeedbackFromMeta(metadata?.feedback);
        const documentSearch = mapDocumentSearchFromMeta(
          metadata?.document_search ?? metadata?.documentSearch,
        );
        const generationDurationRaw =
          metadata?.generation_duration_sec ?? metadata?.generationDurationSec;
        const generationDurationSec =
          typeof generationDurationRaw === 'number' && generationDurationRaw > 0
            ? Math.round(generationDurationRaw)
            : undefined;
        // Если у слотов нет своей длительности — берём с сообщения (старые записи / общий таймер).
        const multiLLMResponsesWithDuration =
          multiLLMResponses.length && generationDurationSec
            ? multiLLMResponses.map((slot) =>
                slot.generationDurationSec && slot.generationDurationSec > 0
                  ? slot
                  : { ...slot, generationDurationSec },
              )
            : multiLLMResponses;

        const chainSteps = mapChainStepsFromMeta(metadata?.chain_steps ?? metadata?.chainSteps);

        return {
          id: String(msg?.message_id || `msg_${Math.random().toString(36).slice(2, 14)}`),
          role: msg?.role === 'assistant' ? 'assistant' : 'user',
          content: mergedContent,
          timestamp: String(msg?.timestamp || new Date().toISOString()),
          isStreaming: false,
          ...(reasoningContent ? { reasoningContent } : {}),
          ...(inlineAttachments?.length ? { inlineAttachments } : {}),
          ...(inlineAttachmentVariants?.length ? { inlineAttachmentVariants } : {}),
          ...(rawAltResponses?.length ? { alternativeResponses: rawAltResponses } : {}),
          ...(typeof currentAltIndex === 'number'
            ? { currentResponseIndex: currentAltIndex }
            : typeof currentImageVariantIndex === 'number'
              ? { currentResponseIndex: currentImageVariantIndex }
              : rawAltResponses && rawAltResponses.length > 1
                ? { currentResponseIndex: rawAltResponses.length - 1 }
                : {}),
          ...(mcpToolCalls.length ? { mcpToolCalls } : {}),
          ...(multiLLMResponsesWithDuration.length
            ? { multiLLMResponses: multiLLMResponsesWithDuration }
            : {}),
          ...(messageFeedback ? { feedback: messageFeedback } : {}),
          ...(documentSearch ? { documentSearch } : {}),
          ...(generationDurationSec ? { generationDurationSec } : {}),
          ...(chainSteps ? { chainSteps } : {}),
          ...(metadata?.hide_sequential_outputs || metadata?.hideSequentialOutputs
            ? { hideSequentialOutputs: true }
            : {}),
        } as Message;
      })
    : [];

  const firstUserContent =
    messages.find((m) => m.role === 'user')?.content?.slice(0, 60) || 'Новый чат';

  const storedTitle = conversation?.title ? String(conversation.title).trim() : '';

  return {
    id: String(conversation?.conversation_id || Date.now().toString()),
    title: storedTitle || firstUserContent,
    messages,
    createdAt: String(conversation?.created_at || new Date().toISOString()),
    updatedAt: String(conversation?.updated_at || new Date().toISOString()),
    projectId: conversation?.project_id ? String(conversation.project_id) : undefined,
    isArchived: Boolean(conversation?.metadata?.archived),
    ...(conversation?.metadata?.hidden_from_sidebar_until_user_message
      ? { hiddenFromSidebarUntilUserMessage: true }
      : {}),
  };
}
