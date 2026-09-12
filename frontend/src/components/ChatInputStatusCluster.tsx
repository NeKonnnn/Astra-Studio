import React, { useMemo } from 'react';
import { Box, Tooltip, Typography } from '@mui/material';
import { useTheme, alpha } from '@mui/material/styles';
import {
  MenuBook as MenuBookIcon,
  ImageOutlined as ImageGenIcon,
  VideocamOutlined as VideoGenIcon,
  ViewQuiltOutlined as ArtifactsStatusIcon,
} from '@mui/icons-material';
import AgentStatusIcon from '../icons/AgentIcon';
import HubIcon from '../icons/McpIcon';
import SkillStatusIcon from '../icons/SkillsIcon';
import type { ActiveMcpServerIndicator } from '../mcp/hooks/useChatInputMcpIndicators';
import type { ActiveSkillRef } from '../utils/skillSelectionStorage';

export interface ChatInputStatusClusterProps {
  isDarkMode: boolean;
  libraryActive: boolean;
  onLibraryToggle?: () => void;
  generationImageActive?: boolean;
  generationVideoActive?: boolean;
  onGenerationClick?: () => void;
  myAgentName: string | null;
  onAgentToggle?: () => void;
  activeMcpServers?: ActiveMcpServerIndicator[];
  onMcpClick?: () => void;
  activeSkills?: ActiveSkillRef[];
  onSkillsToggle?: () => void;
  /** Артефакты включены для текущего чата */
  artifactsActive?: boolean;
  artifactsTooltip?: string;
  /** Снять режим артефактов для чата (клик по иконке) */
  onArtifactsToggle?: () => void;
}

