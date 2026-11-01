import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import ChatInputBar, { ChatInputBarProps } from '@/components/ChatInputBar';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
  API_ENDPOINTS: { VOICE_RECOGNIZE: '/voice/recognize' },
}));
jest.mock('@/utils/dictationAudio', () => ({
  prepareSegmentForStt: jest.fn(async (b: Blob) => b),
}));
jest.mock('@/components/InlineAttachmentsList', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="inline-list" data-variant={p.variant}>
      {p.files.map((f: any, i: number) => (
        <span key={i} data-testid="inline-file" data-type={f.contentType} data-src={f.imageSrc ?? ''}>
          {f.name}
          <button onClick={() => p.onRemove?.(i)}>rm-inline-{i}</button>
        </span>
      ))}
    </div>
  ),
}));
jest.mock('@/components/InlineDocAttachmentChip', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="uploading-chip" data-uploading={String(Boolean(p.uploading))}>
      {p.name}|{p.sizeLabel}
      {p.leading}
      <button disabled={p.removeDisabled} onClick={p.onRemove}>chip-remove</button>
    </div>
  ),
  InlineAttachUploadSpinner: () => <i data-testid="upload-spinner" />,
  InlineDocUploadingThumb: (p: any) => <i data-testid="doc-thumb">{p.filename}</i>,
}));
jest.mock('@/components/chat/SkillMentionAutocomplete', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="skill-ac" data-open={String(p.open)} data-query={p.query}>
      <button onClick={() => p.onSelect({ id: 1, slug: 'sum', name: 'Саммари' })}>pick-skill</button>
    </div>
  ),
}));
jest.mock('@/components/chat/TagMentionAutocomplete', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="tag-ac" data-open={String(p.open)} data-query={p.query}>
      <button onClick={() => p.onSelect({ id: 7, name: 'Финансы' })}>pick-tag</button>
    </div>
  ),
}));

function setup(over: Partial<ChatInputBarProps> = {}) {
  const props: ChatInputBarProps = {
    value: '',
    onChange: jest.fn(),
    onKeyPress: jest.fn(),
    ...over,
  };
  const utils = render(<ChatInputBar {...props} />);
  return { props, ...utils };
}

function textarea(): HTMLTextAreaElement {
  return document.querySelector('textarea:not([aria-hidden])') as HTMLTextAreaElement;
}

/** Подсказка MUI вешает aria-label на обёртку <span>, если кнопка внутри неё — берём саму кнопку. */
function btn(label: string): HTMLElement {
  const el = screen.getByLabelText(label);
  return el.tagName === 'BUTTON' ? el : (el.querySelector('button') as HTMLElement);
}

function rowDirection(): string {
  const root = (textarea().closest('.MuiFormControl-root') || textarea().closest('.MuiInputBase-root')) as HTMLElement;
  return getComputedStyle(root.parentElement!).flexDirection;
}

