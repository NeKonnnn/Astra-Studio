import React from 'react';
import { SvgIcon, type SvgIconProps } from '@mui/material';

type ArtifactsIconProps = SvgIconProps & {
  size?: number | string;
};

/**
 * Иконка артефактов: кристалл.
 * Вписываем в квадрат целиком (как MUI outlined) — без «раздувания» относительно соседей.
 */
export default function ArtifactsIcon({ size, sx, ...props }: ArtifactsIconProps) {
  return (
    <SvgIcon
      viewBox="0 0 100 100"
      fill="currentColor"
      sx={[
        size != null ? { fontSize: size, width: size, height: size } : null,
        ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
      ]}
      {...props}
    >
      {/*
        Контент ~282×420, центр (175, 250).
        scale 0.26 — компромисс: крупнее «вписанного» 0.2, но без раздувания как 0.34.
      */}
      <g fill="currentColor" transform="translate(50 50) scale(0.26) translate(-175 -250)">
        <polygon points="175,40 108,232 242,232" />
        <polygon points="34,232 80,232 141,84" />
        <polygon points="316,232 270,232 209,84" />
        <polygon points="175,460 108,268 242,268" />
        <polygon points="34,268 80,268 141,416" />
        <polygon points="316,268 270,268 209,416" />
      </g>
    </SvgIcon>
  );
}
