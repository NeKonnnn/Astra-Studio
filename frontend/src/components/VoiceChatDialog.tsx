import React, { useState, useRef, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Box,
  Typography,
  Button,
  Card,
  Collapse,
  IconButton,
  Tooltip,
  Slider,
  CircularProgress,
} from '@mui/material';
import {
  Settings as SettingsIcon,
  Stop as StopIcon,
  ChevronLeft as ChevronLeftIcon,
  ChevronRight as ChevronRightIcon,
  VolumeUp as VolumeUpIcon,
  Send as SendIcon,
  Refresh as RefreshIcon,
  Mic as MicIcon,
} from '@mui/icons-material';
import { useAppActions, useAppContext } from '../contexts/AppContext';
import { useSocket } from '../contexts/SocketContext';
import { getApiUrl, getAuthFetchHeaders, getAuthWsUrl, API_ENDPOINTS } from '../config/api';
import VoiceVisualization3D from './VoiceVisualization3D';
import ThinkingShimmerText from './chat/ThinkingShimmerText';
import { useTheme } from '@mui/material/styles';

export interface VoiceChatDialogProps {
  open: boolean;
  onClose: () => void;
}

const silenceThreshold = 0.1;
const silenceTimeout = 5000;

const voiceTestMessages: Record<string, string> = {
  baya: 'Привет! Я Астра Чат И И. Что обсудим?',
  xenia: 'Привет! Я Астра Чат И И. Что обсудим?',
  kseniya: 'Привет! Я Астра Чат И И. Что обсудим?',
  aidar: 'Привет! Я Астра Чат И И. Что обсудим?',
  eugene: 'Привет! Я Астра Чат И И. Что обсудим?',
};