describe('ChatInputBar: поле ввода', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('показывает placeholder по умолчанию и значение', () => {
    setup({ value: 'привет' });
    expect(textarea()).toHaveValue('привет');
    expect(textarea()).toHaveAttribute('placeholder', 'Чем я могу помочь вам сегодня?');
  });

  it('пользовательский placeholder и блокировка ввода', () => {
    setup({ placeholder: 'Пишите', inputDisabled: true });
    expect(textarea()).toBeDisabled();
    expect(textarea()).toHaveAttribute('placeholder', 'Пишите');
  });

  it('ввод вызывает onChange с новым значением', () => {
    const { props } = setup();
    fireEvent.change(textarea(), { target: { value: 'abc' } });
    expect(props.onChange).toHaveBeenCalledWith('abc');
  });

  it('onKeyPress и onPaste пробрасываются в поле', () => {
    const onPaste = jest.fn();
    const { props } = setup({ onPaste });
    fireEvent.keyPress(textarea(), { key: 'Enter', charCode: 13 });
    expect(props.onKeyPress).toHaveBeenCalledTimes(1);
    fireEvent.paste(textarea(), { clipboardData: { getData: () => 'x', files: [] } });
    expect(onPaste).toHaveBeenCalledTimes(1);
  });

  it('короткий текст — одна строка (row), длинный (>100) или с переводом строки — колонка', () => {
    const { rerender, props } = setup({ value: 'коротко' });
    expect(rowDirection()).toBe('row');
    rerender(<ChatInputBar {...props} value={'x'.repeat(101)} />);
    expect(rowDirection()).toBe('column');
    rerender(<ChatInputBar {...props} value={'x'.repeat(100)} />);
    expect(rowDirection()).toBe('row');
    rerender(<ChatInputBar {...props} value={'a\nb'} />);
    expect(rowDirection()).toBe('column');
  });

  it('classic: двухстрочный минимум и тулбар снизу, независимо от длины текста', () => {
    const onSendClick = jest.fn();
    setup({ styleVariant: 'classic', value: 'x', onSendClick });
    expect(textarea()).toBeInTheDocument();
    fireEvent.click(btn('Отправить'));
    expect(onSendClick).toHaveBeenCalledTimes(1);
    expect(textarea().closest('.MuiInputBase-root')).toHaveClass('MuiInputBase-multiline');
  });

  it('toolsMenuAnchorRef получает корневой блок «пилюли»', () => {
    const ref = React.createRef<HTMLDivElement>();
    setup({ toolsMenuAnchorRef: ref });
    expect(ref.current).toBeInstanceOf(HTMLDivElement);
    expect(ref.current!.contains(textarea())).toBe(true);
  });

  it('inputRef указывает на textarea', () => {
    const ref = React.createRef<HTMLTextAreaElement>();
    setup({ inputRef: ref as any });
    expect(ref.current).toBe(textarea());
  });

  it('maxWidth и containerSx применяются к оболочке', () => {
    const ref = React.createRef<HTMLDivElement>();
    setup({ maxWidth: 480, containerSx: { marginTop: '13px' }, toolsMenuAnchorRef: ref });
    const st = getComputedStyle(ref.current!);
    expect(st.maxWidth).toBe('480px');
    expect(st.marginTop).toBe('13px');
  });
});

