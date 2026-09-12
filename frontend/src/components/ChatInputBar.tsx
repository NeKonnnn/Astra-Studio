import React, { RefObject, useState, useEffect, useRef } from 'react';
import {
  Box,
  TextField,
  IconButton,
  Tooltip,
  Typography,
  CircularProgress,
} from '@mui/material';
import {
  CHAT_INPUT_BORDER_DARK,
  CHAT_INPUT_BORDER_LIGHT,
  CHAT_INPUT_SURFACE_DARK,
  CHAT_INPUT_SURFACE_LIGHT,
} from '../constants/workZoneBackground';
import {
  Add as AddIcon,
  Send as SendIcon,
  Mic as MicIcon,
  GraphicEq as DictationIcon,
  Close as CloseIcon,
  Check as CheckIcon,
  Assessment as AssessmentIcon,
  Square as SquareIcon,
  Description as DocumentIcon,
  PictureAsPdf as PdfIcon,
} from '@mui/icons-material';
import ToolsIcon from '../icons/ToolsIcon';
import { formatFileSize } from '../utils/inlineImage';
import { getApiUrl, API_ENDPOINTS, getAuthFetchHeaders } from '../config/api';
import { prepareSegmentForStt } from '../utils/dictationAudio';
import InlineAttachmentsList from './InlineAttachmentsList';
import InlineDocAttachmentChip, {
  InlineAttachUploadSpinner,
  InlineDocUploadingThumb,
} from './InlineDocAttachmentChip';
import { INLINE_ATTACH_ACCEPT } from '../utils/inlineAttachmentRules';
import SkillMentionAutocomplete, { SkillSuggestion } from './chat/SkillMentionAutocomplete';
import {
  getSkillDollarQuery,
  serializeSkillMention,
} from '../utils/skillMentions';

export interface UploadedFile {
  name: string;
  type: string;
}

/** Файл, прикреплённый напрямую (без RAG/эмбединга) */
export interface InlineAttachment {
  name: string;
  contentType: 'text' | 'image';
  content: string; // extracted text или base64 data URL
  size?: number;
  minioObject?: string;
  minioBucket?: string;
  tokenEstimate?: number;
}

export interface ChatInputBarProps {
  value: string;
  onChange: (value: string) => void;
  onKeyPress: (e: React.KeyboardEvent) => void;
  onPaste?: (e: React.ClipboardEvent) => void | Promise<void>;
  placeholder?: string;
  inputDisabled?: boolean;
  inputRef?: RefObject<HTMLInputElement | HTMLTextAreaElement | null>;

  isDarkMode?: boolean;
  containerSx?: object;
  maxWidth?: string | number;

  fileInputRef?: RefObject<HTMLInputElement | null>;
  onAttachClick?: () => void;
  onFileSelect?: (files: FileList) => void;
  attachDisabled?: boolean;
  accept?: string;

  /** Вложения к сообщению (MinIO + inline, без RAG) */
  inlineFiles?: InlineAttachment[];
  onInlineFileRemove?: (index: number) => void;

  uploadedFiles?: UploadedFile[];
  onFileRemove?: (file: UploadedFile, index: number) => void;
  isUploading?: boolean;
  /** Метаданные файла во время прикрепления — для превью со спиннером в «иконке» */
  uploadingFile?: { name: string; size: number; previewUrl?: string } | null;

  showReportButton?: boolean;
  onReportClick?: () => void;
  reportDisabled?: boolean;

  onSettingsClick?: (event: React.MouseEvent<HTMLElement>) => void;
  settingsDisabled?: boolean;

  showStopButton?: boolean;
  onStopClick?: () => void;
  onSendClick?: () => void;
  sendDisabled?: boolean;
  isSending?: boolean;

  onVoiceClick?: () => void;
  voiceDisabled?: boolean;
  voiceTooltip?: string;

  extraActions?: React.ReactNode;

  /** Между «вложения» и «инструменты»: например индикатор «Общая библиотека» */
  libraryBadge?: React.ReactNode;

  /** Подсказки над полем ввода (MCP chips и т.п.) */
  inputSuggestions?: React.ReactNode;

  /** Счётчик контекста (справа над кнопками отправки) */
  contextCounter?: React.ReactNode;

  /** 'compact' — текущий пилюльный стиль (по умолчанию);
   *  'classic' — прямоугольник с тулбаром кнопок снизу */
  styleVariant?: 'compact' | 'classic';

  /** Как на стандартном фоне рабочей зоны (тёмный / #fafafa), чтобы не терялось на чёрном «звёздном» фоне */
  solidWorkZoneBackground?: boolean;

  /** Верхняя граница всей «пилюли» ввода — для Popover «Инструменты» над полем, а не по кнопке (она съезжает при многострочном вводе) */
  toolsMenuAnchorRef?: RefObject<HTMLDivElement | null>;
}

const iconButtonSx = (isDark: boolean, isClassic: boolean) => ({
  flexShrink: 0,
  width: 36,
  height: 36,
  borderRadius: isClassic ? '8px' : '50%',
  p: 0,
  '&:active': { transform: 'none' },
});

const CHAT_INPUT_CONTRAST_KEY = 'chat_input_contrast';
const CHAT_INPUT_COLOR_KEY = 'chat_input_color';
const CHAT_INPUT_CONTRAST_DEFAULT = 35;
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

