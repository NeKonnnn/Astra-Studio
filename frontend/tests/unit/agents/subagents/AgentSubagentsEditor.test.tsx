import '@testing-library/jest-dom';
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import AgentSubagentsEditor, {
  EMPTY_SUBAGENT_CONFIG,
  MAX_SUBAGENTS_UI,
  buildSubagentNamesSnapshot,
  parseAgentNamesMap,
  type SubagentConfig,
} from '@/agents/subagents/AgentSubagentsEditor';

let mockCatalog: Array<{ id: number; name: string }> = [];

jest.mock('@/components/right_bar/AgentTagsField', () => {
  const actual = jest.requireActual('@/components/right_bar/AgentTagsField');
  return {
    __esModule: true,
    normalizeAgentTags: actual.normalizeAgentTags,
    useAgentTags: () => mockCatalog,
    default: (props: any) => (
      <div data-testid="tags-field">
        <span data-testid="tags-values">{props.value.map((v: any) => v.name).join('|')}</span>
        <button onClick={() => props.onChange([{ id: 5, name: 'a' }, { name: 'no-id' }])}>set-tags</button>
      </div>
    ),
  };
});

const chrome = { fgSubtle: '#999', fgMuted: '#777', hoverBg: '#222' };
const AGENTS = [
  { id: 1, name: 'Первый', tags: [{ id: 1, name: 't1' }, { id: 2, name: 't2' }, { id: 3, name: 't3' }, { id: 4, name: 't4' }, { id: 5, name: 't5' }] },
  { id: 2, name: 'Второй', tags: ['x'] },
  { id: 3, name: '  ', tags: undefined },
];

const cfg = (over: Partial<SubagentConfig> = {}): SubagentConfig => ({ ...EMPTY_SUBAGENT_CONFIG, ...over });

function setup(config: SubagentConfig, over: Record<string, unknown> = {}) {
  const onChange = jest.fn();
  render(
    <AgentSubagentsEditor
      currentAgentId={1}
      config={config}
      onChange={onChange}
      agents={AGENTS}
      panelChrome={chrome}
      {...(over as any)}
    />,
  );
  return onChange;
}

beforeEach(() => {
  mockCatalog = [{ id: 7, name: 'catalog-tag' }];
});

