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
}: BookActionButtonProps) {
  const { searchMode } = useSearchMode();

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
