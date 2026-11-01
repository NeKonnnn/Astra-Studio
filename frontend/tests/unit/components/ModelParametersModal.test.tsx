import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import ModelParametersModal from '@/components/ModelParametersModal';

// Тонкие настройки модели проверяются отдельно; здесь важно, что значения/readOnly/onChange пробрасываются.
jest.mock('@/components/ModelSettingsFields', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="msf" data-readonly={String(!!p.readOnly)} data-dark={String(!!p.darkPanel)}>
      <span data-testid="msf-value">{JSON.stringify(p.value)}</span>
      <button onClick={() => p.onChange({ ...p.value, changed: true })}>msf-change</button>
    </div>
  ),
}));

const onClose = jest.fn();
const onSave = jest.fn();
const onSaveMS = jest.fn();

function setup(props: Partial<React.ComponentProps<typeof ModelParametersModal>> = {}) {
  return render(
    <ModelParametersModal
      open
      onClose={onClose}
      currentModel="local/qwen.gguf"
      availableModels={['local/qwen.gguf', 'local/llama.gguf']}
      onSave={onSave}
      {...props}
    />,
  );
}
const providerInput = () => document.getElementById('model-params-provider') as HTMLInputElement;
const modelInput = () => document.getElementById('model-params-model') as HTMLInputElement;
const popover = () => document.querySelector('.MuiPopover-root') as HTMLElement;

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

