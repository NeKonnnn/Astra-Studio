import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import RightBar, { RightBarProps } from '@/components/right_bar/RightBar';
import {
  ASTRA_OPEN_AGENT_CONSTRUCTOR,
  ASTRA_OPEN_SKILLS_SIDEBAR,
  ASTRA_OPEN_TRANSCRIPTION_SIDEBAR,
} from '@/constants/hotkeys';

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: '/', search: '', hash: '', state: null, key: 'k' }),
}));

let mockCanUseAgents = true;
let capturedOpenConstructor: (() => void) | null = null;
let capturedOpenSkills: (() => void) | null = null;

jest.mock('@/hooks/useFeatureAccess', () => ({
  useFeatureAccess: () => ({ canUseAgents: mockCanUseAgents }),
}));

jest.mock('@/hooks/usePendingAgentConstructorOpen', () => ({
  usePendingAgentConstructorOpen: (cb: () => void) => {
    capturedOpenConstructor = cb;
  },
}));

jest.mock('@/hooks/usePendingSkillSidebarOpen', () => ({
  usePendingSkillSidebarOpen: (cb: () => void) => {
    capturedOpenSkills = cb;
  },
}));

jest.mock('@/components/right_bar/AgentConstructorPanel', () => ({
  __esModule: true,
  default: (p: { isDarkMode: boolean; isOpen: boolean }) => (
    <div data-testid="constructor-panel" data-dark={String(p.isDarkMode)} data-open={String(p.isOpen)} />
  ),
}));
jest.mock('@/components/right_bar/SkillsSidebarPanel', () => ({
  __esModule: true,
  default: (p: { isOpen: boolean }) => <div data-testid="skills-panel" data-open={String(p.isOpen)} />,
}));
jest.mock('@/components/right_bar/TranscriptionSidebarSection', () => ({
  __esModule: true,
  default: (p: { open: boolean }) => <div data-testid="transcription-section" data-open={String(p.open)} />,
}));
jest.mock('@/components/right_bar/GalleryNavButton', () => ({
  __esModule: true,
  default: (p: { variant: string }) => <div data-testid={`gallery-${p.variant}`} />,
}));

function makeProps(over: Partial<RightBarProps> = {}): RightBarProps {
  return {
    open: true,
    hidden: false,
    expandedWidthPx: 300,
    widthPinned: false,
    isDarkMode: true,
    onToggleOpen: jest.fn(),
    onHide: jest.fn(),
    onShow: jest.fn(),
    setOpen: jest.fn(),
    setHidden: jest.fn(),
    setExpandedWidthPx: jest.fn(),
    setWidthPinned: jest.fn(),
    ...over,
  };
}

beforeEach(() => {
  mockCanUseAgents = true;
  capturedOpenConstructor = null;
  capturedOpenSkills = null;
  localStorage.clear();
});

