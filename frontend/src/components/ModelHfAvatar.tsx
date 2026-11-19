import React, { useEffect, useState } from 'react';
import { Avatar, Box } from '@mui/material';
import { Computer as ComputerIcon } from '@mui/icons-material';
import { authFetch, getApiUrl } from '../config/api';

const avatarCache = new Map<string, string | null>();

/** Угадываем org/repo только для запроса аватара (важен org). */
export function guessHfRepoId(pathOrName?: string | null): string | undefined {
  const raw = String(pathOrName || '').trim();
  if (!raw) return undefined;
  const base = raw.replace(/\\/g, '/').split('/').pop() || raw;
  const stem = base.replace(/\.gguf$/i, '');
  const lower = stem.toLowerCase();

  const segs = raw.replace(/\\/g, '/').split('/').filter(Boolean);
  if (segs.length >= 2) {
    const a = segs[0];
    const b = segs[1];
    if (
      a
      && b
      && !['local', 'llm-svc', 'vllm', 'ollama', 'openai', 'anthropic', 'models', 'llm'].includes(a.toLowerCase())
      && !a.includes('.')
    ) {
      return `${a}/${b.replace(/\.gguf$/i, '')}`;
    }
  }

  if (lower.includes('unsloth') || /-ud-/i.test(stem)) return 'unsloth/models';
  if (lower.includes('deepseek')) return 'deepseek-ai/DeepSeek';
  if (lower.includes('mistral') || lower.includes('mixtral')) return 'mistralai/Mistral';
  if (lower.includes('gemma')) return 'google/gemma';
  if (lower.includes('phi-') || lower.startsWith('phi')) return 'microsoft/phi';
  if (lower.includes('llama') || lower.includes('codellama')) return 'meta-llama/Llama';
  if (lower.includes('qwen')) return 'Qwen/Qwen';
  if (lower.includes('yi-') || lower.startsWith('yi')) return '01-ai/Yi';
  if (lower.includes('vicuna')) return 'lmsys/vicuna';
  if (lower.includes('wizard')) return 'WizardLM/WizardLM';
  return undefined;
}

type Props = {
  avatarUrl?: string | null;
  repoId?: string | null;
  /** path / filename — для угадывания org, если repoId нет */
  modelKey?: string | null;
  size?: number;
  iconFontSize?: number | string;
  iconColor?: string;
  rounded?: boolean;
};

export default function ModelHfAvatar({
  avatarUrl,
  repoId,
  modelKey,
  size = 18,
  iconFontSize,
  iconColor,
  rounded = false,
}: Props) {
  const [src, setSrc] = useState<string | undefined>(avatarUrl || undefined);
  const resolvedRepo = repoId || guessHfRepoId(modelKey) || guessHfRepoId(repoId);

  useEffect(() => {
    setSrc(avatarUrl || undefined);
  }, [avatarUrl, modelKey, repoId]);

  useEffect(() => {
    if (src || !resolvedRepo) return;
    if (avatarCache.has(resolvedRepo)) {
      const cached = avatarCache.get(resolvedRepo);
      if (cached) setSrc(cached);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const res = await authFetch(
          getApiUrl(`/api/model-downloads/avatar?repo_id=${encodeURIComponent(resolvedRepo)}`),
        );
        if (!res.ok) {
          avatarCache.set(resolvedRepo, null);
          return;
        }
        const data = await res.json();
        const url = typeof data.avatar_url === 'string' ? data.avatar_url : null;
        avatarCache.set(resolvedRepo, url);
        if (!cancelled && url) setSrc(url);
      } catch {
        avatarCache.set(resolvedRepo, null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [src, resolvedRepo]);

  const fallbackIcon = (
    <ComputerIcon
      sx={{
        fontSize: iconFontSize ?? size,
        color: iconColor || 'inherit',
      }}
    />
  );

  if (!src) {
    return (
      <Box
        sx={{
          width: size,
          height: size,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        {fallbackIcon}
      </Box>
    );
  }

  return (
    <Avatar
      src={src}
      alt=""
      variant={rounded ? 'rounded' : 'circular'}
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        bgcolor: 'action.hover',
        '& img': { objectFit: 'cover' },
      }}
    >
      {fallbackIcon}
    </Avatar>
  );
}
