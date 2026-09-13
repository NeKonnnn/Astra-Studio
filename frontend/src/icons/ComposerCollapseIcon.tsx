import React from 'react';
import { SvgIcon, type SvgIconProps } from '@mui/material';

type Props = SvgIconProps & { size?: number | string };

/** Свернуть — стрелки по диагонали внутрь (уголки у центра). */
export default function ComposerCollapseIcon({ size, sx, ...props }: Props) {
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
      {/* внутренний уголок сверху-справа */}
      <path
        d="M18.75 11H13V5.25"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M18.75 5.25L13 11"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* внутренний уголок снизу-слева */}
      <path
        d="M5.25 13H11v5.75"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M5.25 18.75L11 13"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </SvgIcon>
  );
}
