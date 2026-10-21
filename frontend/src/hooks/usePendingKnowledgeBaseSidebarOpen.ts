import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { resolvePendingKnowledgeBaseSidebarId } from '../utils/openKnowledgeBaseSidebarNav';

/** При переходе с галереи открывает правый сайдбар с панелью Баз знаний. */
export function usePendingKnowledgeBaseSidebarOpen(
  openKnowledgeBasesSidebar: () => void,
): void {
  const location = useLocation();
  const openRef = useRef(openKnowledgeBasesSidebar);
  openRef.current = openKnowledgeBasesSidebar;

  useEffect(() => {
    const kbId = resolvePendingKnowledgeBaseSidebarId(location.state);
    if (kbId != null) {
      openRef.current();
    }
  }, [location.pathname, location.state]);
}
