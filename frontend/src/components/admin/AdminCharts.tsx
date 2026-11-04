import React, { useId, useMemo } from 'react';
import { Box, Typography, useTheme } from '@mui/material';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  PieChart,
  Pie,
  Cell,
} from 'recharts';

export type AdminAreaPoint = {
  label: string;
  value: number;
};

export type AdminDonutSlice = {
  name: string;
  value: number;
  color?: string;
};

const DEFAULT_DONUT_COLORS = [
  '#4caf50',
  '#2196f3',
  '#26a69a',
  '#ff9800',
  '#ab47bc',
  '#42a5f5',
  '#66bb6a',
  '#ffa726',
];

type AreaTrendProps = {
  isDarkMode?: boolean;
  data: AdminAreaPoint[];
  title: string;
  subtitle?: string;
  kpi?: string | number;
  kpiHint?: string;
  trendPct?: number | null;
  color?: string;
  height?: number;
  valueLabel?: string;
};

/** Area-график в стиле KPI-карточки: линия + мягкая заливка. */
export function AdminAreaTrendCard({
  isDarkMode = true,
  data,
  title,
  subtitle,
  kpi,
  kpiHint,
  trendPct,
  color,
  height = 240,
  valueLabel = 'Значение',
}: AreaTrendProps) {
  const theme = useTheme();
  const gradId = useId().replace(/:/g, '');
  const stroke = color || theme.palette.success.main;
  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const axis = isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';
  const grid = isDarkMode ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)';
  const trendPositive = trendPct == null ? null : trendPct >= 0;

  return (
    <Box>
      <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 0.25 }}>
        {title}
      </Typography>
      {subtitle ? (
        <Typography variant="body2" sx={{ color: muted, mb: 1 }}>
          {subtitle}
        </Typography>
      ) : null}

      {kpi != null ? (
        <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 0.5, flexWrap: 'wrap' }}>
          <Typography variant="h4" fontWeight={700} sx={{ lineHeight: 1.1 }}>
            {kpi}
          </Typography>
          {trendPct != null && Number.isFinite(trendPct) ? (
            <Box
              sx={{
                px: 0.85,
                py: 0.2,
                borderRadius: 999,
                fontSize: '0.72rem',
                fontWeight: 700,
                bgcolor: trendPositive
                  ? 'rgba(76,175,80,0.18)'
                  : 'rgba(244,67,54,0.18)',
                color: trendPositive ? '#66bb6a' : '#ef5350',
              }}
            >
              {trendPositive ? '↑' : '↓'} {Math.abs(trendPct).toFixed(1)}%
            </Box>
          ) : null}
        </Box>
      ) : null}
      {kpiHint ? (
        <Typography variant="caption" sx={{ color: muted, display: 'block', mb: 1.25 }}>
          {kpiHint}
        </Typography>
      ) : null}

      <Box sx={{ width: '100%', height }}>
        <ResponsiveContainer>
          <AreaChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
            <defs>
              <linearGradient id={`admin-area-${gradId}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity={0.35} />
                <stop offset="85%" stopColor={stroke} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={grid} vertical={false} strokeDasharray="3 6" />
            <XAxis
              dataKey="label"
              tick={{ fill: axis, fontSize: 11 }}
              axisLine={{ stroke: grid }}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: axis, fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              allowDecimals={false}
            />
            <Tooltip
              contentStyle={{
                background: isDarkMode ? 'rgba(30,30,34,0.96)' : '#fff',
                border: isDarkMode
                  ? '1px solid rgba(255,255,255,0.12)'
                  : '1px solid rgba(0,0,0,0.1)',
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={(value) => [value ?? 0, valueLabel]}
            />
            <Area
              type="monotone"
              dataKey="value"
              name={valueLabel}
              stroke={stroke}
              strokeWidth={2.4}
              fill={`url(#admin-area-${gradId})`}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </Box>
    </Box>
  );
}

type DonutProps = {
  isDarkMode?: boolean;
  title: string;
  subtitle?: string;
  data: AdminDonutSlice[];
  height?: number;
  centerLabel?: string;
  centerValue?: string | number;
};

