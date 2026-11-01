import {
  isThinkingCapableModel,
  isValidSelectedModelPath,
  resolveEnableThinkingByMode,
} from '@/utils/modelThinking';

describe('isValidSelectedModelPath', () => {
  it.each([null, undefined, '', '   '])('rejects empty value %p', (value) => {
    expect(isValidSelectedModelPath(value as string | null | undefined)).toBe(false);
  });

  it.each(['llm-svc', 'LLM-SVC', 'llm-svc://', 'local', 'default', ' Default '])(
    'rejects provider placeholder %p',
    (value) => {
      expect(isValidSelectedModelPath(value)).toBe(false);
    },
  );

  it('accepts llm-svc paths with a model id', () => {
    expect(isValidSelectedModelPath('llm-svc://qwen3-8b')).toBe(true);
    expect(isValidSelectedModelPath('llm-svc:///qwen3-8b')).toBe(true);
  });

  it('rejects llm-svc paths with an empty model id', () => {
    expect(isValidSelectedModelPath('llm-svc:///')).toBe(false);
  });

  it('accepts provider/model paths and rejects provider/ without a model', () => {
    expect(isValidSelectedModelPath('openai/gpt-4o')).toBe(true);
    expect(isValidSelectedModelPath('vllm/org/model')).toBe(true);
    expect(isValidSelectedModelPath('openai/')).toBe(false);
    expect(isValidSelectedModelPath('openai/   ')).toBe(false);
  });

  it('accepts local file paths', () => {
    expect(isValidSelectedModelPath('model.gguf')).toBe(true);
    expect(isValidSelectedModelPath('D:\\models\\m.bin')).toBe(true);
    expect(isValidSelectedModelPath('/opt/models/m')).toBe(true);
  });

  it('rejects an arbitrary bare word', () => {
    expect(isValidSelectedModelPath('gpt')).toBe(false);
  });
});

describe('isThinkingCapableModel', () => {
  it.each(['Qwen3-8B', 'deepseek-r1:14b', 'my-Reasoner', 'x-thinking', 'GLM-4.5'])(
    'recognizes %p',
    (name) => {
      expect(isThinkingCapableModel(name)).toBe(true);
    },
  );

  it.each([null, undefined, '', 'llama-3', 'gpt-4o'])('rejects %p', (name) => {
    expect(isThinkingCapableModel(name as string | null | undefined)).toBe(false);
  });
});

describe('resolveEnableThinkingByMode', () => {
  it('forces thinking on', () => {
    expect(resolveEnableThinkingByMode('thinking', 'llama-3')).toBe(true);
  });

  it('forces thinking off even for capable models', () => {
    expect(resolveEnableThinkingByMode('fast', 'qwen3')).toBe(false);
  });

  it('decides by model in auto mode', () => {
    expect(resolveEnableThinkingByMode('auto', 'qwen3')).toBe(true);
    expect(resolveEnableThinkingByMode('auto', 'llama-3')).toBe(false);
    expect(resolveEnableThinkingByMode('auto', null)).toBe(false);
  });
});
