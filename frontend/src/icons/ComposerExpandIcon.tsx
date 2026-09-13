import React from 'react';
import { SvgIcon, type SvgIconProps } from '@mui/material';

type Props = SvgIconProps & { size?: number | string };

/** Развернуть — стрелки по диагонали наружу. */
export default function ComposerExpandIcon({ size, sx, ...props }: Props) {
  return (
    <SvgIcon
      viewBox="0 0 24 24"
      fill="none"
      sx={[
        size != null ? { fontSize: size, width: size, height: size } : null,
        { display: 'block' },
        ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
      ]}
      {...props}
    >
      {/* ↗ */}
      <path
        d="M13 5.25h5.75V11"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M18.75 5.25L11.5 12.5"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* ↙ */}
      <path
        d="M11 18.75H5.25V13"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M5.25 18.75L12.5 11.5"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </SvgIcon>
  );
}
