import React from 'react';
import { Box, type SxProps, type Theme } from '@mui/material';
import CloudOutlinedIcon from '@mui/icons-material/CloudOutlined';
import type { IntegrationId } from './catalog';

type IconProps = {
  sx?: SxProps<Theme>;
  size?: number;
};

function flatten(sx?: SxProps<Theme>): object {
  if (!sx) return {};
  if (Array.isArray(sx)) return Object.assign({}, ...sx.filter(Boolean));
  return typeof sx === 'object' ? sx : {};
}

function IconShell({
  children,
  sx,
  size = 28,
  bgcolor = 'transparent',
}: {
  children: React.ReactNode;
  sx?: SxProps<Theme>;
  size?: number;
  bgcolor?: string;
}) {
  return (
    <Box
      sx={{
        width: size,
        height: size,
        borderRadius: '8px',
        bgcolor,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        overflow: 'hidden',
        ...flatten(sx),
      }}
    >
      {children}
    </Box>
  );
}

function Svg({
  children,
  viewBox = '0 0 24 24',
  size = 22,
}: {
  children: React.ReactNode;
  viewBox?: string;
  size?: number;
}) {
  return (
    <Box
      component="svg"
      viewBox={viewBox}
      sx={{ width: size, height: size, display: 'block' }}
      aria-hidden
    >
      {children}
    </Box>
  );
}

