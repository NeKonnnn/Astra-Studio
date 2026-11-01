import '@testing-library/jest-dom';
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import AgentLimitField from '@/agents/AgentLimitField';
import MaxAgentStepsField from '@/agents/MaxAgentStepsField';
import AgentChainSubagentsSettings from '@/agents/AgentChainSubagentsSettings';

const chrome = { fgSubtle: '#999', fgMuted: '#777' };

describe('AgentLimitField', () => {
  const setup = (over: Record<string, unknown> = {}) => {
    const onChange = jest.fn();
    render(
      <AgentLimitField
        label="Лимит"
        tooltip="подсказка"
        value={3}
        onChange={onChange}
        defaultLimit={10}
        maxLimit={20}
        panelChrome={chrome}
        {...(over as any)}
      />,
    );
    return { onChange, input: screen.getByLabelText('Лимит') as HTMLInputElement };
  };

  it('показывает значение, подсказку по умолчанию и атрибуты min/max', () => {
    const { input } = setup();
    expect(input.value).toBe('3');
    expect(input).toHaveAttribute('placeholder', 'По умолчанию: 10');
    expect(input).toHaveAttribute('min', '1');
    expect(input).toHaveAttribute('max', '20');
    expect(screen.getByLabelText('Справка: Лимит')).toBeInTheDocument();
  });

  it('пустая строка даёт пустое значение, число усекается и ограничивается диапазоном', () => {
    const { onChange, input } = setup();
    fireEvent.change(input, { target: { value: '' } });
    expect(onChange).toHaveBeenLastCalledWith('');
    fireEvent.change(input, { target: { value: '7.9' } });
    expect(onChange).toHaveBeenLastCalledWith(7);
    fireEvent.change(input, { target: { value: '500' } });
    expect(onChange).toHaveBeenLastCalledWith(20);
    fireEvent.change(input, { target: { value: '-5' } });
    expect(onChange).toHaveBeenLastCalledWith(1);
  });

  it('readOnly отключает поле', () => {
    const { input } = setup({ readOnly: true });
    expect(input).toBeDisabled();
  });
});

describe('MaxAgentStepsField', () => {
  const setup = (over: Record<string, unknown> = {}) => {
    const onChange = jest.fn();
    render(
      <MaxAgentStepsField
        value=""
        onChange={onChange}
        defaultSteps={50}
        maxSteps={500}
        panelChrome={chrome}
        {...(over as any)}
      />,
    );
    return { onChange, input: screen.getByLabelText('Максимальное количество шагов агента') as HTMLInputElement };
  };

  it('плейсхолдер и ограничения', () => {
    const { input } = setup();
    expect(input).toHaveAttribute('placeholder', 'По умолчанию: 50');
    expect(input).toHaveAttribute('max', '500');
    expect(screen.getByLabelText('Справка: максимальное количество шагов агента')).toBeInTheDocument();
  });

  it('ввод ограничивается, пустое значение сбрасывает', () => {
    const { onChange, input } = setup({ value: 5 });
    fireEvent.change(input, { target: { value: '9999' } });
    expect(onChange).toHaveBeenLastCalledWith(500);
    fireEvent.change(input, { target: { value: '0' } });
    expect(onChange).toHaveBeenLastCalledWith(1);
    fireEvent.change(input, { target: { value: '12' } });
    expect(onChange).toHaveBeenLastCalledWith(12);
    fireEvent.change(input, { target: { value: '' } });
    expect(onChange).toHaveBeenLastCalledWith('');
  });

  it('readOnly отключает поле', () => {
    const { input } = setup({ readOnly: true });
    expect(input).toBeDisabled();
  });
});

describe('AgentChainSubagentsSettings', () => {
  const setup = (over: Record<string, unknown> = {}) => {
    const handlers = {
      onRecursionLimitChange: jest.fn(),
      onHideSequentialOutputsChange: jest.fn(),
      onSharedChainRagChange: jest.fn(),
      onUseParentSharedRagChange: jest.fn(),
    };
    render(
      <AgentChainSubagentsSettings
        recursionLimit={20}
        defaultGraphSteps={50}
        maxRecursionLimit={500}
        hideSequentialOutputs={false}
        sharedChainRag
        useParentSharedRag={false}
        panelChrome={chrome}
        {...handlers}
        {...(over as any)}
      />,
    );
    return handlers;
  };

  it('три переключателя вызывают свои обработчики с новым значением', () => {
    const h = setup();
    const switches = screen.getAllByRole('switch');
    expect(switches).toHaveLength(3);
    expect((switches[0] as HTMLInputElement).checked).toBe(false);
    expect((switches[1] as HTMLInputElement).checked).toBe(true);
    fireEvent.click(switches[0]);
    expect(h.onHideSequentialOutputsChange).toHaveBeenCalledWith(true);
    fireEvent.click(switches[1]);
    expect(h.onSharedChainRagChange).toHaveBeenCalledWith(false);
    fireEvent.click(switches[2]);
    expect(h.onUseParentSharedRagChange).toHaveBeenCalledWith(true);
  });

  it('поле шагов передаёт изменение наверх; readOnly блокирует всё', () => {
    const h = setup({ readOnly: true });
    const input = screen.getByLabelText('Максимальное количество шагов агента');
    expect(input).toBeDisabled();
    screen.getAllByRole('switch').forEach((c) => expect(c).toBeDisabled());
    expect(h.onRecursionLimitChange).not.toHaveBeenCalled();
  });

  it('изменение лимита шагов вызывает onRecursionLimitChange', () => {
    const h = setup();
    fireEvent.change(screen.getByLabelText('Максимальное количество шагов агента'), { target: { value: '30' } });
    expect(h.onRecursionLimitChange).toHaveBeenCalledWith(30);
  });
});