describe('AgentSubagentsEditor', () => {
  it('выключенный режим: только переключатель и счётчик; включение вызывает onChange', () => {
    const onChange = setup(cfg());
    expect(screen.getByText('0 / 10')).toBeInTheDocument();
    expect(screen.queryByText(/self-spawn/)).toBeNull();
    fireEvent.click(screen.getAllByRole('switch')[0]);
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ enabled: true }));
  });

  it('aria-label переключателей из inputProps не попадает в DOM (known issue)', () => {
    // Правильно: у переключателя «Режим с субагентами» есть доступное имя для скринридера.
    // Сейчас MUI Switch 7.3 не пробрасывает inputProps в input.
    setup(cfg({ enabled: true }));
    expect(screen.queryByLabelText('Режим с субагентами')).toBeNull();
    expect(screen.queryByLabelText('Разрешить self-spawn')).toBeNull();
    expect(screen.queryByLabelText('Только обязательные теги')).toBeNull();
  });

  it('afterModeSlot отображается под переключателем режима', () => {
    setup(cfg(), { afterModeSlot: <div>слот лимита</div> });
    expect(screen.getByText('слот лимита')).toBeInTheDocument();
  });

  it('self-spawn переключатель включает режим и выставляет allow_self', () => {
    const onChange = setup(cfg({ enabled: true, allow_self: true, agent_ids: [2] }));
    fireEvent.click(screen.getAllByRole('switch')[1]);
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ enabled: true, allow_self: false }));
  });

  it('предупреждение, когда запускать некого; пропадает при self-spawn, субагентах или обязательных тегах', () => {
    const base = { enabled: true, allow_self: false };
    const { unmount } = render(
      <AgentSubagentsEditor currentAgentId={1} config={cfg(base)} onChange={jest.fn()} agents={AGENTS} panelChrome={chrome} />,
    );
    expect(screen.getByText(/Включите self-spawn или добавьте/)).toBeInTheDocument();
    unmount();
    const r2 = render(
      <AgentSubagentsEditor currentAgentId={1} config={cfg({ ...base, agent_ids: [2] })} onChange={jest.fn()} agents={AGENTS} panelChrome={chrome} />,
    );
    expect(screen.queryByText(/Включите self-spawn или добавьте/)).toBeNull();
    r2.unmount();
    render(
      <AgentSubagentsEditor currentAgentId={1} config={cfg({ ...base, required_tag_ids: [7] })} onChange={jest.fn()} agents={AGENTS} panelChrome={chrome} />,
    );
    expect(screen.queryByText(/Включите self-spawn или добавьте/)).toBeNull();
  });

  it('имена субагентов: живое имя, снимок (строковый ключ), запасное #id; теги и «+N»', () => {
    setup(
      cfg({
        enabled: true,
        agent_ids: [1, 2, 3, 50, 51],
        agent_names: { 50: ' Из снимка ', ['51' as unknown as number]: 'Строковый ключ' },
      }),
    );
    expect(screen.getByText('Первый')).toBeInTheDocument();
    expect(screen.getByText('Второй')).toBeInTheDocument();
    expect(screen.getByText('Агент #3')).toBeInTheDocument();
    expect(screen.getByText('Из снимка')).toBeInTheDocument();
    expect(screen.getByText('Строковый ключ')).toBeInTheDocument();
    expect(screen.getByText('t1')).toBeInTheDocument();
    expect(screen.getByText('t3')).toBeInTheDocument();
    expect(screen.queryByText('t4')).toBeNull();
    expect(screen.getByText('+2')).toBeInTheDocument();
    expect(screen.getByText('x')).toBeInTheDocument();
  });

  it('удаление субагента убирает его id и имя из снимка', () => {
    const onChange = setup(cfg({ enabled: true, agent_ids: [2, 3], agent_names: { 2: 'Второй', 3: 'Т' } }));
    fireEvent.click(screen.getByLabelText('Убрать субагента Второй'));
    const next = onChange.mock.calls[0][0];
    expect(next.agent_ids).toEqual([3]);
    expect(next.agent_names).toEqual({ 3: 'Т' });
  });

  it('добавление: в списке нет текущего и выбранных; выбор сохраняет id и имя', () => {
    const onChange = setup(cfg({ enabled: true, agent_ids: [2] }));
    fireEvent.click(screen.getByPlaceholderText('Выберите агента'));
    // текущий агент (1) и уже выбранный (2) исключены, остался только третий (имя пустое)
    const items = Array.from(document.querySelectorAll('.MuiPopover-paper span')).map((n) => n.textContent);
    expect(items.map((t) => (t || '').trim())).toEqual(['']);
    fireEvent.click(document.querySelector('.MuiPopover-paper span')!);
    const next = onChange.mock.calls[0][0];
    expect(next.agent_ids).toEqual([2, 3]);
    expect(next.enabled).toBe(true);
    expect(next.agent_names).toEqual({});
  });

  it('добавление с именем сохраняет снимок имени', () => {
    const onChange = setup(cfg({ enabled: true }), { currentAgentId: 'new' });
    fireEvent.click(screen.getByPlaceholderText('Выберите агента'));
    fireEvent.click(screen.getByText('Второй'));
    expect(onChange.mock.calls[0][0].agent_names).toEqual({ 2: 'Второй' });
  });

  it('Escape закрывает список без изменений конфига', () => {
    const onChange = setup(cfg({ enabled: true }));
    fireEvent.click(screen.getByPlaceholderText('Выберите агента'));
    expect(document.querySelector('.MuiPopover-paper')).toBeTruthy();
    fireEvent.keyDown(document.querySelector('.MuiPopover-root')!, { key: 'Escape' });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('нет вариантов -> заглушка', () => {
    setup(cfg({ enabled: true }), { agents: [] });
    fireEvent.click(screen.getByPlaceholderText('Выберите агента'));
    expect(screen.getByText('Нет доступных агентов')).toBeInTheDocument();
  });

  it('при достижении лимита поле добавления скрыто; readOnly блокирует всё', () => {
    setup(cfg({ enabled: true, agent_ids: [2] }), { maxSubagents: 1 });
    expect(screen.queryByPlaceholderText('Выберите агента')).toBeNull();
  });

  it('readOnly: без кнопок удаления и добавления, переключатели отключены', () => {
    setup(cfg({ enabled: true, agent_ids: [2] }), { readOnly: true });
    expect(screen.queryByLabelText(/Убрать субагента/)).toBeNull();
    expect(screen.queryByPlaceholderText('Выберите агента')).toBeNull();
    expect(screen.getAllByRole('switch')[0]).toBeDisabled();
    expect(screen.getAllByRole('switch')[1]).toBeDisabled();
  });

  it('обязательные теги: имена из справочника или #id, изменение возвращает только числовые id', () => {
    const onChange = setup(cfg({ enabled: true, required_tag_ids: [7, 99] }));
    expect(screen.getByTestId('tags-values')).toHaveTextContent('catalog-tag|#99');
    fireEvent.click(screen.getByText('set-tags'));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ enabled: true, required_tag_ids: [5] }));
  });

  it('«Только обязательные»: недоступно без тегов, с тегами включается и показывает пояснение', () => {
    const { unmount } = render(
      <AgentSubagentsEditor currentAgentId={1} config={cfg({ enabled: true })} onChange={jest.fn()} agents={AGENTS} panelChrome={chrome} />,
    );
    expect(screen.getAllByRole('switch')[2]).toBeDisabled();
    unmount();
    const onChange = setup(cfg({ enabled: true, required_tag_ids: [7], required_only: true }));
    expect(screen.getByText(/Список субагентов ниже при этом не используется/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('switch')[2]);
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ required_only: false }));
  });

  it('отсутствие required_tag_ids и agent_names в конфиге переносится', () => {
    setup({ enabled: true, allow_self: true, agent_ids: [], required_only: false } as unknown as SubagentConfig);
    expect(screen.getByTestId('tags-values')).toHaveTextContent('');
  });

  it('светлая панель', () => {
    setup(cfg({ enabled: true }), { panelChrome: { ...chrome, isLight: true } });
    expect(screen.getByTestId('tags-field')).toBeInTheDocument();
  });
});

