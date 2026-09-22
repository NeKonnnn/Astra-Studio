import React from 'react';
import { SvgIcon, type SvgIconProps } from '@mui/material';

type ThinkingBulbIconProps = SvgIconProps & {
  size?: number | string;
};

/**
 * Лампочка для пункта «Мышление».
 * Геометрия вписана в 100×100 с запасом сверху/снизу (без scale → без клипа).
 * Толщина линий как у AgentIcon.
 */
export default function ThinkingBulbIcon({ size, sx, ...props }: ThinkingBulbIconProps) {
  return (
    <SvgIcon
      viewBox="0 0 100 100"
      fill="none"
      sx={[
        size != null ? { fontSize: size, width: size, height: size } : null,
        { overflow: 'visible' },
        ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
      ]}
      {...props}
    >
      {/* Колба — центр круга (50, 44), r=30 → верх ≈14, низ цоколя ≈92 */}
      <path
        d="M 37 74 A 3 3 0 0 1 34 71 C 34 60 29 57 22 46 A 30 30 0 1 1 78 46 C 71 57 66 60 66 71 A 3 3 0 0 1 63 74 Z"
        fill="none"
        stroke="currentColor"
        strokeWidth={6.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <line
        x1={50}
        y1={74}
        x2={50}
        y2={62}
        stroke="currentColor"
        strokeWidth={6.5}
        strokeLinecap="round"
      />
      <rect
        x={34}
        y={74}
        width={32}
        height={10}
        rx={5}
        fill="none"
        stroke="currentColor"
        strokeWidth={6.5}
      />
      <rect
        x={39}
        y={84}
        width={22}
        height={9}
        rx={4.5}
        fill="none"
        stroke="currentColor"
        strokeWidth={6.5}
      />
    </SvgIcon>
  );
}
