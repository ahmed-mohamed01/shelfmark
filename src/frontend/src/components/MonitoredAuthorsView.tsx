import { type CSSProperties } from 'react';
import type { MetadataAuthor } from '../services/monitoredApi';
import { MonitoredAuthorCompactTile } from './MonitoredAuthorCompactTile';
import { MonitoredAuthorTableRow } from './AuthorTableRow';
import { RowThumbnail } from './RowThumbnail';

export interface MonitoredAuthorsViewProps {
  viewMode: 'table' | 'compact';
  authors: MetadataAuthor[];
  entityIdByName: Map<string, number>;
  selectedAuthorKeys: Record<string, boolean>;
  hasActiveSelection: boolean;
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
  compactGridStyle,
  onNavigate,
  onEdit,
  onToggleSelect,
}: MonitoredAuthorsViewProps) {
  if (viewMode === 'table') {
    return (
      <div key="table" className="flex flex-col gap-2">
        {authors.map((author, index) => {
          const booksCountLabel = typeof author.stats?.books_count === 'number' ? `${author.stats.books_count} books` : 'Unknown';
          const subtitle = author.provider ? `${booksCountLabel} • ${author.provider}` : booksCountLabel;
          const authorEntityId = entityIdByName.get((author.name || '').toLowerCase());
          const isSelected = typeof authorEntityId === 'number'
            ? Boolean(selectedAuthorKeys[String(authorEntityId)])
            : false;
          return (
            <div
              key={`${author.provider}:${author.provider_id}`}
              className="animate-pop-up will-change-transform"
              style={{ animationDelay: `${index * 30}ms`, }}
            >
              <MonitoredAuthorTableRow
                name={author.name || 'Unknown author'}
                subtitle={subtitle}
                thumbnail={<RowThumbnail url={author.photo_url} alt={author.name || 'Unknown author'} kind="author" />}
                onOpen={() => onNavigate({ ...author, monitoredEntityId: authorEntityId ?? null })}
                onEdit={typeof authorEntityId === 'number' ? () => onEdit(authorEntityId, author.name || 'Unknown author') : undefined}
                onToggleSelect={typeof authorEntityId === 'number' ? () => onToggleSelect(authorEntityId) : undefined}
                isSelected={isSelected}
                hasActiveSelection={hasActiveSelection}
              />
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div
      key="compact"
      className="grid gap-4 items-start"
      style={compactGridStyle}
    >
      {authors.map((author, index) => {
        const booksCountLabel = typeof author.stats?.books_count === 'number' ? `${author.stats.books_count} books` : 'Unknown';
        const subtitle = booksCountLabel;
        const authorEntityId = entityIdByName.get((author.name || '').toLowerCase());
        const isSelected = typeof authorEntityId === 'number'
          ? Boolean(selectedAuthorKeys[String(authorEntityId)])
          : false;
        return (
          <div
            key={`${author.provider}:${author.provider_id}`}
            className="animate-pop-up will-change-transform"
            style={{ animationDelay: `${index * 30}ms`, }}
          >
            <MonitoredAuthorCompactTile
              name={author.name || 'Unknown author'}
              thumbnail={<RowThumbnail url={author.photo_url} alt={author.name || 'Author photo'} kind="author" className="w-full aspect-[2/3]" />}
              subtitle={subtitle}
              onOpenDetails={() => onNavigate({ ...author, monitoredEntityId: authorEntityId ?? null })}
              onEdit={typeof authorEntityId === 'number' ? () => onEdit(authorEntityId, author.name || 'Unknown author') : undefined}
              onToggleSelect={typeof authorEntityId === 'number' ? () => onToggleSelect(authorEntityId) : undefined}
              isSelected={isSelected}
              hasActiveSelection={hasActiveSelection}
            />
          </div>
        );
      })}
    </div>
  );
}
