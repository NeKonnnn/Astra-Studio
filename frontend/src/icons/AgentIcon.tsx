import React from 'react';
import { SvgIcon, type SvgIconProps } from '@mui/material';

type AgentIconProps = SvgIconProps & {
  size?: number | string;
};

/**
 * Иконка агента: дружелюбный AI-робот (антенна, ушки, глаза, улыбка).
 * Масштаб и толщина линий подогнаны под MUI Outlined (как WidgetsOutlined).
 */
export default function AgentIcon({ size, sx, ...props }: AgentIconProps) {
  return (
    <SvgIcon
      viewBox="0 0 100 100"
      sx={[
        size != null ? { fontSize: size, width: size, height: size } : null,
        ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
      ]}
      {...props}
    >
      {/* Антенна */}
      <path
        d="M50 22 L50 15"
        stroke="currentColor"
        strokeWidth="6.5"
        strokeLinecap="round"
        fill="none"
      />
      <circle
        cx="50"
        cy="10"
        r="4"
        stroke="currentColor"
        strokeWidth="6.5"
        fill="none"
      />

      {/* Главный корпус головы */}
      <rect
        x="17"
        y="24"
        width="66"
        height="60"
        rx="18"
        stroke="currentColor"
        strokeWidth="6.5"
        fill="none"
      />

      {/* Компактные боковые ушки */}
      <rect
        x="9"
        y="44"
        width="8"
        height="20"
        rx="4"
        stroke="currentColor"
        strokeWidth="6.5"
        fill="none"
      />
      <rect
        x="83"
        y="44"
        width="8"
        height="20"
        rx="4"
        stroke="currentColor"
        strokeWidth="6.5"
        fill="none"
      />

      {/* Большие дружелюбные AI-глаза */}
      <circle cx="38" cy="49" r="5.5" fill="currentColor" />
      <circle cx="62" cy="49" r="5.5" fill="currentColor" />

      {/* Аккуратная улыбка */}
      <path
        d="M39 63 Q50 72 61 63"
        stroke="currentColor"
        strokeWidth="6.5"
        strokeLinecap="round"
        fill="none"
      />
    </SvgIcon>
  );
}
