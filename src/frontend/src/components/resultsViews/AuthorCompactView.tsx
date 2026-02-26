import { useState } from 'react';
import { MetadataAuthor } from '../../services/monitoredApi';

const SkeletonLoader = () => (
  <div className="w-full h-full bg-gradient-to-r from-gray-300 via-gray-200 to-gray-300 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
);

interface AuthorCompactViewProps {
  author: MetadataAuthor;
  actionLabel?: string;
  actionDisabled?: boolean;
  onAction?: () => void;
  onOpen: () => void;
  showAction?: boolean;
  animationDelay?: number;
}

export const AuthorCompactView = ({
  author,
  actionLabel,
  actionDisabled,
  onAction,
  onOpen,
  showAction = true,
  animationDelay = 0,
}: AuthorCompactViewProps) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const [isHovered, setIsHovered] = useState(false);

  const booksCount = author.stats?.books_count;
  const providerLabel = author.provider ? author.provider : null;
  const shouldShowAction = showAction && Boolean(onAction) && Boolean(actionLabel);

  return (
    <article
      className="book-card overflow-hidden flex flex-col w-full h-full transition-shadow duration-300 animate-pop-up will-change-transform"
      style={{
        background: 'var(--bg-soft)',
        borderRadius: '.75rem',
        boxShadow: isHovered ? '0 10px 30px rgba(0, 0, 0, 0.15)' : 'none',
        animationDelay: `${animationDelay}ms`,
        animationFillMode: 'both',
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <div className="relative w-full" style={{ aspectRatio: '2/3' }}>
        {author.photo_url && !imageError ? (
          <>
            {!imageLoaded && (
              <div className="absolute inset-0">
                <SkeletonLoader />
              </div>
            )}
            <img
              src={author.photo_url}
              alt={author.name || 'Author photo'}
              className="w-full h-full"
              style={{
                opacity: imageLoaded ? 1 : 0,
                transition: 'opacity 0.3s ease-in-out',
                objectFit: 'cover',
                objectPosition: 'top',
              }}
              onLoad={() => setImageLoaded(true)}
              onError={() => setImageError(true)}
            />
          </>
        ) : (
          <div className="w-full h-full flex items-center justify-center text-sm opacity-50" style={{ background: 'var(--border-muted)' }}>
            No Photo
          </div>
        )}

        <div className="absolute inset-0 bg-white transition-opacity duration-300 pointer-events-none" style={{ opacity: isHovered ? 0.02 : 0 }} />
      </div>

      <div className="p-4 max-sm:p-3 max-sm:py-2 flex flex-col flex-1 min-w-0 gap-2">
        <div className="space-y-0.5 min-w-0">
          <h3 className="font-semibold leading-tight line-clamp-2 text-base min-w-0" title={author.name || 'Unknown author'}>
            {author.name || 'Unknown author'}
          </h3>
          <p className="text-sm opacity-80 truncate min-w-0">{typeof booksCount === 'number' ? `${booksCount} books` : 'Unknown'}</p>
          {providerLabel ? (
            <p className="text-[10px] opacity-70 truncate min-w-0">{providerLabel}</p>
          ) : null}
        </div>

        {shouldShowAction ? (
          <div className="mt-auto flex flex-col gap-2">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onAction?.();
              }}
              disabled={actionDisabled}
              className="inline-flex items-center justify-center gap-1.5 rounded text-white transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-emerald-500 px-2.5 py-1.5 text-xs w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
              </svg>
              <span>{actionLabel}</span>
            </button>
          </div>
        ) : null}
      </div>
    </article>
  );
};
