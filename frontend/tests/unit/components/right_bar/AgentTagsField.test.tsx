import React from 'react';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import AgentTagsField, { normalizeAgentTags, useAgentTags, AgentTagValue } from '@/components/right_bar/AgentTagsField';

const mockFetchAgentTags = jest.fn();
let cacheListener: (() => void) | null = null;
const mockUnsubscribe = jest.fn();

jest.mock('@/constants/AgentTags', () => ({
  fetchAgentTags: (...args: unknown[]) => mockFetchAgentTags(...args),
  subscribeAgentTagsCache: (cb: () => void) => {
    cacheListener = cb;
    return mockUnsubscribe;
  },
}));

const CATALOG = [
  { id: 1, name: 'ВНД' },
  { id: 2, name: 'Презентации' },
];

beforeEach(() => {
  mockFetchAgentTags.mockReset();
  mockFetchAgentTags.mockResolvedValue(CATALOG);
  mockUnsubscribe.mockReset();
  cacheListener = null;
});

describe('normalizeAgentTags', () => {
  it('не массив -> пустой список', () => {
    expect(normalizeAgentTags(null)).toEqual([]);
    expect(normalizeAgentTags('x')).toEqual([]);
    expect(normalizeAgentTags({})).toEqual([]);
  });

  it('приводит строки и объекты, обрезает пробелы, отбрасывает пустые и дубли без учёта регистра', () => {
    const out = normalizeAgentTags([
      ' Alpha ',
      { id: 5, name: 'Beta' },
      'alpha',
      { name: '  ' },
      { id: '7', name: 'Gamma' },
      { id: -1, name: 'Delta' },
      { id: 'abc', name: 'Eps' },
      { id: 3.9, name: 'Zeta' },
      null,
      42,
      { id: 9 },
    ]);
    expect(out).toEqual([
      { name: 'Alpha' },
      { id: 5, name: 'Beta' },
      { id: 7, name: 'Gamma' },
      { name: 'Delta' },
      { name: 'Eps' },
      { id: 3, name: 'Zeta' },
    ]);
  });
});

describe('useAgentTags', () => {
  it('загружает справочник и отписывается при размонтировании', async () => {
    const { result, unmount } = renderHook(() => useAgentTags());
    await waitFor(() => expect(result.current).toEqual(CATALOG));
    expect(mockFetchAgentTags).toHaveBeenCalledWith();
    unmount();
    expect(mockUnsubscribe).toHaveBeenCalled();
  });

  it('при сбросе кэша перезапрашивает справочник с force=true', async () => {
    const { result } = renderHook(() => useAgentTags());
    await waitFor(() => expect(result.current).toHaveLength(2));
    mockFetchAgentTags.mockResolvedValue([...CATALOG, { id: 3, name: 'Новый' }]);
    await act(async () => {
      cacheListener!();
    });
    await waitFor(() => expect(result.current).toHaveLength(3));
    expect(mockFetchAgentTags).toHaveBeenLastCalledWith(true);
  });

  it('ответ, пришедший после размонтирования, игнорируется', async () => {
    let resolve!: (v: unknown) => void;
    mockFetchAgentTags.mockReturnValue(new Promise((r) => { resolve = r; }));
    const { result, unmount } = renderHook(() => useAgentTags());
    unmount();
    await act(async () => {
      resolve(CATALOG);
    });
    expect(result.current).toEqual([]);
  });
});

const EMPTY: AgentTagValue[] = [];

function setup(props: Partial<React.ComponentProps<typeof AgentTagsField>> = {}) {
  const onChange = jest.fn();
  const utils = render(
    <AgentTagsField value={EMPTY} onChange={onChange} label="Теги" {...props} />,
  );
  const input = () => screen.getByRole('combobox') as HTMLInputElement;
  return { onChange, input, ...utils };
}

