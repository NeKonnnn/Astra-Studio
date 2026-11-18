/** Подготовка сегмента MediaRecorder для WhisperX STT. */

export function encodeWavPcm16(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const writeString = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i += 1) {
      view.setUint8(offset + i, value.charCodeAt(i));
    }
  };

  writeString(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i] ?? 0));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += 2;
  }

  return new Blob([buffer], { type: 'audio/wav' });
}

export function resampleLinear(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate || input.length === 0) return input;
  const outLength = Math.max(1, Math.round((input.length * toRate) / fromRate));
  const output = new Float32Array(outLength);
  const ratio = (input.length - 1) / Math.max(1, outLength - 1);
  for (let i = 0; i < outLength; i += 1) {
    const pos = i * ratio;
    const idx = Math.floor(pos);
    const frac = pos - idx;
    const a = input[idx] ?? 0;
    const b = input[Math.min(idx + 1, input.length - 1)] ?? 0;
    output[i] = a + (b - a) * frac;
  }
  return output;
}

export async function segmentBlobToWav16k(segment: Blob): Promise<Blob | null> {
  if (!segment.size) return null;

  const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
  if (!AudioCtx) return null;

  const audioCtx = new AudioCtx();
  try {
    if (audioCtx.state === 'suspended') {
      await audioCtx.resume();
    }
    const buffer = await segment.arrayBuffer();
    const audioBuffer = await audioCtx.decodeAudioData(buffer.slice(0));
    const length = audioBuffer.length;
    const mono = new Float32Array(length);

    for (let ch = 0; ch < audioBuffer.numberOfChannels; ch += 1) {
      const channel = audioBuffer.getChannelData(ch);
      for (let i = 0; i < length; i += 1) {
        mono[i] += (channel[i] ?? 0) / audioBuffer.numberOfChannels;
      }
    }

    const targetRate = 16000;
    const resampled =
      audioBuffer.sampleRate === targetRate
        ? mono
        : resampleLinear(mono, audioBuffer.sampleRate, targetRate);

    if (resampled.length < 1600) return null;
    return encodeWavPcm16(resampled, targetRate);
  } catch {
    return null;
  } finally {
    void audioCtx.close();
  }
}

/** WAV для STT; при ошибке декодирования — исходный webm/opus сегмент. */
export async function prepareSegmentForStt(segment: Blob): Promise<Blob | null> {
  if (segment.size < 100) return null;
  const wav = await segmentBlobToWav16k(segment);
  if (wav && wav.size >= 100) return wav;
  return segment;
}

/** Сырой PCM (float32) → WAV mono 16 kHz для живых чанков STT. */
export function pcmFloat32ToWav16k(samples: Float32Array, fromRate: number): Blob | null {
  if (!samples.length || fromRate <= 0) return null;
  const targetRate = 16000;
  const resampled =
    fromRate === targetRate ? samples : resampleLinear(samples, fromRate, targetRate);
  // меньше ~0.1 c — шум для Whisper
  if (resampled.length < 1600) return null;
  return encodeWavPcm16(resampled, targetRate);
}
