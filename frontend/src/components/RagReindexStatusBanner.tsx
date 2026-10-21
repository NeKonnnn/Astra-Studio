import React, { useEffect, useState } from 'react';
import { useRagReindexStatus } from '../hooks/useRagReindexStatus';
import { STATUS_TOAST_AUTO_DISMISS_MS } from '../constants/statusToast';
import StatusToast from './StatusToast';

/**
 * Оранжевый StatusToast сверху при перечанковке RAG (poll GET /api/rag/reindex-status).
 */
export default function RagReindexStatusBanner() {
  const { anyReindexing, blockMessage } = useRagReindexStatus();
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (anyReindexing) {
      setOpen(!dismissed);
    } else {
      setOpen(false);
      setDismissed(false);
    }
  }, [anyReindexing, dismissed]);

  if (!open || !blockMessage) return null;

  return (
    <StatusToast
      type="warning"
      message={blockMessage}
      onClose={() => {
        setOpen(false);
        setDismissed(true);
      }}
      ariaLabel="Закрыть уведомление о перечанковке RAG"
      fixed
      top={12}
      zIndexOffset={1}
      autoDismissMs={STATUS_TOAST_AUTO_DISMISS_MS}
    />
  );
}