/** Donut для долей (usage mix / топ сущностей). */
export function AdminDonutCard({
  isDarkMode = true,
  title,
  subtitle,
  data,
  height = 240,
  centerLabel,
  centerValue,
}: DonutProps) {
  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const text = isDarkMode ? 'rgba(255,255,255,0.92)' : 'rgba(0,0,0,0.88)';

  const slices = useMemo(() => {
    const cleaned = data
      .map((d, i) => ({
        ...d,
        value: Number(d.value) || 0,
        color: d.color || DEFAULT_DONUT_COLORS[i % DEFAULT_DONUT_COLORS.length],
      }))
      .filter((d) => d.value > 0);
    return cleaned;
  }, [data]);

  const total = useMemo(
    () => slices.reduce((acc, s) => acc + s.value, 0),
    [slices],
  );

  return (
    <Box>
      <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 0.25 }}>
        {title}
      </Typography>
      {subtitle ? (
        <Typography variant="body2" sx={{ color: muted, mb: 1 }}>
          {subtitle}
        </Typography>
      ) : (
        <Box sx={{ mb: 1 }} />
      )}

      {slices.length === 0 ? (
        <Typography variant="body2" sx={{ color: muted, py: 6, textAlign: 'center' }}>
          Пока нет данных
        </Typography>
      ) : (
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', sm: '1.1fr 0.9fr' },
            gap: 1,
            alignItems: 'center',
          }}
        >
          <Box sx={{ width: '100%', height, position: 'relative' }}>
            <ResponsiveContainer>
              <PieChart>
                <Pie
                  data={slices}
                  dataKey="value"
                  nameKey="name"
                  innerRadius="62%"
                  outerRadius="88%"
                  paddingAngle={2}
                  stroke="none"
                >
                  {slices.map((s) => (
                    <Cell key={s.name} fill={s.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: isDarkMode ? 'rgba(30,30,34,0.96)' : '#fff',
                    border: isDarkMode
                      ? '1px solid rgba(255,255,255,0.12)'
                      : '1px solid rgba(0,0,0,0.1)',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(value, name) => {
                    const n = Number(value ?? 0) || 0;
                    const pct = total > 0 ? ((n / total) * 100).toFixed(1) : '0';
                    return [`${n} (${pct}%)`, String(name ?? '')];
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
            {(centerValue != null || centerLabel) && (
              <Box
                sx={{
                  position: 'absolute',
                  inset: 0,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  pointerEvents: 'none',
                }}
              >
                {centerValue != null ? (
                  <Typography variant="h5" fontWeight={700} sx={{ color: text, lineHeight: 1.1 }}>
                    {centerValue}
                  </Typography>
                ) : null}
                {centerLabel ? (
                  <Typography variant="caption" sx={{ color: muted }}>
                    {centerLabel}
                  </Typography>
                ) : null}
              </Box>
            )}
          </Box>

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, pr: 0.5 }}>
            {slices.map((s) => {
              const pct = total > 0 ? ((s.value / total) * 100).toFixed(0) : '0';
              return (
                <Box
                  key={s.name}
                  sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}
                >
                  <Box
                    sx={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      bgcolor: s.color,
                      flexShrink: 0,
                    }}
                  />
                  <Typography
                    variant="body2"
                    noWrap
                    sx={{ flex: 1, minWidth: 0, color: text, fontSize: '0.8rem' }}
                  >
                    {s.name}
                  </Typography>
                  <Typography variant="caption" sx={{ color: muted, flexShrink: 0 }}>
                    {pct}%
                  </Typography>
                </Box>
              );
            })}
          </Box>
        </Box>
      )}
    </Box>
  );
}

/** Сравнение последних 7 дней vs предыдущих 7 по ряду. */
export function calcSeriesTrendPct(values: number[]): number | null {
  if (values.length < 4) return null;
  const mid = Math.floor(values.length / 2);
  const prev = values.slice(0, mid);
  const next = values.slice(mid);
  const sum = (arr: number[]) => arr.reduce((a, b) => a + (Number(b) || 0), 0);
  const a = sum(prev);
  const b = sum(next);
  if (a <= 0 && b <= 0) return null;
  if (a <= 0) return 100;
  return ((b - a) / a) * 100;
}