describe('RightBar: состояния панели', () => {
  it('hidden: показывает только кнопку «Показать панель»', () => {
    const props = makeProps({ hidden: true });
    render(<RightBar {...props} />);
    expect(screen.queryByTestId('transcription-section')).toBeNull();
    // единственная кнопка — «показать»
    fireEvent.click(screen.getByRole('button'));
    expect(props.onShow).toHaveBeenCalledTimes(1);
  });

  it('open: нижние блоки закрыты, транскрибация получает open=false', () => {
    render(<RightBar {...makeProps()} />);
    expect(screen.queryByTestId('transcription-section')).toBeNull();
    expect(screen.queryByTestId('skills-panel')).toBeNull();
    expect(screen.queryByTestId('constructor-panel')).toBeNull();
    expect(screen.getByTestId('gallery-expanded')).toBeTruthy();
    expect(screen.getByText('Skills')).toBeTruthy();
    expect(screen.getByText('Конструктор агента')).toBeTruthy();
  });

  it('open: ширина берётся из expandedWidthPx и зажимается в диапазон', () => {
    const { container, rerender } = render(<RightBar {...makeProps({ expandedWidthPx: 5000 })} />);
    const paper = container.ownerDocument.querySelector('.astra-right-rail') as HTMLElement;
    expect(paper).toBeTruthy();
    expect(getComputedStyle(paper).width).toBe('720px');
    rerender(<RightBar {...makeProps({ expandedWidthPx: 10 })} />);
    expect(getComputedStyle(paper).width).toBe('240px');
  });

  it('open: «Свернуть панель» вызывает onToggleOpen, закрепление — setWidthPinned с инверсией', () => {
    const props = makeProps();
    render(<RightBar {...props} />);
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[0]);
    expect(props.onToggleOpen).toHaveBeenCalledTimes(1);

    const pin = screen.getByRole('button', { pressed: false });
    fireEvent.click(pin);
    const updater = (props.setWidthPinned as jest.Mock).mock.calls[0][0] as (p: boolean) => boolean;
    expect(updater(false)).toBe(true);
    expect(updater(true)).toBe(false);
  });

  it('закреплённая ширина: aria-pressed=true', () => {
    render(<RightBar {...makeProps({ widthPinned: true })} />);
    expect(screen.getByRole('button', { pressed: true })).toBeTruthy();
  });

  it('открытая панель содержит ручку изменения ширины; свёрнутая — нет', () => {
    const { rerender } = render(<RightBar {...makeProps()} />);
    expect(screen.getByRole('separator')).toBeTruthy();
    rerender(<RightBar {...makeProps({ open: false })} />);
    expect(screen.queryByRole('separator')).toBeNull();
  });

  it('перетаскивание ручки: onResize/onResizeEnd пишут зажатую ширину в setExpandedWidthPx', () => {
    const props = makeProps();
    render(<RightBar {...props} />);
    const paper = document.querySelector('.astra-right-rail') as HTMLElement;
    paper.getBoundingClientRect = () => ({ width: 300 } as DOMRect);
    fireEvent.mouseDown(screen.getByRole('separator'), { button: 0, clientX: 500 });
    fireEvent.mouseMove(document, { clientX: 400 });
    expect(props.setExpandedWidthPx).toHaveBeenLastCalledWith(400);
    fireEvent.mouseUp(document, { clientX: 100 });
    expect(props.setExpandedWidthPx).toHaveBeenLastCalledWith(700);
  });

  it('закреплённая ширина отключает ручку', () => {
    const props = makeProps({ widthPinned: true });
    render(<RightBar {...props} />);
    const paper = document.querySelector('.astra-right-rail') as HTMLElement;
    paper.getBoundingClientRect = () => ({ width: 300 } as DOMRect);
    fireEvent.mouseDown(screen.getByRole('separator'), { button: 0, clientX: 500 });
    fireEvent.mouseMove(document, { clientX: 400 });
    expect(props.setExpandedWidthPx).not.toHaveBeenCalled();
  });
});

