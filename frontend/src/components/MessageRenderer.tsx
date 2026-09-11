import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Box, IconButton, Typography, Tooltip, Link, Paper, Table, TableBody, TableCell, TableContainer, TableHead, TableRow } from '@mui/material';
import { ContentCopy as CopyIcon, Check as CheckIcon, Info as InfoIcon, Warning as WarningIcon, Error as ErrorIcon, CheckCircle as SuccessIcon, GetApp as DownloadIcon,
  KeyboardArrowDown as KeyboardArrowDownIcon,
  KeyboardArrowUp as KeyboardArrowUpIcon,
} from '@mui/icons-material';
import {
  artifactMetaLooksLikePresentation,
  coalescePresentationHtmlInMessage,
  extractUnfencedPresentationHtml,
  hasGpbSlideClass,
  isGpbPresentationHtml,
  isGpbPresentationStreaming,
  isHtmlFenceBlock,
  isHtmlFenceLanguage,
  shouldOpenPresentationViewer,
  shouldTreatHtmlFenceAsPresentationStream,
} from '../utils/presentationViewer';
import InlinePresentationViewer from './InlinePresentationViewer';
import ArtifactCard from './artifacts/ArtifactCard';
import { sanitizeMermaidSource, splitContentWithArtifacts, hoistPresentationArtifacts, guessCodeLanguage } from '../utils/artifacts';
import {
  looksLikeAsciiArtTable,
  splitTextWithMarkdownTables,
} from '../utils/markdownTables';
import { normalizeChatInlineHtml, extractPreservedHtmlBlocks } from '../utils/chatInlineHtml';
import Editor, { loader } from '@monaco-editor/react';
import * as XLSX from 'xlsx';
import CodeSelectionMenu from './CodeSelectionMenu';
import ChatInlineHtml from './ChatInlineHtml';
import {
  useArtifactsViewerAllowed,
  useArtifactsViewerLiveGate,
  usePresentationViewerExpected,
} from '../hooks/useArtifactsViewerGate';
import { pinMessageArtifactsViewer } from '../utils/messageArtifactsViewerStorage';
import { useAppContext } from '../contexts/AppContext';
import { useChatFontSize } from '../hooks/useChatFontSize';

// Monaco загружается как статические файлы (не через webpack-бандл).
// Файлы копируются в public/monaco через scripts/copy-monaco-assets.js (prestart/prebuild).
loader.config({
  paths: { vs: `${process.env.PUBLIC_URL || ''}/monaco/vs` },
});

// Начинаем загрузку Monaco сразу при импорте модуля,
// чтобы к моменту рендера редактора он уже был готов.
loader.init();

interface MessageRendererProps {
  content: string;
  isStreaming?: boolean;
  onSendMessage?: (message: string) => void;
  /** Стабильный id сообщения — для ключей артефактов. */
  messageId?: string;
  /** Чат для per-chat skills / viewer gate. */
  chatId?: string | null;
  /**
   * Принудительно разрешить viewer (шаринг и т.п.).
   * Иначе: агент.artifacts_enabled, presentation skill, или уже закреплённый показ.
   */
  forceArtifacts?: boolean;
}

/** Markdown-заголовки: чуть крупнее body, без MUI h1–h6 (там 2–3rem). */
const MARKDOWN_HEADING_SCALE: Record<string, number> = {
  '1': 1.35,
  '2': 1.2,
  '3': 1.1,
  '4': 1.05,
  '5': 1.03,
  '6': 1.0,
};

/** Расширение файла для скачивания блока кода по языку fence / Monaco. */
const LANGUAGE_EXTENSIONS: Record<string, string> = {
  javascript: 'js',
  typescript: 'ts',
  python: 'py',
  ruby: 'rb',
  bash: 'sh',
  shell: 'sh',
  sh: 'sh',
  zsh: 'sh',
  powershell: 'ps1',
  ps1: 'ps1',
  batch: 'bat',
  cmd: 'bat',
  yaml: 'yml',
  yml: 'yml',
  json: 'json',
  html: 'html',
  css: 'css',
  scss: 'scss',
  less: 'less',
  xml: 'xml',
  sql: 'sql',
  java: 'java',
  kotlin: 'kt',
  go: 'go',
  rust: 'rs',
  rs: 'rs',
  c: 'c',
  cpp: 'cpp',
  'c++': 'cpp',
  cc: 'cpp',
  cxx: 'cpp',
  csharp: 'cs',
  'c#': 'cs',
  cs: 'cs',
  php: 'php',
  swift: 'swift',
  r: 'r',
  matlab: 'm',
  lua: 'lua',
  perl: 'pl',
  haskell: 'hs',
  hs: 'hs',
  scala: 'scala',
  dart: 'dart',
  vb: 'vb',
  vba: 'vba',
  pascal: 'pas',
  fortran: 'f90',
  f90: 'f90',
  f95: 'f95',
  dockerfile: 'Dockerfile',
  makefile: 'Makefile',
  markdown: 'md',
  md: 'md',
  plaintext: 'txt',
  text: 'txt',
  txt: 'txt',
  js: 'js',
  ts: 'ts',
  jsx: 'jsx',
  tsx: 'tsx',
  py: 'py',
  rb: 'rb',
};

const getCodeFileExtension = (language: string): string => {
  const key = (language || 'txt').toLowerCase().trim();
  return LANGUAGE_EXTENSIONS[key] || key.replace(/[^a-z0-9_+-]/gi, '') || 'txt';
};

function markdownHeadingFontSize(level: string, baseFontSize: string): string {
  const scale = MARKDOWN_HEADING_SCALE[level] ?? 1.1;
  return `calc(${baseFontSize} * ${scale})`;
}

const COLLAPSED_CODE_HEIGHT = 320;

type CollapsibleCodeBlockProps = {
  code: string;
  language: string;
  editorLanguage: string;
  editorPath: string;
  codeLineCount: number;
  fullEditorHeight: number;
  isStreaming?: boolean;
  isPresentationHtml?: boolean;
  copiedCode: string | null;
  onCopy: (code: string) => void;
  onDownload: (code: string, language: string) => void;
  blockKey?: React.Key;
};

/**
 * Отдельный компонент: стейт expand живёт здесь.
 * MessageRenderer кеширует parseMarkdown в useMemo — родительский setState
 * иначе не пересобирает JSX блока кода.
 */
