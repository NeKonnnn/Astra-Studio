/**
 * @jest-environment jsdom
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import ChatGearRowSettingsButton from '@/components/ChatGearRowSettingsButton';
import { ASTRA_CLOSE_CHAT_GEAR } from '@/constants/hotkeys';

describe('ChatGearRowSettingsButton', () => {
  it('opens settings and closes chat gear menu', () => {
    const onOpen = jest.fn();
    const closeSpy = jest.fn();
    window.addEventListener(ASTRA_CLOSE_CHAT_GEAR, closeSpy);

    render(<ChatGearRowSettingsButton title="Настройки агента" onOpen={onOpen} />);
    fireEvent.click(screen.getByLabelText('Настройки агента'));

    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(closeSpy).toHaveBeenCalledTimes(1);
    window.removeEventListener(ASTRA_CLOSE_CHAT_GEAR, closeSpy);
  });

  it('does not bubble click to parent row (stopPropagation)', () => {
    const onOpen = jest.fn();
    const rowClick = jest.fn();
    render(
      <div onClick={rowClick}>
        <ChatGearRowSettingsButton title="Настройки skill" onOpen={onOpen} />
      </div>,
    );
    fireEvent.click(screen.getByLabelText('Настройки skill'));
    expect(onOpen).toHaveBeenCalled();
    expect(rowClick).not.toHaveBeenCalled();
  });
});
