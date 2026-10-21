/**
 * @jest-environment jsdom
 */
import { resolveProviderOptions } from '@/components/ModelParametersModal';

describe('resolveProviderOptions', () => {
  it('prefers configured providerIds (CORSUR/PHOENIX) over empty models catalog', () => {
    expect(resolveProviderOptions(['CORSUR', 'PHOENIX'], [])).toEqual([
      { value: 'CORSUR', label: 'CORSUR' },
      { value: 'PHOENIX', label: 'PHOENIX' },
    ]);
  });

  it('does not fall back to OpenAI/Local when providerIds are present', () => {
    const opts = resolveProviderOptions(['CORSUR'], []);
    expect(opts.map((o) => o.value)).toEqual(['CORSUR']);
    expect(opts.map((o) => o.value)).not.toContain('OpenAI');
    expect(opts.map((o) => o.value)).not.toContain('Local');
  });

  it('falls back to OpenAI/Local only when neither ids nor models exist', () => {
    expect(resolveProviderOptions([], [])).toEqual([
      { value: 'OpenAI', label: 'OpenAI' },
      { value: 'Local', label: 'Local' },
    ]);
  });

  it('derives providers from models when ids are empty', () => {
    expect(
      resolveProviderOptions([], [
        { provider_id: 'CORSUR', path: 'CORSUR/qwen' },
        { path: 'PHOENIX/other' },
      ]),
    ).toEqual([
      { value: 'CORSUR', label: 'CORSUR' },
      { value: 'PHOENIX', label: 'PHOENIX' },
    ]);
  });
});
