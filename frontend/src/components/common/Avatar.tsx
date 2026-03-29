/**
 * Avatar Component - User avatars with status
 */

import React from 'react';
import { cn } from '@/utils';

type AvatarSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl';

interface AvatarProps {
  src?: string;
  alt?: string;
  name?: string;
  size?: AvatarSize;
  shape?: 'circle' | 'square';
  status?: 'online' | 'offline' | 'busy' | 'away';
  className?: string;
  onClick?: () => void;
}

const sizeMap: Record<AvatarSize, { width: number; fontSize: string }> = {
  xs: { width: 24, fontSize: '0.65rem' },
  sm: { width: 32, fontSize: '0.75rem' },
  md: { width: 40, fontSize: '0.875rem' },
  lg: { width: 48, fontSize: '1rem' },
  xl: { width: 64, fontSize: '1.25rem' },
};

const statusColors: Record<string, string> = {
  online: 'bg-success',
  offline: 'bg-secondary',
  busy: 'bg-danger',
  away: 'bg-warning',
};

export const Avatar: React.FC<AvatarProps> = ({
  src,
  alt,
  name,
  size = 'md',
  shape = 'circle',
  status,
  className,
  onClick,
}) => {
  const { width, fontSize } = sizeMap[size];
  const initials = name
    ? name
        .split(' ')
        .map((n) => n[0])
        .join('')
        .toUpperCase()
        .slice(0, 2)
    : '?';

  return (
    <div
      className={cn('avatar', shape === 'circle' && 'rounded-circle', className)}
      style={{ width, height: width }}
      onClick={onClick}
    >
      {src ? (
        <img
          src={src}
          alt={alt ?? name ?? 'Avatar'}
          className={cn('w-100 h-100', shape === 'circle' && 'rounded-circle')}
          style={{ objectFit: 'cover' }}
        />
      ) : (
        <div
          className={cn(
            'w-100 h-100 d-flex align-items-center justify-content-center bg-secondary text-white',
            shape === 'circle' && 'rounded-circle'
          )}
          style={{ fontSize }}
        >
          {initials}
        </div>
      )}
      {status && (
        <span
          className={cn(
            'avatar-status position-absolute bottom-0 end-0 rounded-circle',
            statusColors[status]
          )}
          style={{ width: width * 0.3, height: width * 0.3 }}
        />
      )}
    </div>
  );
};

/**
 * AvatarGroup - Stack of avatars
 */
interface AvatarGroupProps {
  avatars: Array<{
    src?: string;
    name?: string;
    status?: 'online' | 'offline' | 'busy' | 'away';
  }>;
  max?: number;
  size?: AvatarSize;
  className?: string;
}

export const AvatarGroup: React.FC<AvatarGroupProps> = ({
  avatars,
  max = 4,
  size = 'md',
  className,
}) => {
  const visibleAvatars = avatars.slice(0, max);
  const remaining = avatars.length - max;
  const { width } = sizeMap[size];

  return (
    <div className={cn('avatar-group d-flex', className)}>
      {visibleAvatars.map((avatar, index) => (
        <div
          key={index}
          className="avatar-group-item"
          style={{ marginLeft: index > 0 ? -width / 4 : 0 }}
        >
          <Avatar src={avatar.src} name={avatar.name} status={avatar.status} size={size} />
        </div>
      ))}
      {remaining > 0 && (
        <div className="avatar-group-item" style={{ marginLeft: -width / 4 }}>
          <div
            className={cn(
              'd-flex align-items-center justify-content-center bg-secondary text-white rounded-circle'
            )}
            style={{ width, height: width }}
          >
            +{remaining}
          </div>
        </div>
      )}
    </div>
  );
};
