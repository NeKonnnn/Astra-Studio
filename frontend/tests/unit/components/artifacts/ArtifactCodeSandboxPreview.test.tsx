import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import ArtifactCodeSandboxPreview from '@/components/artifacts/ArtifactCodeSandboxPreview';

describe('ArtifactCodeSandboxPreview', () => {
  it('на стриме iframe не монтируется', () => {
    const { container } = render(
      <ArtifactCodeSandboxPreview content={'print(1)'} kind="python" isStreaming />,
    );
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('после стрима JS запускается в iframe', () => {
    const { container } = render(
      <ArtifactCodeSandboxPreview content={'console.log(1)'} kind="javascript" />,
    );
    const iframe = container.querySelector('iframe');
    expect(iframe).toBeInTheDocument();
    expect(iframe).toHaveAttribute('sandbox', 'allow-scripts');
    expect(iframe?.getAttribute('srcdoc') || '').toContain('console.log(1)');
  });

  it('кнопка «Перезапустить» меняет key iframe', () => {
    const { container } = render(
      <ArtifactCodeSandboxPreview content={'.x{color:red}'} kind="css" />,
    );
    const first = container.querySelector('iframe');
    expect(first).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Перезапустить/i }));
    expect(container.querySelector('iframe')).toBeInTheDocument();
  });
});