/** Официальные/узнаваемые бренд-логотипы (как в Connectors UI). */
export function IntegrationIcon({ id, sx, size = 28 }: IconProps & { id: IntegrationId }) {
  const logo = size - 6;

  switch (id) {
    case 'gmail':
      return (
        <IconShell sx={sx} size={size}>
          <Svg size={logo} viewBox="0 0 24 24">
            <path fill="#EA4335" d="M24 4.5v15c0 .83-.67 1.5-1.5 1.5H18V10.2l-6 4.5-6-4.5V21H1.5C.67 21 0 20.33 0 19.5v-15c0-.45.2-.86.52-1.14L11.4 11.7c.36.27.84.27 1.2 0L23.48 3.36c.32.28.52.69.52 1.14z" />
            <path fill="#34A853" d="M0 4.5V8.1l6 4.5V2.1L1.5 3.36C.6 3.72 0 4.55 0 4.5z" />
            <path fill="#4285F4" d="M24 4.5c0 .05-.6-.78-1.5-1.14L18 2.1v10.5l6-4.5V4.5z" />
            <path fill="#C5221F" d="M0 19.5V21h6v-8.4L0 8.1v11.4z" />
            <path fill="#FBBC04" d="M18 12.6V21h4.5c.83 0 1.5-.67 1.5-1.5V8.1l-6 4.5z" />
          </Svg>
        </IconShell>
      );

    case 'google-drive':
      return (
        <IconShell sx={sx} size={size}>
          <Svg size={logo} viewBox="0 0 87.3 78">
            <path
              fill="#0066DA"
              d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8H0c0 1.55.4 3.1 1.2 4.5z"
            />
            <path
              fill="#00AC47"
              d="M43.65 25 29.9 1.2c-1.35.8-2.5 1.9-3.3 3.3L1.2 45.35c-.8 1.4-1.2 2.95-1.2 4.5h27.5z"
            />
            <path
              fill="#EA4335"
              d="M73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75L62.4 48H35.9l13.75 23.8z"
            />
            <path
              fill="#00832D"
              d="M43.65 25 57.4 1.2C56.05.4 54.5 0 52.9 0H34.4c-1.6 0-3.15.45-4.5 1.2z"
            />
            <path fill="#2684FC" d="M87.3 49.85c0-1.55-.4-3.1-1.2-4.5L64.95 10.2c-.8-1.4-1.95-2.5-3.3-3.3L47.9 30.7l13.75 23.8h24.3c.85-1.3 1.35-2.9 1.35-4.65z" />
            <path fill="#FFBA00" d="M59.8 54.55H27.5L13.75 30.75 6.6 42.95l16.9 29.2c.8 1.4 1.95 2.5 3.3 3.3L59.8 54.55z" />
          </Svg>
        </IconShell>
      );

    case 'github':
      return (
        <IconShell sx={sx} size={size} bgcolor="#24292F">
          <Svg size={logo * 0.85} viewBox="0 0 24 24">
            <path
              fill="#fff"
              d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.1c-3.3.7-4-1.6-4-1.6-.5-1.4-1.3-1.7-1.3-1.7-1-.7.1-.7.1-.7 1.1.1 1.7 1.2 1.7 1.2 1 .1.8 1.7 2.8 1.2.1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-6 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.4 11.4 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.6 1.6.2 2.8.1 3.1.8.8 1.2 1.9 1.2 3.2 0 4.7-2.8 5.7-5.5 6 .4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6A12 12 0 0 0 12 .3z"
            />
          </Svg>
        </IconShell>
      );

    case 'notion':
      return (
        <IconShell sx={sx} size={size} bgcolor="#fff">
          <Svg size={logo * 0.9} viewBox="0 0 24 24">
            <path
              fill="#000"
              d="M4.5 3.5h12.3c.5 0 1 .2 1.3.6l1.6 2c.3.4.4.8.4 1.3v11.7c0 .9-.7 1.6-1.6 1.6H6.2c-.5 0-1-.2-1.3-.6L3.3 17.6A2 2 0 0 1 3 16.4V5.1c0-.9.7-1.6 1.5-1.6zm1.2 2.2v11.6l1.7 1.5h10.1V7.3l-1.5-1.6H5.7zm2.7 2.3h7.4v1.5H11l3.8 5.7h-1.9l-3.5-5.2v5.2H7.9V8z"
            />
          </Svg>
        </IconShell>
      );

    case 'outlook':
      return (
        <IconShell sx={sx} size={size}>
          <Svg size={logo} viewBox="0 0 24 24">
            <path fill="#0A66C2" d="M12.5 5.5h9A1.5 1.5 0 0 1 23 7v10a1.5 1.5 0 0 1-1.5 1.5h-9V5.5z" />
            <path fill="#28A8EA" d="M12.5 5.5 23 11.2V7a1.5 1.5 0 0 0-1.5-1.5h-9z" />
            <path fill="#0072C6" d="M12.5 18.5 23 12.8V17a1.5 1.5 0 0 1-1.5 1.5h-9z" />
            <path fill="#0364B8" d="M2.8 6.2h10.4v11.6H2.8A1.8 1.8 0 0 1 1 16V8a1.8 1.8 0 0 1 1.8-1.8z" />
            <ellipse cx="8" cy="12" rx="3.2" ry="3.6" fill="#fff" />
            <ellipse cx="8" cy="12" rx="1.7" ry="2.1" fill="#0364B8" />
          </Svg>
        </IconShell>
      );

    case 'onedrive':
      return (
        <IconShell sx={sx} size={size}>
          <Svg size={logo} viewBox="0 0 24 24">
            <path
              fill="#0364B8"
              d="M14.7 8.2c.6-1.7 2.3-2.9 4.2-2.9 2.2 0 4 1.5 4.5 3.6.9.3 1.6 1.2 1.6 2.3 0 1.4-1.1 2.5-2.5 2.5H8.6c-1.8 0-3.3-1.4-3.4-3.2-.1-1.9 1.4-3.5 3.3-3.5.5 0 1 .1 1.4.3.7-1.5 2.2-2.5 3.9-2.5 1.3 0 2.5.6 3.3 1.5z"
            />
            <path
              fill="#0078D4"
              d="M2.2 14.2c0-1.7 1.2-3.1 2.8-3.5.3-1.7 1.8-3 3.6-3 .6 0 1.2.2 1.7.4.8-1.3 2.2-2.1 3.8-2.1 2.1 0 3.9 1.5 4.3 3.5 1.5.1 2.7 1.4 2.7 2.9 0 1.6-1.3 2.9-2.9 2.9H5.1c-1.6 0-2.9-1.3-2.9-2.9z"
              opacity={0.95}
            />
            <path
              fill="#28A8EA"
              d="M5.4 16.2h13.3c1.2 0 2.2-1 2.2-2.2 0-1-.7-1.9-1.6-2.1-.3-1.8-1.9-3.2-3.8-3.2-1 0-1.9.4-2.6 1-.7-.5-1.6-.8-2.5-.8-1.7 0-3.1 1.1-3.6 2.7-.9.3-1.6 1.2-1.6 2.2.1 1.3 1.1 2.4 2.2 2.4z"
            />
          </Svg>
        </IconShell>
      );

    case 'yandex-disk':
      return (
        <IconShell sx={sx} size={size} bgcolor="#fff">
          <Svg size={logo} viewBox="0 0 24 24">
            <path
              fill="#FC3F1D"
              d="M12 2.2 4.2 16.1h3.6L12 8.6l4.2 7.5h3.6L12 2.2zm-5.4 15.4L12 21.8l5.4-4.2H6.6z"
            />
          </Svg>
        </IconShell>
      );

    case 'yandex-mail':
      return (
        <IconShell sx={sx} size={size} bgcolor="#FC3F1D">
          <Svg size={logo * 0.85} viewBox="0 0 24 24">
            <path
              fill="#fff"
              d="M3.5 5.5h17A1.5 1.5 0 0 1 22 7v10a1.5 1.5 0 0 1-1.5 1.5h-17A1.5 1.5 0 0 1 2 17V7a1.5 1.5 0 0 1 1.5-1.5zm.7 2.1L12 13.2l7.8-5.6v-.7H4.2v.7zm0 1.8V16.5h15.6V9.4L12 15 4.2 9.4z"
            />
          </Svg>
        </IconShell>
      );

    case 'yandex-calendar':
      return (
        <IconShell sx={sx} size={size} bgcolor="#FC3F1D">
          <Svg size={logo * 0.85} viewBox="0 0 24 24">
            <path
              fill="#fff"
              d="M7 3.5h2v2h6v-2h2v2h2.5A1.5 1.5 0 0 1 21 7v12.5A1.5 1.5 0 0 1 19.5 21h-15A1.5 1.5 0 0 1 3 19.5V7A1.5 1.5 0 0 1 4.5 5.5H7v-2zm12 7.5H5v8.5h14V11zm-8.5 2h2v2h-2v-2zm4 0h2v2h-2v-2zm-8 0h2v2H7.5v-2z"
            />
          </Svg>
        </IconShell>
      );

    case 'yandex-meetings':
      return (
        <IconShell sx={sx} size={size} bgcolor="#FC3F1D">
          <Svg size={logo * 0.9} viewBox="0 0 24 24">
            <path
              fill="#fff"
              d="M3.5 7.5A2.5 2.5 0 0 1 6 5h8a2.5 2.5 0 0 1 2.5 2.5v9A2.5 2.5 0 0 1 14 19H6a2.5 2.5 0 0 1-2.5-2.5v-9zm14.2 1.6 3.1-2.1c.5-.3 1.2 0 1.2.7v8.6c0 .6-.7 1-1.2.7l-3.1-2.1v-5.8z"
            />
          </Svg>
        </IconShell>
      );

    default:
      return (
        <IconShell sx={sx} size={size} bgcolor="rgba(127,127,127,0.2)">
          <CloudOutlinedIcon sx={{ fontSize: logo * 0.85, opacity: 0.8 }} />
        </IconShell>
      );
  }
}

/** Иконка пункта меню «Инструменты → Интеграции». */
export function GearMenuIntegrationsIcon({ sx }: { sx?: SxProps<Theme> }) {
  return <CloudOutlinedIcon sx={sx} />;
}
