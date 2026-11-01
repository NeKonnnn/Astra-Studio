import { prepareSegmentForStt, segmentBlobToWav16k } from '@/utils/dictationAudio';

function blobOf(size: number): Blob {
  const b: any = new Blob([new Uint8Array(size)]);
  b.arrayBuffer = jest.fn(async () => new ArrayBuffer(size));
  return b as Blob;
}

type Ctx = {
  state: string;
  resume: jest.Mock;
  close: jest.Mock;
  decodeAudioData: jest.Mock;
};

function installAudio(opts: {
  state?: string;
  sampleRate: number;
  channels: Float32Array[];
  decodeFails?: boolean;
}): Ctx {
  const ctx: Ctx = {
    state: opts.state ?? 'running',
    resume: jest.fn(async () => {}),
    close: jest.fn(async () => {}),
    decodeAudioData: jest.fn(async () => {
      if (opts.decodeFails) throw new Error('decode');
      return {
        length: opts.channels[0].length,
        numberOfChannels: opts.channels.length,
        sampleRate: opts.sampleRate,
        getChannelData: (i: number) => opts.channels[i],
      };
    }),
  };
  (window as any).AudioContext = jest.fn(() => ctx);
  return ctx;
}

async function readView(b: Blob): Promise<DataView> {
  const buf: ArrayBuffer = await new Promise((res) => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result as ArrayBuffer);
    fr.readAsArrayBuffer(b);
  });
  return new DataView(buf);
}

describe('dictationAudio', () => {
  afterEach(() => {
    delete (window as any).AudioContext;
    delete (window as any).webkitAudioContext;
  });

  it('пустой сегмент -> null', async () => {
    expect(await segmentBlobToWav16k(new Blob([]))).toBeNull();
  });

  it('нет AudioContext -> null', async () => {
    expect(await segmentBlobToWav16k(blobOf(500))).toBeNull();
  });

  it('использует webkitAudioContext как запасной вариант', async () => {
    const ctx = installAudio({ sampleRate: 16000, channels: [new Float32Array(2000)] });
    (window as any).webkitAudioContext = (window as any).AudioContext;
    delete (window as any).AudioContext;
    const wav = await segmentBlobToWav16k(blobOf(500));
    expect(wav).not.toBeNull();
    expect(ctx.close).toHaveBeenCalled();
  });

  it('кодирует 16 кГц моно в WAV PCM16 с клиппингом', async () => {
    const samples = new Float32Array(2000);
    samples[0] = 2;
    samples[1] = -2;
    samples[2] = 0.5;
    const ctx = installAudio({ state: 'suspended', sampleRate: 16000, channels: [samples] });
    const wav = (await segmentBlobToWav16k(blobOf(500)))!;
    expect(ctx.resume).toHaveBeenCalled();
    expect(wav.type).toBe('audio/wav');
    expect(wav.size).toBe(44 + 2000 * 2);
    const v = await readView(wav);
    expect(String.fromCharCode(v.getUint8(0), v.getUint8(1), v.getUint8(2), v.getUint8(3))).toBe('RIFF');
    expect(v.getUint32(24, true)).toBe(16000);
    expect(v.getInt16(44, true)).toBe(0x7fff);
    expect(v.getInt16(46, true)).toBe(-0x8000);
    expect(v.getInt16(48, true)).toBe(Math.trunc(0.5 * 0x7fff));
  });

  it('микширует каналы и ресемплирует 32 кГц -> 16 кГц', async () => {
    const a = new Float32Array(4000).fill(0.5);
    const b = new Float32Array(4000).fill(-0.5);
    installAudio({ sampleRate: 32000, channels: [a, b] });
    const wav = (await segmentBlobToWav16k(blobOf(500)))!;
    expect(wav.size).toBe(44 + 2000 * 2);
    const v = await readView(wav);
    expect(v.getInt16(44, true)).toBe(0);
  });

  it('слишком короткий результат (<1600 семплов) -> null, контекст закрыт', async () => {
    const ctx = installAudio({ sampleRate: 16000, channels: [new Float32Array(1000)] });
    expect(await segmentBlobToWav16k(blobOf(500))).toBeNull();
    expect(ctx.close).toHaveBeenCalled();
  });

  it('ошибка декодирования -> null', async () => {
    const ctx = installAudio({ sampleRate: 16000, channels: [new Float32Array(10)], decodeFails: true });
    expect(await segmentBlobToWav16k(blobOf(500))).toBeNull();
    expect(ctx.close).toHaveBeenCalled();
  });

  it('prepareSegmentForStt: <100 байт -> null', async () => {
    expect(await prepareSegmentForStt(blobOf(50))).toBeNull();
  });

  it('prepareSegmentForStt: возвращает WAV при успехе', async () => {
    installAudio({ sampleRate: 16000, channels: [new Float32Array(2000)] });
    const out = await prepareSegmentForStt(blobOf(500));
    expect(out?.type).toBe('audio/wav');
  });

  it('prepareSegmentForStt: при сбое декодирования отдаёт исходный сегмент', async () => {
    installAudio({ sampleRate: 16000, channels: [new Float32Array(10)], decodeFails: true });
    const seg = blobOf(500);
    expect(await prepareSegmentForStt(seg)).toBe(seg);
  });
});
