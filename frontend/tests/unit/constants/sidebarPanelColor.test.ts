import {
  DARK_MODE_STORAGE_KEY,
  DEFAULT_SIDEBAR_DARK,
  DEFAULT_SIDEBAR_GRADIENT,
  DEFAULT_SIDEBAR_LIGHT,
  SIDEBAR_PANEL_COLOR_KEY,
  extractSidebarSampleRgb,
  getDefaultSidebarPanelBackground,
  getSavedSidebarPanelColor,
  getSidebarChromeSx,
  getSidebarForcedContrastSx,
  getSidebarPanelBackground,
  getSidebarPanelBorderColor,
  getSidebarPanelChrome,
  getSidebarPanelForeground,
  getSidebarPanelHoverBackground,
  getSidebarPanelMutedForeground,
  getSidebarRailGlyphFilter,
  getSidebarSecondaryButtonSx,
  isAppDarkMode,
  isLightSidebarPanelBackground,
  isSidebarPanelLight,
  relativeLuminance,
} from '@/constants/sidebarPanelColor';

beforeEach(() => localStorage.clear());

describe('theme detection', () => {
  it('is light unless the stored flag is exactly true', () => {
    expect(isAppDarkMode()).toBe(false);
    localStorage.setItem(DARK_MODE_STORAGE_KEY, 'true');
    expect(isAppDarkMode()).toBe(true);
    localStorage.setItem(DARK_MODE_STORAGE_KEY, 'false');
    expect(isAppDarkMode()).toBe(false);
    localStorage.setItem(DARK_MODE_STORAGE_KEY, '"true"');
    expect(isAppDarkMode()).toBe(false);
  });

  it('survives corrupted storage', () => {
    localStorage.setItem(DARK_MODE_STORAGE_KEY, '{oops');
    expect(isAppDarkMode()).toBe(false);
  });

  it('default background follows the explicit flag first, then the stored theme', () => {
    expect(getDefaultSidebarPanelBackground(true)).toBe(DEFAULT_SIDEBAR_DARK);
    expect(getDefaultSidebarPanelBackground(false)).toBe(DEFAULT_SIDEBAR_LIGHT);
    localStorage.setItem(DARK_MODE_STORAGE_KEY, 'true');
    expect(getDefaultSidebarPanelBackground()).toBe(DEFAULT_SIDEBAR_DARK);
    expect(getDefaultSidebarPanelBackground(false)).toBe(DEFAULT_SIDEBAR_LIGHT);
  });
});

describe('saved colour', () => {
  it('empty means follow the theme', () => {
    expect(getSavedSidebarPanelColor()).toBe('');
    localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, '   ');
    expect(getSavedSidebarPanelColor()).toBe('');
  });

  it('a stored copy of either theme default counts as "default"', () => {
    localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, DEFAULT_SIDEBAR_LIGHT.toLowerCase());
    expect(getSavedSidebarPanelColor()).toBe('');
    localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, DEFAULT_SIDEBAR_DARK);
    expect(getSavedSidebarPanelColor()).toBe('');
  });

  it('custom colours are returned trimmed', () => {
    localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, '  #123456 ');
    expect(getSavedSidebarPanelColor()).toBe('#123456');
  });

  it('background: custom wins over the theme, default follows it', () => {
    expect(getSidebarPanelBackground(true)).toBe(DEFAULT_SIDEBAR_DARK);
    expect(getSidebarPanelBackground(false)).toBe(DEFAULT_SIDEBAR_LIGHT);
    localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, DEFAULT_SIDEBAR_GRADIENT);
    expect(getSidebarPanelBackground(true)).toBe(DEFAULT_SIDEBAR_GRADIENT);
  });
});

describe('colour sampling and luminance', () => {
  it('reads #rgb and #rrggbb', () => {
    expect(extractSidebarSampleRgb('#fff')).toEqual({ r: 255, g: 255, b: 255 });
    expect(extractSidebarSampleRgb('#0a141e')).toEqual({ r: 10, g: 20, b: 30 });
    expect(extractSidebarSampleRgb('#ABC')).toEqual({ r: 170, g: 187, b: 204 });
  });

  it('reads rgb() and rgba() and clamps channels', () => {
    expect(extractSidebarSampleRgb('rgb(10, 20, 30)')).toEqual({ r: 10, g: 20, b: 30 });
    expect(extractSidebarSampleRgb('rgba(1,2,3,0.5)')).toEqual({ r: 1, g: 2, b: 3 });
    expect(extractSidebarSampleRgb('rgb(999, 0, 0)')).toEqual({ r: 255, g: 0, b: 0 });
  });

  it('takes the first colour of a gradient', () => {
    expect(extractSidebarSampleRgb(DEFAULT_SIDEBAR_GRADIENT)).toEqual({ r: 0x66, g: 0x7e, b: 0xea });
  });

  it('empty input samples the theme default', () => {
    expect(extractSidebarSampleRgb('')).toEqual({ r: 0xf7, g: 0xf7, b: 0xf9 });
    localStorage.setItem(DARK_MODE_STORAGE_KEY, 'true');
    expect(extractSidebarSampleRgb('')).toEqual({ r: 0x21, g: 0x21, b: 0x28 });
  });

  it('unparsable colour names yield null', () => {
    expect(extractSidebarSampleRgb('papayawhip')).toBeNull();
  });

  it('relative luminance is 0 for black, 1 for white and monotonic', () => {
    expect(relativeLuminance({ r: 0, g: 0, b: 0 })).toBe(0);
    expect(relativeLuminance({ r: 255, g: 255, b: 255 })).toBeCloseTo(1, 5);
    expect(relativeLuminance({ r: 128, g: 128, b: 128 })).toBeGreaterThan(relativeLuminance({ r: 64, g: 64, b: 64 }));
    expect(relativeLuminance({ r: 0, g: 255, b: 0 })).toBeGreaterThan(relativeLuminance({ r: 255, g: 0, b: 0 }));
  });
});

