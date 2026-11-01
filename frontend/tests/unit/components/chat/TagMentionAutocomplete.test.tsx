import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import TagMentionAutocomplete from '@/components/chat/TagMentionAutocomplete';

const mockFetchAgentTags = jest.fn();
jest.mock('@/constants/AgentTags', () => ({
  fetchAgentTags: (...a: unknown[]) => mockFetchAgentTags(...a),
}));

const TAGS = [
  { id: 1, name: 'Финансы' },
  { id: 2, name: 'Право' },
  { id: 3, name: 'Финтех' },
];

function anchor(): HTMLElement {
  const el = document.createElement('div');
  el.getBoundingClientRect = () =>
    ({ left: 10, top: 500, width: 100, height: 20, right: 110, bottom: 520 } as DOMRect);
  document.body.appendChild(el);
  return el;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('TagMentionAutocomplete', () => {
  beforeEach(() => {
    mockFetchAgentTags.mockReset();
    mockFetchAgentTags.mockResolvedValue(TAGS);
  });

  it('ничего не рендерит и не грузит каталог, пока закрыт', () => {
    const { container } = render(
      <TagMentionAutocomplete query="" open={false} anchorEl={anchor()} onSelect={jest.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(mockFetchAgentTags).not.toHaveBeenCalled();
  });

  it('ничего не рендерит без anchorEl, но каталог грузится', async () => {
    const { container } = render(
      <TagMentionAutocomplete query="" open anchorEl={null} onSelect={jest.fn()} />,
    );
    await flush();
    expect(container).toBeEmptyDOMElement();
    expect(mockFetchAgentTags).toHaveBeenCalledTimes(1);
  });

  it('показывает индикатор загрузки, затем список тегов с #-подписью', async () => {
    let resolve!: (v: unknown) => void;
    mockFetchAgentTags.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<TagMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    await act(async () => {
      resolve(TAGS);
    });
    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(screen.getByText('Право')).toBeInTheDocument();
    expect(screen.getByText('#Право')).toBeInTheDocument();
  });

  it('фильтрует по подстроке без учёта регистра, начиная с начального query', async () => {
    render(<TagMentionAutocomplete query="ФИН" open anchorEl={anchor()} onSelect={jest.fn()} />);
    await flush();
    expect(screen.getByText('Финансы')).toBeInTheDocument();
    expect(screen.getByText('Финтех')).toBeInTheDocument();
    expect(screen.queryByText('Право')).toBeNull();
  });

  it('поле поиска меняет фильтр; пустой результат -> «Ничего не найдено»', async () => {
    render(<TagMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />);
    await flush();
    const input = screen.getByPlaceholderText('Поиск тегов по имени');
    fireEvent.change(input, { target: { value: 'право' } });
    expect(screen.getByText('Право')).toBeInTheDocument();
    expect(screen.queryByText('Финансы')).toBeNull();
    fireEvent.change(input, { target: { value: 'zzz' } });
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
  });

  it('пустой каталог -> «Нет тегов»', async () => {
    mockFetchAgentTags.mockResolvedValue([]);
    render(<TagMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />);
    await flush();
    expect(screen.getByText('Нет тегов')).toBeInTheDocument();
  });

  it('ограничивает выдачу 20 тегами', async () => {
    mockFetchAgentTags.mockResolvedValue(
      Array.from({ length: 30 }, (_, i) => ({ id: i + 1, name: `tag${i}` })),
    );
    render(<TagMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />);
    await flush();
    expect(screen.getAllByText(/^#tag\d+$/)).toHaveLength(20);
  });

  it('позиционирует панель над якорем и ограничивает ширину снизу 260px', async () => {
    render(<TagMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />);
    await flush();
    const paper = document.querySelector('.MuiPaper-root') as HTMLElement;
    const st = getComputedStyle(paper);
    expect(st.position).toBe('fixed');
    expect(st.left).toBe('10px');
    expect(st.bottom).toBe(`${window.innerHeight - 500 + 8}px`);
    expect(st.width).toBe('260px');
  });

  it('клик (mousedown) по тегу вызывает onSelect с {id,name}', async () => {
    const onSelect = jest.fn();
    render(<TagMentionAutocomplete query="" open anchorEl={anchor()} onSelect={onSelect} />);
    await flush();
    fireEvent.mouseDown(screen.getByText('Право'));
    expect(onSelect).toHaveBeenCalledWith({ id: 2, name: 'Право' });
  });

  it('mousedown в поле поиска не всплывает', async () => {
    const outer = jest.fn();
    render(
      <div onMouseDown={outer}>
        <TagMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} />
      </div>,
    );
    await flush();
    fireEvent.mouseDown(screen.getByPlaceholderText('Поиск тегов по имени'));
    expect(outer).not.toHaveBeenCalled();
  });

  it('клавиатура: стрелки двигают выбор в границах, Enter/Tab выбирают текущий', async () => {
    const onSelect = jest.fn();
    render(<TagMentionAutocomplete query="" open anchorEl={anchor()} onSelect={onSelect} />);
    await flush();
    fireEvent.keyDown(window, { key: 'ArrowUp' }); // уже на 0
    fireEvent.keyDown(window, { key: 'ArrowDown' });
    fireEvent.keyDown(window, { key: 'ArrowDown' });
    fireEvent.keyDown(window, { key: 'ArrowDown' }); // упор в последний
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onSelect).toHaveBeenLastCalledWith({ id: 3, name: 'Финтех' });
    fireEvent.keyDown(window, { key: 'ArrowUp' });
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(onSelect).toHaveBeenLastCalledWith({ id: 2, name: 'Право' });
    fireEvent.keyDown(window, { key: 'a' });
    expect(onSelect).toHaveBeenCalledTimes(2);
  });

  it('наведение мыши переносит выбор на элемент', async () => {
    const onSelect = jest.fn();
    render(<TagMentionAutocomplete query="" open anchorEl={anchor()} onSelect={onSelect} />);
    await flush();
    fireEvent.mouseEnter(screen.getByText('Финтех'));
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith({ id: 3, name: 'Финтех' });
  });

  it('клавиши игнорируются, пока список пуст, и после закрытия', async () => {
    mockFetchAgentTags.mockResolvedValue([]);
    const onSelect = jest.fn();
    const a = anchor();
    const { rerender } = render(
      <TagMentionAutocomplete query="" open anchorEl={a} onSelect={onSelect} />,
    );
    await flush();
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onSelect).not.toHaveBeenCalled();
    rerender(<TagMentionAutocomplete query="" open={false} anchorEl={a} onSelect={onSelect} />);
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('ответ отменённого запроса (закрыли до ответа) не применяется', async () => {
    let resolve!: (v: unknown) => void;
    mockFetchAgentTags.mockReturnValueOnce(new Promise((r) => (resolve = r)));
    const a = anchor();
    const { rerender } = render(
      <TagMentionAutocomplete query="" open anchorEl={a} onSelect={jest.fn()} />,
    );
    rerender(<TagMentionAutocomplete query="" open={false} anchorEl={a} onSelect={jest.fn()} />);
    await act(async () => {
      resolve(TAGS);
    });
    let resolve2!: (v: unknown) => void;
    mockFetchAgentTags.mockReturnValueOnce(new Promise((r) => (resolve2 = r)));
    rerender(<TagMentionAutocomplete query="" open anchorEl={a} onSelect={jest.fn()} />);
    // каталог из отменённого запроса не должен попасть в состояние: идёт загрузка
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(screen.queryByText('Право')).toBeNull();
    await act(async () => {
      resolve2([]);
    });
  });

  it('светлая тема использует тёмный цвет текста поля', async () => {
    render(
      <TagMentionAutocomplete query="" open anchorEl={anchor()} onSelect={jest.fn()} isDarkMode={false} />,
    );
    await flush();
    expect(getComputedStyle(screen.getByPlaceholderText('Поиск тегов по имени')).color).toBe(
      'rgba(0, 0, 0, 0.87)',
    );
  });

  it('смена query при открытом списке синхронизирует поле поиска', async () => {
    const a = anchor();
    const { rerender } = render(
      <TagMentionAutocomplete query="" open anchorEl={a} onSelect={jest.fn()} />,
    );
    await flush();
    rerender(<TagMentionAutocomplete query="прав" open anchorEl={a} onSelect={jest.fn()} />);
    expect(screen.getByPlaceholderText('Поиск тегов по имени')).toHaveValue('прав');
    expect(screen.queryByText('Финансы')).toBeNull();
  });
});
