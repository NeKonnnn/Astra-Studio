import { useCallback, useEffect, useMemo, useState } from 'react';
import { authFetch, getApiUrl } from '../config/api';
import { useAuth } from '../contexts/AuthContext';
import { useAppContext } from '../contexts/AppContext';
import {
  extractCreationsFromChats,
  mergeCreationItems,
  type ImageCreationItem,
} from '../utils/imageCreations';

export function useImageCreations(limit = 500) {
  const { token } = useAuth();
  const { state } = useAppContext();
  const [apiItems, setApiItems] = useState<ImageCreationItem[]>([]);
  const [loading, setLoading] = useState(true);

  const localItems = useMemo(
    () => extractCreationsFromChats(state.chats),
    [state.chats],
  );

  const loadCreations = useCallback(async () => {
    if (!token) {
      setApiItems([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const r = await authFetch(getApiUrl(`/api/image-generation/creations?limit=${limit}`));
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      setApiItems(Array.isArray(data.items) ? data.items : []);
    } catch {
      setApiItems([]);
    } finally {
      setLoading(false);
    }
  }, [limit, token]);

  useEffect(() => {
    void loadCreations();
  }, [loadCreations, token]);

  useEffect(() => {
    const refresh = () => void loadCreations();
    window.addEventListener('astrachatCreationsUpdated', refresh);
    return () => window.removeEventListener('astrachatCreationsUpdated', refresh);
  }, [loadCreations]);

  const items = useMemo(
    () => mergeCreationItems(apiItems, localItems),
    [apiItems, localItems],
  );

  return { items, loading, reload: loadCreations };
}