describe('ModelParametersModal: общая часть', () => {
  it('не рендерит содержимое закрытого диалога', () => {
    setup({ open: false });
    expect(screen.queryByText('Параметры модели')).toBeNull();
  });

  it('показывает текущую модель и значения по умолчанию в legacy-блоке', () => {
    setup();
    expect(screen.getByText('Параметры модели')).toBeInTheDocument();
    expect(modelInput().value).toBe('qwen.gguf'); // последний сегмент пути
    expect(screen.getByText('Температура — 1.00')).toBeInTheDocument();
    expect(screen.getByText('Top P — 1.00')).toBeInTheDocument();
    expect(screen.getByText('Штраф за частоту — 0.00')).toBeInTheDocument();
    expect(screen.getByText('Штраф за присутствие — 0.00')).toBeInTheDocument();
    expect(screen.queryByTestId('msf')).toBeNull();
  });

  it('initialParams переопределяют значения', () => {
    setup({ initialParams: { temperature: 0.5, topP: 0.25, contextTokens: '8192', provider: 'OpenAI' } });
    expect(screen.getByText('Температура — 0.50')).toBeInTheDocument();
    expect(screen.getByText('Top P — 0.25')).toBeInTheDocument();
    expect(providerInput().value).toBe('OpenAI');
    expect(screen.getByDisplayValue('8192')).toBeInTheDocument();
  });

  it('model берётся из initialParams, если currentModel пуст', () => {
    setup({ currentModel: '', initialParams: { model: 'm/p.gguf' } });
    expect(modelInput().value).toBe('p.gguf');
  });

  it('без модели поле модели пустое', () => {
    setup({ currentModel: '' });
    expect(modelInput().value).toBe('');
  });

  it('кнопка «назад» вызывает onClose', () => {
    setup();
    fireEvent.click(document.querySelector('[data-testid="ArrowBackIcon"]')!.closest('button')!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Escape закрывает диалог через onClose', () => {
    setup();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('смена цвета панели (событие sidebarColorChanged) не ломает рендер', () => {
    setup();
    act(() => {
      window.dispatchEvent(new Event('sidebarColorChanged'));
    });
    expect(screen.getByText('Параметры модели')).toBeInTheDocument();
  });

  it('variant="panel" рендерится без Dialog', () => {
    setup({ variant: 'panel' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByText('Параметры модели')).toBeInTheDocument();
  });
});

describe('ModelParametersModal: выбор провайдера и модели (без каталога)', () => {
  it('выпадающий список провайдеров по умолчанию: OpenAI и Local; выбор записывается в поле', async () => {
    setup();
    fireEvent.click(providerInput());
    const pop = popover();
    expect(within(pop).getByText('OpenAI')).toBeInTheDocument();
    fireEvent.click(within(pop).getByText('Local'));
    expect(providerInput().value).toBe('Local');
  });

  it('список моделей берётся из availableModels и выбранная модель попадает в onSave', () => {
    setup();
    fireEvent.click(modelInput());
    fireEvent.click(within(popover()).getByText('llama.gguf'));
    expect(modelInput().value).toBe('llama.gguf');
    fireEvent.click(screen.getByRole('button', { name: /Сохранить настройки/ }));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toBe('local/llama.gguf');
    expect(onSave.mock.calls[0][1]).toMatchObject({ model: 'local/llama.gguf', temperature: 1, topP: 1 });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('пустой список моделей: «Нет доступных моделей»', () => {
    setup({ availableModels: [] });
    fireEvent.click(modelInput());
    expect(within(popover()).getByText('Нет доступных моделей')).toBeInTheDocument();
  });

  it('закрытие выпадающих списков по Escape', async () => {
    setup();
    fireEvent.click(providerInput());
    fireEvent.keyDown(popover(), { key: 'Escape' });
    await waitFor(() => expect(document.querySelector('.MuiPopover-root')).toBeNull());
    fireEvent.click(modelInput());
    fireEvent.keyDown(popover(), { key: 'Escape' });
    await waitFor(() => expect(document.querySelector('.MuiPopover-root')).toBeNull());
  });
});

describe('ModelParametersModal: каталог провайдеров', () => {
  const catalog = [
    { name: 'a.gguf', path: 'CORSUR/a.gguf', display_name: 'Модель А' },
    { name: 'b.gguf', path: 'CORSUR/b.gguf' },
    { name: 'c.gguf', path: 'llm-svc://PHOENIX/c.gguf', provider_id: 'PHOENIX' },
    { name: 'sc', path: 'SC/x', provider_id: 'SC' },
    { name: 'noprov', path: 'plain.gguf' },
  ];

  it('провайдеры выводятся из каталога (без SC), провайдер выводится из пути текущей модели', () => {
    setup({ providerModels: catalog, currentModel: 'CORSUR/a.gguf' });
    expect(providerInput().value).toBe('CORSUR');
    expect(modelInput().value).toBe('Модель А');
    fireEvent.click(providerInput());
    const pop = popover();
    expect(within(pop).getByText('CORSUR')).toBeInTheDocument();
    expect(within(pop).getByText('PHOENIX')).toBeInTheDocument();
    expect(within(pop).queryByText('SC')).toBeNull();
  });

  it('модели фильтруются по провайдеру; имя без .gguf берётся из name', () => {
    setup({ providerModels: catalog, currentModel: 'CORSUR/a.gguf' });
    fireEvent.click(modelInput());
    const pop = popover();
    expect(within(pop).getByText('Модель А')).toBeInTheDocument();
    expect(within(pop).getByText('b')).toBeInTheDocument();
    expect(within(pop).queryByText('c')).toBeNull();
  });

  it('смена провайдера сбрасывает модель, если она принадлежит другому провайдеру', () => {
    setup({ providerModels: catalog, currentModel: 'CORSUR/a.gguf' });
    fireEvent.click(providerInput());
    fireEvent.click(within(popover()).getByText('PHOENIX'));
    expect(providerInput().value).toBe('PHOENIX');
    expect(modelInput().value).toBe('');
    fireEvent.click(screen.getByRole('button', { name: /Сохранить настройки/ }));
    expect(onSave.mock.calls[0][0]).toBe('');
    expect(onSave.mock.calls[0][1]).toMatchObject({ provider: 'PHOENIX', model: '' });
  });

  it('повторный выбор того же провайдера модель сохраняет', () => {
    setup({ providerModels: catalog, currentModel: 'CORSUR/a.gguf' });
    fireEvent.click(providerInput());
    fireEvent.click(within(popover()).getByText('CORSUR'));
    expect(modelInput().value).toBe('Модель А');
  });

  it('providerIds задаёт список и порядок провайдеров', () => {
    setup({ providerModels: catalog, providerIds: ['PHOENIX', 'CORSUR', 'SC'], currentModel: '' });
    fireEvent.click(providerInput());
    const labels = Array.from(popover().querySelectorAll('.MuiBox-root .MuiBox-root')).map((n) => n.textContent);
    expect(labels).toEqual(['PHOENIX', 'CORSUR']);
  });

  it('провайдер SC (служебный) из initialParams не показывается в поле', () => {
    setup({ providerModels: catalog, currentModel: '', initialParams: { provider: 'sc' } });
    expect(providerInput().value).toBe('');
  });

  it('провайдер без совпадающих моделей и пустой список провайдеров', () => {
    setup({ providerModels: [{ name: 'q', path: 'q' }], currentModel: '' });
    fireEvent.click(providerInput());
    expect(within(popover()).getByText('Нет доступных провайдеров')).toBeInTheDocument();
  });

  it('модель, отсутствующая в каталоге, показывается последним сегментом пути', () => {
    setup({ providerModels: catalog, currentModel: 'llm-svc://CORSUR/zzz.gguf' });
    expect(modelInput().value).toBe('zzz.gguf');
  });

  it('пустой путь модели в каталоге без совпадений даёт «—» только для пустого значения (поле остаётся пустым)', () => {
    setup({ providerModels: catalog, currentModel: '' });
    expect(modelInput().value).toBe('');
  });
});

describe('ModelParametersModal: legacy-параметры', () => {
  it('поля токенов редактируются и уходят в onSave', () => {
    setup();
    const [ctx, out] = [screen.getAllByDisplayValue('Системная')[0], screen.getAllByDisplayValue('Системная')[1]];
    fireEvent.change(ctx, { target: { value: '4096' } });
    fireEvent.change(out, { target: { value: '512' } });
    fireEvent.click(screen.getByRole('button', { name: /Сохранить настройки/ }));
    expect(onSave.mock.calls[0][1]).toMatchObject({ contextTokens: '4096', outputTokens: '512' });
  });

  it('ползунки меняют значения температуры, top_p и штрафов', () => {
    setup();
    const sliders = Array.from(document.querySelectorAll('input[type="range"]')) as HTMLInputElement[];
    expect(sliders).toHaveLength(4);
    fireEvent.change(sliders[0], { target: { value: '1.5' } });
    fireEvent.change(sliders[1], { target: { value: '0.5' } });
    fireEvent.change(sliders[2], { target: { value: '0.7' } });
    fireEvent.change(sliders[3], { target: { value: '1.2' } });
    expect(screen.getByText('Температура — 1.50')).toBeInTheDocument();
    expect(screen.getByText('Top P — 0.50')).toBeInTheDocument();
    expect(screen.getByText('Штраф за частоту — 0.70')).toBeInTheDocument();
    expect(screen.getByText('Штраф за присутствие — 1.20')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Сохранить настройки/ }));
    expect(onSave.mock.calls[0][1]).toMatchObject({
      temperature: 1.5,
      topP: 0.5,
      frequencyPenalty: 0.7,
      presencePenalty: 1.2,
    });
  });

  it('«Восстановить настройки» возвращает значения по умолчанию, сохраняя выбранную модель', () => {
    setup({ initialParams: { temperature: 0.2, contextTokens: '1' } });
    fireEvent.click(screen.getByRole('button', { name: /Восстановить настройки/ }));
    expect(screen.getByText('Температура — 1.00')).toBeInTheDocument();
    expect(modelInput().value).toBe('qwen.gguf');
    expect(screen.getAllByDisplayValue('Системная')).toHaveLength(2);
  });
});

describe('ModelParametersModal: тонкая настройка модели', () => {
  const ms = { temperature: 0.3 } as any;

  it('при заданных initialModelSettings legacy-блок скрыт, показан ModelSettingsFields', () => {
    setup({ initialModelSettings: ms, onSaveModelSettings: onSaveMS });
    expect(screen.getByTestId('msf')).toBeInTheDocument();
    expect(screen.queryByText(/Температура —/)).toBeNull();
    expect(screen.getByTestId('msf-value').textContent).toContain('0.3');
  });

  it('сохранение вызывает onSave и onSaveModelSettings с изменённым состоянием', () => {
    setup({ initialModelSettings: ms, onSaveModelSettings: onSaveMS });
    fireEvent.click(screen.getByText('msf-change'));
    fireEvent.click(screen.getByRole('button', { name: /Сохранить настройки/ }));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSaveMS).toHaveBeenCalledWith(expect.objectContaining({ temperature: 0.3, changed: true }));
    expect(onClose).toHaveBeenCalled();
  });

  it('сохранение без onSaveModelSettings не падает', () => {
    setup({ initialModelSettings: ms });
    fireEvent.click(screen.getByRole('button', { name: /Сохранить настройки/ }));
    expect(onSave).toHaveBeenCalled();
  });

  it('«Восстановить» сбрасывает тонкие настройки к значениям по умолчанию', () => {
    setup({ initialModelSettings: ms, onSaveModelSettings: onSaveMS });
    fireEvent.click(screen.getByRole('button', { name: /Восстановить настройки/ }));
    expect(screen.getByTestId('msf-value').textContent).not.toContain('0.3');
  });
});

describe('ModelParametersModal: режим только просмотра', () => {
  it('нет кнопок сохранения/сброса, есть пояснение, выпадающие списки не открываются, поля заблокированы', () => {
    setup({ readOnly: true });
    expect(screen.queryByRole('button', { name: /Сохранить настройки/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Восстановить настройки/ })).toBeNull();
    expect(screen.getByText(/Параметры модели задаёт владелец/)).toBeInTheDocument();
    fireEvent.click(providerInput());
    fireEvent.click(modelInput());
    expect(document.querySelector('.MuiPopover-root')).toBeNull();
    screen.getAllByDisplayValue('Системная').forEach((i) => expect(i).toBeDisabled());
    document.querySelectorAll('input[type="range"]').forEach((i) => expect(i).toBeDisabled());
  });

  it('readOnly пробрасывается в ModelSettingsFields', () => {
    setup({ readOnly: true, initialModelSettings: {} as any });
    expect(screen.getByTestId('msf')).toHaveAttribute('data-readonly', 'true');
  });
});

describe('ModelParametersModal: handleSave в readOnly', () => {
  it('в panel-варианте клик «Назад» закрывает панель', () => {
    setup({ variant: 'panel', readOnly: true });
    fireEvent.click(document.querySelector('[data-testid="ArrowBackIcon"]')!.closest('button')!);
    expect(onClose).toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });
});
