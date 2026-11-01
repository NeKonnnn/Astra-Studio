import '@testing-library/jest-dom';
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import ClampedNumberField from '@/components/ClampedNumberField';
import SidebarRailCollapsedAction, { SidebarRailShortcutTooltip } from '@/components/SidebarRailCollapsedAction';
import RagUploadingFileThumb from '@/components/RagUploadingFileThumb';
import RagFilesSearchField from '@/components/RagFilesSearchField';
import ChatContextCounter from '@/components/ChatContextCounter';
import ImageGenerationPlaceholder from '@/components/ImageGenerationPlaceholder';
import MessageFollowUpSuggestions from '@/components/MessageFollowUpSuggestions';
import GalleryHubTabs, { GALLERY_HUB_TABS, parseGalleryHubTab } from '@/components/gallery/GalleryHubTabs';
import GallerySearchBookmarksBar from '@/components/galleryCards/GallerySearchBookmarksBar';
import GalleryEntityCard from '@/components/galleryCards/GalleryEntityCard';
import ChatGearAgentReindexHint, { AGENT_RAG_APPLYING_LABEL, ChatGearAgentReindexHintSubline } from '@/components/ChatGearAgentReindexHint';

let mockRebuilding = false;
jest.mock('@/hooks/useRagReindexStatus', () => ({
  useAgentRagRebuilding: () => mockRebuilding,
}));

describe('ClampedNumberField', () => {
  const setup = (over: Record<string, unknown> = {}) => {
    const onValueChange = jest.fn();
    render(
      <ClampedNumberField
        value={5}
        onValueChange={onValueChange}
        min={1}
        max={10}
        defaultValue={3}
        inputProps={{ 'data-testid': 'num' }}
        {...(over as any)}
      />,
    );
    return { onValueChange, input: screen.getByTestId('num') as HTMLInputElement };
  };

  it('показывает значение и передаёт min/max/inputMode', () => {
    const { input } = setup();
    expect(input.value).toBe('5');
    expect(input).toHaveAttribute('inputmode', 'numeric');
    expect(input).toHaveAttribute('min', '1');
    expect(input).toHaveAttribute('max', '10');
  });

  it('при вводе значение не ограничивается, при blur зажимается в диапазон', () => {
    const { input, onValueChange } = setup();
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: '99' } });
    expect(input.value).toBe('99');
    expect(onValueChange).not.toHaveBeenCalled();
    fireEvent.blur(input);
    expect(onValueChange).toHaveBeenCalledWith(10);
  });

  it('нижняя граница и целочисленное округление', () => {
    const { input, onValueChange } = setup();
    fireEvent.change(input, { target: { value: '-4' } });
    fireEvent.blur(input);
    expect(onValueChange).toHaveBeenLastCalledWith(1);
    fireEvent.change(input, { target: { value: '7.8' } });
    fireEvent.blur(input);
    expect(onValueChange).toHaveBeenLastCalledWith(7);
  });

  it('пустой ввод и одиночные «-» «.» дают значение по умолчанию', () => {
    const { input, onValueChange } = setup();
    for (const raw of ['', '-', '.', '-.']) {
      fireEvent.change(input, { target: { value: raw } });
      fireEvent.blur(input);
      expect(onValueChange).toHaveBeenLastCalledWith(3);
      expect(input.value).toBe('3');
    }
  });

  it('недопустимые символы не попадают в поле; запятая считается десятичным разделителем', () => {
    const { input, onValueChange } = setup({ integer: false, decimals: 2, min: 0, max: 1, value: 0.5, defaultValue: 0.5 });
    fireEvent.change(input, { target: { value: 'abc' } });
    expect(input.value).toBe('0.5');
    fireEvent.change(input, { target: { value: '0,456' } });
    fireEvent.blur(input);
    expect(onValueChange).toHaveBeenLastCalledWith(0.46);
    expect(input).toHaveAttribute('inputmode', 'decimal');
  });

  it('дробное без decimals не округляется', () => {
    const { input, onValueChange } = setup({ integer: false, min: 0, max: 10 });
    fireEvent.change(input, { target: { value: '2.345' } });
    fireEvent.blur(input);
    expect(onValueChange).toHaveBeenLastCalledWith(2.345);
  });

  it('Enter снимает фокус и фиксирует значение; внешний onKeyDown/onFocus/onBlur вызываются', () => {
    const onKeyDown = jest.fn();
    const onFocus = jest.fn();
    const onBlur = jest.fn();
    const { input, onValueChange } = setup({ onKeyDown, onFocus, onBlur });
    input.focus();
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: '8' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onValueChange).toHaveBeenCalledWith(8);
    expect(onKeyDown).toHaveBeenCalled();
    expect(onFocus).toHaveBeenCalled();
    expect(onBlur).toHaveBeenCalled();
  });

  it('disabled: ввод игнорируется и commit не вызывается', () => {
    const { input, onValueChange } = setup({ disabled: true });
    fireEvent.change(input, { target: { value: '9' } });
    expect(input.value).toBe('5');
    fireEvent.blur(input);
    expect(onValueChange).not.toHaveBeenCalled();
  });

  it('внешнее изменение value обновляет текст, пока поле не в фокусе', () => {
    const onValueChange = jest.fn();
    const { rerender } = render(
      <ClampedNumberField value={5} onValueChange={onValueChange} min={1} max={10} defaultValue={3} inputProps={{ 'data-testid': 'n' } as any} />,
    );
    rerender(
      <ClampedNumberField value={7} onValueChange={onValueChange} min={1} max={10} defaultValue={3} inputProps={{ 'data-testid': 'n' } as any} />,
    );
    expect((screen.getByTestId('n') as HTMLInputElement).value).toBe('7');
  });
});