describe('RightBar: разделы в раскрытой панели', () => {
  it('клик «Транскрибация» открывает секцию; из меню разделов повторный выбор закрывает её', () => {
    render(<RightBar {...makeProps()} />);
    fireEvent.click(screen.getByText('Транскрибация'));
    expect(screen.getByTestId('transcription-section').getAttribute('data-open')).toBe('true');
    fireEvent.click(screen.getByLabelText('Разделы панели'));
    fireEvent.click(within(screen.getByRole('menu')).getByText('Транскрибация'));
    expect(screen.queryByTestId('transcription-section')).toBeNull();
  });

  it('Skills и конструктор взаимно исключают друг друга', () => {
    render(<RightBar {...makeProps({ isDarkMode: false })} />);
    const switchTo = (label: string) => {
      fireEvent.click(screen.getByLabelText('Разделы панели'));
      fireEvent.click(within(screen.getByRole('menu')).getByText(label));
    };
    fireEvent.click(screen.getByText('Skills'));
    expect(screen.getByTestId('skills-panel').getAttribute('data-open')).toBe('true');
    expect(screen.queryByTestId('constructor-panel')).toBeNull();

    switchTo('Конструктор агента');
    expect(screen.getByTestId('constructor-panel').getAttribute('data-dark')).toBe('false');
    expect(screen.getByTestId('constructor-panel').getAttribute('data-open')).toBe('true');
    expect(screen.queryByTestId('skills-panel')).toBeNull();

    switchTo('Skills');
    expect(screen.getByTestId('skills-panel')).toBeTruthy();
    expect(screen.queryByTestId('constructor-panel')).toBeNull();

    // повторный выбор того же раздела в меню закрывает его
    switchTo('Skills');
    expect(screen.queryByTestId('skills-panel')).toBeNull();
    fireEvent.click(screen.getAllByText('Конструктор агента')[0]);
    switchTo('Конструктор агента');
    expect(screen.queryByTestId('constructor-panel')).toBeNull();
  });

  it('без права на агентов пункт и панель конструктора скрыты', () => {
    mockCanUseAgents = false;
    render(<RightBar {...makeProps()} />);
    expect(screen.queryByText('Конструктор агента')).toBeNull();
  });

  it('потеря права на агентов закрывает уже открытый конструктор', () => {
    const props = makeProps();
    const { rerender } = render(<RightBar {...props} />);
    fireEvent.click(screen.getByText('Конструктор агента'));
    expect(screen.getByTestId('constructor-panel')).toBeTruthy();
    mockCanUseAgents = false;
    rerender(<RightBar {...props} />);
    expect(screen.queryByTestId('constructor-panel')).toBeNull();
    mockCanUseAgents = true;
    rerender(<RightBar {...props} />);
    expect(screen.queryByTestId('constructor-panel')).toBeNull();
  });
});

describe('RightBar: свёрнутая рейка', () => {
  it('показывает иконки и кнопки «Открыть панель» / «Скрыть панель»', () => {
    const props = makeProps({ open: false });
    render(<RightBar {...props} />);
    expect(screen.getByTestId('gallery-collapsed')).toBeTruthy();
    expect(screen.queryByTestId('transcription-section')).toBeNull();
    const buttons = screen.getAllByRole('button');
    // [открыть, транскрибация, skills, базы знаний, конструктор, скрыть]
    expect(buttons).toHaveLength(6);
    fireEvent.click(buttons[0]);
    expect(props.onToggleOpen).toHaveBeenCalledTimes(1);
    fireEvent.click(buttons[5]);
    expect(props.onHide).toHaveBeenCalledTimes(1);
  });

  it('иконка транскрибации раскрывает панель, а после раскрытия секция уже открыта', () => {
    const props = makeProps({ open: false });
    const { rerender } = render(<RightBar {...props} />);
    fireEvent.click(screen.getAllByRole('button')[1]);
    expect(props.setOpen).toHaveBeenCalledWith(true);
    rerender(<RightBar {...props} open />);
    expect(screen.getByTestId('transcription-section').getAttribute('data-open')).toBe('true');
  });

  it('иконка Skills раскрывает панель (setHidden(false), setOpen(true))', () => {
    const props = makeProps({ open: false });
    render(<RightBar {...props} />);
    fireEvent.click(screen.getAllByRole('button')[2]);
    expect(props.setHidden).toHaveBeenCalledWith(false);
    expect(props.setOpen).toHaveBeenCalledWith(true);
  });

  it('иконка конструктора агента вызывает setOpen(true)', () => {
    const props = makeProps({ open: false });
    render(<RightBar {...props} />);
    fireEvent.click(screen.getAllByRole('button')[4]);
    expect(props.setOpen).toHaveBeenCalledWith(true);
  });

  it('без права на агентов в рейке нет иконки конструктора', () => {
    mockCanUseAgents = false;
    render(<RightBar {...makeProps({ open: false })} />);
    expect(screen.getAllByRole('button')).toHaveLength(5);
  });
});