describe('isSidebarPanelLight', () => {
  it.each([
    ['#ffffff', true],
    ['#fff', true],
    ['#F7F7F9', true],
    ['#EDF5E1', true],
    ['rgb(255, 255, 255)', true],
    ['white', true],
    ['#212128', false],
    ['#000', false],
    ['#667eea', false],
    [DEFAULT_SIDEBAR_GRADIENT, false],
    ['rgba(255,255,255,0.5)', true],
  ])('%s -> %s', (bg, expected) => {
    expect(isSidebarPanelLight(bg)).toBe(expected);
  });

  it('a gradient is judged by the average of its stops', () => {
    expect(isSidebarPanelLight('linear-gradient(90deg, #ffffff, #000000)')).toBe(false);
    expect(isSidebarPanelLight('linear-gradient(90deg, #ffffff, #f0f0f0)')).toBe(true);
  });

  it('no argument uses the current panel background', () => {
    expect(isSidebarPanelLight()).toBe(true); // светлая тема по умолчанию
    localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, '#101010');
    expect(isSidebarPanelLight()).toBe(false);
  });

  it('null and blank are treated as dark', () => {
    expect(isSidebarPanelLight('   ')).toBe(false);
  });

  it('keeps the old alias', () => {
    expect(isLightSidebarPanelBackground).toBe(isSidebarPanelLight);
  });
});

describe('derived colours', () => {
  it('dark panel gets white text, light panel dark text', () => {
    expect(getSidebarPanelForeground('#111')).toBe('#ffffff');
    expect(getSidebarPanelForeground('#fff')).toBe('rgba(0, 0, 0, 0.87)');
    expect(getSidebarPanelMutedForeground('#111')).toBe('rgba(255, 255, 255, 0.75)');
    expect(getSidebarPanelMutedForeground('#fff')).toBe('rgba(0, 0, 0, 0.6)');
    expect(getSidebarPanelHoverBackground('#111')).toBe('rgba(255, 255, 255, 0.1)');
    expect(getSidebarPanelHoverBackground('#fff')).toBe('rgba(0, 0, 0, 0.1)');
    expect(getSidebarPanelBorderColor('#111')).toBe('rgba(255, 255, 255, 0.08)');
    expect(getSidebarPanelBorderColor('#fff')).toBe('rgba(0, 0, 0, 0.12)');
  });

  it('rail glyphs are inverted only on light panels', () => {
    expect(getSidebarRailGlyphFilter('#fff')).toBe('invert(1) brightness(0)');
    expect(getSidebarRailGlyphFilter('#111')).toBe('none');
  });

  it('chrome describes both variants consistently', () => {
    const light = getSidebarPanelChrome('#fff');
    const dark = getSidebarPanelChrome('#111');
    expect(light).toMatchObject({ isLight: true, invertMenuGlyph: true });
    expect(dark).toMatchObject({ isLight: false, invertMenuGlyph: false, fg: '#ffffff' });
    for (const chrome of [light, dark]) {
      expect(chrome.border.startsWith('1px solid ')).toBe(true);
      expect(chrome.buttonBorder.startsWith('1px solid ')).toBe(true);
    }
  });
});

describe('style objects', () => {
  it('secondary button: solid or dashed border, hover and disabled states', () => {
    const chrome = getSidebarPanelChrome('#111');
    const solid = getSidebarSecondaryButtonSx(chrome);
    const dashed = getSidebarSecondaryButtonSx(chrome, { dashed: true });
    expect(solid.border).toContain('solid');
    expect(dashed.border).toContain('dashed');
    expect((solid['&:hover'] as any).color).toBe(chrome.fg);
    expect((solid['&:disabled'] as any).borderColor).toBe('rgba(255,255,255,0.12)');
    expect((getSidebarSecondaryButtonSx(getSidebarPanelChrome('#fff'))['&:disabled'] as any).borderColor).toBe(
      'rgba(0,0,0,0.14)',
    );
  });

  it('chrome sx: solid background', () => {
    const sx = getSidebarChromeSx('#222222') as any;
    expect(sx.background).toBe('#222222');
    expect(sx.backgroundColor).toBe('#222222');
    expect(sx.backgroundImage).toBe('none');
    expect(sx['--sidebar-fg']).toBe('#ffffff');
    expect(sx['& [data-memo-rail-menu-glyph]'].filter).toBe('none');
  });

  it('chrome sx: gradient background goes to backgroundImage', () => {
    const sx = getSidebarChromeSx(DEFAULT_SIDEBAR_GRADIENT) as any;
    expect(sx.backgroundColor).toBe('transparent');
    expect(sx.backgroundImage).toBe(DEFAULT_SIDEBAR_GRADIENT);
  });

  it('chrome sx without argument uses the current background', () => {
    localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, '#ffffff');
    const sx = getSidebarChromeSx() as any;
    expect(sx.background).toBe('#ffffff');
    expect(sx['& [data-memo-rail-menu-glyph]'].filter).toBe('invert(1) brightness(0)');
  });

  it('forced contrast: nothing on dark panels, strong overrides on light ones', () => {
    expect(getSidebarForcedContrastSx('#111')).toEqual({});
    const sx = getSidebarForcedContrastSx('#ffffff') as any;
    expect(sx['& .MuiSvgIcon-root'].color).toContain('!important');
    expect(sx['& [data-sidebar-danger]'].color).toContain('#d32f2f');
    expect(sx['& .MuiButton-contained'].color).toContain('#ffffff');
    expect(Object.keys(sx).length).toBeGreaterThan(15);
  });
});
