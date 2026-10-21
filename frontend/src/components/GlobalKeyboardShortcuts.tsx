import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppContext, useAppActions, chatIsListedInAllChatsSection } from '../contexts/AppContext';
import {
  ASTRA_FOCUS_CHAT_SEARCH,
  ASTRA_TRIGGER_ATTACH,
  ASTRA_REQUEST_DELETE_CURRENT_CHAT,
  ASTRA_OPEN_SETTINGS,
  ASTRA_OPEN_AGENT_CONSTRUCTOR,
  ASTRA_OPEN_TRANSCRIPTION_SIDEBAR,
  HOTKEY_ACTIONS,
  matchesHotkeyBinding,
  isTypingInField,
  getDisabledHotkeys,
  ASTRA_HOTKEYS_CHANGED,
  type HotkeyActionId,
} from '../constants/hotkeys';
import { useHotkeyBindings } from '../hooks/useHotkeyBindings';
import { prepareNewChatSettings } from '../utils/newChatSettings';

const ACTION_EVENTS: Partial<Record<HotkeyActionId, string>> = {
  searchChats: ASTRA_FOCUS_CHAT_SEARCH,
  attachFiles: ASTRA_TRIGGER_ATTACH,
  deleteChat: ASTRA_REQUEST_DELETE_CURRENT_CHAT,
  openSettings: ASTRA_OPEN_SETTINGS,
  openAgentConstructor: ASTRA_OPEN_AGENT_CONSTRUCTOR,
  openTranscription: ASTRA_OPEN_TRANSCRIPTION_SIDEBAR,
};

/**
 * Глобальные сочетания клавиш (настраиваемые пользователем).
 * Слушатель в фазе capture, с preventDefault где нужно.
 */
export default function GlobalKeyboardShortcuts() {
  const navigate = useNavigate();
  const { state } = useAppContext();
  const { createChat, setCurrentChat, deleteChat } = useAppActions();
  const { bindings } = useHotkeyBindings();
  const disabledRef = useRef(getDisabledHotkeys());
  const stateRef = useRef(state);
  const bindingsRef = useRef(bindings);
  stateRef.current = state;
  bindingsRef.current = bindings;

  useEffect(() => {
    const onChange = () => {
      disabledRef.current = getDisabledHotkeys();
    };
    window.addEventListener(ASTRA_HOTKEYS_CHANGED, onChange);
    return () => window.removeEventListener(ASTRA_HOTKEYS_CHANGED, onChange);
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const currentBindings = bindingsRef.current;

      for (const action of HOTKEY_ACTIONS) {
        if (disabledRef.current.has(action.id)) continue;
        const binding = currentBindings[action.id];
        if (!matchesHotkeyBinding(e, binding)) continue;
        if (!action.worksInInput && isTypingInField(e.target)) continue;

        e.preventDefault();

        if (action.id === 'newChat') {
          const s = stateRef.current;
          const cur = s.chats.find((c) => c.id === s.currentChatId) ?? null;
          if (
            cur &&
            !chatIsListedInAllChatsSection(cur) &&
            !s.loadingChatIds.includes(cur.id) &&
            !cur.messages.some((m) => m.isStreaming)
          ) {
            deleteChat(cur.id);
          }
          const id = createChat();
          prepareNewChatSettings(cur?.id, id);
          setCurrentChat(id);
          navigate('/');
          return;
        }

        const eventName = ACTION_EVENTS[action.id];
        if (eventName) {
          window.dispatchEvent(new CustomEvent(eventName));
        }
        return;
      }
    };

    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [navigate, createChat, setCurrentChat, deleteChat]);

  return null;
}
