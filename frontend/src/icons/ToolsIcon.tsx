import React from 'react';
import { SvgIcon, type SvgIconProps } from '@mui/material';

type ToolsIconProps = SvgIconProps & {
  size?: number | string;
};

/**
 * Иконка меню «Инструменты»: контурный портфель + залитый гаечный ключ.
 */
export default function ToolsIcon({ size, sx, ...props }: ToolsIconProps) {
  return (
    <SvgIcon
      viewBox="0 0 24 24"
      fill="none"
      sx={[
        size != null ? { fontSize: size, width: size, height: size } : null,
        ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
      ]}
      {...props}
    >
      {/* Briefcase — только контур */}
      <rect
        x="1.5"
        y="5.5"
        width="21"
        height="16"
        rx="2.25"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      <path
        d="M7 5.5V3.75A1.75 1.75 0 0 1 8.75 2h6.5A1.75 1.75 0 0 1 17 3.75V5.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />

      {/* Гаечный ключ — заливка */}
      <g transform="translate(12 14) scale(0.58) translate(-12 -12)">
        <path
          d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"
          fill="currentColor"
        />
      </g>
    </SvgIcon>
  );
}