export default function ChatInputBar({
  value,
  onChange,
  onKeyPress,
  onPaste,
  placeholder = 'Чем я могу помочь вам сегодня?',
  inputDisabled = false,
  inputRef,
  isDarkMode = false,
  containerSx,
  maxWidth = '100%',
  fileInputRef,
  onAttachClick,
  onFileSelect,
  attachDisabled = false,
  accept = INLINE_ATTACH_ACCEPT,
  uploadedFiles = [],
  onFileRemove,
  isUploading = false,
  uploadingFile = null,
  inlineFiles = [],
  onInlineFileRemove,
  showReportButton = false,
  onReportClick,
  reportDisabled = false,
  onSettingsClick,
  settingsDisabled = false,
  showStopButton = false,
  onStopClick,
  onSendClick,
  sendDisabled = false,
  isSending = false,
  onVoiceClick,
  voiceDisabled = false,
  voiceTooltip = 'Голосовой ввод',
  extraActions,
  libraryBadge,
  inputSuggestions,
  contextCounter,
  styleVariant = 'compact',
  solidWorkZoneBackground = false,
  toolsMenuAnchorRef,
}: ChatInputBarProps) {
  const getFileIcon = (file: UploadedFile) => {
    if (file.type?.includes('pdf')) return <PdfIcon fontSize="small" />;
    if (file.type?.includes('sheet') || file.type?.includes('excel')) return <DocumentIcon fontSize="small" />;
    return <DocumentIcon fontSize="small" />;
  };

  const fileAttachmentChipSx = {
    display: 'flex',
    alignItems: 'center',
    gap: 1,
    p: 1,
    borderRadius: 2.5,
    minWidth: 0,
    maxWidth: '280px',
    bgcolor: isDarkMode ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.06)',
    border: `1px solid ${isDarkMode ? 'rgba(255, 255, 255, 0.14)' : 'rgba(0, 0, 0, 0.12)'}`,
  };

  const fileAttachmentNameSx = {
    fontWeight: 600,
    display: 'block',
    color: isDarkMode ? 'white' : '#333',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    fontSize: '0.8rem',
    lineHeight: 1.25,
  };

  const fileAttachmentSizeSx = {
    display: 'block',
    mt: 0.25,
    fontSize: '0.7rem',
    lineHeight: 1.2,
    color: isDarkMode ? 'rgba(255, 255, 255, 0.55)' : 'rgba(0, 0, 0, 0.55)',
  };

  const fileAttachmentRemoveSx = {
    color: isDarkMode ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)',
    '&:hover': {
      color: '#ff6b6b',
      bgcolor: isDarkMode ? 'rgba(255, 107, 107, 0.2)' : 'rgba(255, 107, 107, 0.1)',
    },
    p: 0.5,
    borderRadius: 1,
    flexShrink: 0,
  };

  const legacyFileAttachmentIconBoxSx = {
    width: 32,
    height: 32,
    borderRadius: 1,
    bgcolor: isDarkMode ? 'rgba(255, 255, 255, 0.2)' : 'rgba(0, 0, 0, 0.2)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: isDarkMode ? 'white' : '#333',
    flexShrink: 0,
    border: `1px solid ${isDarkMode ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.3)'}`,
  };

  const isImageFilename = (filename: string): boolean =>
    /\.(jpe?g|png|webp|gif)$/i.test(filename);

  const inlineImageRemoveSx = {
    position: 'absolute',
    top: 4,
    right: 4,
    p: 0.25,
    minWidth: 0,
    width: 22,
    height: 22,
    bgcolor: 'rgba(0, 0, 0, 0.55)',
    color: 'white',
    '&:hover': { bgcolor: 'rgba(0, 0, 0, 0.75)', color: 'white' },
  };

  const isClassic = styleVariant === 'classic';
  const [isDictating, setIsDictating] = useState(false);
  const [isDictationProcessing, setIsDictationProcessing] = useState(false);
  const [dictationPreview, setDictationPreview] = useState('');
  const [waveLevels, setWaveLevels] = useState<number[]>(() => Array.from({ length: 72 }, () => 0.1));
  const dictationBaseTextRef = useRef('');
  const dictationSessionTextRef = useRef('');
  const skipDictationProcessingRef = useRef(false);
  const dictationSegmentQueueRef = useRef<Blob[]>([]);
  const dictationQueueProcessingRef = useRef(false);
  const dictationSegmentPreparingRef = useRef(0);
  const dictationSegmentTimerRef = useRef<number | null>(null);
  const dictationStreamRef = useRef<MediaStream | null>(null);
  const dictationRecorderOptionsRef = useRef<{ mimeType: string } | undefined>(undefined);
  const dictationFinalizingRef = useRef(false);
  const isDictatingRef = useRef(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedMimeTypeRef = useRef('audio/webm');
  const audioStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const wavePhaseRef = useRef(0);
  const noiseFloorRef = useRef(0.004);
  const speakingRef = useRef(false);
  const silenceFramesRef = useRef(0);
  const [chatInputContrast, setChatInputContrast] = useState<number>(() => {
    if (typeof window === 'undefined') return CHAT_INPUT_CONTRAST_DEFAULT;
    const raw = Number(localStorage.getItem(CHAT_INPUT_CONTRAST_KEY));
    if (!Number.isFinite(raw)) return CHAT_INPUT_CONTRAST_DEFAULT;
    return clamp(raw, 20, 100);
  });
  const [chatInputColor, setChatInputColor] = useState<string>(() => {
    if (typeof window === 'undefined') return '';
    return localStorage.getItem(CHAT_INPUT_COLOR_KEY) || '';
  });

  const canUseDictation =
    typeof navigator !== 'undefined' &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof MediaRecorder !== 'undefined';

  const concatText = (base: string, addition: string): string => {
    const left = base.trim();
    const right = addition.trim();
    if (!left) return right;
    if (!right) return left;
    return `${left} ${right}`;
  };

  const stopWave = () => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (audioContextRef.current) {
      void audioContextRef.current.close();
      audioContextRef.current = null;
    }
    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach((track) => track.stop());
      audioStreamRef.current = null;
    }
    analyserRef.current = null;
    speakingRef.current = false;
    silenceFramesRef.current = 0;
    setWaveLevels(Array.from({ length: 72 }, () => 0.1));
  };

  const DICTATION_SEGMENT_MS = 2500;
  const DICTATION_MIN_SEGMENT_BYTES = 100;

  const resetDictationState = () => {
    if (dictationSegmentTimerRef.current !== null) {
      window.clearTimeout(dictationSegmentTimerRef.current);
      dictationSegmentTimerRef.current = null;
    }
    dictationSessionTextRef.current = '';
    dictationSegmentQueueRef.current = [];
    dictationQueueProcessingRef.current = false;
    dictationSegmentPreparingRef.current = 0;
    dictationFinalizingRef.current = false;
    isDictatingRef.current = false;
    dictationStreamRef.current = null;
    dictationRecorderOptionsRef.current = undefined;
    setDictationPreview('');
    setIsDictating(false);
    setIsDictationProcessing(false);
    mediaRecorderRef.current = null;
  };

  const appendDictationText = (text: string) => {
    const piece = text.trim();
    if (!piece) return;
    dictationSessionTextRef.current = concatText(dictationSessionTextRef.current, piece);
    setDictationPreview(dictationSessionTextRef.current);
    onChange(concatText(dictationBaseTextRef.current, dictationSessionTextRef.current));
  };

  const recognizeDictationSegment = async (blob: Blob): Promise<string> => {
    const filename = blob.type.includes('wav') ? 'dictation.wav' : 'dictation.webm';
    const formData = new FormData();
    formData.append('audio_file', blob, filename);
    const response = await fetch(getApiUrl(API_ENDPOINTS.VOICE_RECOGNIZE), {
      method: 'POST',
      headers: getAuthFetchHeaders(),
      body: formData,
    });
    if (!response.ok) return '';
    const result = await response.json();
    if (!result.success) return '';
    return (result.text ?? '').trim();
  };

  const processDictationSegmentQueue = async () => {
    if (dictationQueueProcessingRef.current) return;
    dictationQueueProcessingRef.current = true;
    try {
      while (dictationSegmentQueueRef.current.length > 0 && !skipDictationProcessingRef.current) {
        const blob = dictationSegmentQueueRef.current.shift();
        if (!blob) continue;
        if (!dictationSessionTextRef.current) {
          setDictationPreview('Распознаю...');
        }
        try {
          const text = await recognizeDictationSegment(blob);
          if (text) {
            appendDictationText(text);
          }
        } catch {
          if (!dictationSessionTextRef.current) {
            setDictationPreview('Ошибка распознавания');
          }
        }
      }
    } finally {
      dictationQueueProcessingRef.current = false;
    }
  };

  const enqueueDictationSegment = (blob: Blob) => {
    if (skipDictationProcessingRef.current) return;
    dictationSegmentPreparingRef.current += 1;
    void (async () => {
      try {
        const payload = await prepareSegmentForStt(blob);
        if (!payload || skipDictationProcessingRef.current) return;
        dictationSegmentQueueRef.current.push(payload);
        void processDictationSegmentQueue();
      } finally {
        dictationSegmentPreparingRef.current = Math.max(0, dictationSegmentPreparingRef.current - 1);
      }
    })();
  };

  const waitForDictationQueue = (): Promise<void> =>
    new Promise((resolve) => {
      const tick = () => {
        if (
          !dictationQueueProcessingRef.current &&
          dictationSegmentPreparingRef.current === 0 &&
          dictationSegmentQueueRef.current.length === 0
        ) {
          resolve();
          return;
        }
        window.setTimeout(tick, 80);
      };
      window.setTimeout(resolve, 120000);
      tick();
    });

  const stopDictationSegmentTimer = () => {
    if (dictationSegmentTimerRef.current !== null) {
      window.clearTimeout(dictationSegmentTimerRef.current);
      dictationSegmentTimerRef.current = null;
    }
  };

  const finalizeDictation = async () => {
    setIsDictationProcessing(true);
    await waitForDictationQueue();
    stopWave();
    resetDictationState();
  };

  const startDictationSegment = () => {
    const stream = dictationStreamRef.current;
    if (!stream || skipDictationProcessingRef.current || dictationFinalizingRef.current) return;

    const recorder = dictationRecorderOptionsRef.current
      ? new MediaRecorder(stream, dictationRecorderOptionsRef.current)
      : new MediaRecorder(stream);

    recorder.ondataavailable = (event) => {
      if (event.data.size >= DICTATION_MIN_SEGMENT_BYTES) {
        enqueueDictationSegment(event.data);
      }
    };

    recorder.onstop = () => {
      stopDictationSegmentTimer();
      mediaRecorderRef.current = null;

      if (skipDictationProcessingRef.current) {
        stopWave();
        onChange(dictationBaseTextRef.current);
        resetDictationState();
        return;
      }

      if (dictationFinalizingRef.current) {
        void finalizeDictation();
        return;
      }

      if (isDictatingRef.current) {
        startDictationSegment();
      }
    };

    recorder.onerror = () => {
      skipDictationProcessingRef.current = true;
      isDictatingRef.current = false;
      stopDictationSegmentTimer();
      if (recorder.state !== 'inactive') {
        try {
          recorder.stop();
        } catch {
          stopWave();
          onChange(dictationBaseTextRef.current);
          resetDictationState();
        }
      }
    };

    mediaRecorderRef.current = recorder;
    recorder.start();
    dictationSegmentTimerRef.current = window.setTimeout(() => {
      if (recorder.state === 'recording') {
        recorder.stop();
      }
    }, DICTATION_SEGMENT_MS);
  };

  const stopDictation = (cancel: boolean) => {
    if (isDictationProcessing) return;

    if (cancel) {
      skipDictationProcessingRef.current = true;
      isDictatingRef.current = false;
      dictationFinalizingRef.current = false;
      setIsDictating(false);
      stopDictationSegmentTimer();
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      } else {
        stopWave();
        onChange(dictationBaseTextRef.current);
        resetDictationState();
      }
      return;
    }

    skipDictationProcessingRef.current = false;
    dictationFinalizingRef.current = true;
    isDictatingRef.current = false;
    setIsDictating(false);
    stopDictationSegmentTimer();
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
      return;
    }

    void finalizeDictation();
  };

  const setupWaveVisualization = (stream: MediaStream) => {
    audioStreamRef.current = stream;
    const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext;
    const audioCtx = new Ctx();
    audioContextRef.current = audioCtx;
    const source = audioCtx.createMediaStreamSource(stream);
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.88;
    source.connect(analyser);
    analyserRef.current = analyser;
    const data = new Uint8Array(analyser.fftSize);
    const bars = 72;

    const tick = () => {
      if (!analyserRef.current) return;
      analyserRef.current.getByteTimeDomainData(data);
      let sumSquares = 0;
      for (let i = 0; i < data.length; i += 1) {
        const centered = (data[i] - 128) / 128;
        sumSquares += centered * centered;
      }
      const rms = Math.sqrt(sumSquares / Math.max(1, data.length));
      if (!speakingRef.current) {
        noiseFloorRef.current = noiseFloorRef.current * 0.97 + rms * 0.03;
      }
      const gateOn = noiseFloorRef.current + 0.0035;
      const gateOff = noiseFloorRef.current + 0.0018;
      if (speakingRef.current) {
        if (rms < gateOff) silenceFramesRef.current += 1;
        else silenceFramesRef.current = 0;
        if (silenceFramesRef.current > 8) speakingRef.current = false;
      } else if (rms > gateOn) {
        speakingRef.current = true;
        silenceFramesRef.current = 0;
      }

      const voiceLevel = clamp((rms - noiseFloorRef.current - 0.001) * 100, 0, 1);
      const speaking = speakingRef.current && voiceLevel > 0.015;

      if (speaking) wavePhaseRef.current += 0.22;
      const phase = wavePhaseRef.current;
      const next = new Array<number>(bars).fill(0).map((_, index) => {
        if (!speaking) return 0.1;
        const travel = Math.sin(index * 0.42 + phase) * 0.5 + 0.5;
        const ripple = Math.sin(index * 0.17 + phase * 0.63) * 0.5 + 0.5;
        const spread = travel * 0.7 + ripple * 0.3;
        return clamp(0.12 + voiceLevel * (0.25 + spread * 0.75), 0.1, 1);
      });
      setWaveLevels((prev) =>
        prev.map((p, i) => clamp(p * (speaking ? 0.74 : 0.86) + next[i] * (speaking ? 0.26 : 0.14), 0.1, 1)),
      );
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  };

  const startDictation = async () => {
    if (!canUseDictation || isDictating || isDictationProcessing || inputDisabled || voiceDisabled) return;

    dictationBaseTextRef.current = value;
    dictationSessionTextRef.current = '';
    skipDictationProcessingRef.current = false;
    dictationFinalizingRef.current = false;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      dictationStreamRef.current = stream;
      setupWaveVisualization(stream);

      const preferredMimeTypes = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/mp4',
        'audio/ogg;codecs=opus',
      ];
      let selectedOptions: { mimeType: string } | undefined;
      for (const mimeType of preferredMimeTypes) {
        if (MediaRecorder.isTypeSupported(mimeType)) {
          selectedOptions = { mimeType };
          break;
        }
      }

      recordedMimeTypeRef.current = selectedOptions?.mimeType ?? 'audio/webm';
      dictationRecorderOptionsRef.current = selectedOptions;
      isDictatingRef.current = true;
      setDictationPreview('');
      setIsDictating(true);
      startDictationSegment();
    } catch {
      stopWave();
      resetDictationState();
    }
  };

  useEffect(() => {
    return () => {
      skipDictationProcessingRef.current = true;
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        try {
          mediaRecorderRef.current.stop();
        } catch {
          // noop
        }
      }
      stopWave();
    };
  }, []);

  useEffect(() => {
    const syncContrast = () => {
      const raw = Number(localStorage.getItem(CHAT_INPUT_CONTRAST_KEY));
      setChatInputColor(localStorage.getItem(CHAT_INPUT_COLOR_KEY) || '');
      if (!Number.isFinite(raw)) {
        setChatInputContrast(CHAT_INPUT_CONTRAST_DEFAULT);
        return;
      }
      setChatInputContrast(clamp(raw, 20, 100));
    };
    syncContrast();
    window.addEventListener('interfaceSettingsChanged', syncContrast);
    return () => window.removeEventListener('interfaceSettingsChanged', syncContrast);
  }, []);

  const contrastDelta = chatInputContrast - CHAT_INPUT_CONTRAST_DEFAULT;
  const nonSolidBgAlpha = clamp(0.05 + contrastDelta * 0.004, 0.03, 0.32);
  const nonSolidBorderAlpha = clamp(0.1 + contrastDelta * 0.005, 0.06, 0.45);
  const nonSolidBoxShadowAlpha = clamp(0.08 + contrastDelta * 0.004, 0.06, 0.4);

  const shellBg = solidWorkZoneBackground
    ? isDarkMode
      ? CHAT_INPUT_SURFACE_DARK
      : CHAT_INPUT_SURFACE_LIGHT
    : isDarkMode
      ? `rgba(255, 255, 255, ${nonSolidBgAlpha})`
      : `rgba(0, 0, 0, ${nonSolidBgAlpha})`;
  const resolvedShellBg = chatInputColor || shellBg;

  const shellBorder = solidWorkZoneBackground
    ? isDarkMode
      ? isClassic
        ? 'rgba(255, 255, 255, 0.12)'
        : CHAT_INPUT_BORDER_DARK
      : isClassic
        ? 'rgba(0, 0, 0, 0.12)'
        : CHAT_INPUT_BORDER_LIGHT
    : isClassic
      ? isDarkMode
        ? `rgba(255, 255, 255, ${clamp(nonSolidBorderAlpha + 0.02, 0.08, 0.52)})`
        : `rgba(0, 0, 0, ${clamp(nonSolidBorderAlpha + 0.02, 0.08, 0.52)})`
      : isDarkMode
        ? `rgba(255, 255, 255, ${nonSolidBorderAlpha})`
        : `rgba(0, 0, 0, ${nonSolidBorderAlpha})`;

  const shellChrome = solidWorkZoneBackground
    ? {
        bgcolor: resolvedShellBg,
        backgroundColor: resolvedShellBg,
        border: `1px solid ${shellBorder}`,
        boxShadow: isDarkMode
          ? '0 2px 16px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.06)'
          : '0 2px 12px rgba(0,0,0,0.08)',
        position: 'relative' as const,
        zIndex: 1,
      }
    : {
        boxShadow: isDarkMode
          ? `0 2px 16px rgba(0,0,0,${nonSolidBoxShadowAlpha})`
          : `0 2px 12px rgba(0,0,0,${clamp(nonSolidBoxShadowAlpha * 0.9, 0.05, 0.35)})`,
        backdropFilter: `blur(${clamp(2 + contrastDelta * 0.12, 2, 9)}px)`,
      };

  // В компактном режиме: одна строка — кнопки по бокам; со 2-й строки — кнопки снизу.
  // Переключение по числу символов + гистерезис, чтобы не скакало (scrollHeight давал дребезг).
  const CHARS_FIRST_LINE = 100;   // примерно столько символов влезает в одну строку (шрифт 0.875rem, кнопки по бокам)
  const CHARS_SINGLE_BACK = 100;  // обратно в одну строку только когда короче (гистерезис)
  const [compactMultiline, setCompactMultiline] = useState(false);
  const [skillMention, setSkillMention] = useState<{ open: boolean; query: string; start: number }>({
    open: false,
    query: '',
    start: 0,
  });
  const skillAnchorRef = useRef<HTMLDivElement | null>(null);

  const updateSkillMentionFromInput = (nextValue: string, cursor: number | null) => {
    if (cursor == null) {
      setSkillMention((s) => ({ ...s, open: false }));
      return;
    }
    const hit = getSkillDollarQuery(nextValue, cursor);
    if (hit) {
      setSkillMention({ open: true, query: hit.query, start: hit.start });
    } else {
      setSkillMention((s) => ({ ...s, open: false }));
    }
  };

  const handleValueChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const next = e.target.value;
    onChange(next);
    updateSkillMentionFromInput(next, e.target.selectionStart);
  };

  const handleSelectSkill = (skill: SkillSuggestion) => {
    const mention = serializeSkillMention(skill.slug, skill.name);
    const start = skillMention.start;
    const cursorEnd = start + 1 + skillMention.query.length;
    const next = `${value.slice(0, start)}${mention} ${value.slice(cursorEnd)}`;
    onChange(next);
    setSkillMention({ open: false, query: '', start: 0 });
  };

  useEffect(() => {
    if (isClassic) return;
    const hasNewline = value.includes('\n');
    const len = value.length;
    setCompactMultiline((prev) => {
      if (hasNewline || len > CHARS_FIRST_LINE) return true;
      if (len <= CHARS_SINGLE_BACK) return false;
      return prev; // между 45 и 52 — не переключаем
    });
  }, [value, isClassic]);

  // ─── Переиспользуемые кнопки ────────────────────────────────────────────────

  const attachBtn = onAttachClick ? (
    <Tooltip title="Прикрепить файл к сообщению">
      <IconButton
        size="small"
        onClick={onAttachClick}
        disabled={attachDisabled || isUploading}
        disableRipple
        sx={{
          color: inlineFiles.length > 0
            ? 'primary.main'
            : isDarkMode ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)',
          bgcolor: inlineFiles.length > 0
            ? (isDarkMode ? 'rgba(91,105,255,0.15)' : 'rgba(91,105,255,0.08)')
            : 'transparent',
          border: inlineFiles.length > 0
            ? `1px solid ${isDarkMode ? 'rgba(91,105,255,0.45)' : 'rgba(91,105,255,0.3)'}`
            : '1px solid transparent',
          '&:hover:not(:disabled)': {
            bgcolor: isDarkMode ? 'rgba(255, 255, 255, 0.12)' : 'rgba(0, 0, 0, 0.1)',
            border: `1px solid ${isDarkMode ? 'rgba(255, 255, 255, 0.18)' : 'rgba(0, 0, 0, 0.15)'}`,
            borderRadius: isClassic ? '8px' : '50%',
            color: 'primary.main',
            '& .MuiSvgIcon-root': { color: 'primary.main' },
          },
          '&:disabled': {
            color: isDarkMode ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.3)',
          },
          ...iconButtonSx(isDarkMode, isClassic),
        }}
      >
        <AddIcon sx={{ fontSize: '1.25rem' }} />
      </IconButton>
    </Tooltip>
  ) : null;

  const settingsBtn = onSettingsClick ? (
    <Tooltip title="Инструменты">
      <span>
        <IconButton
          size="small"
          onClick={onSettingsClick}
          disabled={settingsDisabled}
          sx={{
            color: isDarkMode ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)',
            bgcolor: 'transparent',
            border: '1px solid transparent',
            '&:hover:not(:disabled)': {
              bgcolor: isDarkMode ? 'rgba(255, 255, 255, 0.12)' : 'rgba(0, 0, 0, 0.1)',
              border: `1px solid ${isDarkMode ? 'rgba(255, 255, 255, 0.18)' : 'rgba(0, 0, 0, 0.15)'}`,
              borderRadius: isClassic ? '8px' : '50%',
              color: 'primary.main',
              '& .MuiSvgIcon-root': { color: 'primary.main' },
            },
            '&:disabled': {
              color: isDarkMode ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.3)',
            },
            ...iconButtonSx(isDarkMode, isClassic),
          }}
        >
          <ToolsIcon sx={{ fontSize: '1.25rem' }} />
        </IconButton>
      </span>
    </Tooltip>
  ) : null;

  const reportBtn = showReportButton && onReportClick ? (
    <Tooltip title="Сгенерировать отчет об уверенности">
      <IconButton
        size="small"
        onClick={onReportClick}
        disabled={reportDisabled}
        disableRipple
        sx={{
          color: isDarkMode ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)',
          bgcolor: 'transparent',
          border: '1px solid transparent',
          '&:hover:not(:disabled)': {
            bgcolor: isDarkMode ? 'rgba(255, 255, 255, 0.12)' : 'rgba(0, 0, 0, 0.1)',
            border: `1px solid ${isDarkMode ? 'rgba(255, 255, 255, 0.18)' : 'rgba(0, 0, 0, 0.15)'}`,
            borderRadius: isClassic ? '8px' : '50%',
            color: 'primary.main',
            '& .MuiSvgIcon-root': { color: 'primary.main' },
          },
          '&:disabled': {
            color: isDarkMode ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.3)',
          },
          ...iconButtonSx(isDarkMode, isClassic),
        }}
      >
        <AssessmentIcon sx={{ fontSize: '1.25rem' }} />
      </IconButton>
    </Tooltip>
  ) : null;

  const stopOrSendBtn = showStopButton && onStopClick ? (
    <Tooltip title="Прервать генерацию">
      <IconButton
        size="small"
        onClick={onStopClick}
        color="error"
        sx={{
          bgcolor: 'error.main',
          color: 'white',
          ...iconButtonSx(isDarkMode, isClassic),
          '&:hover': { bgcolor: 'error.dark' },
          animation: 'pulse 2s ease-in-out infinite',
          '@keyframes pulse': { '0%': { opacity: 1 }, '50%': { opacity: 0.7 }, '100%': { opacity: 1 } },
        }}
      >
        <SquareIcon sx={{ fontSize: '1.25rem' }} />
      </IconButton>
    </Tooltip>
  ) : onSendClick ? (
    <Tooltip title="Отправить">
      <span>
        <IconButton
          size="small"
          onClick={onSendClick}
          disabled={sendDisabled}
          sx={{
            color: isDarkMode ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)',
            bgcolor: 'transparent',
            border: '1px solid transparent',
            ...iconButtonSx(isDarkMode, isClassic),
            '&:hover:not(:disabled)': {
              bgcolor: isDarkMode ? 'rgba(255, 255, 255, 0.12)' : 'rgba(0, 0, 0, 0.1)',
              border: `1px solid ${isDarkMode ? 'rgba(255, 255, 255, 0.18)' : 'rgba(0, 0, 0, 0.15)'}`,
              borderRadius: isClassic ? '8px' : '50%',
              color: 'primary.main',
              '& .MuiSvgIcon-root': { color: 'primary.main' },
            },
            '&:disabled': {
              color: isDarkMode ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.3)',
            },
          }}
        >
          {isSending ? <CircularProgress size={18} sx={{ color: 'inherit' }} /> : <SendIcon sx={{ fontSize: '1.25rem' }} />}
        </IconButton>
      </span>
    </Tooltip>
  ) : null;

  const voiceBtn = onVoiceClick ? (
    <Tooltip title={voiceTooltip}>
      <IconButton
        size="small"
        onClick={onVoiceClick}
        disabled={voiceDisabled}
        sx={{
          color: isDarkMode ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)',
          bgcolor: 'transparent',
          border: '1px solid transparent',
          ...iconButtonSx(isDarkMode, isClassic),
          '&:hover:not(:disabled)': {
            bgcolor: isDarkMode ? 'rgba(255, 255, 255, 0.12)' : 'rgba(0, 0, 0, 0.1)',
            border: `1px solid ${isDarkMode ? 'rgba(255, 255, 255, 0.18)' : 'rgba(0, 0, 0, 0.15)'}`,
            borderRadius: isClassic ? '8px' : '50%',
            color: 'primary.main',
            '& .MuiSvgIcon-root': { color: 'primary.main' },
          },
          '&:disabled': {
            color: isDarkMode ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.3)',
          },
        }}
      >
        <MicIcon sx={{ fontSize: '1.25rem' }} />
      </IconButton>
    </Tooltip>
  ) : null;

  const dictationActive = isDictating || isDictationProcessing;

  const dictationBtn = canUseDictation ? (
    <Tooltip title={isDictationProcessing ? 'Распознаю...' : isDictating ? 'Остановить диктовку' : 'Диктовка в поле ввода'}>
      <IconButton
        size="small"
        onClick={() => {
          if (isDictationProcessing) return;
          if (isDictating) stopDictation(false);
          else void startDictation();
        }}
        disabled={voiceDisabled || inputDisabled || isDictationProcessing}
        sx={{
          color: isDictating ? 'white' : isDarkMode ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)',
          bgcolor: isDictating ? 'primary.main' : 'transparent',
          border: `1px solid ${isDictating ? 'rgba(91, 105, 255, 0.65)' : 'transparent'}`,
          ...iconButtonSx(isDarkMode, isClassic),
          '&:hover:not(:disabled)': {
            bgcolor: isDictating ? 'primary.dark' : isDarkMode ? 'rgba(255, 255, 255, 0.12)' : 'rgba(0, 0, 0, 0.1)',
            border: `1px solid ${isDarkMode ? 'rgba(255, 255, 255, 0.18)' : 'rgba(0, 0, 0, 0.15)'}`,
            borderRadius: isClassic ? '8px' : '50%',
            color: isDictating ? 'white' : 'primary.main',
            '& .MuiSvgIcon-root': { color: isDictating ? 'white' : 'primary.main' },
          },
          '&:disabled': {
            color: isDarkMode ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.3)',
          },
        }}
      >
        <DictationIcon sx={{ fontSize: '1.25rem' }} />
      </IconButton>
    </Tooltip>
  ) : null;

  const rightActions = (
    <>
      {dictationActive ? null : reportBtn}
      {dictationActive ? null : stopOrSendBtn}
      {contextCounter}
      {dictationActive ? null : voiceBtn}
      {dictationBtn}
    </>
  );

  const dictationPanel = dictationActive ? (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minHeight: 42, width: '100%' }}>
      <IconButton
        size="small"
        onClick={() => stopDictation(true)}
        disabled={isDictationProcessing}
        sx={{
          ...iconButtonSx(isDarkMode, isClassic),
          color: isDarkMode ? 'rgba(255,255,255,0.92)' : 'rgba(0,0,0,0.78)',
          bgcolor: isDarkMode ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)',
        }}
      >
        <CloseIcon sx={{ fontSize: '1.1rem' }} />
      </IconButton>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          variant="body2"
          sx={{ color: isDarkMode ? 'white' : '#1f1f1f', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', mb: 0.5 }}
        >
          {dictationPreview || (isDictationProcessing ? 'Распознаю...' : 'Слушаю...')}
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1px', height: 14, width: '100%' }}>
          {waveLevels.map((level, idx) => (
            <Box
              key={`dict-bar-${idx}`}
              sx={{
                flex: 1,
                borderRadius: 2,
                height: `${Math.max(3, Math.round(level * 14))}px`,
                bgcolor: isDarkMode ? 'rgba(255,255,255,0.86)' : 'rgba(33,33,33,0.86)',
                opacity: 0.45 + level * 0.55,
                transition: 'height 90ms linear, opacity 90ms linear',
                animation: 'dictationPulse 900ms ease-in-out infinite',
                animationDelay: `${idx * 28}ms`,
                '@keyframes dictationPulse': {
                  '0%': { transform: 'scaleY(0.72)' },
                  '50%': { transform: 'scaleY(1)' },
                  '100%': { transform: 'scaleY(0.72)' },
                },
              }}
            />
          ))}
        </Box>
      </Box>
      <IconButton
        size="small"
        onClick={() => stopDictation(false)}
        disabled={isDictationProcessing}
        sx={{ ...iconButtonSx(isDarkMode, isClassic), color: 'white', bgcolor: 'primary.main', '&:hover': { bgcolor: 'primary.dark' } }}
      >
        {isDictationProcessing ? (
          <CircularProgress size={16} sx={{ color: 'white' }} />
        ) : (
          <CheckIcon sx={{ fontSize: '1.1rem' }} />
        )}
      </IconButton>
    </Box>
  ) : null;

  // ─── Inline-вложения (прямая передача в модель, без RAG) ───────────────────

  const inlineFilesSection = inlineFiles.length > 0 ? (
    <InlineAttachmentsList
      files={inlineFiles.map((file) => ({
        name: file.name,
        contentType: file.contentType,
        imageSrc: file.contentType === 'image' ? file.content : undefined,
        size: file.size,
      }))}
      isDarkMode={isDarkMode}
      variant="input"
      onRemove={onInlineFileRemove}
      sx={{ mb: isClassic ? 1.5 : 2 }}
    />
  ) : null;

  // ─── Вложения и индикатор загрузки (общие для обоих стилей) ─────────────────

  const filesSection = uploadedFiles.length > 0 && onFileRemove ? (
    <Box sx={{ mb: isClassic ? 1.5 : 2 }}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
        {uploadedFiles.map((file, index) => (
          <Box
            key={`${file.name}-${index}`}
            className="file-attachment"
            sx={fileAttachmentChipSx}
          >
            <Box sx={legacyFileAttachmentIconBoxSx}>
              {getFileIcon(file)}
            </Box>
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography variant="caption" sx={fileAttachmentNameSx} title={file.name}>
                {file.name}
              </Typography>
            </Box>
            <IconButton size="small" onClick={() => onFileRemove(file, index)} sx={fileAttachmentRemoveSx}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Box>
        ))}
      </Box>
    </Box>
  ) : null;

  const uploadingSection = isUploading ? (
    <Box sx={{ mb: isClassic ? 1.5 : 2, maxWidth: 560, width: '100%' }}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
        {uploadingFile?.previewUrl ? (
          <Box sx={{ position: 'relative', width: 72, height: 72, flexShrink: 0 }}>
            <Box
              component="img"
              src={uploadingFile.previewUrl}
              alt={uploadingFile.name}
              sx={{
                width: '100%',
                height: '100%',
                borderRadius: 1.5,
                objectFit: 'cover',
                display: 'block',
                border: `1px solid ${isDarkMode ? 'rgba(255,255,255,0.2)' : 'rgba(0,0,0,0.15)'}`,
              }}
            />
            <Box
              sx={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: 1.5,
                pointerEvents: 'none',
              }}
            >
              <InlineAttachUploadSpinner />
            </Box>
            <IconButton size="small" disabled sx={{ ...inlineImageRemoveSx, opacity: 0.45 }}>
              <CloseIcon sx={{ fontSize: '0.85rem' }} />
            </IconButton>
          </Box>
        ) : (
          <Box sx={{ width: { xs: '100%', sm: 'calc(50% - 4px)' }, minWidth: 0 }}>
            <InlineDocAttachmentChip
              name={uploadingFile?.name || 'Файл'}
              sizeLabel={
                uploadingFile
                  ? `${formatFileSize(uploadingFile.size)} · Анализ...`
                  : 'Анализ...'
              }
              isDarkMode={isDarkMode}
              uploading
              onRemove={() => undefined}
              removeDisabled
              leading={<InlineDocUploadingThumb filename={uploadingFile?.name || ''} isDarkMode={isDarkMode} />}
            />
          </Box>
        )}
      </Box>
    </Box>
  ) : null;

  // ─── Скрытый input для файлов ────────────────────────────────────────────────

  const fileInput = fileInputRef ? (
    <input
      ref={fileInputRef}
      type="file"
      accept={accept}
      style={{ display: 'none' }}
      onChange={(e) => {
        const files = e.target.files;
        if (files?.length && onFileSelect) onFileSelect(files);
        e.target.value = '';
      }}
    />
  ) : null;

  // ─── КЛАССИЧЕСКИЙ стиль ──────────────────────────────────────────────────────
  const setShellRef = (node: HTMLDivElement | null) => {
    skillAnchorRef.current = node;
    if (toolsMenuAnchorRef) {
      (toolsMenuAnchorRef as React.MutableRefObject<HTMLDivElement | null>).current = node;
    }
  };

  const skillAutocomplete = (
    <SkillMentionAutocomplete
      open={skillMention.open}
      query={skillMention.query}
      anchorEl={skillAnchorRef.current}
      onSelect={handleSelectSkill}
      isDarkMode={isDarkMode}
    />
  );

  if (isClassic) {
    return (
      <>
      <Box
        ref={setShellRef}
        sx={{
          width: '100%',
          maxWidth,
          borderRadius: '28px',
          overflow: 'hidden',
          ...containerSx,
          bgcolor: resolvedShellBg,
          border: `1px solid ${shellBorder}`,
          ...shellChrome,
        }}
      >
        {fileInput}
        <Box sx={{ px: 1.5, pt: 2.75, pb: 1 }}>
          {inlineFilesSection}
          {filesSection}
          {uploadingSection}
          {inputSuggestions}
          {dictationActive ? (
            dictationPanel
          ) : (
            <TextField
              inputRef={inputRef}
              multiline
              minRows={2}
              maxRows={8}
              value={value}
              onChange={handleValueChange}
              onSelect={(e) => {
                const t = e.target as HTMLTextAreaElement;
                updateSkillMentionFromInput(t.value, t.selectionStart);
              }}
              onKeyPress={onKeyPress}
              onPaste={onPaste}
              placeholder={placeholder}
              variant="outlined"
              disabled={inputDisabled}
              fullWidth
              sx={{
                '& .MuiOutlinedInput-root': {
                  bgcolor: 'transparent',
                  border: 'none',
                  fontSize: '0.95rem',
                  lineHeight: 1.6,
                  p: 0,
                  px: 0,
                  '& fieldset': { border: 'none' },
                  '&:hover': { bgcolor: 'transparent' },
                  '&.Mui-focused': { bgcolor: 'transparent', '& fieldset': { border: 'none' } },
                  '& textarea': { resize: 'none' },
                },
              }}
            />
          )}
        </Box>

        {/* Тулбар снизу */}
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 1.5,
            pb: 2.5,
            pt: 0,
            mt: 0,
          }}
        >
          {/* Левая группа: вложения, настройки, доп. действия */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
            {attachBtn}
            {libraryBadge}
            {settingsBtn}
            {extraActions}
          </Box>

          {/* Правая группа: отчёт, отправить, контекст, голос */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
            {rightActions}
          </Box>
        </Box>
      </Box>
      {skillAutocomplete}
      </>
    );
  }

  // ─── КОМПАКТНЫЙ стиль: 1 строка — кнопки по бокам; со 2-й строки — кнопки снизу ─
  // Один TextField всегда в DOM: при смене compactMultiline меняется только order и обёртка кнопок, фокус и перенос по ширине сохраняются
  const textFieldSx = {
    minWidth: 0,
    flex: compactMultiline ? undefined : 1,
    order: compactMultiline ? 0 : 1,
    '& .MuiOutlinedInput-root': {
      bgcolor: 'transparent',
      border: 'none',
      fontSize: '0.875rem',
      py: 0.75,
      px: 2,
      '& fieldset': { border: 'none' },
      '&:hover': { bgcolor: 'transparent' },
      '&.Mui-focused': { bgcolor: 'transparent', '& fieldset': { border: 'none' } },
      '& textarea': { resize: 'none', whiteSpace: 'pre-wrap', wordBreak: 'break-word' },
    },
  };

  return (
    <>
    <Box
      ref={setShellRef}
      sx={{
        width: '100%',
        maxWidth,
        p: 1.5,
        px: 2,
        borderRadius: '28px',
        ...containerSx,
        bgcolor: resolvedShellBg,
        border: `1px solid ${shellBorder}`,
        ...shellChrome,
      }}
    >
      {fileInput}
      {inlineFilesSection}
      {filesSection}
      {uploadingSection}
      {inputSuggestions}

      <Box
        sx={{
          display: 'flex',
          flexDirection: compactMultiline ? 'column' : 'row',
          alignItems: compactMultiline ? 'stretch' : 'center',
          gap: 0.5,
          flexWrap: 'nowrap',
          minHeight: 40,
        }}
      >
        {/* Один TextField на всё время — не переключаем разметку через два разных инпута, чтобы не терять фокус и курсор */}
        {dictationActive ? (
          <Box sx={{ width: '100%' }}>{dictationPanel}</Box>
        ) : (
          <TextField
            inputRef={inputRef}
            multiline
            minRows={1}
            maxRows={8}
            value={value}
            onChange={handleValueChange}
            onSelect={(e) => {
              const t = e.target as HTMLTextAreaElement;
              updateSkillMentionFromInput(t.value, t.selectionStart);
            }}
            onKeyPress={onKeyPress}
            onPaste={onPaste}
            placeholder={placeholder}
            variant="outlined"
            size="small"
            disabled={inputDisabled}
            fullWidth={compactMultiline}
            sx={textFieldSx}
          />
        )}
        {dictationActive ? null : compactMultiline ? (
          <Box sx={{ order: 1, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'nowrap' }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
              {attachBtn}
              {libraryBadge}
              {settingsBtn}
              {extraActions}
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
              {rightActions}
            </Box>
          </Box>
        ) : (
          <>
            <Box sx={{ order: 0, display: 'flex', alignItems: 'center', gap: 0.25 }}>
              {attachBtn}
              {libraryBadge}
              {settingsBtn}
              {extraActions}
            </Box>
            <Box sx={{ order: 2, display: 'flex', alignItems: 'center', gap: 0.25 }}>
              {rightActions}
            </Box>
          </>
        )}
      </Box>
    </Box>
    {skillAutocomplete}
    </>
  );
}