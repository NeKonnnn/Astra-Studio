import React from 'react';
import { render } from '@testing-library/react';
import '@testing-library/jest-dom';
import ThinkingShimmerText from '@/components/chat/ThinkingShimmerText';

describe('ThinkingShimmerText', () => {
  it('рендерит текст дважды: базовый слой и декоративный блик (aria-hidden)', () => {
    const { container } = render(<ThinkingShimmerText>Думает…</ThinkingShimmerText>);
    const root = container.querySelector('.cursor-thinking-shimmer') as HTMLElement;
    expect(root).not.toBeNull();
    const layers = root.children;
    expect(layers).toHaveLength(2);
    expect(layers[0]).toHaveTextContent('Думает…');
    expect(layers[0]).not.toHaveAttribute('aria-hidden');
    expect(layers[1]).toHaveTextContent('Думает…');
    expect(layers[1]).toHaveAttribute('aria-hidden', 'true');
  });

  it('по умолчанию использует светлую тему: базовый цвет — тёмный полупрозрачный', () => {
    const { container } = render(<ThinkingShimmerText>x</ThinkingShimmerText>);
    const base = container.querySelector('.cursor-thinking-shimmer')!.children[0] as HTMLElement;
    expect(getComputedStyle(base).color).toBe('rgba(0, 0, 0, 0.38)');
  });

  it('в тёмной теме базовый цвет — белый полупрозрачный', () => {
    const { container } = render(<ThinkingShimmerText isDarkMode>x</ThinkingShimmerText>);
    const base = container.querySelector('.cursor-thinking-shimmer')!.children[0] as HTMLElement;
    expect(getComputedStyle(base).color).toBe('rgba(255, 255, 255, 0.38)');
  });

  it('применяет fontSize, fontWeight и переопределения sx к корневому элементу', () => {
    const { container } = render(
      <ThinkingShimmerText fontSize="2rem" fontWeight={700} sx={{ marginLeft: '7px' }}>
        x
      </ThinkingShimmerText>,
    );
    const root = container.querySelector('.cursor-thinking-shimmer') as HTMLElement;
    const style = getComputedStyle(root);
    expect(style.fontSize).toBe('2rem');
    expect(style.fontWeight).toBe('700');
    expect(style.marginLeft).toBe('7px');
  });
});
