import React from 'react';
import { SvgIcon, type SvgIconProps } from '@mui/material';

type SplitArrowIconProps = SvgIconProps & {
  size?: number | string;
};

/**
 * Иконка «Разветвление чата».
 * Хвостик нижней стрелки приподнят выше, но сохраняет четкий зазор с верхней линией.
 */
export default function SplitArrowIcon({ size, sx, ...props }: SplitArrowIconProps) {
  return (
    <SvgIcon
      viewBox="0 0 24 24"
      sx={[
        size != null ? { fontSize: size, width: size, height: size } : null,
        { '& path': { fill: 'currentColor' } },
        ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
      ]}
      {...props}
    >
      {/*
        Верхняя стрелка: стабильно на высоте Y=6.5
      */}
      <path d="M6 6.5h14v-3l5 4.5-5 4.5v-3H2v-3z" />

      {/*
        Нижнее ответвление:
        1. Старт вертикальной палочки поднят выше к верхней стрелке (Y=11.5).
        2. Горизонтальная полка вытянута вправо, чтобы конец совпал с верхней стрелкой.
        3. Высота горизонтальной части (Y=17) полностью сохранена.
      */}
      <path d="M9 11.5v4.7a0.5 0 0 0 0.5 0.5h10v-3l5 4.5-5 4.5v-3H9.5a2.5 2.5 0 0 1-2.5-2.5v-4.7h2z" />
    </SvgIcon>
  );
}