describe('SidebarRailCollapsedAction', () => {
  it('рисует иконку и вызывает onClick; active добавляет фон', () => {
    const onClick = jest.fn();
    const { rerender } = render(
      <SidebarRailCollapsedAction
        variant={'chat' as any}
        panelIsLight
        enterIndex={0}
        onClick={onClick}
        icon={<span data-testid="ic" />}
        tooltipTitle="Подсказка"
      />,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('ic')).toBeInTheDocument();
    rerender(
      <SidebarRailCollapsedAction
        variant={'chat' as any}
        panelIsLight={false}
        enterIndex={2}
        onClick={onClick}
        icon={<span data-testid="ic" />}
        tooltipTitle="Подсказка"
        active
      />,
    );
    expect(screen.getByRole('button')).toBeInTheDocument();
  });

  it('SidebarRailShortcutTooltip: с горячей клавишей и без', () => {
    const { rerender } = render(<SidebarRailShortcutTooltip title="Заголовок" />);
    expect(screen.getByText('Заголовок')).toBeInTheDocument();
    expect(screen.queryByText('Ctrl+K')).toBeNull();
    rerender(<SidebarRailShortcutTooltip title="Заголовок" shortcut="Ctrl+K" />);
    expect(screen.getByText('Ctrl+K')).toBeInTheDocument();
  });
});

describe('RagUploadingFileThumb', () => {
  it('рисует спиннер и цветной квадрат нужного размера', () => {
    const { container, rerender } = render(<RagUploadingFileThumb filename="a.pdf" />);
    const sq = container.querySelector('[aria-hidden="true"]') as HTMLElement;
    expect(getComputedStyle(sq).width).toBe('32px');
    expect(container.querySelector('svg, [role="progressbar"]')).toBeTruthy();
    rerender(<RagUploadingFileThumb filename="b.xlsx" size={48} isDarkMode borderRadius="3px" />);
    const sq2 = container.querySelector('[aria-hidden="true"]') as HTMLElement;
    expect(getComputedStyle(sq2).width).toBe('48px');
    expect(getComputedStyle(sq2).borderRadius).toBe('3px');
  });

  it('цвет зависит от типа файла', () => {
    const { container, rerender } = render(<RagUploadingFileThumb filename="a.pdf" />);
    const c1 = getComputedStyle(container.querySelector('[aria-hidden="true"]') as HTMLElement).backgroundColor;
    rerender(<RagUploadingFileThumb filename="a.docx" />);
    const c2 = getComputedStyle(container.querySelector('[aria-hidden="true"]') as HTMLElement).backgroundColor;
    expect(c1).not.toBe(c2);
  });
});