export default function ChatInputStatusCluster({
  isDarkMode,
  libraryActive,
  onLibraryToggle,
  generationImageActive = false,
  generationVideoActive = false,
  onGenerationClick,
  myAgentName,
  onAgentToggle,
  activeMcpServers = [],
  onMcpClick,
  activeSkills = [],
  onSkillsToggle,
  artifactsActive = false,
  artifactsTooltip = '',
  onArtifactsToggle,
}: ChatInputStatusClusterProps) {
  const theme = useTheme();
  const agentActive = Boolean(myAgentName);
  const mcpActive = activeMcpServers.length > 0;
  const skillsActive = activeSkills.length > 0;
  const generationActive = generationImageActive || generationVideoActive;
  const artifactsOn = Boolean(artifactsActive);

  const mcpLabel = useMemo(() => {
    if (activeMcpServers.length === 0) return '';
    if (activeMcpServers.length === 1) return activeMcpServers[0].display_name;
    return `${activeMcpServers.length} MCP`;
  }, [activeMcpServers]);

  const skillsLabel = useMemo(() => {
    if (activeSkills.length === 0) return '';
    if (activeSkills.length === 1) {
      return (activeSkills[0].name || activeSkills[0].slug || 'Skill').trim();
    }
    return `${activeSkills.length} Skills`;
  }, [activeSkills]);

  const tooltipTitle = useMemo(() => {
    const parts: string[] = [];
    if (libraryActive) {
      parts.push(
        'Общий RAG в ответах включён. Нажмите на книгу, чтобы отключить. Документы проекта и агента подключаются отдельно.',
      );
    }
    if (generationImageActive) {
      parts.push(
        onGenerationClick
          ? 'Генерация изображений включена. Нажмите, чтобы открыть настройки.'
          : 'Генерация изображений включена.',
      );
    }
    if (generationVideoActive) {
      parts.push(
        onGenerationClick
          ? 'Генерация видео включена. Нажмите, чтобы открыть настройки.'
          : 'Генерация видео включена.',
      );
    }
    if (myAgentName) {
      parts.push(
        onAgentToggle
          ? `Активен агент «${myAgentName}». Нажмите на робота, чтобы отключить.`
          : `Активен агент «${myAgentName}» (Мои агенты).`,
      );
    }
    if (skillsActive) {
      const names = activeSkills.map((s) => s.name || s.slug).join(', ');
      parts.push(
        onSkillsToggle
          ? `Skills: ${names}. Нажмите, чтобы отключить.`
          : `Skills: ${names}.`,
      );
    }
    if (mcpActive) {
      parts.push(
        `MCP: ${activeMcpServers.map((s) => s.display_name).join(', ')}. Инструменты → MCP.`,
      );
    }
    if (artifactsOn) {
      parts.push(
        onArtifactsToggle
          ? artifactsTooltip
            ? `${artifactsTooltip} Нажмите, чтобы отключить.`
            : 'Режим артефактов включён. Нажмите, чтобы отключить.'
          : artifactsTooltip || 'Режим артефактов включён.',
      );
    }
    return parts.join(' ');
  }, [
    libraryActive,
    generationImageActive,
    generationVideoActive,
    onGenerationClick,
    myAgentName,
    skillsActive,
    activeSkills,
    mcpActive,
    activeMcpServers,
    onAgentToggle,
    onSkillsToggle,
    artifactsOn,
    artifactsTooltip,
    onArtifactsToggle,
  ]);

  if (!libraryActive && !generationActive && !agentActive && !skillsActive && !mcpActive && !artifactsOn) return null;

  const borderC = alpha(theme.palette.primary.main, 0.4);
  const bg = alpha(theme.palette.primary.main, 0.12);
  const dividerColor = isDarkMode ? 'rgba(255,255,255,0.28)' : 'rgba(0,0,0,0.18)';

  const segments: Array<'library' | 'genImage' | 'genVideo' | 'agent' | 'skills' | 'artifacts' | 'mcp'> = [];
  if (libraryActive) segments.push('library');
  if (generationImageActive) segments.push('genImage');
  if (generationVideoActive) segments.push('genVideo');
  if (agentActive) segments.push('agent');
  if (skillsActive) segments.push('skills');
  if (artifactsOn) segments.push('artifacts');
  if (mcpActive) segments.push('mcp');

  const segmentButtonSx = {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: 36,
    height: 36,
    border: 'none',
    bgcolor: 'transparent',
    color: 'primary.main',
  } as const;

  const generationClickHandler = onGenerationClick
    ? (e: React.MouseEvent) => {
        e.stopPropagation();
        e.preventDefault();
        onGenerationClick();
      }
    : undefined;

  const renderGenerationSegment = (seg: 'genImage' | 'genVideo', Icon: typeof ImageGenIcon) => (
    <Box
      component={generationClickHandler ? 'button' : 'div'}
      type={generationClickHandler ? 'button' : undefined}
      onClick={generationClickHandler}
      sx={{
        ...segmentButtonSx,
        width: 36,
        p: 0,
        cursor: generationClickHandler ? 'pointer' : 'default',
        '&:hover': generationClickHandler ? { bgcolor: alpha(theme.palette.primary.main, 0.12) } : {},
      }}
    >
      <Icon sx={{ fontSize: '1.15rem' }} />
    </Box>
  );

  return (
    <Tooltip title={tooltipTitle}>
      <Box
        sx={{
          display: 'inline-flex',
          flexDirection: 'row',
          alignItems: 'stretch',
          flexShrink: 0,
          height: 36,
          borderRadius: '18px',
          border: `1px solid ${borderC}`,
          bgcolor: bg,
          overflow: 'hidden',
        }}
      >
        {segments.map((seg, idx) => (
          <React.Fragment key={seg}>
            {idx > 0 ? (
              <Box
                aria-hidden
                sx={{
                  width: '1px',
                  flexShrink: 0,
                  alignSelf: 'stretch',
                  minHeight: 22,
                  my: '7px',
                  bgcolor: dividerColor,
                }}
              />
            ) : null}
            {seg === 'library' ? (
              <Box
                component="button"
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  e.preventDefault();
                  onLibraryToggle?.();
                }}
                sx={{
                  ...segmentButtonSx,
                  width: 36,
                  p: 0,
                  cursor: onLibraryToggle ? 'pointer' : 'default',
                  '&:hover': onLibraryToggle ? { bgcolor: alpha(theme.palette.primary.main, 0.12) } : {},
                }}
              >
                <MenuBookIcon sx={{ fontSize: '1.15rem' }} />
              </Box>
            ) : null}
            {seg === 'genImage' ? renderGenerationSegment('genImage', ImageGenIcon) : null}
            {seg === 'genVideo' ? renderGenerationSegment('genVideo', VideoGenIcon) : null}
            {seg === 'agent' ? (
              <Box
                component={onAgentToggle && myAgentName ? 'button' : 'div'}
                type={onAgentToggle && myAgentName ? 'button' : undefined}
                onClick={
                  onAgentToggle && myAgentName
                    ? (e: React.MouseEvent) => {
                        e.stopPropagation();
                        e.preventDefault();
                        onAgentToggle();
                      }
                    : undefined
                }
                sx={{
                  ...segmentButtonSx,
                  px: libraryActive ? 0.5 : 0.75,
                  cursor: onAgentToggle && myAgentName ? 'pointer' : 'default',
                  '&:hover':
                    onAgentToggle && myAgentName
                      ? { bgcolor: alpha(theme.palette.primary.main, 0.12) }
                      : {},
                }}
              >
                <AgentStatusIcon sx={{ fontSize: '1.15rem' }} />
              </Box>
            ) : null}
            {seg === 'skills' ? (
              <Box
                component={onSkillsToggle ? 'button' : 'div'}
                type={onSkillsToggle ? 'button' : undefined}
                onClick={
                  onSkillsToggle
                    ? (e: React.MouseEvent) => {
                        e.stopPropagation();
                        e.preventDefault();
                        onSkillsToggle();
                      }
                    : undefined
                }
                sx={{
                  ...segmentButtonSx,
                  gap: 0.35,
                  px: 0.75,
                  cursor: onSkillsToggle ? 'pointer' : 'default',
                  maxWidth: 140,
                  '&:hover': onSkillsToggle ? { bgcolor: alpha(theme.palette.primary.main, 0.12) } : {},
                }}
              >
                <SkillStatusIcon sx={{ fontSize: '1.15rem', flexShrink: 0 }} />
                {skillsLabel ? (
                  <Typography
                    variant="caption"
                    sx={{
                      fontWeight: 600,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      lineHeight: 1,
                    }}
                  >
                    {skillsLabel}
                  </Typography>
                ) : null}
              </Box>
            ) : null}
            {seg === 'artifacts' ? (
              <Box
                component={onArtifactsToggle ? 'button' : 'div'}
                type={onArtifactsToggle ? 'button' : undefined}
                onClick={
                  onArtifactsToggle
                    ? (e: React.MouseEvent) => {
                        e.stopPropagation();
                        e.preventDefault();
                        onArtifactsToggle();
                      }
                    : undefined
                }
                sx={{
                  ...segmentButtonSx,
                  width: 36,
                  p: 0,
                  cursor: onArtifactsToggle ? 'pointer' : 'default',
                  '&:hover': onArtifactsToggle ? { bgcolor: alpha(theme.palette.primary.main, 0.12) } : {},
                }}
              >
                <ArtifactsStatusIcon sx={{ fontSize: '1.15rem' }} />
              </Box>
            ) : null}
            {seg === 'mcp' ? (
              <Box
                component={onMcpClick ? 'button' : 'div'}
                type={onMcpClick ? 'button' : undefined}
                onClick={
                  onMcpClick
                    ? (e: React.MouseEvent) => {
                        e.stopPropagation();
                        e.preventDefault();
                        onMcpClick();
                      }
                    : undefined
                }
                sx={{
                  ...segmentButtonSx,
                  gap: 0.35,
                  px: 0.75,
                  cursor: onMcpClick ? 'pointer' : 'default',
                  maxWidth: 140,
                }}
              >
                <HubIcon sx={{ fontSize: '1.15rem', flexShrink: 0 }} />
                {mcpLabel ? (
                  <Typography
                    variant="caption"
                    sx={{
                      fontWeight: 600,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      lineHeight: 1,
                    }}
                  >
                    {mcpLabel}
                  </Typography>
                ) : null}
              </Box>
            ) : null}
          </React.Fragment>
        ))}
      </Box>
    </Tooltip>
  );
}
