import React, { useEffect, useState } from 'react';
import { Box, CircularProgress, SxProps, Theme } from '@mui/material';
import { authFetch, getApiUrl } from '../config/api';
import { useAuth } from '../contexts/AuthContext';

function resolveFetchUrl(src: string): string {
  if (src.startsWith('data:') || src.startsWith('blob:') || /^https?:\/\//i.test(src)) {
    return src;
  }
  return getApiUrl(src.startsWith('/') ? src : `/${src}`);
}

/** Превью из MinIO: JWT + cookie client binding (без credentials img/fetch → 401 SEC007). */
export default function AuthenticatedInlineImage({
  src,
  alt,
  onExpand,
  sx,
}: {
  src: string;
  alt: string;
  onExpand?: (resolvedSrc: string) => void;
  sx?: SxProps<Theme>;
}): React.ReactElement | null {
  const { token } = useAuth();
  const [displaySrc, setDisplaySrc] = useState<string | null>(src.startsWith('data:') || src.startsWith('blob:') ? src : null);

  useEffect(() => {
    if (src.startsWith('data:') || src.startsWith('blob:')) {
      setDisplaySrc(src);
      return;
    }
    if (!token) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    (async () => {
      try {
        const res = await authFetch(resolveFetchUrl(src));
        if (!res.ok || cancelled) return;
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setDisplaySrc(objectUrl);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [src, token]);

  if (!displaySrc) {
    return (
      <Box
        sx={{
          ...(sx as object),
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          minWidth: 72,
          minHeight: 72,
        }}
      >
        <CircularProgress size={22} />
      </Box>
    );
  }

  return (
    <Box
      component="img"
      src={displaySrc}
      alt={alt}
      onClick={() => onExpand?.(displaySrc)}
      sx={sx}
    />
  );
}