const CollapsibleCodeBlock = React.memo(function CollapsibleCodeBlock({
  code,
  language,
  editorLanguage,
  editorPath,
  codeLineCount,
  fullEditorHeight,
  isStreaming,
  isPresentationHtml,
  copiedCode,
  onCopy,
  onDownload,
  blockKey,
}: CollapsibleCodeBlockProps) {
  const [expanded, setExpanded] = useState(false);
  const editorRef = useRef<{ layout: () => void } | null>(null);

  const canCollapse = !isPresentationHtml && fullEditorHeight > COLLAPSED_CODE_HEIGHT;
  const isExpanded = expanded || !canCollapse;
  const editorHeight = canCollapse && !expanded ? COLLAPSED_CODE_HEIGHT : fullEditorHeight;
  const needsInnerScroll = canCollapse && !expanded;

  useEffect(() => {
    if (!editorRef.current) return;
    requestAnimationFrame(() => {
      editorRef.current?.layout();
      requestAnimationFrame(() => editorRef.current?.layout());
    });
  }, [editorHeight, expanded]);

  const toggle = (event: React.MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    setExpanded((prev) => !prev);
  };

  return (
    <Box
      key={isPresentationHtml ? undefined : blockKey}
      sx={{ position: 'relative', my: isPresentationHtml ? 0 : 2, maxWidth: '100%', minWidth: 0 }}
    >
      <Box
        sx={{
          backgroundColor: '#1e1e1e',
          borderRadius: 1,
          p: 0,
          position: 'relative',
          overflow: 'hidden',
          maxWidth: '100%',
          minWidth: 0,
        }}
      >
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            px: 2,
            py: 1,
            backgroundColor: '#2d2d30',
            borderBottom: '1px solid #3e3e42',
          }}
        >
          <Typography
            variant="caption"
            sx={{
              color: '#cccccc',
              fontFamily: 'monospace',
              textTransform: 'uppercase',
              fontSize: '0.75rem',
              fontWeight: 'bold',
            }}
          >
            {language}
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
            <Tooltip title={copiedCode === code ? '✓ Скопировано!' : 'Копировать код'}>
              <IconButton
                size="small"
                onClick={() => onCopy(code)}
                sx={{
                  color: '#cccccc',
                  transition: 'all 0.2s',
                  '&:hover': {
                    backgroundColor: 'rgba(255,255,255,0.1)',
                    color: '#4ec9b0',
                  },
                }}
              >
                {copiedCode === code ? (
                  <CheckIcon fontSize="small" sx={{ color: '#4ec9b0' }} />
                ) : (
                  <CopyIcon fontSize="small" />
                )}
              </IconButton>
            </Tooltip>
            <Tooltip title="Скачать файл">
              <IconButton
                size="small"
                onClick={() => onDownload(code, language)}
                sx={{
                  color: '#cccccc',
                  transition: 'all 0.2s',
                  '&:hover': {
                    backgroundColor: 'rgba(255,255,255,0.1)',
                    color: '#4ec9b0',
                  },
                }}
              >
                <DownloadIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>
        </Box>

        <Box
          sx={{
            cursor: 'text',
            userSelect: 'text',
            position: 'relative',
            maxWidth: '100%',
            minWidth: 0,
            overflowX: 'auto',
            '& .monaco-editor': {
              maxWidth: '100% !important',
            },
            '& .monaco-editor .margin': {
              backgroundColor: '#1e1e1e',
            },
            '& .monaco-editor .margin-view-overlays .line-numbers': {
              width: '100% !important',
              textAlign: 'right',
              paddingRight: '8px',
              boxSizing: 'border-box',
            },
          }}
        >
          {isStreaming ? (
            <Box
              component="pre"
              sx={{
                m: 0,
                px: 2,
                py: 1.5,
                color: '#c8c8c8',
                fontFamily: 'Consolas, "Courier New", monospace',
                fontSize: '0.85rem',
                lineHeight: 1.6,
                background: '#1e1e1e',
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                height: `${editorHeight}px`,
                maxHeight: isExpanded || !canCollapse ? undefined : COLLAPSED_CODE_HEIGHT,
              }}
            >
              {code}
            </Box>
          ) : (
            <Editor
              height={`${editorHeight}px`}
              language={editorLanguage}
              value={code}
              path={editorPath}
              theme="memo-monaco-dark"
              loading={
                <Box
                  component="pre"
                  sx={{
                    m: 0,
                    px: 2,
                    py: 1.5,
                    color: '#c8c8c8',
                    fontFamily: 'Consolas, "Courier New", monospace',
                    fontSize: '0.85rem',
                    lineHeight: 1.6,
                    background: '#1e1e1e',
                    overflow: needsInnerScroll ? 'auto' : 'hidden',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                    height: `${editorHeight}px`,
                  }}
                >
                  {code}
                </Box>
              }
              beforeMount={(monaco) => {
                monaco.editor.defineTheme('memo-monaco-dark', {
                  base: 'vs-dark',
                  inherit: true,
                  rules: [],
                  colors: {
                    'editor.background': '#1e1e1e',
                    'editor.selectionBackground': '#3b6ea899',
                    'editor.inactiveSelectionBackground': '#3b6ea855',
                    'editor.selectionHighlightBackground': '#4e7fbf55',
                    'editor.wordHighlightBackground': '#6f6f6f40',
                    'editor.wordHighlightStrongBackground': '#4e7fbf66',
                    'editor.lineHighlightBackground': '#2a2d2e66',
                    'editorGutter.background': '#1e1e1e',
                    'editorLineNumber.foreground': '#6a9955',
                    'editorLineNumber.activeForeground': '#b5cea8',
                  },
                });
              }}
              onMount={(editor) => {
                editorRef.current = editor;
                requestAnimationFrame(() => {
                  editor.layout();
                  requestAnimationFrame(() => editor.layout());
                });
              }}
              options={{
                readOnly: true,
                readOnlyMessage: { value: 'Код только для чтения' },
                minimap: { enabled: false },
                contextmenu: true,
                folding: true,
                foldingStrategy: 'auto',
                glyphMargin: false,
                lineNumbers: codeLineCount > 5 ? 'on' : 'off',
                lineNumbersMinChars: Math.max(3, String(codeLineCount).length + 1),
                renderLineHighlight: 'all',
                scrollBeyondLastLine: false,
                wordWrap: 'on',
                wrappingIndent: 'same',
                occurrencesHighlight: 'singleFile',
                selectionHighlight: true,
                matchBrackets: 'always',
                guides: { indentation: true },
                cursorStyle: 'line',
                automaticLayout: !isStreaming,
                padding: { top: 12, bottom: 12 },
                fontSize: 14,
                lineHeight: 22,
                scrollbar: {
                  vertical: isPresentationHtml || needsInnerScroll ? 'auto' : 'hidden',
                  horizontal: isPresentationHtml || needsInnerScroll ? 'auto' : 'hidden',
                  alwaysConsumeMouseWheel: false,
                },
                overviewRulerLanes: 0,
              }}
            />
          )}

          {canCollapse && (
            <Box
              sx={{
                position: 'absolute',
                left: 0,
                right: 0,
                bottom: 0,
                zIndex: 5,
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'flex-end',
                pt: expanded ? 0 : 5,
                pb: 1,
                background: expanded
                  ? 'transparent'
                  : 'linear-gradient(to top, rgba(30,30,30,0.95) 20%, rgba(30,30,30,0))',
              }}
            >
              <Tooltip title={expanded ? 'Свернуть' : 'Развернуть'}>
                <IconButton
                  size="small"
                  onClick={toggle}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                  }}
                  aria-label={expanded ? 'Свернуть код' : 'Развернуть код'}
                  sx={{
                    width: 32,
                    height: 32,
                    bgcolor: 'rgba(45, 45, 48, 0.95)',
                    color: '#cccccc',
                    border: '1px solid #3e3e42',
                    boxShadow: '0 2px 8px rgba(0,0,0,0.35)',
                    '&:hover': {
                      bgcolor: 'rgba(60, 60, 64, 0.98)',
                      color: '#4ec9b0',
                    },
                  }}
                >
                  {expanded ? (
                    <KeyboardArrowUpIcon fontSize="small" />
                  ) : (
                    <KeyboardArrowDownIcon fontSize="small" />
                  )}
                </IconButton>
              </Tooltip>
            </Box>
          )}
        </Box>
      </Box>
    </Box>
  );
});

