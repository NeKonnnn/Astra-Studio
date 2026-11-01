import '@testing-library/jest-dom';
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import ModelSettingsFields from '@/components/ModelSettingsFields';
import { MODEL_SETTINGS_DEFAULT } from '@/constants/modelSettingsStyles';

const LABELS = [
  'Размер контекста',
  'Лимит токенов ответа',
  'Температура',
  'Top-p',
  'Штраф за повторы',
  'Top-k',
  'Min-p',
  'Штраф за частоту',
  'Штраф за присутствие',
];

const labelFor = (label: string) => screen.getAllByText(label).find((el) => el.closest('label')) as HTMLElement;
const inputFor = (label: string) =>
  labelFor(label).closest('.MuiFormControl-root')!.querySelector('input') as HTMLInputElement;

function setup(over: Record<string, unknown> = {}) {
  const onChange = jest.fn();
  const value = { ...MODEL_SETTINGS_DEFAULT };
  const utils = render(<ModelSettingsFields value={value} onChange={onChange} {...(over as any)} />);
  return { onChange, value, ...utils };
}

describe('ModelSettingsFields', () => {
  it('аккордеон «Тонкая настройка» по умолчанию содержит все поля и два переключателя', () => {
    setup();
    expect(screen.getByText('Тонкая настройка')).toBeInTheDocument();
    for (const l of LABELS) expect(labelFor(l)).toBeInTheDocument();
    expect(screen.getByText('Использовать GPU')).toBeInTheDocument();
    expect(screen.getByText('Потоковая генерация')).toBeInTheDocument();
    expect(screen.getAllByRole('switch', { hidden: true })).toHaveLength(2);
  });

  it('accordion=false выводит поля без заголовка', () => {
    setup({ accordion: false, darkPanel: true, compact: true });
    expect(screen.queryByText('Тонкая настройка')).toBeNull();
    expect(labelFor('Температура')).toBeInTheDocument();
  });

  it('значения полей отражают состояние', () => {
    setup({ accordion: false });
    expect(inputFor('Размер контекста').value).toBe('100000');
    expect(inputFor('Температура').value).toBe('0.7');
    expect(inputFor('Top-k').value).toBe('40');
  });

  it('изменение поля при blur отдаёт новое состояние с остальными полями без изменений', () => {
    const { onChange, value } = setup({ accordion: false });
    const input = inputFor('Температура');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: '1.2' } });
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledWith({ ...value, temperature: 1.2 });
  });

  it('значение выше максимума ограничивается; целочисленные поля округляются; минимум применяется', () => {
    const { onChange, value } = setup({ accordion: false, maxValues: { top_k: 100, temperature: 1 } });
    const topK = inputFor('Top-k');
    fireEvent.change(topK, { target: { value: '500' } });
    fireEvent.blur(topK);
    expect(onChange).toHaveBeenLastCalledWith({ ...value, top_k: 100 });
    const temp = inputFor('Температура');
    fireEvent.change(temp, { target: { value: '5' } });
    fireEvent.blur(temp);
    expect(onChange).toHaveBeenLastCalledWith({ ...value, temperature: 1 });
    const repeat = inputFor('Штраф за повторы');
    fireEvent.change(repeat, { target: { value: '0.2' } });
    fireEvent.blur(repeat);
    expect(onChange).toHaveBeenLastCalledWith({ ...value, repeat_penalty: 1 });
    const ctx = inputFor('Размер контекста');
    fireEvent.change(ctx, { target: { value: '100' } });
    fireEvent.blur(ctx);
    expect(onChange).toHaveBeenLastCalledWith({ ...value, context_size: 512 });
  });

  it.each([
    ['Размер контекста', '2048', 'context_size', 2048],
    ['Лимит токенов ответа', '1024', 'output_tokens', 1024],
    ['Температура', '0.9', 'temperature', 0.9],
    ['Top-p', '0.5', 'top_p', 0.5],
    ['Штраф за повторы', '1.3', 'repeat_penalty', 1.3],
    ['Top-k', '64', 'top_k', 64],
    ['Min-p', '0.2', 'min_p', 0.2],
    ['Штраф за частоту', '0.5', 'frequency_penalty', 0.5],
    ['Штраф за присутствие', '0.7', 'presence_penalty', 0.7],
  ])('поле «%s» меняет только ключ %s', (label, raw, key, expected) => {
    const { onChange, value } = setup({ accordion: false });
    const input = inputFor(label as string);
    fireEvent.change(input, { target: { value: raw } });
    fireEvent.blur(input);
    expect(onChange).toHaveBeenLastCalledWith({ ...value, [key as string]: expected });
  });

  it('пустой ввод заменяется заводским значением из defaults', () => {
    const { onChange, value } = setup({ accordion: false, defaults: { top_p: 0.8 } });
    const input = inputFor('Top-p');
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.blur(input);
    expect(onChange).toHaveBeenLastCalledWith({ ...value, top_p: 0.8 });
  });

  it('переключатели меняют use_gpu и streaming', () => {
    const { onChange, value } = setup({ accordion: false });
    const [gpu, streaming] = screen.getAllByRole('switch');
    fireEvent.click(gpu);
    expect(onChange).toHaveBeenLastCalledWith({ ...value, use_gpu: true });
    fireEvent.click(streaming);
    expect(onChange).toHaveBeenLastCalledWith({ ...value, streaming: false });
  });

  it('readOnly блокирует поля и переключатели, onChange не вызывается', () => {
    const { onChange } = setup({ accordion: false, readOnly: true });
    screen.getAllByRole('switch').forEach((s) => expect(s).toBeDisabled());
    const input = inputFor('Температура');
    expect(input).toBeDisabled();
    fireEvent.blur(input);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('справочные подсказки доступны у каждого поля', () => {
    setup({ accordion: false });
    expect(document.querySelectorAll('label [data-testid="HelpOutlineIcon"]')).toHaveLength(LABELS.length);
    expect(document.querySelectorAll('[data-testid="HelpOutlineIcon"]').length).toBeGreaterThanOrEqual(LABELS.length + 2);
  });

  it('клик по иконке справки не выбирает поле и не меняет значение', () => {
    const { onChange } = setup({ accordion: false });
    const help = labelFor('Температура').querySelector('button')!;
    fireEvent.click(help);
    expect(onChange).not.toHaveBeenCalled();
    expect(document.activeElement).not.toBe(inputFor('Температура'));
  });
});