describe('ChatInputBar: оформление оболочки', () => {
  const shell = () => {
    const ref = React.createRef<HTMLDivElement>();
    return ref;
  };

  beforeEach(() => localStorage.clear());

  it('контраст из localStorage ограничивается [20..100] и влияет на рамку', () => {
    localStorage.setItem('chat_input_contrast', '100');
    const ref = shell();
    setup({ toolsMenuAnchorRef: ref });
    expect(getComputedStyle(ref.current!).border).toContain('0.425');
  });

  it('значение контраста вне диапазона зажимается; нечисловое — по умолчанию', () => {
    localStorage.setItem('chat_input_contrast', '5');
    const a = shell();
    const first = setup({ toolsMenuAnchorRef: a });
    const borderMin = getComputedStyle(a.current!).border;
    first.unmount();
    localStorage.setItem('chat_input_contrast', 'abc');
    const b = shell();
    setup({ toolsMenuAnchorRef: b });
    expect(getComputedStyle(b.current!).border).toContain('0.1)');
    expect(borderMin).toContain('0.06');
  });

  it('solidWorkZoneBackground + пользовательский цвет поля задают фон оболочки', () => {
    localStorage.setItem('chat_input_color', '#123456');
    const ref = shell();
    setup({ solidWorkZoneBackground: true, toolsMenuAnchorRef: ref });
    expect(getComputedStyle(ref.current!).backgroundColor).toBe('rgb(18, 52, 86)');
  });

  it('solid + светлая/тёмная тема, compact/classic дают разные рамки', () => {
    const borders: string[] = [];
    for (const [isDarkMode, styleVariant] of [
      [false, 'compact'],
      [true, 'compact'],
      [false, 'classic'],
      [true, 'classic'],
    ] as const) {
      const ref = shell();
      const r = setup({ solidWorkZoneBackground: true, isDarkMode, styleVariant, toolsMenuAnchorRef: ref });
      borders.push(getComputedStyle(ref.current!).border);
      r.unmount();
    }
    expect(new Set(borders).size).toBeGreaterThan(1);
    expect(borders[2]).toContain('rgba(0, 0, 0, 0.12)');
    expect(borders[3]).toContain('rgba(255, 255, 255, 0.12)');
  });

  it('не-solid: classic рамка чуть контрастнее compact, тёмная тема — белая рамка', () => {
    const refA = shell();
    const a = setup({ styleVariant: 'classic', toolsMenuAnchorRef: refA });
    expect(getComputedStyle(refA.current!).border).toContain('rgba(0, 0, 0, 0.');
    a.unmount();
    const refB = shell();
    setup({ styleVariant: 'classic', isDarkMode: true, toolsMenuAnchorRef: refB });
    expect(getComputedStyle(refB.current!).border).toContain('rgba(255, 255, 255, 0.');
  });

  it('тёмная тема: тень оболочки без solid отличается от светлой', () => {
    const refA = shell();
    const a = setup({ isDarkMode: true, toolsMenuAnchorRef: refA });
    const dark = getComputedStyle(refA.current!).boxShadow;
    a.unmount();
    const refB = shell();
    setup({ isDarkMode: false, toolsMenuAnchorRef: refB });
    expect(dark).not.toBe(getComputedStyle(refB.current!).boxShadow);
  });

  it('событие interfaceSettingsChanged перечитывает цвет и контраст', () => {
    const ref = shell();
    setup({ solidWorkZoneBackground: true, toolsMenuAnchorRef: ref });
    localStorage.setItem('chat_input_color', '#ff0000');
    act(() => {
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });
    expect(getComputedStyle(ref.current!).backgroundColor).toBe('rgb(255, 0, 0)');
    localStorage.removeItem('chat_input_contrast');
    localStorage.setItem('chat_input_color', '');
    act(() => {
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });
    expect(getComputedStyle(ref.current!).backgroundColor).not.toBe('rgb(255, 0, 0)');
  });

  it('событие interfaceSettingsChanged с валидным контрастом применяет его', () => {
    const ref = shell();
    setup({ toolsMenuAnchorRef: ref });
    localStorage.setItem('chat_input_contrast', '100');
    act(() => {
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });
    expect(getComputedStyle(ref.current!).border).toContain('0.425');
  });
});