const MessageRendererComponent: React.FC<MessageRendererProps> = ({
  content,
  isStreaming = false,
  onSendMessage,
  messageId,
  chatId: chatIdProp,
  forceArtifacts = false,
}) => {
  const { state: appState } = useAppContext();
  const chatId = chatIdProp ?? appState.currentChatId;
  const liveGate = useArtifactsViewerLiveGate(chatId);
  const presentationExpected = usePresentationViewerExpected(chatId);
  const artifactsAllowed = useArtifactsViewerAllowed({
    chatId,
    messageId,
    content,
    force: forceArtifacts,
  });
  /** Presentation skill/агент + стрим: viewer с первого ```html, не ждём pin/effect. */
  const presentationStreamActive = Boolean(isStreaming && presentationExpected);
  const viewerAllowed = artifactsAllowed || presentationStreamActive;

  // Если viewer сейчас разрешён «живым» гейтом — закрепляем показ за сообщением (один раз на messageId).
  const artifactsViewerPinnedRef = useRef(false);

  const tryPinArtifactsViewer = useCallback(() => {
    if (!messageId || !liveGate || artifactsViewerPinnedRef.current) return;
    const text = content || '';
    if (
      text.includes(':::artifact{') ||
      /```(?:html|htm|xhtml|mermaid|mmd|svg)\b/i.test(text) ||
      hasGpbSlideClass(text) ||
      isGpbPresentationHtml(text) ||
      (presentationExpected && /```/i.test(text)) ||
      (presentationExpected && /<!DOCTYPE\s+html\b|<html\b/i.test(text))
    ) {
      queueMicrotask(() => {
        if (artifactsViewerPinnedRef.current) return;
        pinMessageArtifactsViewer(messageId);
        artifactsViewerPinnedRef.current = true;
      });
    }
  }, [messageId, liveGate, presentationExpected, content]);

  useEffect(() => {
    artifactsViewerPinnedRef.current = false;
  }, [messageId]);
  useEffect(() => {
    tryPinArtifactsViewer();
  }, [tryPinArtifactsViewer]);

  const [copiedCode, setCopiedCode] = useState<string | null>(null);
  const { fontSizeValue } = useChatFontSize();
  
  // Используем useRef для стабильного хранения состояния меню (не сбросится при ререндерах!)
  const selectedTextRef = useRef<string>('');
  const selectedHtmlRef = useRef<string>('');
  const menuAnchorRef = useRef<HTMLElement | null>(null);
  const menuPositionRef = useRef<{ top: number; left: number } | null>(null);
  const selectedElementRef = useRef<HTMLElement | null>(null);
  
  // useState для форсирования ререндера только когда нужно показать/скрыть меню
  const [menuVisible, setMenuVisible] = useState<boolean>(false);
  
  const clickTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  // Стабильные пути для Monaco-моделей: назначаются один раз и не меняются при стриминге
  const codeBlockPathsRef = useRef<Map<string, string>>(new Map());
  // Раз уже показали presentation viewer — не откатываемся на HTML/Monaco при следующих чанках.
  const presentationStickyKeysRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    return () => {
      codeBlockPathsRef.current.clear();
    };
  }, []);

  const markPresentationSticky = useCallback((key: string, isPresentation: boolean): boolean => {
    if (isPresentation) {
      presentationStickyKeysRef.current.add(key);
      return true;
    }
    // Пока стрим: раз открыли viewer — не откатываемся на <pre>/Monaco
    // (ложный негатив на обрезанном HTML / гонке чанков).
    if (isStreaming && presentationStickyKeysRef.current.has(key)) {
      return true;
    }
    // После стрима переоцениваем: Excel/обычный HTML снова может стать ArtifactCard.
    presentationStickyKeysRef.current.delete(key);
    return false;
  }, [isStreaming]);

  const sanitizeRawContent = useCallback((raw: string): string => {
    if (!raw) return raw;
    // Skill mentions <$slug|Name> → readable chip-like code `$Name`
    const withSkills = raw.replace(/<\$([^|>]+)\|?([^>]*)>/g, (_m, slug: string, name: string) => {
      const label = String(name || slug || '')
        .trim()
        .replace(/[`*]/g, '');
      return `\`$${label}\``;
    });
    // Не трогаем fence-блоки и GPB HTML: normalizeChatInlineHtml иначе разносит теги,
    // и презентация утекает в ChatInlineHtml (иконки/разметка в тексте вместо viewer).
    const fences: string[] = [];
    const withoutFences = withSkills.replace(/```[\s\S]*?(?:```|$)/g, (block) => {
      const token = `\n__ASTRA_FENCE_${fences.length}__\n`;
      fences.push(block);
      return token;
    });
    const presentations: string[] = [];
    const withoutPresentations = withoutFences.replace(
      /(?:<!DOCTYPE\s+html\b[\s\S]*<\/html>)|(?:<html\b[\s\S]*<\/html>)/gi,
      (block) => {
        if (!isGpbPresentationHtml(block) && !hasGpbSlideClass(block)) return block;
        const token = `\n__ASTRA_PRES_${presentations.length}__\n`;
        presentations.push(block);
        return token;
      },
    );
    let normalized = normalizeChatInlineHtml(withoutPresentations);
    presentations.forEach((block, i) => {
      normalized = normalized.split(`__ASTRA_PRES_${i}__`).join(block);
    });
    fences.forEach((block, i) => {
      normalized = normalized.split(`__ASTRA_FENCE_${i}__`).join(block);
    });
    return normalized;
  }, []);

  const getSelectionClipboardPayload = useCallback((): { plain: string; html: string } => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
      return { plain: '', html: '' };
    }

    const range = selection.getRangeAt(0);
    const temp = document.createElement('div');
    temp.appendChild(range.cloneContents());

    temp
      .querySelectorAll(
        '.react-syntax-highlighter-line-number, .margin-view-overlays, .line-numbers, .monaco-editor .margin',
      )
      .forEach((el) => el.remove());

    // Клон с emotion-классами: временно в DOM, чтобы getComputedStyle видел bold/italic.
    temp.setAttribute('data-astra-copy-root', '1');
    temp.style.cssText =
      'position:fixed;left:-99999px;top:0;width:680px;opacity:0;pointer-events:none;white-space:normal;';
    document.body.appendChild(temp);

    const escapeText = (value: string) =>
      value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');

    const isBold = (cs: CSSStyleDeclaration) =>
      cs.fontWeight === 'bold' || cs.fontWeight === 'bolder' || parseInt(cs.fontWeight, 10) >= 600;
    const isItalic = (cs: CSSStyleDeclaration) => cs.fontStyle === 'italic' || cs.fontStyle === 'oblique';

    const BLOCK_RE =
      /^(div|p|li|ul|ol|h[1-6]|tr|blockquote|pre|table|thead|tbody|tfoot|section|article|header|footer|hr)$/i;

    const serialize = (
      node: Node,
      inherited: { bold: boolean; italic: boolean },
    ): { plain: string; html: string } => {
      if (node.nodeType === Node.TEXT_NODE) {
        const text = (node.textContent || '').replace(/\u00A0/g, ' ');
        return { plain: text, html: escapeText(text) };
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return { plain: '', html: '' };

      const el = node as HTMLElement;
      const tag = el.tagName.toLowerCase();
      if (tag === 'style' || tag === 'script' || tag === 'noscript') return { plain: '', html: '' };
      if (tag === 'br') return { plain: '\n', html: '<br>' };
      if (tag === 'hr') return { plain: '\n---\n', html: '<hr>' };

      let cs: CSSStyleDeclaration | null = null;
      try {
        cs = window.getComputedStyle(el);
      } catch {
        cs = null;
      }

      const boldHere = cs ? isBold(cs) : tag === 'strong' || tag === 'b';
      const italicHere = cs ? isItalic(cs) : tag === 'em' || tag === 'i';
      const nextInherited = {
        bold: inherited.bold || boldHere,
        italic: inherited.italic || italicHere,
      };

      let plain = '';
      let html = '';
      for (let i = 0; i < el.childNodes.length; i += 1) {
        const child = serialize(el.childNodes[i], nextInherited);
        plain += child.plain;
        html += child.html;
      }

      const wrapInline = (innerHtml: string, innerPlain: string) => {
        let h = innerHtml;
        if (boldHere && !inherited.bold) h = `<strong>${h}</strong>`;
        if (italicHere && !inherited.italic) h = `<em>${h}</em>`;
        return { plain: innerPlain, html: h };
      };

      if (tag === 'code' && el.closest('pre') == null) {
        return { plain, html: `<code>${html}</code>` };
      }
      if (tag === 'pre') {
        return {
          plain: plain.endsWith('\n') ? plain : `${plain}\n`,
          html: `<pre style="white-space:pre-wrap;font-family:Consolas,monospace;">${html}</pre>`,
        };
      }
      if (/^h[1-6]$/.test(tag)) {
        const wrapped = wrapInline(html, plain);
        return {
          plain: `${wrapped.plain}\n`,
          html: `<${tag}>${wrapped.html}</${tag}>`,
        };
      }
      if (tag === 'li' || (cs && cs.display === 'list-item')) {
        const valueAttr = el.getAttribute('value');
        let prefix = '• ';
        let parent: Node | null = el.parentNode;
        let inOrdered = valueAttr != null && valueAttr !== '';
        while (parent && parent !== temp) {
          if (parent.nodeType === Node.ELEMENT_NODE) {
            const pt = (parent as HTMLElement).tagName.toLowerCase();
            if (pt === 'ol') {
              inOrdered = true;
              break;
            }
            if (pt === 'ul') {
              inOrdered = false;
              break;
            }
          }
          parent = parent.parentNode;
        }
        if (inOrdered) prefix = valueAttr ? `${valueAttr}. ` : '• ';
        const wrapped = wrapInline(html, plain);
        const body = wrapped.plain.replace(/^[ \t]+|[ \t]+$/g, '');
        return {
          plain: `${prefix}${body}\n`,
          html: `<li>${wrapped.html}</li>`,
        };
      }
      if (tag === 'ul' || tag === 'ol') {
        return {
          plain: plain.endsWith('\n') ? plain : `${plain}\n`,
          html: `<${tag}>${html}</${tag}>`,
        };
      }
      if (tag === 'blockquote') {
        const wrapped = wrapInline(html, plain);
        return {
          plain: `${wrapped.plain}\n`,
          html: `<blockquote>${wrapped.html}</blockquote>`,
        };
      }
      if (tag === 'td' || tag === 'th') {
        return {
          plain: `${plain.replace(/\n+/g, ' ').trim()}\t`,
          html: `<${tag}>${html}</${tag}>`,
        };
      }
      if (tag === 'tr') {
        return {
          plain: `${plain.replace(/\t$/, '')}\n`,
          html: `<tr>${html}</tr>`,
        };
      }
      if (tag === 'table') {
        return { plain, html: `<table>${html}</table>` };
      }

      const wrapped = wrapInline(html, plain);
      if (tag === 'ul' || tag === 'ol' || tag === 'li') {
        return wrapped;
      }
      if (BLOCK_RE.test(tag) || (cs && (cs.display === 'block' || cs.display === 'flex'))) {
        const hasBlockChild = /<(p|div|ul|ol|h[1-6]|li|pre|blockquote|table|tr)\b/i.test(html);
        const plainOut = wrapped.plain
          ? wrapped.plain.endsWith('\n')
            ? wrapped.plain
            : `${wrapped.plain}\n`
          : '\n';
        if (hasBlockChild) {
          return { plain: plainOut, html: wrapped.html };
        }
        return {
          plain: plainOut,
          html: `<p>${wrapped.html}</p>`,
        };
      }
      return wrapped;
    };

    let plain = '';
    let html = '';
    try {
      for (let i = 0; i < temp.childNodes.length; i += 1) {
        const part = serialize(temp.childNodes[i], { bold: false, italic: false });
        plain += part.plain;
        html += part.html;
      }
    } finally {
      temp.remove();
    }

    if (!plain.trim()) {
      plain = selection.toString() || '';
    }

    plain = plain
      .replace(/\u00A0/g, ' ')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trimEnd();

    html = html
      .replace(/<p>\s*<\/p>/g, '')
      .replace(/(<\/p>)\s*(<p>)/g, '$1$2')
      .trim();

    if (html && !/^</.test(html)) {
      html = `<p>${html}</p>`;
    }

    // Полный HTML-фрагмент для Word / почты / rich editors
    const richHtml = html
      ? `<!DOCTYPE html><html><body><!--StartFragment-->${html}<!--EndFragment--></body></html>`
      : '';

    return { plain, html: richHtml };
  }, []);

  // Слушаем изменения размера шрифта
  // Обработчики для меню
  const handleMenuClose = useCallback(() => {
    menuAnchorRef.current = null;
    menuPositionRef.current = null;
    selectedElementRef.current = null;
    selectedTextRef.current = '';
    selectedHtmlRef.current = '';
    setMenuVisible(false);
  }, []);

  // Обработчик клика вне области для закрытия меню
  useEffect(() => {
    if (!menuVisible || !menuAnchorRef.current) return;
    
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Element;
      
      // Проверяем клик на меню или подменю
      const menuElement = document.querySelector('[data-menu="code-selection"]');
      const isClickOnMenu = menuElement && menuElement.contains(target as Node);
      const isClickOnSubMenu = target.closest('.MuiMenu-root') || 
                                target.closest('.MuiPopover-root') ||
                                target.closest('.MuiBackdrop-root');
      
      // Закрываем только если клик вне меню
      if (!isClickOnMenu && !isClickOnSubMenu) {
        handleMenuClose();
      }
    };

    // Небольшая задержка для предотвращения случайного закрытия сразу после открытия
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside, true);
    }, 100);
    
    return () => {
      clearTimeout(timer);
      document.removeEventListener('mousedown', handleClickOutside, true);
    };
  }, [menuVisible, handleMenuClose]);

  const handleCopyCode = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopiedCode(code);
      setTimeout(() => setCopiedCode(null), 2000);
    } catch (error) {
      console.error('Failed to copy code:', error);
    }
  };

  const handleDownloadCode = (code: string, language: string) => {
    try {
      const ext = getCodeFileExtension(language);
      const now = new Date();
      const dateStr = now.toISOString().slice(0, 19).replace(/:/g, '-').replace('T', '_');
      // Dockerfile / Makefile — без лишней точки в имени
      const fileName = ext.includes('.') || /^[A-Z]/.test(ext)
        ? `${ext}_${dateStr}`
        : `code_${dateStr}.${ext}`;

      const blob = new Blob([code], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = fileName;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Failed to download code:', error);
    }
  };

  // Обработчик выделения текста (mouseup)
  const handleTextSelection = (event: React.MouseEvent<HTMLElement>) => {
    if (menuVisible) {
      return;
    }
    
    const selection = window.getSelection();
    
    if (selection && selection.toString().trim()) {
      const payload = getSelectionClipboardPayload();
      const text = payload.plain.trim();
      
      const range = selection.rangeCount > 0 ? selection.getRangeAt(0) : null;
      if (range && containerRef.current && containerRef.current.contains(range.commonAncestorContainer)) {
        if (text.length > 0) {
          selectedTextRef.current = text;
          selectedHtmlRef.current = payload.html;
          
          setTimeout(() => {
            let anchorElement: HTMLElement | null = null;
            
            if (range.commonAncestorContainer.nodeType === Node.TEXT_NODE) {
              anchorElement = range.commonAncestorContainer.parentElement;
            } else if (range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE) {
              anchorElement = range.commonAncestorContainer as HTMLElement;
            }
            
            if (!anchorElement || !containerRef.current?.contains(anchorElement)) {
              anchorElement = containerRef.current;
            }
            
            selectedElementRef.current = anchorElement;
            
            const rect = range.getBoundingClientRect();
            menuPositionRef.current = {
              top: rect.bottom + 8,
              left: rect.left + (rect.width / 2),
            };
            
            menuAnchorRef.current = anchorElement;
            setMenuVisible(true);
          }, 5);
        }
      }
    }
  };

  // Обработчик двойного клика
  const handleDoubleClick = (event: React.MouseEvent<HTMLElement>) => {
    const selection = window.getSelection();
    
    if (selection && selection.toString().trim()) {
      const payload = getSelectionClipboardPayload();
      const text = payload.plain.trim();
      
      const range = selection.rangeCount > 0 ? selection.getRangeAt(0) : null;
      if (range && containerRef.current && containerRef.current.contains(range.commonAncestorContainer)) {
        selectedTextRef.current = text;
        selectedHtmlRef.current = payload.html;
        
        setTimeout(() => {
          let anchorElement: HTMLElement | null = null;
          
          if (range.commonAncestorContainer.nodeType === Node.TEXT_NODE) {
            anchorElement = range.commonAncestorContainer.parentElement;
          } else if (range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE) {
            anchorElement = range.commonAncestorContainer as HTMLElement;
          }
          
          if (!anchorElement || !containerRef.current?.contains(anchorElement)) {
            anchorElement = containerRef.current;
          }
          
          selectedElementRef.current = anchorElement;
          
          const rect = range.getBoundingClientRect();
          menuPositionRef.current = {
            top: rect.bottom + 8,
            left: rect.left + (rect.width / 2),
          };
          
          menuAnchorRef.current = anchorElement;
          setMenuVisible(true);
        }, 5);
      }
    }
  };

  const handleCopy = async () => {
    try {
      const textToCopy = selectedTextRef.current;
      const htmlToCopy = selectedHtmlRef.current;
      if (htmlToCopy && typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
        await navigator.clipboard.write([
          new ClipboardItem({
            'text/plain': new Blob([textToCopy], { type: 'text/plain' }),
            'text/html': new Blob([htmlToCopy], { type: 'text/html' }),
          }),
        ]);
      } else {
        await navigator.clipboard.writeText(textToCopy);
      }
      setCopiedCode(textToCopy);
      setTimeout(() => setCopiedCode(null), 2000);
    } catch (error) {
      console.error('Failed to copy:', error);
    }
  };

  const handleAsk = (prompt: string) => {
    if (onSendMessage) {
      onSendMessage(prompt);
    }
  };

  const handleExplain = (prompt: string) => {
    if (onSendMessage) {
      onSendMessage(prompt);
    }
  };

  const handleTranslate = (prompt: string, targetLanguage: string) => {
    if (onSendMessage) {
      onSendMessage(prompt);
    }
  };

  // Функция для определения ASCII таблицы (бордюры +---+), не GFM pipe-таблицы
  const isAsciiTable = (text: string): boolean => looksLikeAsciiArtTable(text);

  // Парсинг ASCII таблицы в структурированные данные
  const parseAsciiTable = (text: string) => {
    const allLines = text.split('\n');
    const lines: string[] = [];
    
    // Определяем границы таблицы - собираем только строки, которые являются частью таблицы
    let inTable = false;
    let lastTableLineIndex = -1;
    
    for (let i = 0; i < allLines.length; i++) {
      const line = allLines[i].trim();
      if (!line) continue;
      
      // Строка с разделителями или строка с |
      const isTableLine = line.includes('|') || 
                         line.includes('+---') || 
                         line.includes('|---') || 
                         line.includes('===') ||
                         line.match(/^[\s]*[-=+|]+[\s]*$/);
      
      if (isTableLine) {
        inTable = true;
        lines.push(line);
        lastTableLineIndex = i;
      } else if (inTable) {
        // Если мы были в таблице, но встретили строку без символов таблицы - таблица закончилась
        break;
      }
    }
    
    // Находим строки с разделителями
    const separatorIndices = lines
      .map((line, idx) => ({ line, idx }))
      .filter(({ line }) => 
        line.includes('+---') || 
        line.includes('|---') || 
        line.includes('===') ||
        line.match(/^[\s]*[-=+|]+[\s]*$/)
      )
      .map(({ idx }) => idx);
    
    // Извлекаем содержимое ячеек из строки
    const parseCells = (line: string): string[] => {
      return line
        .split('|')
        .map(cell => cell.trim())
        .filter(cell => cell.length > 0);
    };
    
    const headers: string[] = [];
    const rows: string[][] = [];
    
    let currentSection: 'header' | 'body' = 'header';
    
    lines.forEach((line, idx) => {
      // Пропускаем строки-разделители
      if (separatorIndices.includes(idx)) {
        if (currentSection === 'header') {
          currentSection = 'body';
        }
        return;
      }
      
      const cells = parseCells(line);
      if (cells.length === 0) return;
      
      if (currentSection === 'header' && headers.length === 0) {
        headers.push(...cells);
      } else {
        rows.push(cells);
      }
    });
    
    // Возвращаем также количество использованных строк для правильного парсинга остального текста
    return { headers, rows, linesUsed: lastTableLineIndex + 1 };
  };

  // Обработка Markdown внутри ячейки таблицы
  const processCellMarkdown = (cellText: string): string => {
    let processed = cellText;
    
    // Обрабатываем жирный текст
    processed = processed.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    processed = processed.replace(/__(.*?)__/g, '<strong>$1</strong>');
    
    // Обрабатываем курсив
    processed = processed.replace(/\*(.*?)\*/g, '<em>$1</em>');
    processed = processed.replace(/_(.*?)_/g, '<em>$1</em>');
    
    // Обрабатываем зачеркнутый текст
    processed = processed.replace(/~~(.*?)~~/g, '<del>$1</del>');
    
    // Обрабатываем инлайн код
    processed = processed.replace(/`([^`]+)`/g, '<code>$1</code>');
    
    // Обрабатываем ссылки
    processed = processed.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    
    return processed;
  };

  // Функция для экспорта таблицы в Excel
  const exportTableToExcel = (headers: string[], rows: string[][], tableIndex: number) => {
    try {
      // Очищаем ячейки от HTML и Markdown тегов для Excel
      const cleanText = (text: string): string => {
        if (!text) return '';
        
        let cleaned = text;
        
        // Удаляем HTML теги
        cleaned = cleaned.replace(/<[^>]+>/g, '');
        
        // Удаляем Markdown форматирование
        cleaned = cleaned.replace(/\*\*([^*]+)\*\*/g, '$1'); // Жирный текст
        cleaned = cleaned.replace(/__([^_]+)__/g, '$1'); // Жирный текст (альтернативный)
        cleaned = cleaned.replace(/\*([^*]+)\*/g, '$1'); // Курсив
        cleaned = cleaned.replace(/_([^_]+)_/g, '$1'); // Курсив (альтернативный)
        cleaned = cleaned.replace(/~~([^~]+)~~/g, '$1'); // Зачеркнутый текст
        cleaned = cleaned.replace(/`([^`]+)`/g, '$1'); // Инлайн код
        cleaned = cleaned.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1'); // Ссылки
        cleaned = cleaned.replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1'); // Изображения
        
        // Декодируем HTML сущности
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = cleaned;
        cleaned = tempDiv.textContent || tempDiv.innerText || cleaned;
        
        // Убираем лишние пробелы
        cleaned = cleaned.trim();
        
        return cleaned;
      };

      // Подготавливаем данные для Excel
      const excelData: any[][] = [];
      
      // Добавляем заголовки
      if (headers.length > 0) {
        excelData.push(headers.map(header => cleanText(header)));
      }
      
      // Добавляем строки данных
      rows.forEach(row => {
        excelData.push(row.map(cell => cleanText(cell)));
      });

      // Создаем рабочую книгу
      const wb = XLSX.utils.book_new();
      const ws = XLSX.utils.aoa_to_sheet(excelData);

      // Настраиваем ширину колонок
      const colWidths = headers.map((_, colIndex) => {
        let maxLength = headers[colIndex] ? cleanText(headers[colIndex]).length : 10;
        rows.forEach(row => {
          if (row[colIndex]) {
            const cellLength = cleanText(row[colIndex]).length;
            if (cellLength > maxLength) {
              maxLength = cellLength;
            }
          }
        });
        return { wch: Math.min(Math.max(maxLength + 2, 10), 50) };
      });
      ws['!cols'] = colWidths;

      // Добавляем лист в книгу
      XLSX.utils.book_append_sheet(wb, ws, 'Таблица');

      // Генерируем имя файла с датой и временем
      const now = new Date();
      const dateStr = now.toISOString().slice(0, 19).replace(/:/g, '-').replace('T', '_');
      const fileName = `table_${dateStr}.xlsx`;

      // Сохраняем файл
      XLSX.writeFile(wb, fileName);
    } catch (error) {
      console.error('Ошибка при экспорте таблицы в Excel:', error);
    }
  };

  // Рендеринг таблицы
  const renderTable = (headers: string[], rows: string[][], index: number) => {
    const tableScrollSx = {
      maxWidth: '100%',
      overflowX: 'auto',
      overflowY: 'hidden',
      borderRadius: 1,
      '&::-webkit-scrollbar': {
        height: 6,
      },
      '&::-webkit-scrollbar-track': {
        backgroundColor: 'rgba(255, 255, 255, 0.05)',
        borderRadius: 3,
      },
      '&::-webkit-scrollbar-thumb': {
        backgroundColor: 'rgba(255, 255, 255, 0.22)',
        borderRadius: 3,
        '&:hover': {
          backgroundColor: 'rgba(255, 255, 255, 0.32)',
        },
      },
      scrollbarWidth: 'thin',
      scrollbarColor: 'rgba(255,255,255,0.22) rgba(255,255,255,0.05)',
    };

    const headerCellSx = {
      fontWeight: 'bold',
      color: 'white',
      borderBottom: '1px solid rgba(224, 224, 224, 0.3)',
      borderTop: 'none',
      borderLeft: 'none',
      borderRight: 'none',
      fontSize: '0.875rem',
      whiteSpace: 'pre-wrap',
      wordBreak: 'break-word',
      py: 1.25,
      px: 2,
    };

    const bodyCellSx = {
      borderBottom: '1px solid rgba(224, 224, 224, 0.3)',
      borderTop: 'none',
      borderLeft: 'none',
      borderRight: 'none',
      fontSize: '0.875rem',
      whiteSpace: 'pre-wrap',
      wordBreak: 'break-word',
      py: 1.25,
      px: 2,
    };

    return (
      <Box key={index} sx={{ my: 2, position: 'relative', maxWidth: '100%', minWidth: 0 }}>
        <TableContainer component={Paper} sx={tableScrollSx}>
          <Table size="small" sx={{ width: '100%', tableLayout: 'fixed' }}>
            {headers.length > 0 && (
              <TableHead>
                <TableRow sx={{ backgroundColor: 'primary.dark' }}>
                  {headers.map((header, idx) => (
                    <TableCell
                      key={idx}
                      sx={{
                        ...headerCellSx,
                        ...(idx === headers.length - 1 ? { pr: 6 } : {}),
                      }}
                    >
                      <ChatInlineHtml text={processCellMarkdown(header)} />
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
            )}
            <TableBody>
              {rows.map((row, rowIdx) => (
                <TableRow
                  key={rowIdx}
                  sx={{
                    '&:nth-of-type(odd)': { backgroundColor: 'action.hover' },
                    '&:hover': { backgroundColor: 'action.selected' },
                  }}
                >
                  {row.map((cell, cellIdx) => (
                    <TableCell
                      key={cellIdx}
                      sx={{
                        ...bodyCellSx,
                        fontFamily: cell.match(/^\d+$/) ? 'monospace' : 'inherit',
                      }}
                    >
                      <ChatInlineHtml text={processCellMarkdown(cell)} />
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>

        <Box
          sx={{
            position: 'absolute',
            top: 0,
            right: 4,
            height: 41,
            display: 'flex',
            alignItems: 'center',
            zIndex: 2,
            pointerEvents: 'none',
            '& > *': { pointerEvents: 'auto' },
          }}
        >
          <Tooltip title="Скачать таблицу в Excel">
            <IconButton
              size="small"
              onClick={() => exportTableToExcel(headers, rows, index)}
              sx={{
                color: 'rgba(255, 255, 255, 0.75)',
                transition: 'all 0.2s',
                '&:hover': {
                  backgroundColor: 'rgba(255,255,255,0.1)',
                  color: '#fff',
                },
              }}
            >
              <DownloadIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>
    );
  };

  // Извлечение ASCII таблицы и остального текста
  const extractAsciiTable = (text: string): { table: string; remaining: string } | null => {
    const allLines = text.split('\n');
    const tableLines: string[] = [];
    let tableEndIndex = -1;
    
    for (let i = 0; i < allLines.length; i++) {
      const line = allLines[i].trim();
      
      const isTableLine = line.includes('|') || 
                         line.includes('+---') || 
                         line.includes('|---') || 
                         line.includes('===') ||
                         Boolean(line.match(/^[\s]*[-=+|]+[\s]*$/));
      
      if (isTableLine && line) {
        tableLines.push(allLines[i]);
        tableEndIndex = i;
      } else if (tableLines.length > 0) {
        // Таблица закончилась
        break;
      }
    }
    
    if (tableLines.length === 0) return null;
    
    const table = tableLines.join('\n');
    const remaining = allLines.slice(tableEndIndex + 1).join('\n');
    
    return { table, remaining };
  };

  /** Рендер текстового куска с любым числом GFM/pipe-таблиц внутри. */
  const renderTextWithTables = (part: string, keyBase: number) => {
    const blocks = splitTextWithMarkdownTables(part);
    if (!blocks.length) return renderMarkdownText(part, keyBase);
    if (blocks.length === 1 && blocks[0].kind === 'text') {
      return renderMarkdownText(blocks[0].text, keyBase);
    }

    return (
      <React.Fragment key={keyBase}>
        {blocks.map((block, bi) => {
          if (block.kind === 'table') {
            return (
              <React.Fragment key={`${keyBase}-tbl-${bi}`}>
                {renderTable(block.headers, block.rows, keyBase * 100 + bi)}
              </React.Fragment>
            );
          }
          if (!(block.text || '').trim()) return null;
          return (
            <React.Fragment key={`${keyBase}-txt-${bi}`}>
              {renderMarkdownText(block.text, keyBase * 100 + bi + 50)}
            </React.Fragment>
          );
        })}
      </React.Fragment>
    );
  };

  // Функция для парсинга Markdown
  const parseMarkdown = (text: string) => {
    // Auto-continue часто дописывает второй ```html — склеиваем в один viewer.
    const coalesced =
      presentationExpected || hasGpbSlideClass(text)
        ? coalescePresentationHtmlInMessage(text)
        : text;
    // Сначала вырезаем :::artifact — внутри них свои fence ```, обычный split ломается.
    const rawSegments = splitContentWithArtifacts(coalesced, { messageId, isStreaming });
    // Презентация сверху, остальные артефакты (Mermaid/HTML/…) под ней.
    const segments = hoistPresentationArtifacts(rawSegments, (content) =>
      isGpbPresentationHtml(content) ||
      (Boolean(isStreaming) &&
        presentationExpected &&
        isGpbPresentationStreaming(content || '')),
    );

    return segments.map((segment, segIndex) => {
      if (segment.kind === 'artifact') {
        const art = segment.artifact;
        // Без флага агента — только код, без ArtifactCard / presentation viewer.
        if (!viewerAllowed) {
          const lang = guessCodeLanguage(art.type) || 'text';
          return (
            <React.Fragment key={`artifact-raw-${art.id}-${segIndex}`}>
              {renderCodeBlock(`\`\`\`${lang}\n${art.content}\n\`\`\``, segIndex * 10000 + 9000)}
            </React.Fragment>
          );
        }
        // Live-спиннер только пока сообщение реально стримится.
        // Незакрытый :::artifact после обрыва/F5 не должен крутить спиннер вечно.
        const presentationPending = Boolean(isStreaming && !art.closed);
        const stickyKey = `artifact:${art.id || art.identifier || segIndex}`;
        let isPresentationArtifact =
          isGpbPresentationHtml(art.content) ||
          (isStreaming &&
            presentationExpected &&
            (isGpbPresentationStreaming(art.content || '') ||
              artifactMetaLooksLikePresentation({
                title: art.title,
                identifier: art.identifier,
                type: art.type,
              })));
        isPresentationArtifact = markPresentationSticky(stickyKey, isPresentationArtifact);
        if (isPresentationArtifact) {
          return (
            <InlinePresentationViewer
              key={`artifact-presentation-${art.id}-${segIndex}`}
              html={art.content}
              isStreaming={presentationPending}
            />
          );
        }
        return (
          <ArtifactCard
            key={`artifact-${art.id}-${segIndex}`}
            artifact={art}
            isStreaming={isStreaming}
            autoOpen={Boolean(isStreaming)}
          />
        );
      }

      const partText = segment.text;
      const parts = partText.split(/(```[\s\S]*?```|```[\s\S]*$)/g);

      return (
        <React.Fragment key={`seg-${segIndex}`}>
          {parts.map((part, index) => {
            if (part.startsWith('```') && part.endsWith('```')) {
              return renderCodeBlock(part, segIndex * 10000 + index);
            }

            if (part.startsWith('```') && !part.endsWith('```') && isStreaming) {
              // Парсинг — с синтетическим ```; streamFenceOpen=true, иначе closed=true → iframe на стриме → #185.
              return renderCodeBlock(part + '\n```', segIndex * 10000 + index, { streamFenceOpen: true });
            }

            if (isAsciiTable(part)) {
              const extraction = extractAsciiTable(part);
              if (extraction) {
                const { headers, rows } = parseAsciiTable(extraction.table);
                return (
                  <React.Fragment key={segIndex * 10000 + index}>
                    {renderTable(headers, rows, segIndex * 10000 + index)}
                    {extraction.remaining.trim() &&
                      renderTextWithTables(extraction.remaining, segIndex * 10000 + index + 1000)}
                  </React.Fragment>
                );
              }
            }

            // Unfenced GPB HTML (без ```html) → presentation viewer, не ChatInlineHtml с иконками.
            if (artifactsAllowed) {
              const extracted = extractUnfencedPresentationHtml(part);
              const stickyKey = `raw-html:${messageId || 'msg'}:${segIndex}:${index}`;
              let isPres = Boolean(
                extracted.html &&
                  shouldOpenPresentationViewer(extracted.html, {
                    isStreaming: Boolean(isStreaming),
                    presentationExpected,
                    language: 'html',
                  }),
              );
              isPres = markPresentationSticky(stickyKey, isPres);
              if (isPres && extracted.html) {
                return (
                  <React.Fragment key={segIndex * 10000 + index}>
                    {extracted.before.trim()
                      ? renderTextWithTables(extracted.before, segIndex * 10000 + index + 2000)
                      : null}
                    <InlinePresentationViewer
                      html={extracted.html}
                      isStreaming={Boolean(isStreaming)}
                    />
                    {extracted.after.trim()
                      ? renderTextWithTables(extracted.after, segIndex * 10000 + index + 3000)
                      : null}
                  </React.Fragment>
                );
              }
            }

            return renderTextWithTables(part, segIndex * 10000 + index);
          })}
        </React.Fragment>
      );
    });
  };

  // Рендер кодового блока с подсветкой синтаксиса
  const renderCodeBlock = (
    codeBlock: string,
    index: number,
    options?: { streamFenceOpen?: boolean },
  ) => {
    // Незакрытый fence на стриме: для парсинга подставляем ```, но closed должен оставаться false.
    const streamFenceOpen =
      options?.streamFenceOpen ??
      (Boolean(isStreaming) && !String(codeBlock).trimEnd().endsWith('```'));
    // Допускаем ```html, ```HTML, ```html с пробелами после языка
    let codeMatch = codeBlock.match(/```(\w+)[^\n]*\n([\s\S]*?)```/);
    let language = 'text';
    let code = '';
    
    if (codeMatch) {
      language = codeMatch[1];
      code = codeMatch[2];
    } else {
      const simpleMatch = codeBlock.match(/```[^\n]*\n?([\s\S]*?)```/);
      if (simpleMatch) {
        code = simpleMatch[1];
      }
    }
    
    if (code !== undefined) {
      // Убираем только служебный завершающий перенос из markdown-блока,
      // чтобы не рисовать "лишнюю" пустую строку внизу.
      code = code.replace(/\r\n/g, '\n').replace(/\n$/, '');

      // Маппинг языков для Monaco
      const languageMap: { [key: string]: string } = {
        'js': 'javascript',
        'ts': 'typescript',
        'py': 'python',
        'rb': 'ruby',
        'sh': 'bash',
        'yml': 'yaml',
        'cmd': 'batch',
        'ps1': 'powershell',
        'shell': 'shell',
        'cpp': 'cpp',
        'c++': 'cpp',
        'cc': 'cpp',
        'cxx': 'cpp',
        'cs': 'csharp',
        'c#': 'csharp',
        'java': 'java',
        'rust': 'rust',
        'rs': 'rust',
        'haskell': 'haskell',
        'hs': 'haskell',
        'vba': 'vb',
        'vb': 'vb',
        'pascal': 'pascal',
        'fortran': 'fortran',
        'f90': 'fortran',
        'f95': 'fortran',
      };
      
      const editorLanguage = languageMap[language] || language || 'plaintext';

      // Fallback: обычный ```mermaid / ```svg без :::artifact всё равно открываем как артефакт
      // (модели часто забывают обёртку, а пользователю нужна визуализация).
      // Только если у активного агента включены артефакты.
      const langLower = (language || '').toLowerCase();
      const looksLikeMermaidSource =
        /^\s*(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|mindmap|timeline|gitGraph|xychart-beta|xychart)\b/m.test(
          code,
        );
      if (
        artifactsAllowed &&
        (langLower === 'mermaid' || (looksLikeMermaidSource && langLower === 'text'))
      ) {
        const artifact = {
          id: `fence-mermaid-${messageId || 'msg'}-${index}`,
          identifier: `mermaid-diagram-${index}`,
          type: 'application/vnd.mermaid',
          title: 'Диаграмма Mermaid',
          content: sanitizeMermaidSource(code),
          closed: !streamFenceOpen,
          messageId,
        };
        return (
          <ArtifactCard
            key={`fence-artifact-mermaid-${index}`}
            artifact={artifact}
            isStreaming={isStreaming}
            autoOpen={Boolean(isStreaming)}
          />
        );
      }
      if (
        artifactsAllowed &&
        (langLower === 'svg' || (langLower === 'xml' && /<svg[\s>]/i.test(code)))
      ) {
        const artifact = {
          id: `fence-svg-${messageId || 'msg'}-${index}`,
          identifier: `svg-image-${index}`,
          type: 'image/svg+xml',
          title: 'SVG',
          content: code,
          closed: true,
          messageId,
        };
        return (
          <ArtifactCard
            key={`fence-artifact-svg-${index}`}
            artifact={artifact}
            isStreaming={isStreaming}
            autoOpen={Boolean(isStreaming)}
          />
        );
      }

      // Обычный HTML (диаграммы, страницы) → inline-артефакт, НЕ presentation viewer.
      // Presentation viewer: GPB-слайды ИЛИ skill презентации у агента/в чате (сразу со стрима).
      // Ключ без index: при росте текста индексы parts плывут → sticky терялся.
      const stickyFenceKey = `fence-html:${messageId || 'msg'}:presentation`;
      const langForPresentation = language || editorLanguage;
      const fenceLooksHtml =
        isHtmlFenceBlock(codeBlock) ||
        isHtmlFenceLanguage(language) ||
        isHtmlFenceLanguage(editorLanguage);

      let isPresentationHtml = shouldOpenPresentationViewer(code, {
        isStreaming: Boolean(isStreaming),
        presentationExpected,
        language: langForPresentation,
      });
      if (
        !isPresentationHtml &&
        shouldTreatHtmlFenceAsPresentationStream(code, codeBlock, langForPresentation, {
          isStreaming: Boolean(isStreaming),
          presentationExpected,
        })
      ) {
        isPresentationHtml = true;
      }
      // Обрезанная презентация после complete (нет закрывающего ```) — всё равно viewer.
      if (!isPresentationHtml && !isStreaming && presentationExpected && isGpbPresentationStreaming(code)) {
        isPresentationHtml = true;
      }
      // Skill презентации + стрим + html-fence: форсируем viewer до появления .slide,
      // иначе мелькает ArtifactCard/Monaco с сырым HTML.
      if (
        !isPresentationHtml &&
        isStreaming &&
        presentationExpected &&
        fenceLooksHtml
      ) {
        isPresentationHtml = true;
      }
      isPresentationHtml = markPresentationSticky(stickyFenceKey, isPresentationHtml);

      // Презентация GPB — единый return ниже (со sourceSlot для кнопки «код»).

      // HTML-артефакт: isHtmlFenceBlock ловит ```html до перевода строки (иначе Monaco+HTML → #185).
      if (
        (viewerAllowed || artifactsAllowed) &&
        !isPresentationHtml &&
        fenceLooksHtml
      ) {
        const artifact = {
          id: `fence-html-${messageId || 'msg'}-${index}`,
          identifier: `html-preview-${index}`,
          type: 'text/html',
          title: 'HTML',
          content: code,
          closed: !streamFenceOpen,
          messageId,
        };
        return (
          <ArtifactCard
            key={`fence-artifact-html-${index}`}
            artifact={artifact}
            isStreaming={isStreaming}
            autoOpen={Boolean(isStreaming)}
          />
        );
      }

      // Live viewer только при реальном стриме сообщения (не из-за хвоста без ```).
      const canShowPresentation = Boolean(artifactsAllowed || presentationExpected || viewerAllowed);
      if (canShowPresentation && isPresentationHtml && isStreaming) {
        return (
          <InlinePresentationViewer
            key={`presentation-${messageId || 'msg'}`}
            html={code}
            isStreaming
          />
        );
      }

      const codeLineCount = Math.max(1, code.split('\n').length);
      // Для HTML презентаций не раздуваем Monaco на тысячи px — фиксированная высота + скролл.
      // Иначе в Collapse «Показать HTML» редактор часто рисует пустую половину.
      const editorHeight = isPresentationHtml
        ? Math.min(480, Math.max(200, Math.min(codeLineCount, 22) * 22 + 18))
        : Math.max(120, codeLineCount * 22 + 18);

      // Получаем или создаём стабильный путь для этого блока кода.
      // Путь должен быть уникальным глобально (разные MessageRenderer-экземпляры),
      // поэтому используем случайный суффикс, генерируемый единожды через ref.
      const pathKey = `${editorLanguage}-${index}`;
      let editorPath = codeBlockPathsRef.current.get(pathKey);
      if (!editorPath) {
        const uid = Math.random().toString(36).substring(2, 10);
        editorPath = `readonly://${editorLanguage}/${uid}-${index}.code`;
        codeBlockPathsRef.current.set(pathKey, editorPath);
      }

      const codeEditorBlock = (
        <CollapsibleCodeBlock
          key={isPresentationHtml ? undefined : index}
          blockKey={index}
          code={code}
          language={language}
          editorLanguage={editorLanguage}
          editorPath={editorPath}
          codeLineCount={codeLineCount}
          fullEditorHeight={editorHeight}
          isStreaming={isStreaming}
          isPresentationHtml={isPresentationHtml}
          copiedCode={copiedCode}
          onCopy={handleCopyCode}
          onDownload={handleDownloadCode}
        />
      );
      if ((artifactsAllowed || presentationExpected || viewerAllowed) && isPresentationHtml) {
        return (
          <InlinePresentationViewer
            key={`presentation-${messageId || 'msg'}`}
            html={code}
            isStreaming={false}
            sourceSlot={codeEditorBlock}
          />
        );
      }

      return codeEditorBlock;
    }
    return null;
  };

  // Рендер специальных блоков (Info, Warning, Error, Success)
  const renderSpecialBlock = (type: 'info' | 'warning' | 'error' | 'success', content: string, key: any) => {
    const configs = {
      info: { icon: <InfoIcon />, color: '#2196f3', bgColor: 'rgba(33, 150, 243, 0.1)', title: 'Информация' },
      warning: { icon: <WarningIcon />, color: '#ff9800', bgColor: 'rgba(255, 152, 0, 0.1)', title: 'Внимание' },
      error: { icon: <ErrorIcon />, color: '#f44336', bgColor: 'rgba(244, 67, 54, 0.1)', title: 'Ошибка' },
      success: { icon: <SuccessIcon />, color: '#4caf50', bgColor: 'rgba(76, 175, 80, 0.1)', title: 'Успех' },
    };
    
    const config = configs[type];
    
    return (
      <Box
        key={key}
        sx={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 1.5,
          p: 2,
          my: 2,
          borderRadius: 1,
          backgroundColor: config.bgColor,
          borderLeft: `4px solid ${config.color}`,
        }}
      >
        <Box sx={{ color: config.color, mt: 0.25, flexShrink: 0 }}>
          {config.icon}
        </Box>
        <Box sx={{ flex: 1 }}>
          <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.6, fontSize: fontSizeValue }}>
            <ChatInlineHtml text={content} />
          </Typography>
        </Box>
      </Box>
    );
  };

  // Рендер Markdown текста
  const renderMarkdownText = (text: string, index: number) => {
    if (!text.trim()) return null;

    // Нормализуем em-теги перед markdown/inline парсингом.
    text = sanitizeRawContent(text);

    // Целые HTML-блоки от LLM (ul/ol/pre/blockquote/…) — до построчной нарезки.
    const preserved = extractPreservedHtmlBlocks(text);
    text = preserved.text;
    const htmlBlocks = preserved.blocks;

    // Обрабатываем специальные блоки с эмодзи (✅, ⚠️, ❌, ℹ️, 📝, 💡)
    const specialBlockRegex = /^[►✅⚠️❌ℹ️📝💡🔔]\s*(.+)$/gim;
    const specialLines: { type: 'info' | 'warning' | 'error' | 'success', content: string }[] = [];
    
    text = text.replace(specialBlockRegex, (match, content) => {
      let type: 'info' | 'warning' | 'error' | 'success' = 'info';
      
      if (match.startsWith('✅') || match.startsWith('►')) {
        type = 'success';
      } else if (match.startsWith('⚠️') || match.startsWith('🔔')) {
        type = 'warning';
      } else if (match.startsWith('❌')) {
        type = 'error';
      } else {
        type = 'info';
      }
      
      specialLines.push({ type, content });
      return `<special-block type="${type}">${content}</special-block>`;
    });

    // Обрабатываем заголовки h1–h6 (количество # = уровень)
    text = text.replace(/^(#{1,6})\s+(.+)$/gim, (_match, hashes: string, headingContent: string) => {
      const level = hashes.length;
      return `<h${level}>${headingContent}</h${level}>`;
    });

    // Списки — ДО курсива через *, иначе маркеры «* пункт» на соседних строках
    // схлопываются в один <em> и только последний «- пункт» остаётся в <ul>.
    text = text.replace(/^[\s]*(\d+)\.\s+(.+)$/gim, '<li data-list-type="ordered" data-list-number="$1">$2</li>');
    text = text.replace(/^[\s]*[-*+]\s+(.+)$/gim, '<li data-list-type="unordered">$1</li>');

    // Обрабатываем вложенные форматирования правильно
    // Сначала обрабатываем самые внешние теги (жирный), потом внутренние (курсив)
    // Используем жадное совпадение для внешних тегов
    
    // Обрабатываем жирный текст с возможным вложенным курсивом: **текст *курсив* текст**
    text = text.replace(/\*\*([^*]*(?:\*[^*]+\*[^*]*)*)\*\*/g, (match, content) => {
      // Обрабатываем курсив внутри жирного
      const processed = content.replace(/\*([^*]+)\*/g, '<em>$1</em>');
      return `<strong>${processed}</strong>`;
    });
    
    // Обрабатываем жирный с __
    text = text.replace(/__([^_]*(?:_[^_]+_[^_]*)*)__/g, (match, content) => {
      const processed = content.replace(/_([^_]+)_/g, '<em>$1</em>');
      return `<strong>${processed}</strong>`;
    });
    
    // Обрабатываем оставшийся курсив (который не внутри жирного); не через перенос строки
    text = text.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
    // Применяем "_" как курсив только на границах слова,
    // чтобы не ломать snake_case (например, df_date).
    text = text.replace(/(^|[^\w])_([^_\n]+)_(?=[^\w]|$)/g, '$1<em>$2</em>');

    // Обрабатываем зачеркнутый текст
    text = text.replace(/~~(.*?)~~/g, '<del>$1</del>');

    // Обрабатываем подчеркнутый текст (Markdown не поддерживает, но может быть в HTML)
    text = text.replace(/<u>(.*?)<\/u>/g, '<u>$1</u>');
    text = text.replace(/<U>(.*?)<\/U>/g, '<u>$1</u>');

    // Обрабатываем верхние индексы (superscript) для формул
    text = text.replace(/(\w+)\^(\d+)/g, '$1<sup>$2</sup>');
    text = text.replace(/(\w+)²/g, '$1<sup>2</sup>');
    text = text.replace(/(\w+)³/g, '$1<sup>3</sup>');
    text = text.replace(/(\w+)¹/g, '$1<sup>1</sup>');
    text = text.replace(/(\w+)⁰/g, '$1<sup>0</sup>');

    // Обрабатываем нижние индексы (subscript)
    text = text.replace(/(\w+)_(\d+)/g, '$1<sub>$2</sub>');

    // Обрабатываем ссылки
    text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

    // Обрабатываем изображения
    text = text.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" style="max-width: 100%; height: auto;" />');

    // Обрабатываем инлайн код
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Обрабатываем цитаты
    text = text.replace(/^>\s+(.+)$/gim, '<blockquote>$1</blockquote>');

    // Обрабатываем горизонтальные линии
    text = text.replace(/^---$/gim, '<hr>');

         // Разбиваем на строки для обработки списков
     const lines = text.split('\n');
     let inList = false;
     let listType: 'ordered' | 'unordered' | null = null;
     let listItems: React.ReactElement[] = [];
     let specialBlockIndex = 0;
     let orderedListCounter = 0; // Счетчик для нумерованных списков
     
     const processedLines = lines.map((line, lineIndex) => {
      const trimmedLine = line.trim();

      const htmlBlockMatch = trimmedLine.match(/^__ASTRA_HTML_BLOCK_(\d+)__$/);
      if (htmlBlockMatch) {
        const blockHtml = htmlBlocks[Number(htmlBlockMatch[1])];
        if (blockHtml) {
          return (
            <Box key={`${index}-htmlblock-${lineIndex}`} sx={{ my: 0.75 }}>
              <ChatInlineHtml text={blockHtml} keyPrefix={`${index}-hb-${lineIndex}`} />
            </Box>
          );
        }
      }

      // Обрабатываем специальные блоки
      if (line.includes('<special-block')) {
        const typeMatch = line.match(/type="(\w+)"/);
        const contentMatch = line.match(/<special-block[^>]*>(.*?)<\/special-block>/);
        
        if (typeMatch && contentMatch && specialLines[specialBlockIndex]) {
          const block = renderSpecialBlock(
            specialLines[specialBlockIndex].type,
            specialLines[specialBlockIndex].content,
            `${index}-special-${lineIndex}`
          );
          specialBlockIndex++;
          return block;
        }
      }

      const headingMatch = trimmedLine.match(/^<h([1-6])>(.*)<\/h\1>$/);
      if (headingMatch) {
        const level = headingMatch[1];
        const content = headingMatch[2];
        const mt =
          level === '1' ? 2 : level === '2' ? 1.75 : level === '3' ? 1.25 : level === '4' ? 1.1 : 1;
        return (
          <Typography
            key={`${index}-${lineIndex}`}
            component={`h${level}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6'}
            variant="body1"
            sx={{
              mt,
              mb: 0.5,
              fontWeight: 600,
              fontSize: `${markdownHeadingFontSize(level, fontSizeValue)} !important`,
              lineHeight: 1.35,
              color: 'inherit',
            }}
          >
            <ChatInlineHtml text={content} />
          </Typography>
        );
      }

      // Обрабатываем элементы списка
      if (line.includes('<li')) {
        const listTypeMatch = line.match(/data-list-type="(ordered|unordered)"/);
        const currentListType = listTypeMatch ? (listTypeMatch[1] as 'ordered' | 'unordered') : 'unordered';
        const listNumberMatch = line.match(/data-list-number="(\d+)"/);
        const originalNumber = listNumberMatch ? parseInt(listNumberMatch[1], 10) : null;
        const content = line.replace(/<li[^>]*>(.*?)<\/li>/, '$1');
        
        // Для нумерованных списков используем сохраненный номер или продолжаем счетчик
        let listItemValue: number | undefined = undefined;
        if (currentListType === 'ordered') {
          if (originalNumber !== null) {
            // Используем оригинальный номер из markdown
            listItemValue = originalNumber;
            orderedListCounter = originalNumber; // Обновляем счетчик для следующего элемента
          } else {
            // Если номера нет, продолжаем счетчик
            orderedListCounter++;
            listItemValue = orderedListCounter;
          }
        }

        const listItem =
          currentListType === 'ordered' ? (
            <Box
              key={`${index}-${lineIndex}`}
              component="li"
              sx={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 0.75,
                mb: 0.5,
                listStyle: 'none',
              }}
            >
              <Box
                component="span"
                sx={{
                  color: 'primary.main',
                  fontWeight: 600,
                  flexShrink: 0,
                  minWidth: `${Math.max(2, String(listItemValue ?? 0).length + 1)}ch`,
                  textAlign: 'right',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {listItemValue}.
              </Box>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <ChatInlineHtml text={content} />
              </Box>
            </Box>
          ) : (
            <Box
              key={`${index}-${lineIndex}`}
              component="li"
              sx={{
                display: 'list-item',
                ml: 2,
                mb: 0.5,
                '&::marker': {
                  color: 'primary.main',
                },
              }}
            >
              <ChatInlineHtml text={content} />
            </Box>
          );
        
        if (!inList || listType !== currentListType) {
          // Начинаем новый список или меняем тип
          if (inList && listItems.length > 0) {
            // Завершаем предыдущий список
            const prevList = (
              <Box
                key={`${index}-list-${lineIndex}-prev`}
                component={listType === 'ordered' ? 'ol' : 'ul'}
                sx={{
                  margin: '8px 0',
                  paddingLeft: listType === 'ordered' ? '4px' : '20px',
                  listStyleType: listType === 'ordered' ? 'none' : 'disc',
                }}
              >
                {listItems}
              </Box>
            );
            listItems = [];
            inList = false;
            // Начинаем новый список
            inList = true;
            listType = currentListType;
            listItems.push(listItem);
            return prevList;
          } else {
            // Начинаем первый список
            inList = true;
            listType = currentListType;
            listItems.push(listItem);
            return null;
          }
        } else {
          // Продолжаем текущий список
          listItems.push(listItem);
          return null;
        }
      } else if (inList) {
        // Завершаем список
        inList = false;
        const list = (
          <Box
            key={`${index}-list-${lineIndex}`}
            component={listType === 'ordered' ? 'ol' : 'ul'}
            sx={{
              margin: '8px 0',
              paddingLeft: listType === 'ordered' ? '4px' : '20px',
              listStyleType: listType === 'ordered' ? 'none' : 'disc',
            }}
          >
            {listItems}
          </Box>
        );
        listItems = [];
        listType = null;
        // Не сбрасываем счетчик - он может продолжиться после прерывания
        return list;
      }

      if (line.startsWith('<blockquote>')) {
        const content = line.replace(/<blockquote>(.*?)<\/blockquote>/, '$1');
        return (
          <Box
            key={`${index}-${lineIndex}`}
            sx={{
              borderLeft: '4px solid',
              borderColor: 'primary.main',
              pl: 2,
              ml: 2,
              my: 1,
              fontStyle: 'italic',
              color: 'text.secondary',
            }}
          >
            <ChatInlineHtml text={content} />
          </Box>
        );
      }

      if (line === '<hr>') {
        return (
          <Box
            key={`${index}-${lineIndex}`}
            sx={{
              borderTop: '1px solid',
              borderColor: 'divider',
              my: 2,
            }}
          />
        );
      }

      if (line.trim()) {
        return (
          <Typography
            key={`${index}-${lineIndex}`}
            variant="body1"
            component="div"
            sx={{
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              lineHeight: 1.5,
              mb: 0.5,
              fontSize: fontSizeValue,
              cursor: 'text',
              userSelect: 'text',
            }}
          >
            <ChatInlineHtml text={line} />
          </Typography>
        );
      }

      return <br key={`${index}-${lineIndex}`} />;
    });

         // Проверяем, не остался ли незавершенный список
     if (inList && listItems.length > 0) {
       const finalList = (
         <Box
           key={`${index}-final-list`}
           component={listType === 'ordered' ? 'ol' : 'ul'}
           sx={{
             margin: '8px 0',
             paddingLeft: listType === 'ordered' ? '4px' : '20px',
             listStyleType: listType === 'ordered' ? 'none' : 'disc',
           }}
         >
           {listItems}
         </Box>
       );
       processedLines.push(finalList);
     }
     
     return (
       <Box key={index} sx={{ mb: 1 }}>
         {processedLines.filter(line => line !== null)}
       </Box>
     );
  };

  const renderedContent = useMemo(
    () => parseMarkdown(sanitizeRawContent(content)),
    // parseMarkdown зависит от gate'ов viewer — иначе презентация «залипает» как текст.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      content,
      isStreaming,
      sanitizeRawContent,
      fontSizeValue,
      artifactsAllowed,
      viewerAllowed,
      presentationExpected,
      messageId,
    ],
  );

  return (
    <Box 
      ref={containerRef}
      sx={{
        position: 'relative',
        maxWidth: '100%',
        minWidth: 0,
        overflowX: 'hidden',
        overflowWrap: 'anywhere',
        wordBreak: 'break-word',
      }}
      onMouseUp={onSendMessage && !menuVisible ? handleTextSelection : undefined}
      onDoubleClick={onSendMessage && !menuVisible ? handleDoubleClick : undefined}
      onCopy={(event) => {
        const payload = getSelectionClipboardPayload();
        if (!payload.plain) return;

        // Plain + HTML: в Word/почту попадает жирный/курсив/заголовки,
        // в обычный инпут — читаемый текст с переносами.
        event.preventDefault();
        event.clipboardData.setData('text/plain', payload.plain);
        if (payload.html) {
          event.clipboardData.setData('text/html', payload.html);
        }
      }}
    >
      {renderedContent}
      
      {onSendMessage && menuVisible && menuAnchorRef.current && menuPositionRef.current && (
        <CodeSelectionMenu
          anchorEl={menuAnchorRef.current}
          position={menuPositionRef.current}
          open={menuVisible}
          onClose={handleMenuClose}
          selectedText={selectedTextRef.current || ''}
          onCopy={handleCopy}
          onAsk={handleAsk}
          onExplain={handleExplain}
          onTranslate={handleTranslate}
        />
      )}
    </Box>
  );
};

// Мемоизируем компонент, чтобы он НЕ ререндерился при каждом рендере родителя
// Ререндер произойдет ТОЛЬКО если изменятся props: content, isStreaming, onSendMessage, messageId
const MessageRenderer = React.memo(MessageRendererComponent, (prevProps, nextProps) => {
  return (
    prevProps.content === nextProps.content &&
    prevProps.isStreaming === nextProps.isStreaming &&
    prevProps.onSendMessage === nextProps.onSendMessage &&
    prevProps.messageId === nextProps.messageId &&
    prevProps.chatId === nextProps.chatId &&
    prevProps.forceArtifacts === nextProps.forceArtifacts
  );
});

MessageRenderer.displayName = 'MessageRenderer';

export default MessageRenderer;