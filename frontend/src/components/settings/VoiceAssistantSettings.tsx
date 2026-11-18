import React, { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Slider,
  Switch,
  TextField,
  Typography,
  Chip,
} from '@mui/material';
import RecordVoiceOverIcon from '@mui/icons-material/RecordVoiceOver';
import MicIcon from '@mui/icons-material/Mic';
import {
  isWebSpeechAvailable,
  loadVoiceAssistantSettings,
  updateVoiceAssistantSettings,
  VOICE_ASSISTANT_SETTINGS_CHANGED,
  SILERO_VOICE_OPTIONS,
  type VoiceAssistantSettings,
  type TtsStatusResponse,
  type VoiceAssistantTtsEngine,
} from '../../voiceAssistant';
import { fetchVaTtsStatus } from '../../voiceAssistant/ttsStatus';
import { speakConfirmation } from '../../voiceAssistant/ttsConfirm';
import { authFetch, getApiUrl, API_ENDPOINTS } from '../../config/api';
import { useAppActions } from '../../contexts/AppContext';

export default function VoiceAssistantSettings() {
  const [settings, setSettings] = useState<VoiceAssistantSettings>(() =>
    loadVoiceAssistantSettings(),
  );
  const [ttsStatus, setTtsStatus] = useState<TtsStatusResponse | null>(null);
  const [testing, setTesting] = useState(false);
  const [recording, setRecording] = useState(false);
  const [profileExists, setProfileExists] = useState(false);
  const [refText, setRefText] = useState('');
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const { showNotification } = useAppActions();
  const webSpeech = isWebSpeechAvailable();

  const refreshTts = async () => {
    const st = await fetchVaTtsStatus(true);
    setTtsStatus(st);
  };

  const refreshProfile = async () => {
    try {
      const r = await authFetch(getApiUrl(API_ENDPOINTS.VOICE_ASSISTANT_VOICE_PROFILE));
      if (!r.ok) {
        setProfileExists(false);
        return;
      }
      const data = (await r.json()) as { exists?: boolean; ref_text?: string };
      setProfileExists(Boolean(data.exists));
      if (data.ref_text) setRefText(data.ref_text);
    } catch {
      setProfileExists(false);
    }
  };

  useEffect(() => {
    const onChange = () => setSettings(loadVoiceAssistantSettings());
    window.addEventListener(VOICE_ASSISTANT_SETTINGS_CHANGED, onChange);
    void refreshTts();
    void refreshProfile();
    return () => window.removeEventListener(VOICE_ASSISTANT_SETTINGS_CHANGED, onChange);
  }, []);

  const patch = (partial: Partial<VoiceAssistantSettings>) => {
    const next = updateVoiceAssistantSettings(partial);
    setSettings(next);
  };

  const onTestVoice = async () => {
    setTesting(true);
    try {
      await speakConfirmation('Привет! Я голосовой помощник Астра. Проверка голоса.', {
        voice_id: settings.voiceId,
        voice_speaker: settings.voiceSpeaker,
        speech_rate: settings.speechRate,
        sileroAvailable: ttsStatus?.available !== false,
      });
    } finally {
      setTesting(false);
    }
  };

  const sileroOk = ttsStatus?.available === true;
  const sileroBlocked = ttsStatus?.blocked === true;

  return (
    <Box sx={{ p: 3 }}>
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <RecordVoiceOverIcon color="primary" />
            Голосовой помощник
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            После «{settings.wakeWord || 'астра'}» — команда или вопрос. Ответ стримится и
            озвучивается по фразам; можно перебить речь (barge-in). Логи: Console → [VA].
          </Typography>

          {!webSpeech && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              Web Speech API недоступен. Будет WhisperX (медленнее). Рекомендуется Chrome.
            </Alert>
          )}

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2, flexWrap: 'wrap' }}>
            <Typography variant="body2" fontWeight={600}>
              Silero TTS:
            </Typography>
            {ttsStatus == null ? (
              <Chip size="small" label="проверка…" />
            ) : sileroOk ? (
              <Chip size="small" color="success" label={`доступен (${ttsStatus.status})`} />
            ) : (
              <Chip size="small" color="warning" label={`недоступен / blocked (${ttsStatus.status})`} />
            )}
            <Button size="small" onClick={() => void refreshTts()}>
              Обновить
            </Button>
          </Box>
          {sileroBlocked && (
            <Alert severity="info" sx={{ mb: 2 }}>
              {ttsStatus?.hint ||
                'Silero недоступен. Озвучка пойдёт через голос браузера (speechSynthesis), пока сервис не поднимется.'}
              {ttsStatus?.error ? ` (${ttsStatus.error})` : ''}
            </Alert>
          )}

          <FormControlLabel
            control={
              <Switch
                checked={settings.enabled}
                onChange={(_, checked) => patch({ enabled: checked })}
                color="primary"
              />
            }
            label="Включить голосовой помощник"
          />

          <FormControlLabel
            sx={{ display: 'block', mt: 1 }}
            control={
              <Switch
                checked={settings.confirmTts}
                onChange={(_, checked) => patch({ confirmTts: checked })}
                color="primary"
              />
            }
            label="Голосовое подтверждение / ответы (TTS)"
          />

          <TextField
            fullWidth
            size="small"
            label="Wake-word"
            value={settings.wakeWord}
            onChange={(e) => patch({ wakeWord: e.target.value })}
            sx={{ mt: 2, maxWidth: 360 }}
            helperText="Фраза активации, например «астра»"
          />

          <TextField
            fullWidth
            size="small"
            label="Язык распознавания"
            value={settings.language}
            onChange={(e) => patch({ language: e.target.value })}
            sx={{ mt: 2, maxWidth: 360 }}
            helperText="BCP-47, по умолчанию ru-RU"
          />
        </CardContent>
      </Card>

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Голос озвучки
          </Typography>

          <FormControl size="small" sx={{ mt: 1, minWidth: 220, mr: 2 }}>
            <InputLabel id="va-voice-label">Голос Silero</InputLabel>
            <Select
              labelId="va-voice-label"
              label="Голос Silero"
              value={settings.voiceSpeaker}
              onChange={(e) => patch({ voiceSpeaker: String(e.target.value) })}
            >
              {SILERO_VOICE_OPTIONS.map((v) => (
                <MenuItem key={v.id} value={v.id}>
                  {v.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <FormControl size="small" sx={{ mt: 1, minWidth: 220 }}>
            <InputLabel id="va-engine-label">Движок TTS</InputLabel>
            <Select
              labelId="va-engine-label"
              label="Движок TTS"
              value={settings.ttsEngine}
              onChange={(e) => patch({ ttsEngine: e.target.value as VoiceAssistantTtsEngine })}
            >
              <MenuItem value="auto">Авто (браузер — быстрее)</MenuItem>
              <MenuItem value="browser">Только браузер</MenuItem>
              <MenuItem value="silero">Только Silero</MenuItem>
              <MenuItem value="omnivoice">Мой голос (OmniVoice)</MenuItem>
            </Select>
          </FormControl>

          <Box sx={{ mt: 2, maxWidth: 360 }}>
            <Typography variant="body2" gutterBottom>
              Скорость речи: {settings.speechRate.toFixed(2)}
            </Typography>
            <Slider
              min={0.5}
              max={1.8}
              step={0.05}
              value={settings.speechRate}
              onChange={(_, v) => patch({ speechRate: v as number })}
              valueLabelDisplay="auto"
            />
          </Box>

          <Button
            variant="outlined"
            sx={{ mt: 2, mr: 1 }}
            disabled={testing}
            onClick={() => void onTestVoice()}
          >
            {testing ? 'Озвучиваю…' : 'Проверить голос'}
          </Button>
        </CardContent>
      </Card>

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Мой голос (OmniVoice)
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Запишите 3–10 секунд своей речи. Модель клонирует голос для озвучки VA.
            Веса модели — CC-BY-NC (некоммерческое использование). Записывайте только свой голос.
          </Typography>

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
            <Chip
              size="small"
              color={
                ttsStatus?.omnivoice?.ready
                  ? 'success'
                  : ttsStatus?.omnivoice?.available
                    ? 'warning'
                    : 'default'
              }
              label={
                ttsStatus?.omnivoice?.ready
                  ? 'OmniVoice готов'
                  : ttsStatus?.omnivoice?.available
                    ? `OmniVoice: ${ttsStatus.omnivoice.status}`
                    : ttsStatus?.omnivoice?.status === 'unhealthy'
                      ? 'OmniVoice ошибка загрузки'
                      : 'OmniVoice выкл / недоступен'
              }
            />
            <Chip
              size="small"
              color={profileExists ? 'success' : 'default'}
              label={profileExists ? 'Образец записан' : 'Образец не записан'}
            />
          </Box>
          {(ttsStatus?.omnivoice?.hint || ttsStatus?.omnivoice?.error) && (
            <Alert
              severity={ttsStatus?.omnivoice?.available ? 'info' : 'warning'}
              sx={{ mb: 1 }}
            >
              {ttsStatus?.omnivoice?.hint || ttsStatus?.omnivoice?.error}
            </Alert>
          )}

          <TextField
            fullWidth
            size="small"
            label="Текст образца (необязательно)"
            value={refText}
            onChange={(e) => setRefText(e.target.value)}
            sx={{ mb: 1, maxWidth: 480 }}
            helperText="Что вы произнесёте при записи — повышает качество клона"
          />

          <Button
            variant={recording ? 'contained' : 'outlined'}
            color={recording ? 'error' : 'primary'}
            startIcon={<MicIcon />}
            sx={{ mr: 1 }}
            onClick={() => {
              if (recording) {
                mediaRecorderRef.current?.stop();
                setRecording(false);
                return;
              }
              void (async () => {
                try {
                  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                  const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                    ? 'audio/webm;codecs=opus'
                    : 'audio/webm';
                  const rec = new MediaRecorder(stream, { mimeType: mime });
                  chunksRef.current = [];
                  rec.ondataavailable = (e) => {
                    if (e.data.size) chunksRef.current.push(e.data);
                  };
                  rec.onstop = async () => {
                    stream.getTracks().forEach((t) => t.stop());
                    const blob = new Blob(chunksRef.current, { type: mime });
                    const form = new FormData();
                    form.append('file', blob, 'ref.webm');
                    if (refText.trim()) form.append('ref_text', refText.trim());
                    const resp = await authFetch(
                      getApiUrl(API_ENDPOINTS.VOICE_ASSISTANT_VOICE_PROFILE),
                      { method: 'POST', body: form },
                    );
                    if (!resp.ok) {
                      showNotification('error', 'Не удалось сохранить образец голоса');
                      return;
                    }
                    setProfileExists(true);
                    patch({ ttsEngine: 'omnivoice' });
                    showNotification('success', 'Голос сохранён. Движок TTS: OmniVoice');
                  };
                  mediaRecorderRef.current = rec;
                  rec.start();
                  setRecording(true);
                  // авто-стоп ~7с
                  window.setTimeout(() => {
                    if (mediaRecorderRef.current?.state === 'recording') {
                      mediaRecorderRef.current.stop();
                      setRecording(false);
                    }
                  }, 7000);
                  showNotification('info', 'Говорите 3–7 секунд…');
                } catch {
                  showNotification('error', 'Нет доступа к микрофону');
                }
              })();
            }}
          >
            {recording ? 'Стоп' : 'Записать мой голос (~7с)'}
          </Button>
          <Button
            variant="text"
            color="inherit"
            disabled={!profileExists}
            onClick={() => {
              void (async () => {
                await authFetch(getApiUrl(API_ENDPOINTS.VOICE_ASSISTANT_VOICE_PROFILE), {
                  method: 'DELETE',
                });
                setProfileExists(false);
                if (settings.ttsEngine === 'omnivoice') patch({ ttsEngine: 'auto' });
                showNotification('info', 'Образец голоса удалён');
              })();
            }}
          >
            Удалить образец
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            Примеры
          </Typography>
          <Typography variant="body2" color="text.secondary" component="div">
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              <li>«Астра, создай чат»</li>
              <li>«Астра, какая погода?» — live-ответ с озвучкой</li>
              <li>Во время ответа можно перебить речь — генерация остановится</li>
              <li>«Астра, выключи помощника»</li>
            </ul>
          </Typography>
        </CardContent>
      </Card>
    </Box>
  );
}
