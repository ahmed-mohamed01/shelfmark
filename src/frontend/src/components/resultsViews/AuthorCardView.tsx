import { useState } from 'react';
import { MetadataAuthor } from '../../services/api';

const SkeletonLoader = () => (
  <div className="w-full h-full bg-gradient-to-r from-gray-300 via-gray-200 to-gray-300 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
);

interface AuthorCardViewProps {
  author: MetadataAuthor;
  actionLabel?: string;
  actionDisabled?: boolean;
  onAction?: () => void;
  onOpen: () => void;
  onRemove?: () => void;
  showAction?: boolean;
  animationDelay?: number;
}

export const AuthorCardView = ({
  author,
  actionLabel,
  actionDisabled,
  onAction,
  onOpen,
  onRemove,
  showAction = true,
  animationDelay = 0,
}: AuthorCardViewProps) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const [isHovered, setIsHovered] = useState(false);

  const booksCount = author.stats?.books_count;
  const shouldShowAction = showAction && Boolean(onAction) && Boolean(actionLabel);
  const shouldShowRemove = showAction && Boolean(onRemove);

  return (
    <article
      className="book-card overflow-hidden flex flex-col sm:flex-col max-sm:flex-row space-between w-full sm:max-w-[292px] max-sm:h-[180px] h-full transition-shadow duration-300 animate-pop-up will-change-transform"
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
      <div className="relative w-full sm:w-full max-sm:w-[120px] max-sm:h-full max-sm:flex-shrink-0 group" style={{ aspectRatio: '2/3' }}>
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

        <div
          className="absolute inset-0 bg-white transition-opacity duration-300 pointer-events-none"
          style={{ opacity: isHovered ? 0.02 : 0 }}
        />

        {shouldShowRemove ? (
          <button
            type="button"
            className="absolute top-2 right-2 w-8 h-8 rounded-full bg-white/90 dark:bg-gray-800/90 backdrop-blur-sm flex items-center justify-center transition-all duration-300 shadow-lg hover:scale-110"
            style={{
              opacity: isHovered ? 1 : 0,
              pointerEvents: isHovered ? 'auto' : 'none',
            }}
            onClick={(e) => {
              e.stopPropagation();
              onRemove?.();
            }}
            aria-label="Remove author"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        ) : null}
      </div>

      <div className="p-4 max-sm:p-3 max-sm:py-2 flex flex-col gap-3 max-sm:gap-2 max-sm:flex-1 max-sm:justify-between max-sm:min-w-0 sm:flex-1 sm:flex sm:flex-col sm:justify-end">
        <div className="space-y-1 max-sm:space-y-0.5 max-sm:min-w-0">
          <h3 className="font-semibold leading-tight line-clamp-2 text-base max-sm:line-clamp-3 max-sm:min-w-0" title={author.name || 'Unknown author'}>
            {author.name || 'Unknown author'}
          </h3>
          <p className="text-sm max-sm:text-xs opacity-80 truncate max-sm:min-w-0">{typeof booksCount === 'number' ? `${booksCount} books` : 'Unknown'}</p>
        </div>
      </div>

      {shouldShowAction ? (
        <>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onAction?.();
            }}
            disabled={actionDisabled}
            className="inline-flex items-center justify-center gap-1.5 rounded text-white transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-emerald-500 px-4 py-2.5 text-sm w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 rounded-none hidden sm:flex"
            style={{
              borderBottomLeftRadius: '.75rem',
              borderBottomRightRadius: '.75rem',
            }}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
            <span>{actionLabel}</span>
          </button>

          <div className="flex gap-1.5 sm:hidden px-3 pb-3" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              onClick={onAction}
              disabled={actionDisabled}
              className="inline-flex items-center justify-center gap-1.5 rounded text-white transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-emerald-500 px-2.5 py-1.5 text-xs w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
              </svg>
              <span>{actionLabel}</span>
            </button>
          </div>
        </>
      ) : null}
    </article>
  );
};
