import type { SvgIconComponent } from '@mui/icons-material';
import FolderOutlined from '@mui/icons-material/FolderOutlined';
import AttachMoneyOutlined from '@mui/icons-material/AttachMoneyOutlined';
import LightbulbOutlined from '@mui/icons-material/LightbulbOutlined';
import ImageOutlined from '@mui/icons-material/ImageOutlined';
import PlayArrowOutlined from '@mui/icons-material/PlayArrowOutlined';
import MusicNoteOutlined from '@mui/icons-material/MusicNoteOutlined';
import AutoAwesomeOutlined from '@mui/icons-material/AutoAwesomeOutlined';
import EditOutlined from '@mui/icons-material/EditOutlined';
import WorkOutline from '@mui/icons-material/WorkOutline';
import LanguageOutlined from '@mui/icons-material/LanguageOutlined';
import SchoolOutlined from '@mui/icons-material/SchoolOutlined';
import AccountBalanceWalletOutlined from '@mui/icons-material/AccountBalanceWalletOutlined';
import FavoriteBorder from '@mui/icons-material/FavoriteBorder';
import SportsBaseballOutlined from '@mui/icons-material/SportsBaseballOutlined';
import RestaurantOutlined from '@mui/icons-material/RestaurantOutlined';
import LocalCafeOutlined from '@mui/icons-material/LocalCafeOutlined';
import CodeOutlined from '@mui/icons-material/CodeOutlined';
import LocalFloristOutlined from '@mui/icons-material/LocalFloristOutlined';
import PetsOutlined from '@mui/icons-material/PetsOutlined';
import DirectionsCarOutlined from '@mui/icons-material/DirectionsCarOutlined';
import MenuBookOutlined from '@mui/icons-material/MenuBookOutlined';
import CloudOutlined from '@mui/icons-material/CloudOutlined';
import CalendarTodayOutlined from '@mui/icons-material/CalendarTodayOutlined';
import ComputerOutlined from '@mui/icons-material/ComputerOutlined';
import VolumeUpOutlined from '@mui/icons-material/VolumeUpOutlined';
import AssessmentOutlined from '@mui/icons-material/AssessmentOutlined';
import EmailOutlined from '@mui/icons-material/EmailOutlined';
import AssignmentOutlined from '@mui/icons-material/AssignmentOutlined';
import LuggageOutlined from '@mui/icons-material/LuggageOutlined';

/** Тонкие outlined-иконки проектов (как в пикере). */
export const PROJECT_ICON_MAP: Record<string, SvgIconComponent> = {
  folder: FolderOutlined,
  money: AttachMoneyOutlined,
  lightbulb: LightbulbOutlined,
  gallery: ImageOutlined,
  video: PlayArrowOutlined,
  music: MusicNoteOutlined,
  sparkle: AutoAwesomeOutlined,
  edit: EditOutlined,
  briefcase: WorkOutline,
  globe: LanguageOutlined,
  graduation: SchoolOutlined,
  wallet: AccountBalanceWalletOutlined,
  heart: FavoriteBorder,
  baseball: SportsBaseballOutlined,
  cutlery: RestaurantOutlined,
  coffee: LocalCafeOutlined,
  code: CodeOutlined,
  leaf: LocalFloristOutlined,
  cat: PetsOutlined,
  car: DirectionsCarOutlined,
  book: MenuBookOutlined,
  umbrella: CloudOutlined,
  calendar: CalendarTodayOutlined,
  desktop: ComputerOutlined,
  speaker: VolumeUpOutlined,
  chart: AssessmentOutlined,
  mail: EmailOutlined,
  assignment: AssignmentOutlined,
  luggage: LuggageOutlined,
};

export const PROJECT_ICON_OPTIONS: Array<{ name: string; icon: SvgIconComponent }> = Object.entries(
  PROJECT_ICON_MAP,
).map(([name, icon]) => ({ name, icon }));

export const PROJECT_DEFAULT_ICON = FolderOutlined;

export const PROJECT_ICON_COLOR_WHITE = '#ffffff';
export const PROJECT_ICON_COLOR_BLACK = '#111111';

const PROJECT_ICON_COLOR_ACCENTS: Array<{ name: string; value: string }> = [
  { name: 'red', value: '#f44336' },
  { name: 'orange', value: '#ff9800' },
  { name: 'green', value: '#4caf50' },
  { name: 'blue', value: '#2196f3' },
  { name: 'purple', value: '#9c27b0' },
  { name: 'dark-purple', value: '#673ab7' },
];

/** Палитра пикера: на тёмном фоне — белый (без чёрного), на светлом — чёрный (без белого). */
export function getProjectIconColorOptions(isDarkBackground: boolean): Array<{ name: string; value: string }> {
  const mono = isDarkBackground
    ? { name: 'white', value: PROJECT_ICON_COLOR_WHITE }
    : { name: 'black', value: PROJECT_ICON_COLOR_BLACK };
  return [mono, ...PROJECT_ICON_COLOR_ACCENTS];
}

export function getDefaultProjectIconColor(isDarkBackground: boolean): string {
  return isDarkBackground ? PROJECT_ICON_COLOR_WHITE : PROJECT_ICON_COLOR_BLACK;
}

function normalizeHex(color: string): string {
  return color.trim().toLowerCase();
}

function isNearWhite(color: string): boolean {
  const c = normalizeHex(color);
  return c === '#ffffff' || c === '#fff' || c === 'white' || c === PROJECT_ICON_COLOR_WHITE;
}

function isNearBlack(color: string): boolean {
  const c = normalizeHex(color);
  return (
    c === '#000000' ||
    c === '#000' ||
    c === '#111111' ||
    c === '#111' ||
    c === 'black' ||
    c === PROJECT_ICON_COLOR_BLACK
  );
}

/** Контраст: белый на светлом → чёрный; чёрный на тёмном → белый. */
export function resolveProjectIconColor(
  iconColor: string | undefined | null,
  isDarkBackground: boolean,
): string {
  const fallback = getDefaultProjectIconColor(isDarkBackground);
  if (!iconColor) return fallback;
  if (!isDarkBackground && isNearWhite(iconColor)) return PROJECT_ICON_COLOR_BLACK;
  if (isDarkBackground && isNearBlack(iconColor)) return PROJECT_ICON_COLOR_WHITE;
  return iconColor;
}
