import '@testing-library/jest-dom';
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import CreationsGallery from '@/components/CreationsGallery';
import ChatInputSuggestions from '@/components/ChatInputSuggestions';

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));

let mockCreations: { items: any[]; loading: boolean };
jest.mock('@/hooks/useImageCreations', () => ({
  useImageCreations: () => mockCreations,
}));

jest.mock('@/utils/imageCreations', () => ({
  resolveCreationPreviewSrc: (url?: string | null) => (url ? `resolved:${url}` : ''),
}));

jest.mock('@/components/AuthenticatedInlineImage', () => ({
  __esModule: true,
  default: ({ src, alt }: { src: string; alt: string }) => <img data-testid="thumb" src={src} alt={alt} />,
}));

jest.mock('@/components/InlineImageLightbox', () => ({
  __esModule: true,
  default: ({ open, src, name, onClose, onEdit }: any) =>
    open ? (
      <div data-testid="lightbox" data-src={src} data-name={name}>
        <button onClick={onClose}>lb-close</button>
        <button onClick={onEdit}>lb-edit</button>
      </div>
    ) : null,
}));

beforeEach(() => {
  mockNavigate.mockReset();
  mockCreations = { items: [], loading: false };
});

describe('CreationsGallery', () => {
  const items = [
    { id: 1, name: 'one.png', prompt: 'котик', preview_url: '/p/1', conversation_id: 'c1' },
    { id: 2, name: 'two.png', prompt: '', preview_url: '', conversation_id: 'c2' },
    { id: 3, name: 'three.png', prompt: '', preview_url: '', conversation_id: '' },
  ];

  it('индикатор загрузки', () => {
    mockCreations = { items: [], loading: true };
    render(<CreationsGallery />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(screen.getByText('Моё творение')).toBeInTheDocument();
  });

  it('пустое состояние с подсказкой', () => {
    render(<CreationsGallery isDarkMode={false} />);
    expect(screen.getByText(/Пока нет сгенерированных изображений/)).toBeInTheDocument();
  });

  it('фильтры переключаются визуально, список не меняется', () => {
    mockCreations = { items, loading: false };
    render(<CreationsGallery />);
    fireEvent.click(screen.getByRole('button', { name: 'Изображение' }));
    fireEvent.click(screen.getByRole('button', { name: 'Все' }));
    expect(screen.getAllByTestId('thumb')).toHaveLength(1);
  });

  it('карточки: превью с alt по промпту, запасной текст без превью', () => {
    mockCreations = { items, loading: false };
    render(<CreationsGallery />);
    expect(screen.getByAltText('котик')).toHaveAttribute('src', 'resolved:/p/1');
    expect(document.querySelectorAll('span.MuiTypography-caption')).toHaveLength(2);
  });

  it('клик по карточке с превью открывает просмотр, без превью — переход в чат, без чата — ничего', () => {
    mockCreations = { items, loading: false };
    render(<CreationsGallery />);
    fireEvent.click(screen.getByAltText('котик'));
    expect(screen.getByTestId('lightbox')).toHaveAttribute('data-src', 'resolved:/p/1');
    expect(screen.getByTestId('lightbox')).toHaveAttribute('data-name', 'one.png');
    fireEvent.click(screen.getByText('lb-close'));
    expect(screen.queryByTestId('lightbox')).toBeNull();

    const textCards = Array.from(document.querySelectorAll('span.MuiTypography-caption')) as HTMLElement[];
    fireEvent.click(textCards[0]);
    expect(mockNavigate).toHaveBeenCalledWith('/?chat=c2');
    mockNavigate.mockClear();
    fireEvent.click(textCards[1]);
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('редактирование: подставляет текст в поле ввода, переходит в чат, закрывает просмотр', () => {
    mockCreations = { items, loading: false };
    const events: any[] = [];
    const handler = (e: Event) => events.push((e as CustomEvent).detail);
    window.addEventListener('astrachatPrefillChatInput', handler);
    render(<CreationsGallery />);
    fireEvent.click(screen.getByAltText('котик'));
    fireEvent.click(screen.getByText('lb-edit'));
    window.removeEventListener('astrachatPrefillChatInput', handler);
    expect(mockNavigate).toHaveBeenCalledWith('/?chat=c1');
    expect(events).toEqual([{ text: 'Отредактируй изображение: котик' }]);
    expect(screen.queryByTestId('lightbox')).toBeNull();
  });

  it('редактирование без промпта и без чата не переходит', () => {
    mockCreations = { items: [{ id: 9, name: 'n.png', prompt: '  ', preview_url: '/p/9', conversation_id: '' }], loading: false };
    const events: any[] = [];
    const handler = (e: Event) => events.push((e as CustomEvent).detail);
    window.addEventListener('astrachatPrefillChatInput', handler);
    render(<CreationsGallery />);
    fireEvent.click(screen.getByTestId('thumb'));
    fireEvent.click(screen.getByText('lb-edit'));
    window.removeEventListener('astrachatPrefillChatInput', handler);
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(events).toEqual([{ text: 'Отредактируй изображение: ' }]);
  });
});

describe('ChatInputSuggestions', () => {
  const sugg = [
    { id: 'a', title: 'Написать письмо', subtitle: 'Деловое', content: 'Напиши письмо', source: 'general' },
    { id: 'b', title: 'Резюме текста', content: 'Сделай резюме', source: 'general' },
    { id: 'c', title: 'Из базы', content: 'Найди в базе', source: 'rag' },
    { id: 'd', title: 'Четвёртый', content: 'четвёртый', source: 'general' },
    { id: 'e', title: 'Пятый', content: 'пятый', source: 'mcp' },
  ] as any;

  it('пустой ввод: не более трёх, контекстные (rag/mcp) идут первыми', () => {
    render(<ChatInputSuggestions suggestions={sugg} inputValue="" onSelect={jest.fn()} />);
    const titles = screen.getAllByRole('button').map((b) => b.textContent);
    expect(titles).toHaveLength(3);
    expect(titles).toEqual(expect.arrayContaining(['Из базы', 'Пятый']));
    expect(screen.getByText('Предложено')).toBeInTheDocument();
  });

  it('с текстом фильтрует по совпадению и сохраняет порядок', () => {
    render(<ChatInputSuggestions suggestions={sugg} inputValue="резюме" onSelect={jest.fn()} />);
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Резюме текста']);
  });

  it('клик передаёт content; подзаголовок показывается только если есть', () => {
    const onSelect = jest.fn();
    render(<ChatInputSuggestions suggestions={sugg} inputValue="письмо" onSelect={onSelect} showLabel={false} isDarkMode maxWidth={400} contentInset={2} />);
    expect(screen.queryByText('Предложено')).toBeNull();
    expect(screen.getByText('Деловое')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Написать письмо'));
    expect(onSelect).toHaveBeenCalledWith('Напиши письмо');
  });

  it('disabled, слишком длинный ввод и отсутствие совпадений ничего не рисуют', () => {
    const { container, rerender } = render(<ChatInputSuggestions suggestions={sugg} inputValue="" onSelect={jest.fn()} disabled />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ChatInputSuggestions suggestions={sugg} inputValue={'я'.repeat(501)} onSelect={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ChatInputSuggestions suggestions={sugg} inputValue="zzzzzz" onSelect={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ChatInputSuggestions suggestions={[]} inputValue="" onSelect={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('ввод ровно на границе 500 символов ещё допустим', () => {
    render(<ChatInputSuggestions suggestions={[{ id: 'x', title: 'я', content: 'я'.repeat(500), source: 'general' } as any]} inputValue={'я'.repeat(500)} onSelect={jest.fn()} />);
    expect(screen.getAllByRole('button')).toHaveLength(1);
  });
});