describe('RightBar: внешние события и хуки открытия', () => {
  it('ASTRA_OPEN_AGENT_CONSTRUCTOR открывает конструктор и раскрывает панель', () => {
    const props = makeProps();
    render(<RightBar {...props} />);
    act(() => {
      window.dispatchEvent(new Event(ASTRA_OPEN_AGENT_CONSTRUCTOR));
    });
    expect(props.setHidden).toHaveBeenCalledWith(false);
    expect(props.setOpen).toHaveBeenCalledWith(true);
    expect(screen.getByTestId('constructor-panel')).toBeTruthy();
  });

  it('ASTRA_OPEN_AGENT_CONSTRUCTOR без права на агентов ничего не открывает', () => {
    mockCanUseAgents = false;
    const props = makeProps();
    render(<RightBar {...props} />);
    act(() => {
      window.dispatchEvent(new Event(ASTRA_OPEN_AGENT_CONSTRUCTOR));
    });
    expect(props.setOpen).not.toHaveBeenCalled();
    expect(screen.queryByTestId('constructor-panel')).toBeNull();
  });

  it('ASTRA_OPEN_SKILLS_SIDEBAR открывает Skills и закрывает конструктор', () => {
    const props = makeProps();
    render(<RightBar {...props} />);
    fireEvent.click(screen.getByText('Конструктор агента'));
    act(() => {
      window.dispatchEvent(new Event(ASTRA_OPEN_SKILLS_SIDEBAR));
    });
    expect(screen.getByTestId('skills-panel')).toBeTruthy();
    expect(screen.queryByTestId('constructor-panel')).toBeNull();
    expect(props.setOpen).toHaveBeenCalledWith(true);
  });

  it('ASTRA_OPEN_TRANSCRIPTION_SIDEBAR раскрывает секцию транскрибации', () => {
    const props = makeProps();
    render(<RightBar {...props} />);
    act(() => {
      window.dispatchEvent(new Event(ASTRA_OPEN_TRANSCRIPTION_SIDEBAR));
    });
    expect(props.setHidden).toHaveBeenCalledWith(false);
    expect(props.setOpen).toHaveBeenCalledWith(true);
    expect(screen.getByTestId('transcription-section').getAttribute('data-open')).toBe('true');
  });

  it('обработчики событий снимаются при размонтировании', () => {
    const props = makeProps();
    const { unmount } = render(<RightBar {...props} />);
    unmount();
    act(() => {
      window.dispatchEvent(new Event(ASTRA_OPEN_SKILLS_SIDEBAR));
    });
    expect(props.setOpen).not.toHaveBeenCalled();
  });

  it('колбэки, переданные в usePending*Open, открывают соответствующие панели', () => {
    const props = makeProps();
    render(<RightBar {...props} />);
    act(() => capturedOpenSkills!());
    expect(screen.getByTestId('skills-panel')).toBeTruthy();
    act(() => capturedOpenConstructor!());
    expect(screen.getByTestId('constructor-panel')).toBeTruthy();
    expect(screen.queryByTestId('skills-panel')).toBeNull();
  });
});

describe('RightBar: цвет панели', () => {
  it('легаси-значение цвета по умолчанию в localStorage сбрасывается', () => {
    localStorage.setItem('sidebar_panel_color', '#F7F7F9');
    render(<RightBar {...makeProps()} />);
    expect(localStorage.getItem('sidebar_panel_color')).toBe('');
  });

  it('другое пользовательское значение цвета не трогается', () => {
    localStorage.setItem('sidebar_panel_color', '#112233');
    render(<RightBar {...makeProps()} />);
    expect(localStorage.getItem('sidebar_panel_color')).toBe('#112233');
  });

  it('событие sidebarColorChanged и storage пересчитывают фон без ошибок', () => {
    render(<RightBar {...makeProps()} />);
    localStorage.setItem('sidebar_panel_color', '#ffffff');
    act(() => {
      window.dispatchEvent(new Event('sidebarColorChanged'));
      window.dispatchEvent(new Event('storage'));
    });
    expect(screen.getByText('Skills')).toBeTruthy();
  });
});
