import type { SxProps, Theme } from '@mui/material';

/** Карточки админ-дашборда в стилистике рабочей зоны / gallery glass (без «ecommerce neon»). */
export function getAdminCardSx(isDarkMode: boolean): SxProps<Theme> {
  return {
    p: 2,
    borderRadius: 2,
    height: '100%',
    backgroundColor: isDarkMode ? 'rgba(28, 28, 32, 0.55)' : 'rgba(255, 255, 255, 0.72)',
    backgroundImage: isDarkMode
      ? 'linear-gradient(145deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0.01) 100%)'
      : 'linear-gradient(145deg, rgba(255,255,255,0.9) 0%, rgba(255,255,255,0.5) 100%)',
    border: isDarkMode ? '1px solid rgba(255,255,255,0.1)' : '1px solid rgba(0,0,0,0.08)',
    boxShadow: isDarkMode
      ? '0 8px 24px rgba(0,0,0,0.28), inset 0 1px 0 rgba(255,255,255,0.06)'
      : '0 6px 18px rgba(0,0,0,0.06), inset 0 1px 0 rgba(255,255,255,0.9)',
    backdropFilter: 'blur(12px) saturate(1.2)',
    WebkitBackdropFilter: 'blur(12px) saturate(1.2)',
  };
}

export function getAdminNavItemSx(isDarkMode: boolean, active: boolean): SxProps<Theme> {
  return {
    borderRadius: '10px',
    mb: 0.5,
    mx: 0.5,
    py: 1,
    px: 1.25,
    gap: 1,
    position: 'relative',
    color: active
      ? isDarkMode
        ? '#fff'
        : 'rgba(0,0,0,0.9)'
      : isDarkMode
        ? 'rgba(255,255,255,0.7)'
        : 'rgba(0,0,0,0.65)',
    bgcolor: active
      ? isDarkMode
        ? 'rgba(33, 150, 243, 0.22)'
        : 'rgba(33, 150, 243, 0.12)'
      : 'transparent',
    '&:hover': {
      bgcolor: active
        ? isDarkMode
          ? 'rgba(33, 150, 243, 0.28)'
          : 'rgba(33, 150, 243, 0.16)'
        : isDarkMode
          ? 'rgba(255,255,255,0.06)'
          : 'rgba(0,0,0,0.04)',
    },
    '&::before': active
      ? {
          content: '""',
          position: 'absolute',
          left: 0,
          top: 8,
          bottom: 8,
          width: 3,
          borderRadius: 2,
          bgcolor: 'primary.main',
        }
      : undefined,
  };
}