describe('ChatInputBar: кнопки', () => {
  it('кнопка вложения: подсказка, клик, блокировка при attachDisabled/isUploading', () => {
    const onAttachClick = jest.fn();
    const { rerender, props } = setup({ onAttachClick });
    fireEvent.click(screen.getByLabelText('Прикрепить файл к сообщению'));
    expect(onAttachClick).toHaveBeenCalledTimes(1);
    rerender(<ChatInputBar {...props} onAttachClick={onAttachClick} attachDisabled />);
    expect(screen.getByLabelText('Прикрепить файл к сообщению')).toBeDisabled();
    rerender(<ChatInputBar {...props} onAttachClick={onAttachClick} isUploading />);
    expect(screen.getByLabelText('Прикрепить файл к сообщению')).toBeDisabled();
  });

  it('кнопка вложения подсвечивается, если уже есть inline-файлы', () => {
    setup({ onAttachClick: jest.fn(), inlineFiles: [{ name: 'a.txt', contentType: 'text', content: 'x' }] });
    expect(getComputedStyle(screen.getByLabelText('Прикрепить файл к сообщению')).borderColor.replace(/\s/g, '')).toContain('rgba(91,105,255');
  });

  it('без обработчиков кнопок вложения/инструментов/отправки/голоса/отчёта нет', () => {
    setup();
    ['Прикрепить файл к сообщению', 'Инструменты', 'Отправить', 'Голосовой ввод', 'Сгенерировать отчет об уверенности', 'Прервать генерацию'].forEach(
      (l) => expect(screen.queryByLabelText(l)).toBeNull(),
    );
  });

  it('инструменты: клик передаёт событие; можно заблокировать', () => {
    const onSettingsClick = jest.fn();
    const { rerender, props } = setup({ onSettingsClick });
    fireEvent.click(btn('Инструменты'));
    expect(onSettingsClick).toHaveBeenCalledTimes(1);
    expect(onSettingsClick.mock.calls[0][0]).toHaveProperty('type', 'click');
    rerender(<ChatInputBar {...props} onSettingsClick={onSettingsClick} settingsDisabled />);
    expect(btn('Инструменты')).toBeDisabled();
  });

  it('кнопка отчёта требует и флаг, и обработчик', () => {
    const onReportClick = jest.fn();
    const { rerender, props } = setup({ onReportClick });
    expect(screen.queryByLabelText('Сгенерировать отчет об уверенности')).toBeNull();
    rerender(<ChatInputBar {...props} onReportClick={onReportClick} showReportButton />);
    fireEvent.click(screen.getByLabelText('Сгенерировать отчет об уверенности'));
    expect(onReportClick).toHaveBeenCalledTimes(1);
    rerender(<ChatInputBar {...props} onReportClick={onReportClick} showReportButton reportDisabled />);
    expect(screen.getByLabelText('Сгенерировать отчет об уверенности')).toBeDisabled();
    rerender(<ChatInputBar {...props} onReportClick={undefined} showReportButton />);
    expect(screen.queryByLabelText('Сгенерировать отчет об уверенности')).toBeNull();
  });

  it('отправка: клик, блокировка, спиннер при isSending', () => {
    const onSendClick = jest.fn();
    const { rerender, props } = setup({ onSendClick });
    fireEvent.click(btn('Отправить'));
    expect(onSendClick).toHaveBeenCalledTimes(1);
    rerender(<ChatInputBar {...props} onSendClick={onSendClick} sendDisabled />);
    expect(btn('Отправить')).toBeDisabled();
    expect(screen.queryByRole('progressbar')).toBeNull();
    rerender(<ChatInputBar {...props} onSendClick={onSendClick} isSending />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('при генерации вместо «Отправить» показывается «Прервать генерацию»', () => {
    const onStopClick = jest.fn();
    setup({ showStopButton: true, onStopClick, onSendClick: jest.fn() });
    expect(screen.queryByLabelText('Отправить')).toBeNull();
    fireEvent.click(screen.getByLabelText('Прервать генерацию'));
    expect(onStopClick).toHaveBeenCalledTimes(1);
  });

  it('showStopButton без onStopClick оставляет «Отправить»', () => {
    setup({ showStopButton: true, onSendClick: jest.fn() });
    expect(btn('Отправить')).toBeInTheDocument();
  });

  it('голосовой режим: кастомная подсказка, клик, блокировка', () => {
    const onVoiceClick = jest.fn();
    const { rerender, props } = setup({ onVoiceClick, voiceTooltip: 'Голос!' });
    fireEvent.click(btn('Голос!'));
    expect(onVoiceClick).toHaveBeenCalledTimes(1);
    rerender(<ChatInputBar {...props} onVoiceClick={onVoiceClick} voiceDisabled />);
    expect(btn('Голос!')).toBeDisabled();
  });

  it('libraryBadge, extraActions, contextCounter, inputSuggestions рендерятся (compact и classic)', () => {
    const extra = {
      libraryBadge: <i data-testid="badge" />,
      extraActions: <i data-testid="extra" />,
      contextCounter: <i data-testid="counter" />,
      inputSuggestions: <i data-testid="suggestions" />,
    };
    const a = setup(extra);
    ['badge', 'extra', 'counter', 'suggestions'].forEach((id) => expect(screen.getByTestId(id)).toBeInTheDocument());
    a.unmount();
    setup({ ...extra, styleVariant: 'classic' });
    ['badge', 'extra', 'counter', 'suggestions'].forEach((id) => expect(screen.getByTestId(id)).toBeInTheDocument());
  });

  it('в многострочном compact-режиме кнопки тоже доступны', () => {
    const onSendClick = jest.fn();
    setup({ value: 'a\nb', onSendClick, onAttachClick: jest.fn(), onSettingsClick: jest.fn() });
    fireEvent.click(btn('Отправить'));
    expect(onSendClick).toHaveBeenCalledTimes(1);
    expect(btn('Инструменты')).toBeInTheDocument();
  });
});

describe('ChatInputBar: вложения', () => {
  it('скрытый input файлов: accept по умолчанию, выбор файлов вызывает onFileSelect и сбрасывает значение', () => {
    const fileInputRef = React.createRef<HTMLInputElement>();
    const onFileSelect = jest.fn();
    setup({ fileInputRef, onFileSelect });
    const input = fileInputRef.current!;
    expect(input).toHaveAttribute('type', 'file');
    expect(input.getAttribute('accept')).toBeTruthy();
    const file = new File(['x'], 'a.txt', { type: 'text/plain' });
    fireEvent.change(input, { target: { files: [file] } });
    expect(onFileSelect).toHaveBeenCalledTimes(1);
    expect(onFileSelect.mock.calls[0][0][0]).toBe(file);
    expect(input.value).toBe('');
  });

  it('пустой выбор файлов или отсутствие onFileSelect не падают; accept переопределяется', () => {
    const fileInputRef = React.createRef<HTMLInputElement>();
    const onFileSelect = jest.fn();
    const a = setup({ fileInputRef, onFileSelect, accept: 'image/*' });
    expect(fileInputRef.current).toHaveAttribute('accept', 'image/*');
    fireEvent.change(fileInputRef.current!, { target: { files: [] } });
    expect(onFileSelect).not.toHaveBeenCalled();
    a.unmount();
    const ref2 = React.createRef<HTMLInputElement>();
    setup({ fileInputRef: ref2 });
    expect(() =>
      fireEvent.change(ref2.current!, { target: { files: [new File(['x'], 'b.txt')] } }),
    ).not.toThrow();
  });

  it('без fileInputRef скрытого input нет', () => {
    setup();
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });

  it('inline-файлы передаются в список (картинки — с data-src), удаление пробрасывается', () => {
    const onInlineFileRemove = jest.fn();
    setup({
      inlineFiles: [
        { name: 'a.txt', contentType: 'text', content: 'текст' },
        { name: 'p.png', contentType: 'image', content: 'data:image/png;base64,AAA', size: 10 },
      ],
      onInlineFileRemove,
    });
    const files = screen.getAllByTestId('inline-file');
    expect(files[0]).toHaveAttribute('data-src', '');
    expect(files[1]).toHaveAttribute('data-src', 'data:image/png;base64,AAA');
    expect(screen.getByTestId('inline-list')).toHaveAttribute('data-variant', 'input');
    fireEvent.click(screen.getByText('rm-inline-1'));
    expect(onInlineFileRemove).toHaveBeenCalledWith(1);
  });

  it('загруженные файлы: список с именами и удалением (нужен onFileRemove)', () => {
    const onFileRemove = jest.fn();
    const files = [
      { name: 'отчёт.pdf', type: 'application/pdf' },
      { name: 'таблица.xlsx', type: 'application/vnd.ms-excel.sheet' },
      { name: 'x.docx', type: 'application/msword' },
    ];
    const a = setup({ uploadedFiles: files, onFileRemove });
    expect(screen.getByText('отчёт.pdf')).toBeInTheDocument();
    expect(screen.getByText('таблица.xlsx')).toBeInTheDocument();
    expect(document.querySelectorAll('.file-attachment')).toHaveLength(3);
    expect(document.querySelector('[data-testid="PictureAsPdfIcon"]')).not.toBeNull();
    fireEvent.click(document.querySelectorAll('.file-attachment button')[1]);
    expect(onFileRemove).toHaveBeenCalledWith(files[1], 1);
    a.unmount();
    setup({ uploadedFiles: files });
    expect(document.querySelectorAll('.file-attachment')).toHaveLength(0);
  });

  it('файл без типа получает значок документа', () => {
    setup({ uploadedFiles: [{ name: 'a', type: undefined as unknown as string }], onFileRemove: jest.fn() });
    expect(document.querySelector('[data-testid="DescriptionIcon"]')).not.toBeNull();
  });

  it('идёт загрузка с превью: картинка и неактивная кнопка закрытия', () => {
    setup({ isUploading: true, uploadingFile: { name: 'фото.png', size: 1000, previewUrl: 'blob:preview' } });
    const img = document.querySelector('img[alt="фото.png"]') as HTMLImageElement;
    expect(img).toHaveAttribute('src', 'blob:preview');
    expect(screen.getByTestId('upload-spinner')).toBeInTheDocument();
  });

  it('идёт загрузка документа: чип «размер · Анализ...» и заблокированное удаление', () => {
    setup({ isUploading: true, uploadingFile: { name: 'док.pdf', size: 2048 } });
    const chip = screen.getByTestId('uploading-chip');
    expect(chip).toHaveAttribute('data-uploading', 'true');
    expect(chip.textContent).toContain('док.pdf|2');
    expect(chip.textContent).toContain('Анализ...');
    expect(screen.getByText('chip-remove')).toBeDisabled();
    expect(screen.getByTestId('doc-thumb')).toHaveTextContent('док.pdf');
    fireEvent.click(screen.getByText('chip-remove'));
  });

  it('загрузка без метаданных файла: имя «Файл» и только «Анализ...»', () => {
    setup({ isUploading: true, uploadingFile: null });
    expect(screen.getByTestId('uploading-chip').textContent).toContain('Файл|Анализ...');
  });

  it('classic: те же секции вложений', () => {
    setup({
      styleVariant: 'classic',
      inlineFiles: [{ name: 'a.txt', contentType: 'text', content: 'x' }],
      uploadedFiles: [{ name: 'f.pdf', type: 'pdf' }],
      onFileRemove: jest.fn(),
      isUploading: true,
    });
    expect(screen.getByTestId('inline-list')).toBeInTheDocument();
    expect(screen.getByText('f.pdf')).toBeInTheDocument();
    expect(screen.getByTestId('uploading-chip')).toBeInTheDocument();
  });
});

describe('ChatInputBar: упоминания skills и тегов', () => {
  function typeAt(value: string, cursor: number = value.length, onChange = jest.fn()) {
    const utils = setup({ value: '', onChange });
    fireEvent.change(textarea(), { target: { value } });
    utils.rerender(<ChatInputBar {...utils.props} value={value} onChange={onChange} />);
    const ta = textarea();
    ta.setSelectionRange(cursor, cursor);
    fireEvent.select(ta);
    return { ...utils, onChange };
  }

  it('по умолчанию оба автокомплита закрыты', () => {
    setup();
    expect(screen.getByTestId('skill-ac')).toHaveAttribute('data-open', 'false');
    expect(screen.getByTestId('tag-ac')).toHaveAttribute('data-open', 'false');
  });

  it('$ в позиции курсора открывает автокомплит skills с запросом', () => {
    typeAt('привет $su', 10);
    expect(screen.getByTestId('skill-ac')).toHaveAttribute('data-open', 'true');
    expect(screen.getByTestId('skill-ac')).toHaveAttribute('data-query', 'su');
    expect(screen.getByTestId('tag-ac')).toHaveAttribute('data-open', 'false');
  });

  it('# в позиции курсора открывает автокомплит тегов', () => {
    typeAt('тег #фин', 8);
    expect(screen.getByTestId('tag-ac')).toHaveAttribute('data-open', 'true');
    expect(screen.getByTestId('tag-ac')).toHaveAttribute('data-query', 'фин');
    expect(screen.getByTestId('skill-ac')).toHaveAttribute('data-open', 'false');
  });

  it('переключение с $ на # закрывает предыдущий автокомплит', () => {
    const { rerender, props } = typeAt('a $s', 4);
    expect(screen.getByTestId('skill-ac')).toHaveAttribute('data-open', 'true');
    rerender(<ChatInputBar {...props} value="a #t" />);
    const ta = textarea();
    ta.setSelectionRange(3, 3); // React пропускает select при неизменном выделении
    fireEvent.select(ta);
    ta.setSelectionRange(4, 4);
    fireEvent.select(ta);
    expect(screen.getByTestId('skill-ac')).toHaveAttribute('data-open', 'false');
    expect(screen.getByTestId('tag-ac')).toHaveAttribute('data-open', 'true');
  });

  it('обычный текст закрывает автокомплиты', () => {
    const { rerender, props } = typeAt('a $s', 4);
    rerender(<ChatInputBar {...props} value="a s" />);
    const ta = textarea();
    ta.setSelectionRange(3, 3);
    fireEvent.select(ta);
    expect(screen.getByTestId('skill-ac')).toHaveAttribute('data-open', 'false');
    expect(screen.getByTestId('tag-ac')).toHaveAttribute('data-open', 'false');
  });

  it('событие select (перемещение курсора) пересчитывает автокомплит', () => {
    setup({ value: 'x $ab' });
    const ta = textarea();
    ta.setSelectionRange(5, 5);
    fireEvent.select(ta);
    expect(screen.getByTestId('skill-ac')).toHaveAttribute('data-query', 'ab');
    ta.setSelectionRange(1, 1);
    fireEvent.select(ta);
    expect(screen.getByTestId('skill-ac')).toHaveAttribute('data-open', 'false');
  });

  it('select в classic-варианте работает так же', () => {
    setup({ value: 'x #ab', styleVariant: 'classic' });
    const ta = textarea();
    ta.setSelectionRange(5, 5);
    fireEvent.select(ta);
    expect(screen.getByTestId('tag-ac')).toHaveAttribute('data-open', 'true');
  });

  it('если курсор неизвестен (selectionStart=null), автокомплиты закрываются', () => {
    typeAt('a $s', 4);
    const ta = textarea();
    Object.defineProperty(ta, 'selectionStart', { configurable: true, get: () => null });
    fireEvent.select(ta);
    expect(screen.getByTestId('skill-ac')).toHaveAttribute('data-open', 'false');
  });

  it('выбор skill подставляет <$slug|Имя> вместо $запроса и закрывает автокомплит', () => {
    const { onChange } = typeAt('привет $su мир', 10);
    fireEvent.click(screen.getByText('pick-skill'));
    expect(onChange).toHaveBeenLastCalledWith('привет <$sum|Саммари>  мир');
    expect(screen.getByTestId('skill-ac')).toHaveAttribute('data-open', 'false');
  });

  it('выбор тега подставляет <#id|Имя> вместо #запроса', () => {
    const { onChange } = typeAt('тег #фи', 7);
    fireEvent.click(screen.getByText('pick-tag'));
    expect(onChange).toHaveBeenLastCalledWith('тег <#7|Финансы> ');
    expect(screen.getByTestId('tag-ac')).toHaveAttribute('data-open', 'false');
  });
});

describe('ChatInputBar: развернуть поле', () => {
  let scrollSpy: jest.SpyInstance;
  let clientSpy: jest.SpyInstance;

  beforeEach(() => {
    jest.useFakeTimers();
    scrollSpy = jest.spyOn(HTMLTextAreaElement.prototype, 'scrollHeight', 'get').mockReturnValue(300);
    clientSpy = jest.spyOn(HTMLTextAreaElement.prototype, 'clientHeight', 'get').mockReturnValue(100);
  });
  afterEach(() => {
    scrollSpy.mockRestore();
    clientSpy.mockRestore();
    jest.useRealTimers();
  });

  function withRef(value: string, extra: Partial<ChatInputBarProps> = {}) {
    const inputRef = React.createRef<HTMLTextAreaElement>();
    const r = setup({ value, inputRef: inputRef as any, ...extra });
    act(() => {
      jest.advanceTimersByTime(50);
    });
    return { ...r, inputRef };
  }

  it('кнопка «Развернуть» появляется, когда текст не помещается', () => {
    withRef('длинный текст');
    expect(screen.getByLabelText('Развернуть поле ввода')).toBeInTheDocument();
  });

  it('кнопки нет, если текст помещается или поле пустое', () => {
    scrollSpy.mockReturnValue(100);
    withRef('короткий');
    expect(screen.queryByLabelText('Развернуть поле ввода')).toBeNull();
    const empty = setup({ value: '   ' });
    act(() => {
      jest.advanceTimersByTime(50);
    });
    expect(empty.container.querySelectorAll('[aria-label="Развернуть поле ввода"]')).toHaveLength(0);
  });

  it('без inputRef кнопка не показывается (нет textarea для измерения)', () => {
    setup({ value: 'текст' });
    act(() => {
      jest.advanceTimersByTime(50);
    });
    expect(screen.queryByLabelText('Развернуть поле ввода')).toBeNull();
  });

  it('inputRef указывает не на textarea: ищем textarea в ближайшем MuiInputBase-root', () => {
    const holder = document.createElement('div');
    holder.className = 'MuiInputBase-root';
    const inner = document.createElement('input');
    const hiddenTa = document.createElement('textarea');
    holder.append(inner, hiddenTa);
    document.body.appendChild(holder);
    const fakeRef = { get current() { return inner; }, set current(_v) { /* игнорируем запись MUI */ } };
    setup({ value: 'текст', inputRef: fakeRef as any });
    act(() => {
      jest.advanceTimersByTime(50);
    });
    expect(screen.getByLabelText('Развернуть поле ввода')).toBeInTheDocument();
  });

  it('inputRef.current=null и input вне MuiInputBase-root: кнопки нет', () => {
    const orphan = document.createElement('input');
    const fakeRef = { get current() { return orphan; }, set current(_v) { /* ignore */ } };
    setup({ value: 'текст', inputRef: fakeRef as any });
    act(() => {
      jest.advanceTimersByTime(50);
    });
    expect(screen.queryByLabelText('Развернуть поле ввода')).toBeNull();
  });

  it('клик разворачивает (колонка, «Свернуть»), повторный клик сворачивает', () => {
    withRef('длинный текст');
    fireEvent.click(screen.getByLabelText('Развернуть поле ввода'));
    expect(screen.getByLabelText('Свернуть поле ввода')).toBeInTheDocument();
    expect(textarea()).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Свернуть поле ввода'));
    expect(screen.getByLabelText('Развернуть поле ввода')).toBeInTheDocument();
  });

  it('Escape сворачивает развёрнутое поле', () => {
    withRef('длинный текст');
    fireEvent.click(screen.getByLabelText('Развернуть поле ввода'));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByLabelText('Развернуть поле ввода')).toBeInTheDocument();
  });

  it('другие клавиши не сворачивают', () => {
    withRef('длинный текст');
    fireEvent.click(screen.getByLabelText('Развернуть поле ввода'));
    fireEvent.keyDown(window, { key: 'a' });
    expect(screen.getByLabelText('Свернуть поле ввода')).toBeInTheDocument();
  });

  it('очистка текста сама сворачивает развёрнутый режим', () => {
    const { rerender, props } = withRef('длинный текст');
    fireEvent.click(screen.getByLabelText('Развернуть поле ввода'));
    rerender(<ChatInputBar {...props} value="" />);
    act(() => {
      jest.advanceTimersByTime(50);
    });
    expect(screen.queryByLabelText('Свернуть поле ввода')).toBeNull();
  });

  it('в classic-варианте работает так же', () => {
    withRef('длинный текст', { styleVariant: 'classic' });
    fireEvent.click(screen.getByLabelText('Развернуть поле ввода'));
    expect(screen.getByLabelText('Свернуть поле ввода')).toBeInTheDocument();
  });
});
