import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import UpdatesDialog from '@/components/left_bar/UpdatesDialog';
import { RELEASE_NOTES_SHORT } from '@/components/left_bar/releaseNotesShort';

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

beforeEach(() => {
  mockNavigate.mockReset();
});

describe('UpdatesDialog', () => {
  it('закрытый диалог ничего не рендерит', () => {
    render(<UpdatesDialog open={false} onClose={jest.fn()} isDarkMode />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('показывает заголовок и все релизы из RELEASE_NOTES_SHORT: даты, названия, разделы и пункты', () => {
    render(<UpdatesDialog open onClose={jest.fn()} isDarkMode={false} />);
    const dlg = screen.getByRole('dialog');
    expect(within(dlg).getByText('Обновления')).toBeInTheDocument();
    expect(RELEASE_NOTES_SHORT.length).toBeGreaterThan(0);
    for (const release of RELEASE_NOTES_SHORT) {
      expect(within(dlg).getByText(release.title)).toBeInTheDocument();
      expect(within(dlg).getAllByText(release.date).length).toBeGreaterThan(0);
      for (const section of release.sections) {
        expect(within(dlg).getAllByText(section.title).length).toBeGreaterThan(0);
        for (const item of section.items) {
          expect(within(dlg).getAllByText(item).length).toBeGreaterThan(0);
        }
      }
    }
    expect(within(dlg).getAllByText('Читать подробнее.')).toHaveLength(RELEASE_NOTES_SHORT.length);
  });

  it('крестик и Escape вызывают onClose', () => {
    const onClose = jest.fn();
    render(<UpdatesDialog open onClose={onClose} isDarkMode />);
    fireEvent.click(screen.getByLabelText('Закрыть'));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('«Читать подробнее.» закрывает диалог и открывает страницу документации этого релиза', () => {
    const onClose = jest.fn();
    render(<UpdatesDialog open onClose={onClose} isDarkMode />);
    const links = screen.getAllByText('Читать подробнее.');
    RELEASE_NOTES_SHORT.forEach((release, i) => {
      mockNavigate.mockClear();
      onClose.mockClear();
      fireEvent.click(links[i]);
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(mockNavigate).toHaveBeenCalledWith(`/docs/astrachat-release-${release.version}`);
    });
  });

  it('release notes: версии уникальны, у каждого релиза есть хотя бы один раздел с пунктами', () => {
    const versions = RELEASE_NOTES_SHORT.map((r) => r.version);
    expect(new Set(versions).size).toBe(versions.length);
    for (const r of RELEASE_NOTES_SHORT) {
      expect(r.sections.length).toBeGreaterThan(0);
      for (const s of r.sections) expect(s.items.length).toBeGreaterThan(0);
    }
  });
});
