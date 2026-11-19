import React from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
} from '@mui/material';
import {
  ComputerOutlined as ComputerIcon,
  MemoryOutlined as MemoryIcon,
  StorageOutlined as StorageIcon,
  SpeedOutlined as SpeedIcon,
  DeveloperBoardOutlined as GpuIcon,
  SecurityOutlined as SecurityIcon,
} from '@mui/icons-material';
import UpdatesSettings from './UpdatesSettings';

const REQUIREMENTS = [
  {
    title: 'Диск',
    icon: StorageIcon,
    text: 'от 40 ГБ свободного места (образы Docker + базовые модели); для полного профиля со speech/vision — 80–150+ ГБ в зависимости от набора моделей',
  },
  {
    title: 'Оперативная память (RAM)',
    icon: MemoryIcon,
    text: 'минимум 16 ГБ; рекомендуется 32 ГБ и выше при одновременной работе LLM, RAG и speech-сервисов',
  },
  {
    title: 'CPU',
    icon: SpeedIcon,
    text: 'от 4 современных ядер (x86_64); для комфортной работы на CPU без GPU — 8+ ядер',
  },
  {
    title: 'GPU (опционально)',
    icon: GpuIcon,
    text: 'NVIDIA с CUDA и ≥ 8 ГБ VRAM для ускорения LLM / STT / OCR / генерации изображений; без GPU платформа работает в CPU-режиме (медленнее)',
  },
] as const;

export default function AboutSettings() {
  return (
    <Box sx={{ p: 3 }}>
      <UpdatesSettings />

      {/* Техническая информация / минимальные системные требования */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <ComputerIcon color="primary" />
            Техническая информация
          </Typography>

          <Typography variant="subtitle1" fontWeight={600} gutterBottom>
            Платформы
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Windows 10/11 (Docker Desktop) и Linux (Docker Engine). GPU-ускорение — на Linux или WSL2 с
            NVIDIA Container Toolkit и драйвером NVIDIA.
          </Typography>

          <Typography variant="subtitle1" fontWeight={600} gutterBottom>
            Минимальные системные требования
          </Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {REQUIREMENTS.map(({ title, icon: Icon, text }) => (
              <Box key={title}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                  <Icon sx={{ fontSize: 22, color: 'text.primary', flexShrink: 0 }} />
                  <Typography variant="subtitle1" fontWeight={700} sx={{ lineHeight: 1.3 }}>
                    {title}
                  </Typography>
                </Box>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ pl: '30px', lineHeight: 1.5 }}
                >
                  {text}
                </Typography>
              </Box>
            ))}
          </Box>
        </CardContent>
      </Card>

      {/* Безопасность и лицензия */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <SecurityIcon color="primary" />
            Безопасность и лицензия
          </Typography>

          <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, gap: 3 }}>
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Лицензия
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                MIT License
              </Typography>

              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Безопасность
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                Локальная обработка данных
              </Typography>
            </Box>

            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Конфиденциальность
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                Данные не передаются третьим лицам
              </Typography>

              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Обновления
              </Typography>
              <Typography variant="body1">
                Проверка и установка — в блоке «Обновления» выше
              </Typography>
            </Box>
          </Box>
        </CardContent>
      </Card>
    </Box>
  );
}
