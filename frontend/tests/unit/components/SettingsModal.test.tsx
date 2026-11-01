import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import SettingsModal from '@/components/SettingsModal';
import { ASTRA_OPEN_SETTINGS_SECTION } from '@/constants/hotkeys';

jest.mock('@/components/settings', () => {
  const stub = (name: string) => (props: any) => (
    <div data-testid={`section-${name}`}>
      {name}
      {props.isDarkMode !== undefined ? `:${props.isDarkMode}` : ''}
      {props.onToggleTheme ? <button onClick={props.onToggleTheme}>toggle-theme</button> : null}
    </div>
  );
  return {
    GeneralSettings: stub('general'),
    ProfileSettings: stub('profile'),
    ModelsSettings: stub('models'),
    RAGSettings: stub('rag'),
    TranscriptionSettings: stub('transcription'),
    AboutSettings: stub('about'),
    ChatSettings: stub('chats'),
    InterfaceSettings: stub('interface'),
  };
});

const TITLES: Array<[string, string]> = [
  ['Общее', 'general'],
  ['Профиль', 'profile'],
  ['Интерфейс', 'interface'],
  ['Чаты', 'chats'],
  ['Модели', 'models'],
  ['RAG', 'rag'],
  ['Транскрибация', 'transcription'],
  ['О приложении', 'about'],
];

describe('SettingsModal', () => {
  const setup = (over: Record<string, unknown> = {}) => {
    const h = { onClose: jest.fn(), onToggleTheme: jest.fn() };
    const utils = render(<SettingsModal open isDarkMode={false} {...h} {...(over as any)} />);
    return { ...h, ...utils };
  };

  it('закрытое окно ничего не рисует', () => {
    setup({ open: false });
    expect(screen.queryByText('Настройки')).toBeNull();
  });

  it('по умолчанию открыт раздел «Общее» и передаётся тема и переключатель', () => {
    const h = setup({ isDarkMode: true });
    expect(screen.getByTestId('section-general')).toHaveTextContent('general:true');
    fireEvent.click(screen.getByText('toggle-theme'));
    expect(h.onToggleTheme).toHaveBeenCalledTimes(1);
  });

  it.each(TITLES)('пункт «%s» открывает раздел %s', (title, id) => {
    setup();
    fireEvent.click(screen.getByText(title));
    expect(screen.getByTestId(`section-${id}`)).toBeInTheDocument();
  });

  it('initialSection выбирается при открытии; неизвестный раздел игнорируется', () => {
    const { unmount } = setup({ initialSection: 'rag' });
    expect(screen.getByTestId('section-rag')).toBeInTheDocument();
    unmount();
    setup({ initialSection: 'nope' });
    expect(screen.getByTestId('section-general')).toBeInTheDocument();
  });

  it('событие смены раздела переключает вкладку; неизвестный и пустой раздел игнорируются', () => {
    setup();
    act(() => {
      window.dispatchEvent(new CustomEvent(ASTRA_OPEN_SETTINGS_SECTION, { detail: { section: 'about' } }));
    });
    expect(screen.getByTestId('section-about')).toBeInTheDocument();
    act(() => {
      window.dispatchEvent(new CustomEvent(ASTRA_OPEN_SETTINGS_SECTION, { detail: { section: 'zzz' } }));
      window.dispatchEvent(new CustomEvent(ASTRA_OPEN_SETTINGS_SECTION));
    });
    expect(screen.getByTestId('section-about')).toBeInTheDocument();
  });

  it('после размонтирования слушатель события снят', () => {
    const { unmount } = setup();
    unmount();
    expect(() =>
      window.dispatchEvent(new CustomEvent(ASTRA_OPEN_SETTINGS_SECTION, { detail: { section: 'rag' } })),
    ).not.toThrow();
  });

  it('крестик закрывает диалог', () => {
    const h = setup();
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(h.onClose).toHaveBeenCalledTimes(1);
  });

  it('в навигации восемь разделов', () => {
    setup();
    for (const [title] of TITLES) expect(screen.getByText(title)).toBeInTheDocument();
  });
});
