import React from 'react';
import {
  Box,
  Button,
  Card,
  CardActions,
  CardContent,
  CircularProgress,
  IconButton,
  Rating,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  Bookmark as BookmarkIcon,
  BookmarkBorder as BookmarkBorderIcon,
  ContentCopy as CopyIcon,
  Person as PersonIcon,
  TrendingUp as TrendingUpIcon,
  Visibility as ViewIcon,
} from '@mui/icons-material';
import type { GalleryCardItem } from './types';

export interface GalleryEntityCardProps {
  item: GalleryCardItem;
  isDarkMode: boolean;
  using?: boolean;
  cloning?: boolean;
  useLabel?: string;
  cloneLabel?: string;
  onOpen: () => void;
  onUse: () => void;
  onClone?: () => void;
  onRate?: (rating: number) => void;
  onToggleBookmark?: () => void;
}

/** Унифицированная карточка сущности галереи (агент / skill). */
export default function GalleryEntityCard({
  item,
  isDarkMode,
  using = false,
  cloning = false,
  useLabel = 'Использовать',
  cloneLabel = 'Клонировать',
  onOpen,
  onUse,
  onClone,
  onRate,
  onToggleBookmark,
}: GalleryEntityCardProps) {
  return (
    <Card
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: isDarkMode ? undefined : '#ffffff',
        boxShadow: isDarkMode ? undefined : '0 2px 8px rgba(0,0,0,0.1)',
        border: isDarkMode ? undefined : '1px solid rgba(0,0,0,0.08)',
        cursor: 'pointer',
        transition: 'box-shadow 0.15s ease',
        '&:hover': {
          boxShadow: isDarkMode ? '0 4px 16px rgba(0,0,0,0.45)' : '0 4px 16px rgba(0,0,0,0.14)',
        },
      }}
      onClick={onOpen}
    >
      <CardContent sx={{ flex: 1 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
          <Typography variant="h6" fontWeight="bold" sx={{ flex: 1, pr: 1 }}>
            {item.title}
          </Typography>
          {onToggleBookmark && (
            <Box onClick={(e) => e.stopPropagation()}>
              <Tooltip title={item.isBookmarked ? 'Удалить из закладок' : 'Добавить в закладки'}>
                <IconButton size="small" onClick={onToggleBookmark}>
                  {item.isBookmarked ? (
                    <BookmarkIcon fontSize="small" color="primary" />
                  ) : (
                    <BookmarkBorderIcon fontSize="small" />
                  )}
                </IconButton>
              </Tooltip>
            </Box>
          )}
        </Box>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
          <PersonIcon fontSize="small" color="action" />
          <Typography variant="caption" color="text.secondary">
            {item.authorName}
          </Typography>
        </Box>

        <Typography
          variant="body2"
          color="text.secondary"
          sx={{
            mb: 2,
            display: '-webkit-box',
            WebkitLineClamp: 4,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {item.preview}
        </Typography>

        {onRate && (
          <Box
            sx={{ display: 'flex', alignItems: 'center', gap: 1 }}
            onClick={(e) => e.stopPropagation()}
          >
            <Rating
              value={item.averageRating ?? 0}
              precision={0.1}
              readOnly={!!item.userRating}
              onChange={(_, value) => {
                if (value !== null) onRate(Math.round(value));
              }}
            />
            <Typography variant="caption" color="text.secondary">
              {Number(item.averageRating || 0).toFixed(1)} ({item.totalVotes || 0})
              {item.userRating ? ` • Ваша: ${item.userRating}` : ''}
            </Typography>
          </Box>
        )}

        <Box sx={{ display: 'flex', gap: 2, mt: 1, flexWrap: 'wrap' }}>
          <Tooltip title="Просмотров">
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <ViewIcon fontSize="small" color="action" />
              <Typography variant="caption">{item.viewsCount || 0}</Typography>
            </Box>
          </Tooltip>
          <Tooltip title="Использований">
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <TrendingUpIcon fontSize="small" color="action" />
              <Typography variant="caption">{item.usageCount || 0}</Typography>
            </Box>
          </Tooltip>
          {item.metaLine && (
            <Typography variant="caption" color="text.secondary">
              {item.metaLine}
            </Typography>
          )}
        </Box>
      </CardContent>

      <CardActions
        onClick={(e) => e.stopPropagation()}
        sx={{
          display: 'flex',
          gap: 1,
          px: 2,
          pb: 2,
          pt: 0,
          flexWrap: 'wrap',
        }}
      >
        <Button
          size="small"
          startIcon={using ? <CircularProgress size={14} /> : <CopyIcon />}
          onClick={onUse}
          disabled={using || cloning}
          fullWidth={!onClone}
          variant="contained"
          sx={onClone ? { flex: 1, minWidth: 0 } : undefined}
        >
          {useLabel}
        </Button>
        {onClone && (
          <Button
            size="small"
            startIcon={cloning ? <CircularProgress size={14} /> : <CopyIcon />}
            onClick={onClone}
            disabled={using || cloning}
            variant="outlined"
            sx={{ flex: 1, minWidth: 0 }}
          >
            {cloneLabel}
          </Button>
        )}
      </CardActions>
    </Card>
  );
}