export default function VoiceChatDialog({ open, onClose }: VoiceChatDialogProps) {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const { state } = useAppContext();
  const { showNotification, setRecording, setSpeaking, getCurrentMessages } = useAppActions();
  const { isConnected } = useSocket();

  const messages = getCurrentMessages();

  // ================================
  // СОСТОЯНИЕ
  // ================================
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [audioLevel, setAudioLevel] = useState(0);
  const [recordedText, setRecordedText] = useState('');
  const [recordingTime, setRecordingTime] = useState(0);
  const [voiceSettings, setVoiceSettings] = useState(() => {
    const savedVoiceSpeaker = localStorage.getItem('voice_speaker');
    const savedVoiceId = localStorage.getItem('voice_id');
    const savedSpeechRate = localStorage.getItem('speech_rate');
    return {
      voice_id: savedVoiceId || 'ru',
      speech_rate: savedSpeechRate ? parseFloat(savedSpeechRate) : 1.0,
      voice_speaker: savedVoiceSpeaker || 'baya',
    };
  });
  const [currentTestVoice, setCurrentTestVoice] = useState<string | null>(null);
  const [voiceSocket, setVoiceSocket] = useState<WebSocket | null>(null);
  const [isVoiceConnected, setIsVoiceConnected] = useState(false);
  const [shouldReconnect, setShouldReconnect] = useState(true);
  const [realtimeText, setRealtimeText] = useState('');
  const [showVoiceSettings, setShowVoiceSettings] = useState(false);
  const [currentVoiceIndex, setCurrentVoiceIndex] = useState(0);
  const [streamForVisualization, setStreamForVisualization] = useState<MediaStream | null>(null);

  // ================================
  // РЕФЫ
  // ================================
  const audioChunksRef = useRef<Blob[]>([]);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentStreamRef = useRef<MediaStream | null>(null);
  const silenceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const lastAudioLevelRef = useRef<number>(0);
  // BUG-FE-63: цикл замера уровня звука и автостоп по тишине живут в замыканиях, созданных
  // до того, как isRecording стал true, поэтому читают актуальное значение из ref.
  const isRecordingRef = useRef(false);
  const stopRecordingRef = useRef<() => void>(() => undefined);

  // ================================
  // СИНХРОНИЗАЦИЯ ИНДЕКСА ГОЛОСА
  // ================================
  useEffect(() => {
    const voices = Object.keys(voiceTestMessages);
    const currentIndex = voices.indexOf(voiceSettings.voice_speaker);
    if (currentIndex !== -1) {
      setCurrentVoiceIndex(currentIndex);
    }
  }, [voiceSettings.voice_speaker]);

  useEffect(() => {
    const voices = Object.keys(voiceTestMessages);
    const currentIndex = voices.indexOf(voiceSettings.voice_speaker);
    if (currentIndex !== -1) {
      setCurrentVoiceIndex(currentIndex);
    }
  }, []);

  useEffect(() => {
    const voices = Object.keys(voiceTestMessages);
    const currentIndex = voices.indexOf(voiceSettings.voice_speaker);
    if (currentIndex !== -1 && currentIndex !== currentVoiceIndex) {
      setCurrentVoiceIndex(currentIndex);
    }
  });

  // ================================
  // ТАЙМЕР ЗАПИСИ И REAL-TIME РАСПОЗНАВАНИЕ
  // ================================
  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (isRecording) {
      interval = setInterval(() => {
        setRecordingTime(prev => prev + 1);
        if (recordingTime > 0 && recordingTime % 2 === 0 && audioChunksRef.current.length > 0) {
          sendRealtimeChunk();
        }
      }, 1000);
    } else {
      setRecordingTime(0);
      setRealtimeText('');
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [isRecording, recordingTime]);

  // Синхронизация с глобальным состоянием
  useEffect(() => {
    isRecordingRef.current = isRecording;
    setRecording(isRecording);
  }, [isRecording]);

  useEffect(() => {
    setSpeaking(isSpeaking);
  }, [isSpeaking]);

  // Очистка при размонтировании
  useEffect(() => {
    return () => {
      if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
        mediaRecorderRef.current = null;
      }
      if (currentStreamRef.current) {
        currentStreamRef.current.getTracks().forEach(track => track.stop());
        currentStreamRef.current = null;
      }
      if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
        audioContextRef.current.close();
        audioContextRef.current = null;
      }
      if (currentAudioRef.current) {
        currentAudioRef.current.pause();
        currentAudioRef.current.src = '';
        currentAudioRef.current = null;
      }
      setRecording(false);
      setSpeaking(false);
    };
  }, []);

  // ================================
  // WEBSOCKET
  // ================================
  const connectVoiceWebSocket = () => {
    if (voiceSocket && voiceSocket.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(getAuthWsUrl('/ws/voice'));
    setVoiceSocket(ws);

    ws.onopen = () => {
      setIsVoiceConnected(true);
      showNotification('success', 'Голосовой чат подключен');
    };

    ws.onmessage = (event) => {
      try {
        if (typeof event.data === 'string') {
          const data = JSON.parse(event.data);
          switch (data.type) {
            case 'listening_started':
              showNotification('success', 'Готов к приему голоса');
              break;
            case 'speech_recognized':
              setRealtimeText(prev => prev + ' ' + data.text);
              showNotification('success', 'Речь распознана в реальном времени');
              break;
            case 'ai_response':
              setRecordedText(data.text);
              showNotification('success', 'Получен ответ от astrachat');
              break;
            case 'speech_error':
              showNotification('warning', data.error || 'Ошибка распознавания речи');
              break;
            case 'tts_error':
              showNotification('error', data.error || 'Ошибка синтеза речи');
              break;
            case 'error':
              showNotification('error', data.error || 'Ошибка WebSocket');
              break;
          }
        } else if (event.data instanceof Blob) {
          playAudioResponse(event.data);
        }
      } catch {}
    };

    ws.onerror = () => {
      setIsVoiceConnected(false);
      showNotification('error', 'Ошибка подключения к голосовому чату');
      setTimeout(() => {
        if (!isVoiceConnected && shouldReconnect) {
          showNotification('info', 'Попытка переподключения...');
          connectVoiceWebSocket();
        }
      }, 5000);
    };

    ws.onclose = (event) => {
      setIsVoiceConnected(false);
      setVoiceSocket(null);
      if (event.code !== 1000 && shouldReconnect) {
        showNotification('warning', 'Соединение с голосовым чатом закрыто, переподключаюсь...');
        setTimeout(() => {
          if (!isVoiceConnected && shouldReconnect) connectVoiceWebSocket();
        }, 3000);
      }
    };
  };

  // ================================
  // ФУНКЦИИ
  // ================================
  const cleanupVoiceResources = () => {
    if (silenceTimerRef.current) { clearTimeout(silenceTimerRef.current); silenceTimerRef.current = null; }
    if (animationFrameRef.current) { cancelAnimationFrame(animationFrameRef.current); animationFrameRef.current = null; }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop(); mediaRecorderRef.current = null;
    }
    if (currentStreamRef.current) {
      currentStreamRef.current.getTracks().forEach(track => track.stop());
      currentStreamRef.current = null;
    }
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      audioContextRef.current.close(); audioContextRef.current = null;
    }
    if (currentAudioRef.current) {
      currentAudioRef.current.pause(); currentAudioRef.current.src = ''; currentAudioRef.current = null;
    }
    if (voiceSocket && voiceSocket.readyState === WebSocket.OPEN) {
      voiceSocket.close(); setVoiceSocket(null);
    }
    setIsRecording(false);
    setIsProcessing(false);
    setIsSpeaking(false);
    setRecordingTime(0);
    setRealtimeText('');
    setAudioLevel(0);
    setStreamForVisualization(null);
    setRecording(false);
    setSpeaking(false);
    showNotification('info', 'Все процессы остановлены');
  };

  const checkSilence = (level: number) => {
    if (level < silenceThreshold) {
      if (!silenceTimerRef.current) {
        silenceTimerRef.current = setTimeout(() => {
          silenceTimerRef.current = null;
          stopRecordingRef.current();
          showNotification('info', 'Автоматическая остановка: не обнаружена речь');
        }, silenceTimeout);
      }
    } else {
      if (silenceTimerRef.current) { clearTimeout(silenceTimerRef.current); silenceTimerRef.current = null; }
    }
  };

  const playAudioResponse = async (audioBlob: Blob) => {
    try {
      const audioUrl = URL.createObjectURL(audioBlob);
      const audio = new Audio(audioUrl);
      currentAudioRef.current = audio;
      audio.onended = () => {
        setIsSpeaking(false); setIsProcessing(false);
        URL.revokeObjectURL(audioUrl); currentAudioRef.current = null;
        showNotification('success', 'Готов к следующему запросу');
      };
      audio.onerror = () => {
        setIsSpeaking(false); setIsProcessing(false);
        showNotification('error', 'Ошибка воспроизведения речи');
        URL.revokeObjectURL(audioUrl); currentAudioRef.current = null;
      };
      setIsProcessing(false);
      setIsSpeaking(true);
      await audio.play();
    } catch {
      setIsSpeaking(false); setIsProcessing(false);
      showNotification('error', 'Ошибка воспроизведения речи');
    }
  };

  const sendRealtimeChunk = async () => {
    if (audioChunksRef.current.length > 0 && voiceSocket && voiceSocket.readyState === WebSocket.OPEN) {
      try {
        const lastChunk = audioChunksRef.current[audioChunksRef.current.length - 1];
        voiceSocket.send(lastChunk);
      } catch {}
    }
  };

  const updateAudioLevel = () => {
    if (analyserRef.current && isRecordingRef.current) {
      const dataArray = new Uint8Array(analyserRef.current.frequencyBinCount);
      analyserRef.current.getByteFrequencyData(dataArray);
      const average = dataArray.reduce((a, b) => a + b) / dataArray.length;
      const normalizedLevel = average / 255;
      setAudioLevel(normalizedLevel);
      lastAudioLevelRef.current = normalizedLevel;
      checkSilence(normalizedLevel);
      animationFrameRef.current = requestAnimationFrame(updateAudioLevel);
    }
  };

  const processAudio = async (audioBlob: Blob): Promise<void> => {
    if (!isConnected) { showNotification('error', 'Нет соединения с сервером'); return; }
    setIsProcessing(true);
    try {
      const formData = new FormData();
      formData.append('audio_file', audioBlob, 'recording.wav');
      const response = await fetch(getApiUrl(API_ENDPOINTS.VOICE_RECOGNIZE), { method: 'POST', body: formData });
      if (!response.ok) {
        showNotification('error', `Ошибка распознавания: ${response.status}`);
        return;
      }
      const result = await response.json();
      if (result.success) {
        const recognizedText = result.text;
        setRecordedText(recognizedText);
        if (recognizedText?.trim()) {
          showNotification('success', 'Речь распознана');
          await sendVoiceMessage(recognizedText);
        } else {
          showNotification('warning', 'Речь не распознана. Попробуйте еще раз.');
        }
      } else {
        showNotification('error', 'Ошибка распознавания речи');
      }
    } catch {
      showNotification('error', 'Ошибка подключения к серверу распознавания');
    } finally {
      setIsProcessing(false);
    }
  };

  const sendVoiceMessage = async (text: string) => {
    try {
      const response = await fetch(getApiUrl(API_ENDPOINTS.CHAT), {
        method: 'POST',
        headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ message: text, streaming: false }),
      });
      const result = await response.json();
      if (result.success) {
        await synthesizeSpeech(result.response);
      } else {
        showNotification('error', 'Ошибка получения ответа от astrachat');
      }
    } catch {
      showNotification('error', 'Ошибка отправки сообщения');
    }
  };

  const synthesizeSpeech = async (text: string) => {
    if (!text.trim()) return;
    if (currentAudioRef.current) {
      currentAudioRef.current.pause(); currentAudioRef.current.src = ''; currentAudioRef.current = null;
    }
    setIsSpeaking(true);
    try {
      const response = await fetch(getApiUrl(API_ENDPOINTS.VOICE_SYNTHESIZE), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          voice_id: voiceSettings.voice_id,
          voice_speaker: voiceSettings.voice_speaker,
          speech_rate: voiceSettings.speech_rate,
        }),
      });
      if (response.ok) {
        const audioBlob = await response.blob();
        const audioUrl = URL.createObjectURL(audioBlob);
        const audio = new Audio(audioUrl);
        currentAudioRef.current = audio;
        audio.onended = () => {
          setIsSpeaking(false); URL.revokeObjectURL(audioUrl); currentAudioRef.current = null;
        };
        audio.onerror = () => {
          setIsSpeaking(false); showNotification('error', 'Ошибка воспроизведения речи');
          URL.revokeObjectURL(audioUrl); currentAudioRef.current = null;
        };
        await audio.play();
      } else {
        setIsSpeaking(false);
        showNotification('error', 'Ошибка синтеза речи');
      }
    } catch {
      setIsSpeaking(false);
      showNotification('error', 'Ошибка синтеза речи');
    }
  };

  const startRecording = async (): Promise<void> => {
    try {
      setShouldReconnect(true);
      if (!isVoiceConnected || !voiceSocket || voiceSocket.readyState !== WebSocket.OPEN) {
        showNotification('info', 'Подключаю голосовой чат...');
        connectVoiceWebSocket();
      }
      if (voiceSocket && voiceSocket.readyState === WebSocket.OPEN) {
        voiceSocket.send(JSON.stringify({ type: 'start_listening' }));
      }
      if (currentStreamRef.current) {
        currentStreamRef.current.getTracks().forEach(track => track.stop());
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      currentStreamRef.current = stream;
      setStreamForVisualization(stream);
      audioContextRef.current = new AudioContext();
      analyserRef.current = audioContextRef.current.createAnalyser();
      const source = audioContextRef.current.createMediaStreamSource(stream);
      source.connect(analyserRef.current);
      analyserRef.current.fftSize = 256;

      const preferredMimeTypes = [
        'audio/wav', 'audio/webm;codecs=pcm', 'audio/webm;codecs=opus',
        'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus',
      ];
      let selectedOptions: { mimeType: string } | undefined;
      for (const mimeType of preferredMimeTypes) {
        if (MediaRecorder.isTypeSupported(mimeType)) { selectedOptions = { mimeType }; break; }
      }
      mediaRecorderRef.current = selectedOptions
        ? new MediaRecorder(stream, selectedOptions)
        : new MediaRecorder(stream);

      audioChunksRef.current = [];
      mediaRecorderRef.current.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data);
      };
      mediaRecorderRef.current.onstop = async () => {
        setIsProcessing(true);
        try {
          const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
          if (audioBlob.size < 100) {
            showNotification('warning', 'Запись слишком короткая, попробуйте еще раз');
            setIsProcessing(false);
            return;
          }
          if (voiceSocket && voiceSocket.readyState === WebSocket.OPEN) {
            voiceSocket.send(audioBlob);
            showNotification('info', 'Отправляю голос на обработку...');
          } else {
            showNotification('warning', 'WebSocket не подключен, использую fallback...');
            await processAudio(audioBlob);
            setIsProcessing(false);
          }
        } catch {
          showNotification('error', 'Ошибка обработки аудио');
          setIsProcessing(false);
        }
      };
      mediaRecorderRef.current.onerror = () => {
        showNotification('error', 'Ошибка записи аудио');
        setIsRecording(false);
      };
      mediaRecorderRef.current.start(1000);
      isRecordingRef.current = true;
      setIsRecording(true);
      updateAudioLevel();
      showNotification('info', 'Запись началась. Говорите...');
    } catch (error) {
      const errorObj = error as { name?: string };
      if (errorObj?.name === 'NotAllowedError') {
        showNotification('error', 'Доступ к микрофону заблокирован. Разрешите доступ в браузере.');
      } else if (errorObj?.name === 'NotFoundError') {
        showNotification('error', 'Микрофон не найден');
      } else {
        showNotification('error', 'Не удалось получить доступ к микрофону');
      }
      isRecordingRef.current = false;
      setIsRecording(false);
    }
  };

  const stopRecording = (): void => {
    setShouldReconnect(false);
    if (mediaRecorderRef.current && isRecordingRef.current) {
      mediaRecorderRef.current.stop(); mediaRecorderRef.current = null;
    }
    isRecordingRef.current = false;
    if (currentStreamRef.current) {
      currentStreamRef.current.getTracks().forEach(track => track.stop());
      currentStreamRef.current = null;
    }
    if (animationFrameRef.current) { cancelAnimationFrame(animationFrameRef.current); animationFrameRef.current = null; }
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      audioContextRef.current.close(); audioContextRef.current = null;
    }
    if (silenceTimerRef.current) { clearTimeout(silenceTimerRef.current); silenceTimerRef.current = null; }
    setIsRecording(false);
    setAudioLevel(0);
    setRealtimeText('');
    setRecordingTime(0);
    setStreamForVisualization(null);
    showNotification('info', 'Прослушивание остановлено');
  };

  stopRecordingRef.current = stopRecording;

  const handleManualSend = () => {
    if (recordedText.trim()) {
      sendVoiceMessage(recordedText);
      setRecordedText('');
    }
  };

  const saveVoiceSettings = (settings: typeof voiceSettings) => {
    localStorage.setItem('voice_speaker', settings.voice_speaker);
    localStorage.setItem('voice_id', settings.voice_id);
    localStorage.setItem('speech_rate', settings.speech_rate.toString());
  };

  const switchVoice = (direction: 'next' | 'prev') => {
    const voices = Object.keys(voiceTestMessages);
    let newIndex: number;
    if (direction === 'next') {
      newIndex = currentVoiceIndex === voices.length - 1 ? 0 : currentVoiceIndex + 1;
    } else {
      newIndex = currentVoiceIndex === 0 ? voices.length - 1 : currentVoiceIndex - 1;
    }
    const newVoice = voices[newIndex];
    if (currentAudioRef.current) {
      currentAudioRef.current.pause(); currentAudioRef.current.src = ''; currentAudioRef.current = null;
    }
    setIsSpeaking(false);
    setCurrentTestVoice(null);
    setCurrentVoiceIndex(newIndex);
    const newSettings = { ...voiceSettings, voice_speaker: newVoice };
    setVoiceSettings(newSettings);
    saveVoiceSettings(newSettings);
    testVoice(newVoice);
  };

  const testVoice = async (voiceName: string) => {
    try {
      if (currentAudioRef.current) {
        currentAudioRef.current.pause(); currentAudioRef.current.src = ''; currentAudioRef.current = null;
      }
      setCurrentTestVoice(voiceName);
      const testMessage = voiceTestMessages[voiceName];
      const response = await fetch(getApiUrl(API_ENDPOINTS.VOICE_SYNTHESIZE), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: testMessage,
          voice_id: voiceSettings.voice_id,
          voice_speaker: voiceName,
          speech_rate: voiceSettings.speech_rate,
        }),
      });
      if (response.ok) {
        const audioBlob = await response.blob();
        const audioUrl = URL.createObjectURL(audioBlob);
        const audio = new Audio(audioUrl);
        audio.onended = () => { setCurrentTestVoice(null); URL.revokeObjectURL(audioUrl); };
        audio.onerror = () => {
          setCurrentTestVoice(null);
          showNotification('error', 'Ошибка воспроизведения тестового голоса');
          URL.revokeObjectURL(audioUrl);
        };
        currentAudioRef.current = audio;
        await audio.play();
        showNotification('success', `Тестирую голос ${voiceName}...`);
      } else {
        setCurrentTestVoice(null);
        throw new Error(`HTTP ${response.status}`);
      }
    } catch (error) {
      setCurrentTestVoice(null);
      showNotification('error', `Ошибка тестирования голоса: ${error instanceof Error ? error.message : 'Неизвестная ошибка'}`);
    }
  };

  // ================================
  // РЕНДЕР
  // ================================
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      TransitionComponent={undefined}
      transitionDuration={0}
      PaperProps={{ sx: { bgcolor: 'background.paper', borderRadius: 3 } }}
    >
      <DialogTitle sx={{ textAlign: 'center', pb: 1 }}>
        Голосовой чат
      </DialogTitle>
      <DialogContent sx={{ textAlign: 'center', py: 3 }}>
        {/* Индикатор подключения WebSocket */}
        <Box sx={{ mb: 3, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 1 }}>
          <Box
            sx={{
              width: 12, height: 12, borderRadius: '50%',
              backgroundColor: isVoiceConnected ? 'success.main' : 'warning.main',
              animation: isVoiceConnected ? 'pulse 2s ease-in-out infinite' : 'none',
              border: isVoiceConnected ? '2px solid rgba(76, 175, 80, 0.3)' : '2px solid rgba(255, 152, 0, 0.3)',
            }}
          />
          <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 500 }}>
            {isVoiceConnected ? 'Real-Time Голосовой Чат' : 'WebSocket подключится при записи'}
          </Typography>
        </Box>

        {/* Кнопка настроек голоса */}
        <Box sx={{ position: 'absolute', bottom: 20, left: 20, zIndex: 10 }}>
          <Tooltip title="Настройки голоса">
            <IconButton
              onClick={() => setShowVoiceSettings(!showVoiceSettings)}
              sx={{
                color: 'primary.main', bgcolor: 'background.default',
                border: '2px solid', borderColor: 'primary.main',
                width: 48, height: 48,
                '&:hover': { bgcolor: 'primary.main', color: 'white', transform: 'scale(1.05)' },
                transition: 'all 0.3s ease',
                animation: showVoiceSettings ? 'spin 2s linear infinite' : 'none',
                '@keyframes spin': { '0%': { transform: 'rotate(0deg)' }, '100%': { transform: 'rotate(360deg)' } },
              }}
            >
              <SettingsIcon />
            </IconButton>
          </Tooltip>
        </Box>

        {/* Кнопка остановки всех процессов */}
        <Box sx={{ position: 'absolute', bottom: 20, right: 20, zIndex: 10 }}>
          {(isRecording || isProcessing || isSpeaking || (voiceSocket && voiceSocket.readyState === WebSocket.OPEN)) && (
            <Tooltip title="Остановить все процессы">
              <IconButton
                onClick={cleanupVoiceResources}
                sx={{
                  color: 'error.main', bgcolor: 'background.default',
                  border: '2px solid', borderColor: 'error.main',
                  width: 48, height: 48,
                  '&:hover': { bgcolor: 'error.main', color: 'white', transform: 'scale(1.05)' },
                  transition: 'all 0.3s ease',
                }}
              >
                <StopIcon />
              </IconButton>
            </Tooltip>
          )}
        </Box>

        {/* Меню выбора голоса */}
        <Collapse in={showVoiceSettings}>
          <Card sx={{ mb: 3, p: 2, backgroundColor: 'background.default' }}>
            <Typography variant="subtitle2" color="primary" gutterBottom sx={{ textAlign: 'center', mb: 3 }}>
              Выберите голос:
            </Typography>
            <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 1, position: 'relative', height: 120, overflow: 'hidden' }}>
              <IconButton
                onClick={() => switchVoice('prev')}
                sx={{ color: 'text.secondary', '&:hover': { color: 'primary.main' }, zIndex: 2, position: 'absolute', left: 220, top: '50%', transform: 'translateY(-50%)' }}
              >
                <ChevronLeftIcon />
              </IconButton>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, position: 'relative', width: 400, height: 100, mx: 'auto', ml: '168px' }}>
                {Object.entries(voiceTestMessages).map(([voiceKey], index) => {
                  const isSelected = voiceSettings.voice_speaker === voiceKey;
                  const isPlaying = isSpeaking && currentTestVoice === voiceKey;
                  const distance = Math.abs(index - currentVoiceIndex);
                  let size: number, opacity: number, scale: number, zIndex: number, translateX: number;
                  if (distance === 0) { size = 80; opacity = 1; scale = 1; zIndex = 3; translateX = 0; }
                  else if (distance === 1) { size = 60; opacity = 0.7; scale = 0.8; zIndex = 2; translateX = index < currentVoiceIndex ? -62 : 81; }
                  else { size = 40; opacity = 0.3; scale = 0.6; zIndex = 1; translateX = index < currentVoiceIndex ? -95 : 134; }
                  return (
                    <Box
                      key={voiceKey}
                      sx={{ position: 'absolute', left: '50%', transform: `translateX(${translateX}px)`, cursor: 'pointer', transition: 'all 0.5s cubic-bezier(0.4, 0, 0.2, 1)', zIndex }}
                      onClick={() => {
                        setCurrentVoiceIndex(index);
                        const newSettings = { ...voiceSettings, voice_speaker: voiceKey };
                        setVoiceSettings(newSettings);
                        saveVoiceSettings(newSettings);
                        testVoice(voiceKey);
                      }}
                    >
                      <Box
                        sx={{
                          width: size, height: size, borderRadius: '50%',
                          background: isSelected
                            ? 'linear-gradient(135deg, #ff6b9d 0%, #c44569 50%, #ff6b9d 100%)'
                            : 'linear-gradient(135deg, #667eea 0%, #764ba2 50%, #667eea 100%)',
                          backgroundSize: '200% 200%',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          boxShadow: isSelected ? '0 8px 25px rgba(255, 107, 157, 0.4)' : '0 4px 15px rgba(102, 126, 234, 0.3)',
                          transition: 'all 0.3s ease', opacity, transform: `scale(${scale})`,
                          outline: 'none', border: 'none',
                          animation: isSelected
                            ? 'gradientShift 3s ease-in-out infinite, float 2s ease-in-out infinite'
                            : 'gradientShift 4s ease-in-out infinite',
                          '@keyframes gradientShift': { '0%': { backgroundPosition: '0% 50%' }, '50%': { backgroundPosition: '100% 50%' }, '100%': { backgroundPosition: '0% 50%' } },
                          '@keyframes float': { '0%, 100%': { transform: `scale(${scale}) translateY(0px)` }, '50%': { transform: `scale(${scale}) translateY(-3px)` } },
                          '&:hover': { transform: `scale(${scale * 1.05})`, boxShadow: isSelected ? '0 12px 35px rgba(255, 107, 157, 0.6)' : '0 8px 25px rgba(102, 126, 234, 0.5)', outline: 'none', border: 'none' },
                          '&:focus': { outline: 'none', border: 'none' },
                        }}
                      >
                        <Box sx={{ position: 'absolute', top: '15%', left: '15%', width: '30%', height: '30%', borderRadius: '50%', background: 'radial-gradient(circle, rgba(255,255,255,0.4) 0%, transparent 70%)', animation: 'sparkle 2s ease-in-out infinite', '@keyframes sparkle': { '0%, 100%': { opacity: 0.4, transform: 'scale(1)' }, '50%': { opacity: 0.8, transform: 'scale(1.2)' } } }} />
                      </Box>
                      {isPlaying && (
                        <Box sx={{ position: 'absolute', top: -5, right: -5, width: 20, height: 20, borderRadius: '50%', backgroundColor: 'success.main', display: 'flex', alignItems: 'center', justifyContent: 'center', animation: 'pulse 1s infinite', '@keyframes pulse': { '0%': { transform: 'scale(1)', opacity: 1 }, '50%': { transform: 'scale(1.2)', opacity: 0.7 }, '100%': { transform: 'scale(1)', opacity: 1 } } }}>
                          <VolumeUpIcon sx={{ fontSize: 12, color: 'white' }} />
                        </Box>
                      )}
                      {isSelected && (
                        <Typography variant="caption" sx={{ textAlign: 'center', mt: 1, display: 'block', fontWeight: 'bold', color: 'primary.main', opacity: 1, fontSize: size * 0.2, whiteSpace: 'nowrap' }}>
                          {voiceKey === 'baya' && 'Baya'}{voiceKey === 'xenia' && 'Xenia'}{voiceKey === 'kseniya' && 'Kseniya'}{voiceKey === 'aidar' && 'Aidar'}{voiceKey === 'eugene' && 'Eugene'}
                        </Typography>
                      )}
                    </Box>
                  );
                })}
              </Box>
              <IconButton
                onClick={() => switchVoice('next')}
                sx={{ color: 'text.secondary', '&:hover': { color: 'primary.main' }, zIndex: 2, position: 'absolute', right: 220, top: '50%', transform: 'translateY(-50%)' }}
              >
                <ChevronRightIcon />
              </IconButton>
            </Box>
            <Box sx={{ textAlign: 'center', mt: 2 }}>
              <Typography variant="body2" color="text.secondary">
                {currentVoiceIndex + 1} / {Object.keys(voiceTestMessages).length}
              </Typography>
            </Box>
            <Box sx={{ mt: 3, px: 2 }}>
              <Typography variant="subtitle2" color="primary" gutterBottom sx={{ textAlign: 'center', mb: 2 }}>
                Скорость речи ассистента:
              </Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 1 }}>
                <Typography variant="caption" color="text.secondary" sx={{ minWidth: 40 }}>Медленно</Typography>
                <Slider
                  value={voiceSettings.speech_rate}
                  onChange={(_, value) => {
                    const newSettings = { ...voiceSettings, speech_rate: value as number };
                    setVoiceSettings(newSettings);
                    saveVoiceSettings(newSettings);
                  }}
                  min={0.5} max={2.0} step={0.1}
                  marks={[{ value: 0.5, label: '0.5x' }, { value: 1.0, label: '1.0x' }, { value: 1.5, label: '1.5x' }, { value: 2.0, label: '2.0x' }]}
                  valueLabelDisplay="auto"
                  sx={{ flex: 1, '& .MuiSlider-mark': { backgroundColor: 'primary.main' }, '& .MuiSlider-markLabel': { color: 'text.secondary', fontSize: '0.75rem' }, '& .MuiSlider-valueLabel': { backgroundColor: 'primary.main', color: 'white' } }}
                />
                <Typography variant="caption" color="text.secondary" sx={{ minWidth: 40 }}>Быстро</Typography>
              </Box>
              <Typography variant="caption" color="text.secondary" sx={{ textAlign: 'center', display: 'block' }}>
                Текущая скорость: {voiceSettings.speech_rate.toFixed(1)}x
              </Typography>
              <Box sx={{ mt: 2, textAlign: 'center' }}>
                <Button
                  variant="outlined" size="small"
                  startIcon={<VolumeUpIcon />}
                  onClick={() => synthesizeSpeech('Это тест скорости речи ассистента. Настройте скорость по вашему вкусу.')}
                  disabled={isSpeaking}
                  sx={{ fontSize: '0.75rem', px: 2, py: 0.5, borderColor: 'primary.main', color: 'primary.main', '&:hover': { borderColor: 'primary.dark', backgroundColor: 'primary.light', color: 'primary.dark' } }}
                >
                  Тестировать скорость
                </Button>
              </Box>
            </Box>
          </Card>
        </Collapse>

        {!isRecording ? (
          <Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
              Нажмите кнопку микрофона для начала записи
            </Typography>
            <IconButton
              size="large"
              onClick={startRecording}
              disabled={state.isLoading && !messages.some(msg => msg.isStreaming)}
              sx={{
                width: 80, height: 80, bgcolor: 'primary.main', color: 'white',
                '&:hover': { bgcolor: 'primary.dark' },
                '&:disabled': { bgcolor: 'action.disabledBackground', color: 'action.disabled' },
              }}
            >
              <MicIcon sx={{ fontSize: 40 }} />
            </IconButton>
          </Box>
        ) : (
          <Box>
            <Box sx={{ mb: 4, position: 'relative', display: 'inline-block' }}>
              {streamForVisualization ? (
                <Box sx={{ position: 'relative', display: 'inline-block', bgcolor: 'transparent', border: 'none', boxShadow: 'none' }}>
                  <Box sx={{ width: 300, height: 300, position: 'relative', bgcolor: 'transparent', border: 'none', boxShadow: 'none' }}>
                    <VoiceVisualization3D stream={streamForVisualization} />
                  </Box>
                  <Box
                    sx={{
                      position: 'absolute',
                      bottom: 12,
                      left: '50%',
                      transform: 'translateX(-50%)',
                      zIndex: 1,
                    }}
                  >
                    <IconButton
                      onClick={stopRecording}
                      disabled={isProcessing || isSpeaking}
                      sx={{
                        width: 52,
                        height: 52,
                        backgroundColor: 'rgba(244, 67, 54, 0.95)',
                        color: 'white',
                        '&:hover': { backgroundColor: 'error.dark' },
                      }}
                    >
                      <StopIcon sx={{ fontSize: 26 }} />
                    </IconButton>
                  </Box>
                </Box>
              ) : (
                <Box
                  sx={{
                    width: 200, height: 200, borderRadius: '50%',
                    background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                >
                  <IconButton
                    onClick={stopRecording}
                    disabled={isProcessing || isSpeaking}
                    sx={{ width: 120, height: 120, backgroundColor: 'white', color: 'error.main', '&:hover': { backgroundColor: 'grey.100' } }}
                  >
                    <StopIcon sx={{ fontSize: 48 }} />
                  </IconButton>
                </Box>
              )}
              {isProcessing && <Box sx={{ position: 'absolute', top: -10, right: -10 }}><CircularProgress size={24} color="secondary" /></Box>}
              {isSpeaking && (
                <Box sx={{ position: 'absolute', bottom: -10, right: -10 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, height: 32 }}>
                    {[...Array(5)].map((_, i) => (
                      <Box key={i} sx={{ width: 4, height: 16, background: 'linear-gradient(180deg, #4caf50 0%, #66bb6a 50%, #81c784 100%)', borderRadius: 2, animation: 'soundWave 1s infinite ease-in-out', animationDelay: `${i * 0.1}s`, boxShadow: '0 2px 6px rgba(76, 175, 80, 0.4)', '@keyframes soundWave': { '0%, 100%': { transform: 'scaleY(0.2)', opacity: 0.6 }, '50%': { transform: 'scaleY(1)', opacity: 1 } } }} />
                    ))}
                  </Box>
                </Box>
              )}
            </Box>
            <Box sx={{ mb: 2 }}>
              <Typography variant="h6" color="error.main" gutterBottom>
                Прослушивание... {Math.floor(recordingTime / 60)}:{(recordingTime % 60).toString().padStart(2, '0')}
              </Typography>
              <Box sx={{ display: 'flex', justifyContent: 'center', gap: 1 }}>
                {[0, 0.2, 0.4].map((delay, i) => (
                  <Box key={i} sx={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: 'error.main', animation: `pulse 1s infinite`, animationDelay: `${delay}s` }} />
                ))}
              </Box>
            </Box>
            <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
              Говорите четко и ясно. Real-time распознавание каждые 2 секунды. Автоматическая остановка через 5 секунд тишины.
            </Typography>
          </Box>
        )}

        {isRecording && realtimeText && (
          <Card sx={{ mb: 3, p: 2, backgroundColor: 'warning.light' }}>
            <Typography variant="subtitle2" color="warning.dark" gutterBottom>
              Real-time распознавание (каждые 2 сек):
            </Typography>
            <Typography variant="body1" sx={{ fontStyle: 'italic', color: 'warning.dark' }}>
              "{realtimeText}"
            </Typography>
          </Card>
        )}

        {recordedText && (
          <Card sx={{ mb: 3, p: 2, backgroundColor: 'background.default' }}>
            <Typography variant="subtitle2" color="primary" gutterBottom>
              Финальный распознанный текст:
            </Typography>
            <Typography variant="body1" sx={{ fontStyle: 'italic' }}>
              "{recordedText}"
            </Typography>
            <Box sx={{ mt: 2, display: 'flex', gap: 1, justifyContent: 'center' }}>
              <Button variant="contained" startIcon={<SendIcon />} onClick={handleManualSend} disabled={isProcessing || isSpeaking}>
                Отправить
              </Button>
              <Button variant="outlined" startIcon={<RefreshIcon />} onClick={() => setRecordedText('')}>
                Очистить
              </Button>
            </Box>
          </Card>
        )}

        {isProcessing && (
          <Box sx={{ mb: 2, textAlign: 'center' }}>
            <ThinkingShimmerText isDarkMode={isDarkMode} fontSize="0.875rem" fontWeight={500}>
              Ассистент думает...
            </ThinkingShimmerText>
          </Box>
        )}

        {isSpeaking && !isProcessing && (
          <Box sx={{ mb: 2, textAlign: 'center' }}>
            <Box sx={{ display: 'flex', justifyContent: 'center', gap: 1 }}>
              {[...Array(9)].map((_, i) => (
                <Box key={i} sx={{ width: 4, height: 22, background: 'linear-gradient(180deg, #4caf50 0%, #66bb6a 50%, #81c784 100%)', borderRadius: 2, animation: 'soundWave2 1.2s infinite ease-in-out', animationDelay: `${i * 0.08}s`, boxShadow: '0 3px 8px rgba(76, 175, 80, 0.5)', '@keyframes soundWave2': { '0%, 100%': { transform: 'scaleY(0.3)', opacity: 0.5 }, '50%': { transform: 'scaleY(1)', opacity: 1 } } }} />
              ))}
            </Box>
          </Box>
        )}
      </DialogContent>
      <DialogActions sx={{ justifyContent: 'center', pb: 3 }}>
        <Button onClick={onClose}>Закрыть</Button>
      </DialogActions>
    </Dialog>
  );
}
