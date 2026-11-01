import React from 'react';
import { fireEvent, render } from '@testing-library/react';
import '@testing-library/jest-dom';
import ChatInputStatusCluster, { ChatInputStatusClusterProps } from '@/components/ChatInputStatusCluster';

const base: ChatInputStatusClusterProps = {
  isDarkMode: false,
  libraryActive: false,
  myAgentName: null,
};

const mcp = (name: string) => ({ display_name: name } as any);

function renderCluster(over: Partial<ChatInputStatusClusterProps> = {}) {
  const { container } = render(<ChatInputStatusCluster {...base} {...over} />);
  const root = container.firstElementChild as HTMLElement | null;
  return { container, root, title: root?.getAttribute('aria-label') ?? null };
}

describe('ChatInputStatusCluster', () => {
  it('ничего не рисует, пока нет ни одного активного индикатора', () => {
    const { container } = renderCluster();
    expect(container).toBeEmptyDOMElement();
  });

  it('библиотека: одна кнопка-книга, подсказка про общий RAG, клик вызывает onLibraryToggle без всплытия', () => {
    const onLibraryToggle = jest.fn();
    const outer = jest.fn();
    const { container: inner, title } = renderCluster({ libraryActive: true, onLibraryToggle });
    const container = inner;
    const wrapper = document.createElement('div');
    wrapper.addEventListener('click', outer);
    container.parentElement!.insertBefore(wrapper, container);
    wrapper.appendChild(container);
    expect(title).toContain('Общий RAG в ответах включён');
    const btn = container.querySelector('button')!;
    expect(btn).toHaveAttribute('type', 'button');
    fireEvent.click(btn);
    expect(onLibraryToggle).toHaveBeenCalledTimes(1);
    expect(outer).not.toHaveBeenCalled();
  });

  it('библиотека без обработчика: клик безопасен, курсор не pointer', () => {
    const { container } = renderCluster({ libraryActive: true });
    const btn = container.querySelector('button')!;
    expect(() => fireEvent.click(btn)).not.toThrow();
    expect(getComputedStyle(btn).cursor).toBe('default');
  });

  it('агент с обработчиком: кнопка, подсказка «Нажмите на робота», клик снимает агента', () => {
    const onAgentToggle = jest.fn();
    const { container, title } = renderCluster({ myAgentName: 'Юрист', onAgentToggle });
    expect(title).toBe('Активен агент «Юрист». Нажмите на робота, чтобы отключить.');
    const btn = container.querySelector('button')!;
    fireEvent.click(btn);
    expect(onAgentToggle).toHaveBeenCalledTimes(1);
  });

  it('агент без обработчика: не кнопка, нейтральная подсказка', () => {
    const { container, title } = renderCluster({ myAgentName: 'Юрист' });
    expect(title).toBe('Активен агент «Юрист» (Мои агенты).');
    expect(container.querySelector('button')).toBeNull();
    expect(() => fireEvent.click(container.querySelector('svg')!)).not.toThrow();
  });

  it('один skill: имя в подписи; без имени — slug; без обоих — «Skill»', () => {
    const a = renderCluster({ activeSkills: [{ name: ' Саммари ', slug: 's' } as any] });
    expect(a.container).toHaveTextContent('Саммари');
    expect(a.title).toBe('Skills:  Саммари .');
    a.container.remove();

    const b = renderCluster({ activeSkills: [{ name: '', slug: 'slug-1' } as any] });
    expect(b.container).toHaveTextContent('slug-1');

    const c = renderCluster({ activeSkills: [{ name: '', slug: '' } as any] });
    expect(c.container).toHaveTextContent('Skill');
  });

  it('несколько skills: «N Skills», подсказка перечисляет имена; клик (если есть обработчик) снимает', () => {
    const onSkillsToggle = jest.fn();
    const { container, title } = renderCluster({
      activeSkills: [{ name: 'A', slug: 'a' } as any, { name: '', slug: 'b' } as any],
      onSkillsToggle,
    });
    expect(container).toHaveTextContent('2 Skills');
    expect(title).toBe('Skills: A, b. Нажмите, чтобы отключить.');
    fireEvent.click(container.querySelector('button')!);
    expect(onSkillsToggle).toHaveBeenCalledTimes(1);
  });

  it('skills без обработчика — div и подсказка без призыва к клику', () => {
    const { container, title } = renderCluster({ activeSkills: [{ name: 'A', slug: 'a' } as any] });
    expect(title).toBe('Skills: A.');
    expect(container.querySelector('button')).toBeNull();
  });

  it('MCP: один сервер — его имя, несколько — «N MCP»; клик вызывает onMcpClick', () => {
    const onMcpClick = jest.fn();
    const one = renderCluster({ activeMcpServers: [mcp('Jira')], onMcpClick });
    expect(one.container).toHaveTextContent('Jira');
    expect(one.title).toBe('MCP: Jira. Инструменты → MCP.');
    fireEvent.click(one.container.querySelector('button')!);
    expect(onMcpClick).toHaveBeenCalledTimes(1);
    one.container.remove();

    const many = renderCluster({ activeMcpServers: [mcp('Jira'), mcp('Git')] });
    expect(many.container).toHaveTextContent('2 MCP');
    expect(many.title).toBe('MCP: Jira, Git. Инструменты → MCP.');
    expect(many.container.querySelector('button')).toBeNull();
  });

  it('артефакты: подсказка по умолчанию, пользовательская, и вариант без обработчика', () => {
    const onArtifactsToggle = jest.fn();
    const a = renderCluster({ artifactsActive: true, onArtifactsToggle });
    expect(a.title).toBe('Режим артефактов включён. Нажмите, чтобы отключить.');
    fireEvent.click(a.container.querySelector('button')!);
    expect(onArtifactsToggle).toHaveBeenCalledTimes(1);
    a.container.remove();

    const b = renderCluster({ artifactsActive: true, artifactsTooltip: 'Артефакты: HTML.', onArtifactsToggle });
    expect(b.title).toBe('Артефакты: HTML. Нажмите, чтобы отключить.');
    b.container.remove();

    const c = renderCluster({ artifactsActive: true, artifactsTooltip: 'Артефакты: HTML.' });
    expect(c.title).toBe('Артефакты: HTML.');
    c.container.remove();

    const d = renderCluster({ artifactsActive: true });
    expect(d.title).toBe('Режим артефактов включён.');
    expect(d.container.querySelector('button')).toBeNull();
  });

  it('несколько индикаторов — одна «пилюля» с разделителями между сегментами в фиксированном порядке', () => {
    const { container, title } = renderCluster({
      libraryActive: true,
      myAgentName: 'А',
      onAgentToggle: jest.fn(),
      activeSkills: [{ name: 'S', slug: 's' } as any],
      artifactsActive: true,
      activeMcpServers: [mcp('M')],
      onMcpClick: jest.fn(),
    });
    expect(container.querySelectorAll('div[aria-hidden="true"]')).toHaveLength(4);
    // порядок: книга, робот, skills (текст), артефакты, MCP (текст)
    const text = container.textContent;
    expect(text!.indexOf('S')).toBeLessThan(text!.indexOf('M'));
    expect(title!.indexOf('RAG')).toBeLessThan(title!.indexOf('агент'));
    expect(title!.indexOf('агент')).toBeLessThan(title!.indexOf('Skills'));
    expect(title!.indexOf('Skills')).toBeLessThan(title!.indexOf('MCP'));
    expect(title!.indexOf('MCP')).toBeLessThan(title!.indexOf('артефактов'));
  });

  it('клики по сегментам не всплывают к родителю (поле ввода не получает фокус/отправку)', () => {
    const parentClick = jest.fn();
    const handlers = { onLibraryToggle: jest.fn(), onAgentToggle: jest.fn(), onSkillsToggle: jest.fn(), onArtifactsToggle: jest.fn(), onMcpClick: jest.fn() };
    const { container } = render(
      <div onClick={parentClick}>
        <ChatInputStatusCluster
          {...base}
          {...handlers}
          libraryActive
          myAgentName="А"
          activeSkills={[{ name: 'S', slug: 's' } as any]}
          artifactsActive
          activeMcpServers={[mcp('M')]}
        />
      </div>,
    );
    container.querySelectorAll('button').forEach((b) => fireEvent.click(b));
    Object.values(handlers).forEach((h) => expect(h).toHaveBeenCalledTimes(1));
    expect(parentClick).not.toHaveBeenCalled();
  });

  it('работает в тёмной теме (цвет разделителя светлый)', () => {
    const { container } = renderCluster({ isDarkMode: true, libraryActive: true, myAgentName: 'А' });
    const divider = container.querySelector('div[aria-hidden="true"]') as HTMLElement;
    expect(getComputedStyle(divider).backgroundColor).toBe('rgba(255, 255, 255, 0.28)');
  });
});
