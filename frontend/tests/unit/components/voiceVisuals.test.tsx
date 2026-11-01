import '@testing-library/jest-dom';
import React from 'react';
import { act, render, screen } from '@testing-library/react';
import VoiceVisualization3D from '@/components/VoiceVisualization3D';
import VoiceIndicator from '@/components/VoiceIndicator';

/* ---------- общие моки аудио и rAF ---------- */

let rafQueue: Array<() => void>;
let rafSeq: number;
let cancelledRaf: number[];
let analyserData: number[];
let audioInstances: any[];
let audioThrows: boolean;

class FakeAnalyser {
  fftSize = 0;
  smoothingTimeConstant = 0;
  get frequencyBinCount() {
    return this.fftSize / 2;
  }
  getByteFrequencyData(arr: Uint8Array) {
    arr.set(analyserData.slice(0, arr.length));
  }
}

class FakeAudioContext {
  analyser = new FakeAnalyser();
  closed = false;
  connected = false;
  constructor() {
    if (audioThrows) throw new Error('no audio');
    audioInstances.push(this);
  }
  createMediaStreamSource() {
    return { connect: () => { this.connected = true; } };
  }
  createAnalyser() {
    return this.analyser;
  }
  close() {
    this.closed = true;
  }
}

function tick() {
  const cbs = rafQueue.splice(0);
  act(() => cbs.forEach((cb) => cb()));
}

beforeEach(() => {
  rafQueue = [];
  rafSeq = 0;
  cancelledRaf = [];
  analyserData = [];
  audioInstances = [];
  audioThrows = false;
  jest.spyOn(window, 'requestAnimationFrame').mockImplementation(((cb: () => void) => {
    rafQueue.push(cb);
    return ++rafSeq;
  }) as any);
  jest.spyOn(window, 'cancelAnimationFrame').mockImplementation(((id: number) => {
    cancelledRaf.push(id);
  }) as any);
  (window as any).AudioContext = FakeAudioContext;
});

afterEach(() => {
  jest.restoreAllMocks();
  delete (window as any).AudioContext;
});

/* ---------- three ---------- */

const mockThreeLog: { scenes: any[]; renderers: any[]; meshes: any[]; renders: number; clock: { t: number } } = {
  scenes: [],
  renderers: [],
  meshes: [],
  renders: 0,
  clock: { t: 0 },
};

jest.mock('three', () => {
  class Scene {
    children: any[] = [];
    add(o: any) {
      this.children.push(o);
    }
  }
  class PerspectiveCamera {
    aspect = 0;
    position = { set: jest.fn() };
    lookAt = jest.fn();
    updateProjectionMatrix = jest.fn();
    constructor(_fov: number, aspect: number) {
      this.aspect = aspect;
    }
  }
  class WebGLRenderer {
    domElement = (global as any).document.createElement('canvas');
    setSize = jest.fn();
    setPixelRatio = jest.fn();
    setClearColor = jest.fn();
    dispose = jest.fn();
    forceContextLoss = jest.fn();
    render = jest.fn(() => {
      mockThreeLog.renders += 1;
    });
    constructor(_opts: unknown) {
      mockThreeLog.renderers.push(this);
    }
  }
  class ShaderMaterial {
    params: any;
    constructor(params: any) {
      this.params = params;
    }
  }
  class IcosahedronGeometry {
    constructor(_r: number, _d: number) {}
  }
  class Mesh {
    rotation = { y: 0 };
    geometry: any;
    material: any;
    constructor(geometry: any, material: any) {
      this.geometry = geometry;
      this.material = material;
      mockThreeLog.meshes.push(this);
    }
  }
  class Clock {
    getElapsedTime() {
      return mockThreeLog.clock.t;
    }
  }
  return {
    Scene,
    PerspectiveCamera,
    WebGLRenderer,
    ShaderMaterial,
    IcosahedronGeometry,
    Mesh,
    Clock,
    DoubleSide: 2,
    SRGBColorSpace: 'srgb',
  };
});

