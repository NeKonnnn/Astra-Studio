import { render } from '@testing-library/react';
import SplitArrowIcon from '@/icons/SplitArrowIcon';

describe('SplitArrowIcon', () => {
  it('рисует две стрелки (два path) в viewBox 24', () => {
    const { container } = render(<SplitArrowIcon />);
    const svg = container.querySelector('svg')!;
    expect(svg.getAttribute('viewBox')).toBe('0 0 24 24');
    expect(svg.querySelectorAll('path')).toHaveLength(2);
  });

  it('size задаёт ширину и высоту, sx массивом и объектом объединяется', () => {
    const { container, rerender } = render(<SplitArrowIcon size={40} sx={{ color: 'red' }} />);
    let svg = container.querySelector('svg')!;
    const style = getComputedStyle(svg);
    expect(style.width).toBe('40px');
    expect(style.height).toBe('40px');
    rerender(<SplitArrowIcon size="2em" sx={[{ opacity: 0.5 }]} data-testid="x" />);
    svg = container.querySelector('svg')!;
    expect(getComputedStyle(svg).width).toBe('2em');
    expect(svg.getAttribute('data-testid')).toBe('x');
  });
});
