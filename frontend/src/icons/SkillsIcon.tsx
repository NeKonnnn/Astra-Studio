import React from 'react';
import { SvgIcon, type SvgIconProps } from '@mui/material';

type SkillsIconProps = SvgIconProps & {
  size?: number | string;
};

/**
 * Иконка Skills: планшет со строками и звёздами.
 * Крупнее в viewBox, толще линии; звёзды залиты — читаются на мелком размере.
 */
export default function SkillsIcon({ size, sx, ...props }: SkillsIconProps) {
  return (
    <SvgIcon
      viewBox="12 2 76 94"
      fill="none"
      sx={[
        size != null ? { fontSize: size, width: size, height: size } : null,
        ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
      ]}
      {...props}
    >
      {/* Clipboard body */}
      <path
        d="M27 13h46a7.5 7.5 0 0 1 7.5 7.5v59a7.5 7.5 0 0 1-7.5 7.5H27a7.5 7.5 0 0 1-7.5-7.5v-59A7.5 7.5 0 0 1 27 13z"
        stroke="currentColor"
        strokeWidth="5.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />

      {/* Clip */}
      <path
        d="M40 13V8.5a4 4 0 0 1 4-4h12a4 4 0 0 1 4 4V13"
        stroke="currentColor"
        strokeWidth="5.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />

      {/* Lines — чуть длиннее и толще */}
      <path
        d="M30 30h20"
        stroke="currentColor"
        strokeWidth="6"
        strokeLinecap="round"
        fill="none"
      />
      <path
        d="M30 51h20"
        stroke="currentColor"
        strokeWidth="6"
        strokeLinecap="round"
        fill="none"
      />
      <path
        d="M30 72h20"
        stroke="currentColor"
        strokeWidth="6"
        strokeLinecap="round"
        fill="none"
      />

      {/* Stars — заливка, без обводки: на 18px читаются как звёзды, а не «кляксы» */}
      <path
        d="M66 22l2.8 5.6 6.2.95-4.5 4.4 1.05 6.2-5.55-2.9-5.55 2.9 1.05-6.2-4.5-4.4 6.2-.95z"
        fill="currentColor"
      />
      <path
        d="M66 43l2.8 5.6 6.2.95-4.5 4.4 1.05 6.2-5.55-2.9-5.55 2.9 1.05-6.2-4.5-4.4 6.2-.95z"
        fill="currentColor"
      />
      <path
        d="M66 64l2.8 5.6 6.2.95-4.5 4.4 1.05 6.2-5.55-2.9-5.55 2.9 1.05-6.2-4.5-4.4 6.2-.95z"
        fill="currentColor"
      />
    </SvgIcon>
  );
}
