import { useState } from 'react';

interface RowThumbnailProps {
  url?: string | null;
  alt?: string;
  /** Determines which fallback icon to show when url is absent or fails to load. */
  kind?: 'book' | 'author';
  /** Tailwind size classes. Defaults to the standard small table-row size. */
  className?: string;
}

const BookFallback = ({ iconClass }: { iconClass: string }) => (
  <svg
    className={iconClass}
    fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    strokeWidth={1.2}
    aria-hidden="true"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
    />
  </svg>
);

const AuthorFallback = ({ iconClass }: { iconClass: string }) => (
  <svg
    className={iconClass}
    fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    strokeWidth={1.2}
    aria-hidden="true"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z"
    />
  </svg>
);

export const RowThumbnail = ({
  url,
  alt,
  kind = 'book',
  className = 'w-7 h-10 sm:w-10 sm:h-14',
}: RowThumbnailProps) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);

  if (!url || imageError) {
    const iconClass = 'w-1/2 h-1/2 opacity-40';
    return (
      <div
        className={`${className} rounded bg-gray-200 dark:bg-gray-700 flex items-center justify-center`}
        aria-label={alt || (kind === 'author' ? 'No photo available' : 'No cover available')}
      >
        {kind === 'author' ? (
          <AuthorFallback iconClass={iconClass} />
        ) : (
          <BookFallback iconClass={iconClass} />
        )}
      </div>
    );
  }

  return (
    <div className={`relative ${className} rounded overflow-hidden bg-gray-100 dark:bg-gray-800 border border-white/40 dark:border-gray-700/70`}>
      {!imageLoaded && (
        <div className="absolute inset-0 bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
      )}
      <img
        src={url}
        alt={alt || (kind === 'author' ? 'Author photo' : 'Book cover')}
        className="w-full h-full object-cover object-top"
        loading="lazy"
        onLoad={() => setImageLoaded(true)}
        onError={() => setImageError(true)}
        style={{ opacity: imageLoaded ? 1 : 0, transition: 'opacity 0.2s ease-in-out' }}
      />
    </div>
  );
};
