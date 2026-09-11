import React from 'react';
import StatusToast from './StatusToast';
import { STATUS_TOAST_AUTO_DISMISS_MS } from '../constants/statusToast';

/** @deprecated используйте STATUS_TOAST_AUTO_DISMISS_MS */
export const TOP_ERROR_BANNER_AUTO_DISMISS_MS = STATUS_TOAST_AUTO_DISMISS_MS;

interface TopErrorBannerProps {
  message: string;
  onClose: () => void;
  ariaLabel?: string;
}

/** Красный StatusToast сверху по центру (ошибки в рабочей зоне). */
export default function TopErrorBanner({
  message,
  onClose,
  ariaLabel = 'Закрыть уведомление об ошибке',
}: TopErrorBannerProps) {
  return (
    <StatusToast
      type="error"
      message={message}
      onClose={onClose}
      ariaLabel={ariaLabel}
      fixed
      top={12}
      zIndexOffset={3}
      autoDismissMs={STATUS_TOAST_AUTO_DISMISS_MS}
    />
  );
}
