/**
 * Разбор и очистка рассуждений модели для UI: ответ без цепочки + блок «рассуждение».
 */

/** Сиротский </think> без открывающего (Qwen3.5 / GLM в fast-режиме). */
function peelOrphanCloseThink(
  visible: string,
): { visible: string; extraReasoning: string | null } {
  const closeRe = /<\/(?:think|redacted_thinking)>/i;
  const m = visible.match(closeRe);
  if (!m || m.index === undefined) {
    return { visible, extraReasoning: null };
  }
  const reasoning = visible.slice(0, m.index).trim();
  const rest = visible.slice(m.index + m[0].length).trim();
  return { visible: rest, extraReasoning: reasoning || null };
}


export function stripReasoningMarkers(raw: string): string {
  if (!raw) return '';
  let visible = raw;
  visible = visible.replace(/<think>[\s\S]*?<\/redacted_thinking>/gi, '');
  visible = visible.replace(/<think>[\s\S]*?<\/think>/gi, '');
  visible = visible.replace(/<think>[\s\S]*$/i, '');
  visible = visible.replace(/<think>\s*$/gi, '');
  const orphan = peelOrphanCloseThink(visible);
  visible = orphan.visible;
  return visible.trim();
}

/** Заголовки «рассуждение в plain text», когда провайдер не шлёт отдельный reasoning-канал. */
const PLAINTEXT_THINKING_HEADER_RE =
  /^\s*(Thinking Process|Chain of Thought|Рассуждение(?:\s+модели)?)\s*:?\s*\r?\n/i;

/**
 * Ищет первую границу `\n\n` после блока анализа, за которой начинается видимый ответ
 * (не нумерованный подпункт, не маркер «Wait», не внутренняя секция **Drafting** / **Final Check**).
 */
function findPlaintextThinkingBodySplitIndex(s: string): number {
  const minBreak = Math.min(500, Math.max(160, Math.floor(s.length * 0.12)));
  let idx = s.indexOf('\n\n', 24);
  while (idx !== -1) {
    if (idx + 2 < minBreak) {
      idx = s.indexOf('\n\n', idx + 2);
      continue;
    }
    const after = s.slice(idx + 2);
    const nlPos = after.search(/\r?\n/);
    const firstLine = (nlPos === -1 ? after : after.slice(0, nlPos)).replace(/\s+$/, '');
    if (!firstLine) {
      idx = s.indexOf('\n\n', idx + 2);
      continue;
    }
    if (/^\d+\.\s+\*\*/.test(firstLine)) {
      idx = s.indexOf('\n\n', idx + 2);
      continue;
    }
    if (/^Wait,\s/i.test(firstLine)) {
      idx = s.indexOf('\n\n', idx + 2);
      continue;
    }
    if (/^\*\s+/.test(firstLine)) {
      idx = s.indexOf('\n\n', idx + 2);
      continue;
    }
    if (
      /^\*\*(?:Analyze|Drafting|Refining|Final Check|Constraint|General|Context|Request)/i.test(
        firstLine,
      )
    ) {
      idx = s.indexOf('\n\n', idx + 2);
      continue;
    }
    if (/^[-•]\s/.test(firstLine)) {
      idx = s.indexOf('\n\n', idx + 2);
      continue;
    }
    return idx;
  }
  return -1;
}

function peelPlaintextThinkingPrefix(
  visible: string,
  isStreaming?: boolean,
): { visible: string; extraReasoning: string | null; isPlainThinkingStreaming: boolean } {
  const trimmed = visible.replace(/^\uFEFF/, '');
  const m = trimmed.match(PLAINTEXT_THINKING_HEADER_RE);
  if (!m || m.index !== 0) {
    return { visible, extraReasoning: null, isPlainThinkingStreaming: false };
  }
  const splitAt = findPlaintextThinkingBodySplitIndex(trimmed);
  if (splitAt >= 0) {
    const reasoning = trimmed.slice(0, splitAt).trim();
    const rest = trimmed.slice(splitAt + 2).trim();
    return { visible: rest, extraReasoning: reasoning || null, isPlainThinkingStreaming: false };
  }
  if (isStreaming) {
    return { visible: '', extraReasoning: trimmed.trim() || null, isPlainThinkingStreaming: true };
  }
  return { visible: trimmed, extraReasoning: null, isPlainThinkingStreaming: false };
}

export function extractReasoningBlock(
  rawText: string,
  isStreaming?: boolean,
): { visibleContent: string; reasoningContent: string | null; isThinkingStreaming: boolean } {
  if (!rawText) return { visibleContent: rawText, reasoningContent: null, isThinkingStreaming: false };
  const reasoningParts: string[] = [];
  let visible = rawText;
  let isThinkingStreaming = false;

  const strip = (re: RegExp) => {
    visible = visible.replace(re, (_, inner: string) => {
      const normalized = (inner || '').trim();
      if (normalized) reasoningParts.push(normalized);
      return '';
    });
  };

  strip(/<think>([\s\S]*?)<\/redacted_thinking>/gi);
  strip(/<think>([\s\S]*?)<\/think>/gi);

  const unclosedMatch = visible.match(/<think>([\s\S]*)$/i);
  if (unclosedMatch) {
    const thinkContent = (unclosedMatch[1] || '').trim();
    if (thinkContent) reasoningParts.push(thinkContent);
    visible = visible.slice(0, unclosedMatch.index ?? visible.length).trim();
    if (isStreaming) isThinkingStreaming = true;
  }

  visible = visible.replace(/<think>\s*$/gi, '').trim();

  const orphan = peelOrphanCloseThink(visible);
  if (orphan.extraReasoning) {
    reasoningParts.push(orphan.extraReasoning);
    visible = orphan.visible;
  }

  const peel = peelPlaintextThinkingPrefix(visible, isStreaming);
  visible = peel.visible;
  if (peel.extraReasoning) reasoningParts.push(peel.extraReasoning);
  if (peel.isPlainThinkingStreaming) isThinkingStreaming = true;

  return {
    visibleContent: visible || (reasoningParts.length > 0 || isThinkingStreaming ? '' : rawText),
    // Пустая строка при незакрытом <think> — чтобы UI мог смонтировать рамку до первого токена.
    reasoningContent:
      reasoningParts.length > 0
        ? reasoningParts.join('\n\n')
        : isThinkingStreaming
          ? ''
          : null,
    isThinkingStreaming,
  };
}

/** Финализация после chat_complete: отдельный trace из сокета или теги внутри response (legacy). */
export function finalizeVisibleAndReasoning(
  response: string,
  thinkingTrace: string,
): { content: string; reasoningContent?: string } {
  const t = (thinkingTrace || '').trim();
  if (t) {
    const noTags = stripReasoningMarkers(response) || response.trimEnd();
    const peeled = extractReasoningBlock(noTags, false);
    const c = ((peeled.visibleContent || '').trim() || noTags).trimEnd();
    return { content: c, reasoningContent: t };
  }
  const p = extractReasoningBlock(response, false);
  return {
    content: p.visibleContent || response,
    ...(p.reasoningContent ? { reasoningContent: p.reasoningContent } : {}),
  };
}
