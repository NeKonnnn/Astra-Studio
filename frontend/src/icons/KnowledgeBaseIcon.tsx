import React, { useId } from 'react';
import { SvgIcon, type SvgIconProps } from '@mui/material';

type KnowledgeBaseIconProps = SvgIconProps & {
  size?: number | string;
};

/**
 * Иконка базы знаний: цилиндр БД + круг с плюсом.
 * ViewBox и толщины линий подобраны так, чтобы на 18px
 * визуальный размер совпадал со Skills.
 */
export default function KnowledgeBaseIcon({ size, sx, ...props }: KnowledgeBaseIconProps) {
  const uid = useId().replace(/:/g, '');
  const maskId = `kb-gap-mask-${uid}`;

  return (
    <SvgIcon
      viewBox="40 10 430 480"
      fill="none"
      sx={[
        size != null ? { fontSize: size, width: size, height: size } : null,
        ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
      ]}
      {...props}
    >
      <defs>
        <mask id={maskId}>
          <rect x="40" y="10" width="430" height="480" fill="#fff" />
          <circle cx="366" cy="366" r="118" fill="#000" />
        </mask>
      </defs>

      <g
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <g mask={`url(#${maskId})`}>
          <ellipse cx="232" cy="116" rx="146" ry="66" strokeWidth="36" />
          <line x1="86" y1="116" x2="86" y2="396" strokeWidth="36" />
          <line x1="378" y1="116" x2="378" y2="396" strokeWidth="36" />
          <path d="M86 210 C86 276 378 276 378 210" strokeWidth="34" />
          <path d="M86 304 C86 370 378 370 378 304" strokeWidth="34" />
          <path d="M86 396 C86 462 378 462 378 396" strokeWidth="36" />
        </g>

        <circle cx="366" cy="366" r="82" strokeWidth="36" />
        <line x1="324" y1="366" x2="408" y2="366" strokeWidth="34" />
        <line x1="366" y1="324" x2="366" y2="408" strokeWidth="34" />
      </g>
    </SvgIcon>
  );
}
