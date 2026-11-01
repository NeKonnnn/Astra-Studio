import type { SxProps, Theme } from '@mui/material';

/** Эффект стекла для карточек разделов Галереи (агенты / skills / KB / плагины). */
export function getGalleryCardGlassSx(
  isDarkMode: boolean,
  options?: { clickable?: boolean },
): SxProps<Theme> {
  const clickable = options?.clickable !== false;
  return {
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    backgroundColor: isDarkMode ? 'rgba(28, 28, 32, 0.55)' : 'rgba(255, 255, 255, 0.62)',
    backgroundImage: isDarkMode
      ? 'linear-gradient(145deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0.01) 100%)'
      : 'linear-gradient(145deg, rgba(255,255,255,0.85) 0%, rgba(255,255,255,0.45) 100%)',
    border: isDarkMode ? '1px solid rgba(255,255,255,0.12)' : '1px solid rgba(255,255,255,0.65)',
    boxShadow: isDarkMode
      ? '0 8px 28px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.08)'
      : '0 8px 24px rgba(0,0,0,0.08), inset 0 1px 0 rgba(255,255,255,0.9)',
    backdropFilter: 'blur(14px) saturate(1.25)',
    WebkitBackdropFilter: 'blur(14px) saturate(1.25)',
    cursor: clickable ? 'pointer' : 'default',
    transition: 'box-shadow 0.18s ease, border-color 0.18s ease, transform 0.18s ease',
    '&:hover': {
      boxShadow: isDarkMode
        ? '0 12px 36px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.12)'
        : '0 12px 32px rgba(0,0,0,0.12), inset 0 1px 0 rgba(255,255,255,1)',
      borderColor: isDarkMode ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.9)',
      transform: clickable ? 'translateY(-1px)' : undefined,
    },
  };
}
