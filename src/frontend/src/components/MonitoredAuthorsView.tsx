import { type CSSProperties } from 'react';
import type { MetadataAuthor } from '../services/monitoredApi';
import { MonitoredAuthorCompactTile } from './MonitoredAuthorCompactTile';
import { MonitoredAuthorTableRow } from './AuthorTableRow';
import { RowThumbnail } from './RowThumbnail';

const GRID_CLASSES = {
  mobile: 'grid-cols-1 items-start',
  compact: 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 items-start',
} as const;

export interface MonitoredAuthorsViewProps {
  viewMode: 'table' | 'compact';
  authors: MetadataAuthor[];
  entityIdByName: Map<string, number>;
  selectedAuthorKeys: Record<string, boolean>;
  hasActiveSelection: boolean;
  isDesktop: boolean;
  compactGridStyle: CSSProperties | undefined;
  onNavigate: (author: MetadataAuthor & { monitoredEntityId: number | null }) => void;
  onEdit: (entityId: number, authorName: string) => void;
  onToggleSelect: (entityId: number) => void;
}

export function MonitoredAuthorsView({
  viewMode,
  authors,
  entityIdByName,
  selectedAuthorKeys,
  hasActiveSelection,
  isDesktop,
  compactGridStyle,
  onNavigate,
  onEdit,
  onToggleSelect,
}: MonitoredAuthorsViewProps) {
  if (viewMode === 'table') {
    return (
      <div className="flex flex-col gap-2">
        {authors.map((author) => {
          const booksCountLabel = typeof author.stats?.books_count === 'number' ? `${author.stats.books_count} books` : 'Unknown';
          const subtitle = author.provider ? `${booksCountLabel} • ${author.provider}` : booksCountLabel;
          const authorEntityId = entityIdByName.get((author.name || '').toLowerCase());
          const isSelected = typeof authorEntityId === 'number'
            ? Boolean(selectedAuthorKeys[String(authorEntityId)])
            : false;
          return (
            <MonitoredAuthorTableRow
              key={`${author.provider}:${author.provider_id}`}
              name={author.name || 'Unknown author'}
              subtitle={subtitle}
              thumbnail={<RowThumbnail url={author.photo_url} alt={author.name || 'Unknown author'} kind="author" />}
              onOpen={() => onNavigate({ ...author, monitoredEntityId: authorEntityId ?? null })}
              onEdit={typeof authorEntityId === 'number' ? () => onEdit(authorEntityId, author.name || 'Unknown author') : undefined}
              onToggleSelect={typeof authorEntityId === 'number' ? () => onToggleSelect(authorEntityId) : undefined}
              isSelected={isSelected}
              hasActiveSelection={hasActiveSelection}
            />
          );
        })}
      </div>
    );
  }

  return (
    <div
      className={`grid gap-4 ${!isDesktop ? GRID_CLASSES.mobile : 'items-stretch'}`}
      style={compactGridStyle}
    >
      {authors.map((author) => {
        const booksCountLabel = typeof author.stats?.books_count === 'number' ? `${author.stats.books_count} books` : 'Unknown';
        const subtitle = booksCountLabel;
        const authorEntityId = entityIdByName.get((author.name || '').toLowerCase());
        const isSelected = typeof authorEntityId === 'number'
          ? Boolean(selectedAuthorKeys[String(authorEntityId)])
          : false;
        return (
          <MonitoredAuthorCompactTile
            key={`${author.provider}:${author.provider_id}`}
            name={author.name || 'Unknown author'}
            thumbnail={<RowThumbnail url={author.photo_url} alt={author.name || 'Author photo'} kind="author" className="w-full aspect-[2/3]" />}
            subtitle={subtitle}
            onOpenDetails={() => onNavigate({ ...author, monitoredEntityId: authorEntityId ?? null })}
            onEdit={typeof authorEntityId === 'number' ? () => onEdit(authorEntityId, author.name || 'Unknown author') : undefined}
            onToggleSelect={typeof authorEntityId === 'number' ? () => onToggleSelect(authorEntityId) : undefined}
            isSelected={isSelected}
            hasActiveSelection={hasActiveSelection}
          />
        );
      })}
    </div>
  );
}
