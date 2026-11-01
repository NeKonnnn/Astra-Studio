import React from 'react';
import { Avatar, AvatarProps } from '@mui/material';
import { useAuth } from '../contexts/AuthContext';
import { getUserInitials } from '../utils/userInitials';

type UserAvatarProps = Omit<AvatarProps, 'src' | 'children'> & {
  username?: string | null;
  fullName?: string | null;
  fallbackInitials?: string;
};

export default function UserAvatar({
  username,
  fullName,
  fallbackInitials,
  sx,
  ...avatarProps
}: UserAvatarProps) {
  const { user, avatarSrc } = useAuth();
  const resolvedUsername = username ?? user?.username ?? '';
  const resolvedFullName = fullName ?? user?.full_name ?? null;
  const initials =
    fallbackInitials ??
    getUserInitials(resolvedFullName, resolvedUsername);

  return (
    <Avatar src={avatarSrc} sx={{ fontSize: initials.length > 1 ? '0.65em' : undefined, ...sx }} {...avatarProps}>
      {initials}
    </Avatar>
  );
}