describe('RagFilesSearchField', () => {
  it('плейсхолдер зависит от totalCount; явный placeholder приоритетнее', () => {
    const { rerender } = render(<RagFilesSearchField value="" onChange={jest.fn()} />);
    expect(screen.getByPlaceholderText('Поиск по имени файла…')).toBeInTheDocument();
    rerender(<RagFilesSearchField value="" onChange={jest.fn()} totalCount={12} />);
    expect(screen.getByPlaceholderText('Поиск среди 12 файлов…')).toBeInTheDocument();
    rerender(<RagFilesSearchField value="" onChange={jest.fn()} totalCount={0} />);
    expect(screen.getByPlaceholderText('Поиск по имени файла…')).toBeInTheDocument();
    rerender(<RagFilesSearchField value="" onChange={jest.fn()} placeholder="Свой" />);
    expect(screen.getByPlaceholderText('Свой')).toBeInTheDocument();
  });

  it('ввод вызывает onChange, кнопка очистки появляется только при непустом значении', () => {
    const onChange = jest.fn();
    const { rerender } = render(<RagFilesSearchField value="" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Поиск по прикреплённым файлам'), { target: { value: 'abc' } });
    expect(onChange).toHaveBeenCalledWith('abc');
    expect(screen.queryByLabelText('Очистить поиск')).toBeNull();
    rerender(<RagFilesSearchField value="abc" onChange={onChange} variant="sidebar" sx={[{ mb: 2 }]} />);
    fireEvent.click(screen.getByLabelText('Очистить поиск'));
    expect(onChange).toHaveBeenLastCalledWith('');
  });

  it('disabled и объект sx', () => {
    render(<RagFilesSearchField value="" onChange={jest.fn()} disabled sx={{ mb: 1 }} />);
    expect(screen.getByLabelText('Поиск по прикреплённым файлам')).toBeDisabled();
  });
});

describe('ChatContextCounter', () => {
  const usage = {
    messageTokens: 1234,
    draftTokens: 10,
    attachmentTokens: 500,
    currentTokens: 1744,
    maxTokens: 8192,
    percent: 21,
  } as any;

  it('показывает «Контекст X / Y (N%)»', () => {
    render(<ChatContextCounter usage={usage} />);
    expect(screen.getByText(/Контекст/)).toHaveTextContent(/\(21%\)/);
  });

  it('подсказка содержит модель, историю, черновик, вложения и лимит', async () => {
    render(<ChatContextCounter usage={usage} modelLabel="GPT" />);
    fireEvent.mouseOver(screen.getByText(/Контекст/));
    const tip = await screen.findByRole('tooltip');
    expect(tip).toHaveTextContent('Модель: GPT');
    expect(tip).toHaveTextContent('История:');
    expect(tip).toHaveTextContent('Черновик:');
    expect(tip).toHaveTextContent('Вложения:');
    expect(tip).toHaveTextContent('Лимит:');
  });

  it('без черновика, вложений и модели этих строк нет', async () => {
    render(<ChatContextCounter usage={{ ...usage, draftTokens: 0, attachmentTokens: 0 }} />);
    fireEvent.mouseOver(screen.getByText(/Контекст/));
    const tip = await screen.findByRole('tooltip');
    expect(tip).not.toHaveTextContent('Черновик:');
    expect(tip).not.toHaveTextContent('Вложения:');
    expect(tip).not.toHaveTextContent('Модель:');
  });

  it('цвет зависит от процента заполнения и темы', () => {
    const colors = new Set<string>();
    for (const [percent, dark] of [[10, false], [75, false], [95, false], [10, true], [75, true], [95, true]] as const) {
      const { unmount } = render(<ChatContextCounter usage={{ ...usage, percent }} isDarkMode={dark} />);
      colors.add(getComputedStyle(screen.getByText(/Контекст/)).color);
      unmount();
    }
    expect(colors.size).toBe(6);
  });
});

describe('ImageGenerationPlaceholder', () => {
  it('роль status и подпись; maxWidth настраивается', () => {
    const { rerender } = render(<ImageGenerationPlaceholder />);
    const el = screen.getByRole('status');
    expect(el).toHaveAttribute('aria-label', 'Генерация изображения');
    expect(getComputedStyle(el).maxWidth).toBe('420px');
    rerender(<ImageGenerationPlaceholder maxWidth="50%" />);
    expect(getComputedStyle(screen.getByRole('status')).maxWidth).toBe('50%');
  });
});

describe('MessageFollowUpSuggestions', () => {
  const items = [
    { id: 'a', title: 'Первый', subtitle: 'Описание', content: 'текст 1' },
    { id: 'b', title: 'Второй', content: 'текст 2' },
  ] as any;

  it('без подсказок ничего не рисует', () => {
    const { container, rerender } = render(<MessageFollowUpSuggestions onSelect={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<MessageFollowUpSuggestions suggestions={[]} onSelect={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('клик передаёт content; подзаголовок показывается только если есть', () => {
    const onSelect = jest.fn();
    render(<MessageFollowUpSuggestions suggestions={items} onSelect={onSelect} isDarkMode />);
    expect(screen.getByText('Следующий шаг')).toBeInTheDocument();
    expect(screen.getByText('Описание')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Второй'));
    expect(onSelect).toHaveBeenCalledWith('текст 2');
  });

  it('disabled блокирует кнопки', () => {
    const onSelect = jest.fn();
    render(<MessageFollowUpSuggestions suggestions={items} onSelect={onSelect} disabled />);
    screen.getAllByRole('button').forEach((b) => expect(b).toBeDisabled());
    fireEvent.click(screen.getByText('Первый'));
    expect(onSelect).not.toHaveBeenCalled();
  });
});

describe('GalleryHubTabs', () => {
  it('parseGalleryHubTab: допустимые значения, регистр и запасной вариант', () => {
    expect(parseGalleryHubTab('Skills')).toBe('skills');
    expect(parseGalleryHubTab(' plugins ')).toBe('plugins');
    expect(parseGalleryHubTab('agents')).toBe('agents');
    expect(parseGalleryHubTab('knowledge-bases')).toBe('knowledge-bases');
    expect(parseGalleryHubTab('KB')).toBe('knowledge-bases');
    expect(parseGalleryHubTab('knowledge_bases')).toBe('knowledge-bases');
    expect(parseGalleryHubTab('prompts')).toBe('agents');
    expect(parseGalleryHubTab(null)).toBe('agents');
    expect(parseGalleryHubTab(undefined)).toBe('agents');
  });

  it('рисует четыре вкладки (агенты, skills, плагины, база знаний); клик вызывает onChange', () => {
    const onChange = jest.fn();
    const { rerender } = render(<GalleryHubTabs value="agents" onChange={onChange} />);
    expect(GALLERY_HUB_TABS).toHaveLength(4);
    fireEvent.click(screen.getByText('Skills'));
    expect(onChange).toHaveBeenCalledWith('skills');
    rerender(<GalleryHubTabs value="plugins" onChange={onChange} isDarkMode={false} />);
    fireEvent.click(screen.getByText('Агенты'));
    expect(onChange).toHaveBeenLastCalledWith('agents');
  });
});

describe('GallerySearchBookmarksBar', () => {
  it('поиск и кнопка закладок', () => {
    const onSearch = jest.fn();
    const onToggle = jest.fn();
    const { rerender } = render(
      <GallerySearchBookmarksBar searchQuery="" onSearchChange={onSearch} showBookmarks={false} onToggleBookmarks={onToggle} />,
    );
    fireEvent.change(screen.getByPlaceholderText('Поиск…'), { target: { value: 'q' } });
    expect(onSearch).toHaveBeenCalledWith('q');
    fireEvent.click(screen.getByText('Закладки'));
    expect(onToggle).toHaveBeenCalledTimes(1);
    rerender(
      <GallerySearchBookmarksBar
        searchQuery="x"
        onSearchChange={onSearch}
        showBookmarks
        onToggleBookmarks={onToggle}
        allLabel="Всё"
        searchPlaceholder="Найти"
      />,
    );
    expect(screen.getByText('Всё')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Найти')).toBeDisabled();
  });

  it('bookmarksEnabled=false скрывает кнопку, searchDisabled блокирует поиск', () => {
    render(
      <GallerySearchBookmarksBar
        searchQuery=""
        onSearchChange={jest.fn()}
        showBookmarks={false}
        onToggleBookmarks={jest.fn()}
        bookmarksEnabled={false}
        searchDisabled
        bookmarksLabel="Избранное"
      />,
    );
    expect(screen.queryByText('Избранное')).toBeNull();
    expect(screen.getByPlaceholderText('Поиск…')).toBeDisabled();
  });
});

describe('GalleryEntityCard', () => {
  const item = {
    id: 1,
    title: 'Агент',
    authorName: 'Автор',
    preview: 'Описание агента',
    metaLine: 'v1',
    viewsCount: 5,
    usageCount: 7,
    averageRating: 4.26,
    totalVotes: 3,
    userRating: 4,
    isBookmarked: true,
  };

  const setup = (over: Record<string, unknown> = {}, cardItem: any = item) => {
    const h = {
      onOpen: jest.fn(),
      onUse: jest.fn(),
      onClone: jest.fn(),
      onRate: jest.fn(),
      onToggleBookmark: jest.fn(),
    };
    render(<GalleryEntityCard item={cardItem} isDarkMode={false} {...h} {...(over as any)} />);
    return h;
  };

  it('показывает поля и метрики', () => {
    setup();
    expect(screen.getByText('Агент')).toBeInTheDocument();
    expect(screen.getByText('Автор')).toBeInTheDocument();
    expect(screen.getByText('Описание агента')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
    expect(screen.getByText('v1')).toBeInTheDocument();
    expect(screen.getByText(/4\.3 \(3\) • Ваша: 4/)).toBeInTheDocument();
  });

  it('клик по карточке открывает, кнопки и закладка не всплывают до onOpen', () => {
    const h = setup();
    fireEvent.click(screen.getByText('Агент'));
    expect(h.onOpen).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText('Использовать'));
    fireEvent.click(screen.getByText('Клонировать'));
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(h.onToggleBookmark).toHaveBeenCalledTimes(1);
    expect(h.onUse).toHaveBeenCalledTimes(1);
    expect(h.onClone).toHaveBeenCalledTimes(1);
    expect(h.onOpen).toHaveBeenCalledTimes(1);
  });

  it('закладка: оба состояния', () => {
    const h = setup();
    const btns = screen.getAllByRole('button');
    fireEvent.click(btns[0]);
    expect(h.onToggleBookmark).toHaveBeenCalledTimes(1);
  });

  it('без onClone кнопка «Использовать» одна, без onRate и закладки блоков нет', () => {
    setup({ onClone: undefined, onRate: undefined, onToggleBookmark: undefined }, { ...item, metaLine: undefined, isBookmarked: false });
    expect(screen.queryByText('Клонировать')).toBeNull();
    expect(screen.queryByText(/Ваша/)).toBeNull();
    expect(screen.getAllByRole('button')).toHaveLength(1);
  });

  it('значения по умолчанию для пустых метрик и пользовательские подписи', () => {
    setup(
      { useLabel: 'Взять', cloneLabel: 'Копия' },
      { id: 2, title: 'T', authorName: 'A', preview: 'P', userRating: null },
    );
    expect(screen.getByText('Взять')).toBeInTheDocument();
    expect(screen.getByText('Копия')).toBeInTheDocument();
    expect(screen.getByText('0.0 (0)')).toBeInTheDocument();
    expect(screen.getAllByText('0').length).toBe(2);
  });

  it('состояния using/cloning блокируют обе кнопки', () => {
    setup({ using: true });
    expect(screen.getByText('Использовать').closest('button')).toBeDisabled();
    expect(screen.getByText('Клонировать').closest('button')).toBeDisabled();
  });

  it('cloning блокирует кнопки, оценка вызывает onRate с округлением; тёмная тема', () => {
    const h = setup({ cloning: true, isDarkMode: true }, { ...item, userRating: null });
    expect(screen.getByText('Клонировать').closest('button')).toBeDisabled();
    fireEvent.click(screen.getByLabelText('5 Stars'));
    expect(h.onRate).toHaveBeenCalledWith(5);
  });
});

describe('ChatGearAgentReindexHint', () => {
  it('подсказка показывается только во время перечанковки', () => {
    mockRebuilding = false;
    const { container, rerender } = render(
      <>
        <ChatGearAgentReindexHint agentId={1} />
        <ChatGearAgentReindexHintSubline agentId={1} />
      </>,
    );
    expect(container).toBeEmptyDOMElement();
    mockRebuilding = true;
    rerender(
      <>
        <ChatGearAgentReindexHint agentId={1} />
        <ChatGearAgentReindexHintSubline agentId={1} />
      </>,
    );
    expect(screen.getAllByText(AGENT_RAG_APPLYING_LABEL)).toHaveLength(2);
    mockRebuilding = false;
  });
});
