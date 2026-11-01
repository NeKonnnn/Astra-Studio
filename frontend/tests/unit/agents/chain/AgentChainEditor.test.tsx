import '@testing-library/jest-dom';
import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import AgentChainEditor from '@/agents/chain/AgentChainEditor';

const AGENTS = [
  { id: 1, name: 'Первый' },
  { id: 2, name: 'Второй' },
  { id: 3, name: 'Третий' },
  { id: 4, name: 'Четвёртый' },
];
const chrome = { fgSubtle: '#999', fgMuted: '#777', hoverBg: '#222' };

function setup(over: Record<string, unknown> = {}) {
  const onChange = jest.fn();
  const utils = render(
    <AgentChainEditor
      currentAgentId={1}
      currentAgentName="Мой агент"
      agentIds={[2]}
      onChange={onChange}
      agents={AGENTS}
      panelChrome={chrome}
      {...(over as any)}
    />,
  );
  return { onChange, ...utils };
}

const openAdd = () => fireEvent.click(document.getElementById('agent-constructor-chain-add')!);
const popoverItems = () => Array.from(document.querySelectorAll('.MuiPopover-paper > div > div')).map((n) => n.textContent);

describe('AgentChainEditor', () => {
  it('показывает текущего агента, звенья цепочки и счётчик', () => {
    setup({ agentIds: [2, 99] });
    expect(screen.getByText('Мой агент')).toBeInTheDocument();
    expect(screen.getByText('Второй')).toBeInTheDocument();
    expect(screen.getByText('Агент #99')).toBeInTheDocument();
    expect(screen.getByText('2 / 10')).toBeInTheDocument();
  });

  it('пустое имя текущего агента заменяется на «Этот агент»', () => {
    setup({ currentAgentName: '   ', currentAgentId: 'new' });
    expect(screen.getByText('Этот агент')).toBeInTheDocument();
  });

  it('удаление звена вызывает onChange без него', () => {
    const { onChange } = setup({ agentIds: [2, 3] });
    fireEvent.click(screen.getByLabelText('Убрать Второй из цепочки'));
    expect(onChange).toHaveBeenCalledWith([3]);
  });

  it('добавление: список без текущего и уже выбранных, выбор добавляет и закрывает меню', () => {
    const { onChange } = setup();
    openAdd();
    expect(popoverItems()).toEqual(['Третий', 'Четвёртый']);
    fireEvent.click(screen.getByText('Четвёртый'));
    expect(onChange).toHaveBeenCalledWith([2, 4]);
  });

  it('для нового агента (id=new) в списке есть все невыбранные', () => {
    setup({ currentAgentId: 'new', agentIds: [] });
    openAdd();
    expect(popoverItems()).toEqual(['Первый', 'Второй', 'Третий', 'Четвёртый']);
  });

  it('замена звена: в списке сам заменяемый и свободные; выбор подставляет агента на ту же позицию', () => {
    const { onChange } = setup({ agentIds: [2, 3] });
    fireEvent.click(screen.getByText('Второй'));
    expect(popoverItems()).toEqual(['Второй', 'Четвёртый']);
    fireEvent.click(screen.getByText('Четвёртый'));
    expect(onChange).toHaveBeenCalledWith([4, 3]);
  });

  it('замена на самого себя допустима и даёт тот же список', () => {
    const { onChange } = setup({ agentIds: [2, 3] });
    fireEvent.click(screen.getByText('Второй'));
    const items = within(document.querySelector('.MuiPopover-paper') as HTMLElement).getAllByText('Второй');
    fireEvent.click(items[0]);
    expect(onChange).toHaveBeenCalledWith([2, 3]);
  });

  it('при достижении лимита нельзя добавлять и показывается сообщение', () => {
    setup({ agentIds: [2, 3], maxAgents: 2 });
    expect(document.getElementById('agent-constructor-chain-add')).toBeNull();
    expect(screen.getByText('Максимум 2 агентов в цепочке')).toBeInTheDocument();
  });

  it('когда некого добавлять, поле добавления скрыто', () => {
    setup({ agentIds: [2, 3, 4] });
    expect(document.getElementById('agent-constructor-chain-add')).toBeNull();
  });

  it('когда в режиме замены нет вариантов, показывается заглушка', () => {
    setup({ agents: [{ id: 1, name: 'Первый' }], agentIds: [], currentAgentId: 5 });
    openAdd();
    expect(screen.getByText('Первый')).toBeInTheDocument();
    setup({ agents: [], agentIds: [7], currentAgentId: 5 });
    fireEvent.click(screen.getByText('Агент #7'));
    expect(screen.getByText('Нет доступных агентов')).toBeInTheDocument();
  });

  it('readOnly: нет кнопок удаления и добавления, замена недоступна', () => {
    const { onChange } = setup({ readOnly: true, agentIds: [2] });
    expect(screen.queryByLabelText('Убрать Второй из цепочки')).toBeNull();
    expect(document.getElementById('agent-constructor-chain-add')).toBeNull();
    expect(screen.getByText('Второй')).toBeDisabled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('светлая панель использует светлую рамку звена', () => {
    setup({ panelChrome: { ...chrome, isLight: true } });
    expect(screen.getByText('Второй')).toBeInTheDocument();
  });

  it('закрытие поповера по Escape сбрасывает режим замены', () => {
    const { onChange } = setup({ agentIds: [2] });
    openAdd();
    fireEvent.keyDown(document.querySelector('.MuiPopover-root')!, { key: 'Escape' });
    expect(onChange).not.toHaveBeenCalled();
  });
});
