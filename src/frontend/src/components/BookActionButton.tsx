import { CSSProperties } from 'react';
import { Book, ButtonStateInfo } from '../types';
import { useSearchMode } from '../contexts/SearchModeContext';
import { BookDownloadButton } from './BookDownloadButton';
import { BookGetButton } from './BookGetButton';

type ButtonSize = 'sm' | 'md';
type ButtonVariant = 'default' | 'icon';

interface BookActionButtonProps {
  book: Book;
  buttonState: ButtonStateInfo;
  onDownload: (book: Book) => Promise<void>;
  onGetReleases: (book: Book) => void;
  onGetReleasesAuto?: (book: Book) => void;
  isLoadingReleases?: boolean;
  isLoadingAutoReleases?: boolean;
  showDualGetButtons?: boolean;
  size?: ButtonSize;
  variant?: ButtonVariant;
  fullWidth?: boolean;
  className?: string;
  style?: CSSProperties;
  customAction?: {
    label: string;
    onClick: (book: Book) => void;
    isDisabled?: (book: Book) => boolean;
    getLabel?: (book: Book) => string;
  };
}

export function BookActionButton({
  book,
  buttonState,
  onDownload,
  onGetReleases,
  onGetReleasesAuto,
  isLoadingReleases,
  isLoadingAutoReleases,
  showDualGetButtons = false,
  size,
  variant = 'default',
  fullWidth,
  className,
  style,
  customAction,
}: BookActionButtonProps) {
  const { searchMode } = useSearchMode();

  if (customAction) {
    const isIconVariant = variant === 'icon';
    const disabled = customAction.isDisabled ? customAction.isDisabled(book) : false;
    const label = customAction.getLabel ? customAction.getLabel(book) : customAction.label;

    return (
      <button
        type="button"
        onClick={() => {
          if (disabled) return;
          customAction.onClick(book);
        }}
        disabled={disabled}
        className={isIconVariant
          ? `inline-flex items-center justify-center rounded-full p-1.5 sm:p-2 transition-all duration-200 text-gray-700 dark:text-gray-200 ${disabled ? 'opacity-60 cursor-not-allowed' : 'hover-action'} ${className || ''}`.trim()
          : `inline-flex items-center justify-center rounded-full px-3 py-2 text-sm font-medium transition-all duration-200 ${fullWidth ? 'w-full' : ''} ${disabled ? 'bg-gray-500 text-white opacity-70 cursor-not-allowed' : 'bg-emerald-600 hover:bg-emerald-700 text-white'} ${className || ''}`.trim()}
        style={style}
        aria-label={label}
        title={label}
      >
        {isIconVariant ? (
          <svg className="w-4 h-4 sm:w-5 sm:h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M17.25 6.75v10.5L12 14.25l-5.25 3V6.75A2.25 2.25 0 0 1 9 4.5h6a2.25 2.25 0 0 1 2.25 2.25Z" />
          </svg>
        ) : (
          <span>{label}</span>
        )}
      </button>
    );
  }

  if (searchMode === 'universal') {
    return (
      <BookGetButton
        book={book}
        onGetReleases={onGetReleases}
        onGetReleasesAuto={onGetReleasesAuto}
        buttonState={buttonState}
        isLoading={isLoadingReleases}
        isAutoLoading={isLoadingAutoReleases}
        showDualActions={showDualGetButtons}
        size={size}
        variant={variant}
        fullWidth={fullWidth}
        className={className}
        style={style}
      />
    );
  }

  return (
    <BookDownloadButton
      buttonState={buttonState}
      onDownload={() => onDownload(book)}
      size={size}
      variant={variant === 'default' ? 'primary' : 'icon'}
      fullWidth={fullWidth}
      className={className}
      style={style}
      ariaLabel={buttonState.text}
    />
  );
}
