/** Ключ в localStorage для пользовательского цвета боковых панелей. Пустая строка = цвет по умолчанию. */
export const SIDEBAR_PANEL_COLOR_KEY = 'sidebar_panel_color';

/** Ключ темы приложения (см. App.tsx). */
export const DARK_MODE_STORAGE_KEY = 'gazikii-dark-mode';

/** Дефолт боковых панелей в светлой теме. */
export const DEFAULT_SIDEBAR_LIGHT = '#F7F7F9';

/** Дефолт боковых панелей в тёмной теме («Тёмный графит»). */
export const DEFAULT_SIDEBAR_DARK = '#212128';

/** Фиолетовый градиент из палитры настроек (не дефолт панелей). */
export const DEFAULT_SIDEBAR_GRADIENT = 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';

const HEX_RE = /#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b/g;

/** Текущая тема приложения из localStorage. */
export function isAppDarkMode(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    const saved = localStorage.getItem(DARK_MODE_STORAGE_KEY);
    return saved ? JSON.parse(saved) === true : false;
  } catch {
    return false;
  }
}

/** Цвет панелей «По умолчанию» для текущей (или переданной) темы. */
export function getDefaultSidebarPanelBackground(isDark?: boolean): string {
  const dark = typeof isDark === 'boolean' ? isDark : isAppDarkMode();
  return dark ? DEFAULT_SIDEBAR_DARK : DEFAULT_SIDEBAR_LIGHT;
}

type Rgb = { r: number; g: number; b: number };

function expandHex(hex: string): Rgb | null {
  const raw = hex.replace('#', '').trim();
  if (raw.length === 3) {
    const r = parseInt(raw[0] + raw[0], 16);
    const g = parseInt(raw[1] + raw[1], 16);
    const b = parseInt(raw[2] + raw[2], 16);
    return { r, g, b };
  }
  if (raw.length === 6) {
    return {
      r: parseInt(raw.slice(0, 2), 16),
      g: parseInt(raw.slice(2, 4), 16),
      b: parseInt(raw.slice(4, 6), 16),
    };
  }
  return null;
}

/** Извлечь все #RGB / #RRGGBB и rgb(...) из строки фона (solid или gradient). */
function extractSidebarSampleRgbs(background: string): Rgb[] {
  const value = (background || '').trim();
  if (!value) return extractSidebarSampleRgbs(getDefaultSidebarPanelBackground());

  const out: Rgb[] = [];
  const hexMatches = value.match(HEX_RE);
  if (hexMatches) {
    for (const hex of hexMatches) {
      const rgb = expandHex(hex);
      if (rgb) out.push(rgb);
    }
  }
  // Без /g на том же regex повторно — сброс lastIndex
  const rgbRe = /rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})/g;
  let rgbMatch: RegExpExecArray | null;
  while ((rgbMatch = rgbRe.exec(value)) !== null) {
    out.push({
      r: Math.min(255, Number(rgbMatch[1])),
      g: Math.min(255, Number(rgbMatch[2])),
      b: Math.min(255, Number(rgbMatch[3])),
    });
  }
  return out;
}

/** Достаёт «характерный» цвет фона: для градиента — первый hex/rgb. */
export function extractSidebarSampleRgb(background: string): Rgb | null {
  const samples = extractSidebarSampleRgbs(background);
  return samples[0] ?? null;
}

