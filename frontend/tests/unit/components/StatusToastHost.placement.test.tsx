/**
 * @jest-environment node
 */
import fs from 'fs';
import path from 'path';

describe('StatusToastHost placement', () => {
  it('anchors regular status toasts to the top center', () => {
    const src = fs.readFileSync(
      path.join(__dirname, '../../../src/components/StatusToastHost.tsx'),
      'utf8',
    );
    expect(src).toMatch(/top:\s*12/);
    expect(src).toMatch(/left:\s*'50%'/);
    expect(src).toMatch(/transform:\s*'translateX\(-50%\)'/);
    expect(src).not.toMatch(/right:\s*12/);
  });
});