const flushImport = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('VoiceVisualization3D', () => {
  const stream = {} as MediaStream;

  beforeEach(() => {
    mockThreeLog.scenes = [];
    mockThreeLog.renderers = [];
    mockThreeLog.meshes = [];
    mockThreeLog.renders = 0;
    mockThreeLog.clock.t = 0;
  });

  it('без потока ничего не рисуется', () => {
    const { container } = render(<VoiceVisualization3D stream={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('с потоком создаёт контейнер, canvas рендерера и запускает анимацию', async () => {
    const { container } = render(<VoiceVisualization3D stream={stream} className="vv" style={{ opacity: 0.5 }} />);
    await flushImport();
    const box = container.firstChild as HTMLElement;
    expect(box).toHaveClass('vv');
    expect(box).toHaveAttribute('aria-hidden', 'true');
    expect(box.style.opacity).toBe('0.5');
    expect(box.style.pointerEvents).toBe('none');
    expect(box.querySelector('canvas')).toBeTruthy();
    const renderer = mockThreeLog.renderers[0];
    expect(renderer.setSize).toHaveBeenCalledWith(360, 360);
    expect(renderer.setClearColor).toHaveBeenCalledWith(0x000000, 0);
    expect(renderer.outputColorSpace).toBe('srgb');
    expect(mockThreeLog.renders).toBe(1);
    expect(audioInstances[0].analyser.fftSize).toBe(256);
    expect(audioInstances[0].connected).toBe(true);
  });

  it('каждый кадр обновляет время, громкость (среднее/255, не более 1) и вращение', async () => {
    render(<VoiceVisualization3D stream={stream} />);
    await flushImport();
    const mesh = mockThreeLog.meshes[0];
    const uniforms = mesh.material.params.uniforms;
    analyserData = new Array(128).fill(255);
    mockThreeLog.clock.t = 2.5;
    tick();
    expect(uniforms.u_time.value).toBe(2.5);
    expect(uniforms.u_frequency.value).toBe(1);
    expect(mesh.rotation.y).toBeCloseTo(0.004, 6);
    analyserData = new Array(128).fill(51);
    tick();
    expect(uniforms.u_frequency.value).toBeCloseTo(0.2, 5);
    expect(mockThreeLog.renders).toBe(3);
  });

  it('ошибка аудио логируется, визуализация продолжает работать без анализа громкости', async () => {
    audioThrows = true;
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    render(<VoiceVisualization3D stream={stream} />);
    await flushImport();
    expect(warn).toHaveBeenCalledWith('VoiceVisualization3D: audio error', expect.any(Error));
    tick();
    expect(mockThreeLog.meshes[0].material.params.uniforms.u_frequency.value).toBe(0);
  });

  it('resize обновляет камеру и рендерер', async () => {
    render(<VoiceVisualization3D stream={stream} />);
    await flushImport();
    const container = document.querySelector('div[aria-hidden="true"]') as HTMLElement;
    Object.defineProperty(container, 'clientWidth', { configurable: true, value: 800 });
    Object.defineProperty(container, 'clientHeight', { configurable: true, value: 400 });
    window.dispatchEvent(new Event('resize'));
    expect(mockThreeLog.renderers[0].setSize).toHaveBeenLastCalledWith(800, 400);
  });

  it('при размонтировании освобождаются аудио, рендерер, canvas и слушатель resize', async () => {
    const { unmount, container } = render(<VoiceVisualization3D stream={stream} />);
    await flushImport();
    const renderer = mockThreeLog.renderers[0];
    const box = container.firstChild as HTMLElement;
    unmount();
    expect(audioInstances[0].closed).toBe(true);
    expect(renderer.dispose).toHaveBeenCalled();
    expect(renderer.forceContextLoss).toHaveBeenCalled();
    expect(cancelledRaf.length).toBeGreaterThan(0);
    expect(box.querySelector('canvas')).toBeNull();
    const before = renderer.setSize.mock.calls.length;
    window.dispatchEvent(new Event('resize'));
    expect(renderer.setSize.mock.calls.length).toBe(before);
    // после очистки кадр не рендерит
    const rendersBefore = mockThreeLog.renders;
    tick();
    expect(mockThreeLog.renders).toBe(rendersBefore);
  });

  it('размонтирование до загрузки three не создаёт сцену', async () => {
    const { unmount } = render(<VoiceVisualization3D stream={stream} />);
    unmount();
    await flushImport();
    expect(mockThreeLog.renderers).toHaveLength(0);
  });

  it('смена потока пересоздаёт визуализацию', async () => {
    const { rerender } = render(<VoiceVisualization3D stream={stream} />);
    await flushImport();
    rerender(<VoiceVisualization3D stream={{} as MediaStream} />);
    await flushImport();
    expect(mockThreeLog.renderers).toHaveLength(2);
    expect(mockThreeLog.renderers[0].dispose).toHaveBeenCalled();
  });
});

describe('VoiceIndicator', () => {
  const stream = {} as MediaStream;
  const bars = (container: HTMLElement) =>
    Array.from(container.querySelectorAll('.MuiBox-root > .MuiBox-root > .MuiBox-root')) as HTMLElement[];

  it('не рисуется, когда запись выключена', () => {
    const { container } = render(<VoiceIndicator isRecording={false} stream={stream} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('при записи без потока показывает 7 пустых столбиков минимальной высоты', () => {
    const { container } = render(<VoiceIndicator isRecording />);
    expect(screen.getByText('Уровень голоса')).toBeInTheDocument();
    const b = bars(container);
    expect(b).toHaveLength(7);
    b.forEach((el) => expect(getComputedStyle(el).height).toBe('4px'));
    expect(audioInstances).toHaveLength(0);
  });

  it('с потоком анализирует спектр: уровни нормируются по максимуму, тихие столбики полупрозрачны', () => {
    // 32 бина, шаг 4: столбики 0..5 по 4 бина, последний — оставшиеся 8
    analyserData = new Array(32).fill(0);
    for (let i = 0; i < 4; i += 1) analyserData[i] = 200; // столбик 0 — максимум
    for (let i = 4; i < 8; i += 1) analyserData[i] = 100; // столбик 1 — 50%
    const { container } = render(<VoiceIndicator isRecording stream={stream} />);
    expect(audioInstances[0].analyser.fftSize).toBe(64);
    expect(audioInstances[0].analyser.smoothingTimeConstant).toBe(0.8);
    const b = bars(container);
    expect(getComputedStyle(b[0]).height).toBe('60px'); // 100% * 0.6
    expect(getComputedStyle(b[1]).height).toBe('30px');
    expect(getComputedStyle(b[2]).height).toBe('4px');
    expect(getComputedStyle(b[0]).opacity).toBe('1');
    expect(getComputedStyle(b[2]).opacity).toBe('0.4');
  });

  it('полная тишина не меняет уровни; последующий кадр запрашивается', () => {
    analyserData = new Array(32).fill(0);
    const { container } = render(<VoiceIndicator isRecording stream={stream} />);
    tick();
    bars(container).forEach((el) => expect(getComputedStyle(el).height).toBe('4px'));
    expect(rafQueue.length).toBe(1);
  });

  it('при остановке записи кадр отменяется, контекст закрывается, столбики сбрасываются', () => {
    analyserData = new Array(32).fill(100);
    const { rerender, container } = render(<VoiceIndicator isRecording stream={stream} />);
    expect(audioInstances[0].closed).toBe(false);
    rerender(<VoiceIndicator isRecording={false} stream={stream} />);
    expect(audioInstances[0].closed).toBe(true);
    expect(cancelledRaf.length).toBeGreaterThan(0);
    expect(container).toBeEmptyDOMElement();
  });

  it('размонтирование закрывает контекст', () => {
    const { unmount } = render(<VoiceIndicator isRecording stream={stream} />);
    unmount();
    expect(audioInstances[0].closed).toBe(true);
  });

  it('ошибка создания AudioContext логируется', () => {
    audioThrows = true;
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    render(<VoiceIndicator isRecording stream={stream} />);
    expect(err).toHaveBeenCalledWith('Ошибка анализа звука:', expect.any(Error));
  });
});