/** Относительная яркость по WCAG (0 = чёрный, 1 = белый). */
export function relativeLuminance(rgb: Rgb): number {
  const toLinear = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  const r = toLinear(rgb.r);
  const g = toLinear(rgb.g);
  const b = toLinear(rgb.b);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * Светлая ли панель: на ней белые иконки/текст пропадают.
 * Для градиента берётся средняя яркость стоп-цветов (как в ASTRA).
 * Порог ~0.62 — покрывает #EDF5E1 и белый/почти белый custom.
 */
export function isSidebarPanelLight(background?: string | null): boolean {
  const bg = (background ?? getSidebarPanelBackground()).trim();
  if (!bg) return false;

  const samples = extractSidebarSampleRgbs(bg);
  if (samples.length > 0) {
    const avg = samples.reduce((sum, rgb) => sum + relativeLuminance(rgb), 0) / samples.length;
    return avg > 0.62;
  }

  const lower = bg.toLowerCase();
  if (
    lower === 'white' ||
    lower === '#fff' ||
    lower.includes('rgb(255') ||
    lower.includes('rgba(255, 255, 255') ||
    lower.includes('rgba(255,255,255')
  ) {
    return true;
  }
  return false;
}

/** Alias ASTRA API. */
export const isLightSidebarPanelBackground = isSidebarPanelLight;

export function getSidebarPanelBackground(): string {
  if (typeof window === 'undefined') return getDefaultSidebarPanelBackground(false);
  const saved = localStorage.getItem(SIDEBAR_PANEL_COLOR_KEY);
  return saved || getDefaultSidebarPanelBackground();
}

export function getSidebarPanelForeground(background?: string): string {
  return isSidebarPanelLight(background) ? 'rgba(0, 0, 0, 0.87)' : '#ffffff';
}

export function getSidebarPanelMutedForeground(background?: string): string {
  return isSidebarPanelLight(background) ? 'rgba(0, 0, 0, 0.6)' : 'rgba(255, 255, 255, 0.75)';
}

export function getSidebarPanelHoverBackground(background?: string): string {
  return isSidebarPanelLight(background) ? 'rgba(0, 0, 0, 0.06)' : 'rgba(255, 255, 255, 0.08)';
}

export function getSidebarPanelBorderColor(background?: string): string {
  return isSidebarPanelLight(background) ? 'rgba(0, 0, 0, 0.12)' : 'rgba(255, 255, 255, 0.08)';
}

/** Белые PNG-глифы rail на светлой панели инвертируем в тёмные. */
export function getSidebarRailGlyphFilter(background?: string): string {
  return isSidebarPanelLight(background) ? 'invert(1) brightness(0)' : 'none';
}

/** Цвета хрома сайдбара под светлый/тёмный фон панели (ASTRA). */
export type SidebarPanelChrome = {
  isLight: boolean;
  /** Основной цвет текста и иконок. */
  fg: string;
  /** Приглушённый текст. */
  fgMuted: string;
  /** Ещё более приглушённый (плейсхолдеры, подписи). */
  fgSubtle: string;
  /** Рамка панели. */
  border: string;
  /** Рамка вторичных outlined/dashed кнопок. */
  buttonBorder: string;
  /** Рамка вторичных кнопок при hover. */
  buttonBorderHover: string;
  /** Hover по строкам / кнопкам. */
  hoverBg: string;
  /** Активный/выделенный фон. */
  activeBg: string;
  /** Инвертировать PNG-глиф меню rail (глиф светлый — на светлой панели нужен тёмный). */
  invertMenuGlyph: boolean;
};

export function getSidebarPanelChrome(background?: string | null): SidebarPanelChrome {
  const isLight = isSidebarPanelLight(background);
  if (isLight) {
    return {
      isLight: true,
      fg: 'rgba(0,0,0,0.87)',
      fgMuted: 'rgba(0,0,0,0.72)',
      fgSubtle: 'rgba(0,0,0,0.55)',
      border: '1px solid rgba(0,0,0,0.12)',
      buttonBorder: '1px solid rgba(0,0,0,0.28)',
      buttonBorderHover: 'rgba(0,0,0,0.45)',
      hoverBg: 'rgba(0,0,0,0.08)',
      activeBg: 'rgba(0,0,0,0.12)',
      invertMenuGlyph: true,
    };
  }
  return {
    isLight: false,
    fg: '#ffffff',
    fgMuted: 'rgba(255,255,255,0.75)',
    fgSubtle: 'rgba(255,255,255,0.55)',
    border: '1px solid rgba(255,255,255,0.08)',
    buttonBorder: '1px solid rgba(255,255,255,0.22)',
    buttonBorderHover: 'rgba(255,255,255,0.4)',
    hoverBg: 'rgba(255,255,255,0.1)',
    activeBg: 'rgba(255,255,255,0.15)',
    invertMenuGlyph: false,
  };
}

/** Outlined/dashed кнопка на боковой панели («Добавить файлы», «Загрузить файл», «Настройки РАГ»…). */
export function getSidebarSecondaryButtonSx(
  chrome: SidebarPanelChrome,
  opts?: { dashed?: boolean },
): Record<string, unknown> {
  const border = opts?.dashed
    ? chrome.buttonBorder.replace('solid', 'dashed')
    : chrome.buttonBorder;
  return {
    color: chrome.fgMuted,
    border,
    '&:hover': {
      bgcolor: chrome.hoverBg,
      borderColor: chrome.buttonBorderHover,
      color: chrome.fg,
    },
    '&:disabled': {
      color: chrome.fgSubtle,
      borderColor: chrome.isLight ? 'rgba(0,0,0,0.14)' : 'rgba(255,255,255,0.12)',
    },
  };
}

/**
 * Общие стили хрома левой/правой панели: фон, CSS-переменные контраста, глиф меню.
 * Иконки rail должны брать `var(--sidebar-fg)` (см. SIDEBAR_LIST_ICON_SX).
 */
export function getSidebarChromeSx(background?: string): Record<string, unknown> {
  const bg = background ?? getSidebarPanelBackground();
  const chrome = getSidebarPanelChrome(bg);
  return {
    background: bg,
    color: chrome.fg,
    borderColor: chrome.border.replace('1px solid ', ''),
    '--sidebar-fg': chrome.fg,
    '--sidebar-muted-fg': chrome.fgMuted,
    '--sidebar-hover-bg': chrome.hoverBg,
    '--sidebar-border-color': chrome.border.replace('1px solid ', ''),
    '--sidebar-selected-bg': chrome.activeBg,
    transition: 'background 0.3s ease, color 0.3s ease',
    '& [data-memo-rail-menu-glyph]': {
      filter: chrome.invertMenuGlyph ? 'invert(1) brightness(0)' : 'none',
    },
  };
}

/**
 * Принудительный контраст для содержимого панели (иконки/текст/кнопки с захардкоженным #fff).
 * Диалоги MUI порталятся наружу — на них не действует.
 * Contained-кнопки (Сохранить и т.п.) оставляем со светлым текстом/иконками.
 */
export function getSidebarForcedContrastSx(background?: string): Record<string, unknown> {
  const bg = background ?? getSidebarPanelBackground();
  if (!isSidebarPanelLight(bg)) return {};
  const chrome = getSidebarPanelChrome(bg);
  const fg = chrome.fg;
  const muted = chrome.fgMuted;
  const border = chrome.border.replace('1px solid ', '');
  const hover = chrome.hoverBg;
  return {
    '& .MuiSvgIcon-root': { color: `${fg} !important` },
    // Danger-кнопки (удаление и т.п.) сохраняют свой красный цвет.
    '& [data-sidebar-danger]': { color: '#d32f2f !important' },
    '& [data-sidebar-danger] .MuiSvgIcon-root': { color: '#d32f2f !important' },
    '& .MuiTypography-root': { color: `${fg} !important` },
    '& .MuiIconButton-root': { color: `${fg} !important` },
    '& .MuiListItemIcon-root': { color: `${fg} !important` },
    '& .MuiListItemText-primary, & .MuiListItemText-secondary': { color: `${fg} !important` },
    '& .MuiFormControlLabel-label': { color: `${fg} !important` },
    '& .MuiFormLabel-root': { color: `${muted} !important` },
    '& .MuiInputBase-root': { color: `${fg} !important` },
    '& .MuiInputBase-input': {
      color: `${fg} !important`,
      WebkitTextFillColor: fg,
    },
    '& .MuiInputBase-input::placeholder': {
      color: `${muted} !important`,
      opacity: '1 !important',
      WebkitTextFillColor: muted,
    },
    '& .MuiOutlinedInput-notchedOutline': { borderColor: `${border} !important` },
    '& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline': {
      borderColor: `${muted} !important`,
    },
    '& .MuiButton-root:not(.MuiButton-contained):not([data-sidebar-danger])': {
      color: `${fg} !important`,
      borderColor: `${border} !important`,
      '&:hover': {
        backgroundColor: `${hover} !important`,
        borderColor: `${fg} !important`,
      },
      '&.Mui-disabled': {
        color: `${muted} !important`,
        borderColor: `${border} !important`,
      },
    },
    '& .MuiButton-contained': {
      color: '#ffffff !important',
    },
    '& .MuiButton-contained .MuiSvgIcon-root': { color: '#ffffff !important' },
    '& .MuiButton-contained .MuiCircularProgress-root': { color: '#ffffff !important' },
    '& .MuiButton-contained.Mui-disabled': {
      color: 'rgba(255,255,255,0.7) !important',
    },
    '& .MuiCheckbox-root': { color: `${muted} !important` },
    '& .MuiCheckbox-root.Mui-checked': { color: '#1976d2 !important' },
    '& .MuiCircularProgress-root': { color: `${fg} !important` },
  };
}