describe('AgentTagsField', () => {
  it('Enter по набранному слову создаёт новый тег без id', async () => {
    const { onChange, input } = setup();
    await waitFor(() => expect(mockFetchAgentTags).toHaveBeenCalled());
    fireEvent.change(input(), { target: { value: 'Новый' } });
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith([{ name: 'Новый' }]);
  });

  it('набранное имя существующего тега (в другом регистре) берёт id из справочника', async () => {
    const { onChange, input } = setup();
    await waitFor(() => expect(mockFetchAgentTags).toHaveBeenCalled());
    await act(async () => {});
    fireEvent.change(input(), { target: { value: 'вНд' } });
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith([{ id: 1, name: 'ВНД' }]);
  });

  it('запятая завершает тег: «ВНД, Презентации,» даёт два чипа', async () => {
    const { onChange, input } = setup();
    await act(async () => {});
    fireEvent.change(input(), { target: { value: 'ВНД, Презентации,' } });
    expect(onChange).toHaveBeenCalledWith([
      { id: 1, name: 'ВНД' },
      { id: 2, name: 'Презентации' },
    ]);
    // хвост после последней запятой остаётся в поле ввода
    expect(input().value).toBe('');
  });

  it('текст после запятой остаётся в поле без ведущих пробелов; пустые части отбрасываются', async () => {
    const { onChange, input } = setup();
    fireEvent.change(input(), { target: { value: ' , ,  хвост' } });
    // fresh пуст -> onChange не вызывается
    expect(onChange).not.toHaveBeenCalled();
    expect(input().value).toBe('хвост');
  });

  it('запятая добавляет к уже выбранным тегам и не плодит дубликаты', async () => {
    const { onChange, input } = setup({ value: [{ name: 'Один' }] });
    fireEvent.change(input(), { target: { value: 'один,Два,' } });
    expect(onChange).toHaveBeenCalledWith([{ name: 'Один' }, { name: 'Два' }]);
  });

  it('при allowCreate=false запятая не режет ввод, а лишних тегов нет', async () => {
    const { onChange, input } = setup({ allowCreate: false });
    await act(async () => {});
    fireEvent.focus(input());
    fireEvent.change(input(), { target: { value: 'а,б' } });
    expect(onChange).not.toHaveBeenCalled();
    expect(input().value).toBe('а,б');
  });

  it('выбор из выпадающего списка добавляет тег с id', async () => {
    const { onChange, input } = setup();
    await act(async () => {});
    fireEvent.mouseDown(input());
    fireEvent.click(await screen.findByText('Презентации'));
    expect(onChange).toHaveBeenCalledWith([{ id: 2, name: 'Презентации' }]);
  });

  it('уже выбранные теги скрыты в списке (filterSelectedOptions)', async () => {
    const { input } = setup({ value: [{ id: 1, name: 'ВНД' }] });
    await act(async () => {});
    fireEvent.mouseDown(input());
    expect(await screen.findByText('Презентации')).toBeTruthy();
    // ВНД присутствует только как чип, а не как опция списка
    expect(screen.getAllByText('ВНД')).toHaveLength(1);
  });

  it('удаление чипа вызывает onChange без него', async () => {
    const value: AgentTagValue[] = [{ id: 1, name: 'ВНД' }, { name: 'Свой' }];
    const { onChange, container } = setup({ value });
    const del = container.querySelectorAll('.MuiChip-deleteIcon');
    expect(del).toHaveLength(2);
    fireEvent.click(del[0]);
    expect(onChange).toHaveBeenCalledWith([{ name: 'Свой' }]);
  });

  it('allowCreate=false и пустой справочник: подсказка «Тегов пока нет»', async () => {
    mockFetchAgentTags.mockResolvedValue([]);
    const { input } = setup({ allowCreate: false });
    await act(async () => {});
    fireEvent.mouseDown(input());
    expect(await screen.findByText(/Тегов пока нет/)).toBeTruthy();
  });

  it('allowCreate=false и непустой справочник: ничего не найдено -> «Нет тегов»', async () => {
    const { input } = setup({ allowCreate: false });
    await act(async () => {});
    fireEvent.focus(input());
    fireEvent.mouseDown(input());
    fireEvent.change(input(), { target: { value: 'нет_такого' } });
    expect(await screen.findByText('Нет тегов')).toBeTruthy();
  });

  it('placeholder показывается только пока нет значений', () => {
    const a = setup({ placeholder: 'Введите тег' });
    expect(a.input().getAttribute('placeholder')).toBe('Введите тег');
    a.unmount();
    const b = setup({ placeholder: 'Введите тег', value: [{ name: 'x' }] });
    expect(b.input().getAttribute('placeholder')).toBeNull();
  });

  it('readOnly отключает поле', () => {
    const { input } = setup({ readOnly: true });
    expect(input().disabled).toBe(true);
  });

  it('required помечает поле звёздочкой', () => {
    setup({ required: true });
    expect(document.querySelector('.MuiFormLabel-asterisk')).not.toBeNull();
  });

  it('help рисует кнопку справки с aria-label', () => {
    setup({ help: 'Подсказка', helpTitle: 'Заголовок' });
    expect(screen.getByRole('button', { name: 'Справка: Теги' })).toBeTruthy();
  });

  it('без help кнопки справки нет', () => {
    setup();
    expect(screen.queryByRole('button', { name: /Справка/ })).toBeNull();
  });

  it('принимает sx массивом и объектом, darkFields=false', () => {
    const a = setup({ sx: [{ mb: 1 }], darkFields: false });
    expect(a.input()).toBeTruthy();
    a.unmount();
    const b = setup({ sx: { mb: 2 } });
    expect(b.input()).toBeTruthy();
  });

  it('allowCreate=false: внешняя смена value (например, загрузка другой карточки) очищает набранный текст', async () => {
    const onChange = jest.fn();
    const v1: AgentTagValue[] = [{ id: 1, name: 'ВНД' }];
    const { rerender } = render(
      <AgentTagsField value={v1} onChange={onChange} label="Теги" allowCreate={false} />,
    );
    const input = screen.getByRole('combobox') as HTMLInputElement;
    await act(async () => {});
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'Пре' } });
    expect(input.value).toBe('Пре');
    rerender(
      <AgentTagsField
        value={[{ id: 2, name: 'Презентации' }]}
        onChange={onChange}
        label="Теги"
        allowCreate={false}
      />,
    );
    expect(input.value).toBe('');
  });

  it('allowCreate=true: после добавления тега в управляемом режиме поле очищается', async () => {
    function Controlled() {
      const [v, setV] = React.useState<AgentTagValue[]>([]);
      return <AgentTagsField value={v} onChange={setV} label="Теги" />;
    }
    const { container } = render(<Controlled />);
    const input = screen.getByRole('combobox') as HTMLInputElement;
    await act(async () => {});
    fireEvent.change(input, { target: { value: 'Мой тег' } });
    expect(input.value).toBe('Мой тег');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(input.value).toBe('');
    expect(container.querySelectorAll('.MuiChip-root')).toHaveLength(1);
    expect(screen.getByText('Мой тег')).toBeTruthy();
  });
});