describe('вспомогательные функции субагентов', () => {
  it('константы', () => {
    expect(MAX_SUBAGENTS_UI).toBe(10);
    expect(EMPTY_SUBAGENT_CONFIG).toEqual({
      enabled: false,
      allow_self: true,
      agent_ids: [],
      agent_names: {},
      required_tag_ids: [],
      required_only: false,
    });
  });

  it('parseAgentNamesMap: фильтрует мусорные ключи и пустые имена', () => {
    expect(parseAgentNamesMap(null)).toEqual({});
    expect(parseAgentNamesMap([1, 2])).toEqual({});
    expect(parseAgentNamesMap('x')).toEqual({});
    expect(parseAgentNamesMap({ '1': ' А ', '0': 'нуль', abc: 'x', '2': '', '3': null, '4': 5 })).toEqual({ 1: 'А', 4: '5' });
  });

  it('buildSubagentNamesSnapshot: приоритет живого имени, затем прежнего, пустые отбрасываются', () => {
    const out = buildSubagentNamesSnapshot(
      [1, 2, 3, 4],
      [{ id: 1, name: ' Живое ' }, { id: 2, name: '  ' }, { id: 3 }],
      { 2: ' Старое ', 3: '', 1: 'Не используется' },
    );
    expect(out).toEqual({ 1: 'Живое', 2: 'Старое' });
    expect(buildSubagentNamesSnapshot([1], [])).toEqual({});
  });
});
