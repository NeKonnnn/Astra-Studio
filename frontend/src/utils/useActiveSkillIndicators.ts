import { useEffect, useState } from 'react';
import {
  getActiveSkillRefs,
  SKILL_SELECTION_CHANGED_EVENT,
  type ActiveSkillRef,
} from '../utils/skillSelectionStorage';

/** Активные skills из localStorage + событие выбора. */
export function useActiveSkillIndicators(): ActiveSkillRef[] {
  const [skills, setSkills] = useState<ActiveSkillRef[]>(() => getActiveSkillRefs());

  useEffect(() => {
    const sync = () => setSkills(getActiveSkillRefs());
    window.addEventListener(SKILL_SELECTION_CHANGED_EVENT, sync);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener(SKILL_SELECTION_CHANGED_EVENT, sync);
      window.removeEventListener('storage', sync);
    };
  }, []);

  return skills;
}