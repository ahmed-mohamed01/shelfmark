import { type CSSProperties } from 'react';
import type { MetadataAuthor } from '../services/monitoredApi';
import { MonitoredAuthorCompactTile } from './MonitoredAuthorCompactTile';
import { MonitoredAuthorTableRow } from './AuthorTableRow';
import { RowThumbnail } from './RowThumbnail';

const ERROR_TYPE_LABELS: Record<string, string> = {
  network: 'Network Error',
  timeout: 'Timeout',
  rate_limit: 'Rate Limited',
  auth: 'Auth Error',
  api_error: 'API Error',
  not_found: 'Not Found',
};

function parseErrorDisplay(raw: string): { label: string; message: string } {
  const match = raw.match(/^\[(\w+)]\s*(.*)/s);
  if (match) {
    const label = ERROR_TYPE_LABELS[match[1]] || match[1];
    return { label, message: match[2] };
  }
  return { label: 'Error', message: raw };
}

export interface MonitoredAuthorsViewProps {
  viewMode: 'table' | 'compact';
  authors: MetadataAuthor[];
  entityIdByName: Map<string, number>;
  entityErrorById: Map<number, string>;
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
  entityErrorById,
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
          const errorRaw = typeof authorEntityId === 'number' ? entityErrorById.get(authorEntityId) : undefined;
          const errorInfo = errorRaw ? parseErrorDisplay(errorRaw) : undefined;
          return (
            <div
              key={`${author.provider}:${author.provider_id}`}
              className="animate-pop-up will-change-transform"
              style={{ animationDelay: `${index * 30}ms` }}
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
                trailingAction={errorInfo ? (
                  <span
                    className="shrink-0 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-500/15 text-red-600 dark:text-red-400 cursor-help"
                    title={`${errorInfo.label}: ${errorInfo.message}`}
                  >
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
                    </svg>
                    {errorInfo.label}
                  </span>
                ) : undefined}
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
        const errorRaw = typeof authorEntityId === 'number' ? entityErrorById.get(authorEntityId) : undefined;
        const errorInfo = errorRaw ? parseErrorDisplay(errorRaw) : undefined;
        return (
          <div
            key={`${author.provider}:${author.provider_id}`}
            className="animate-pop-up will-change-transform"
            style={{ animationDelay: `${index * 30}ms` }}
          >
            <MonitoredAuthorCompactTile
              name={author.name || 'Unknown author'}
              thumbnail={<RowThumbnail url={author.photo_url} alt={author.name || 'Author photo'} kind="author" className="w-full aspect-[2/3]" />}
              subtitle={subtitle}
              metaLine={errorInfo ? `⚠ ${errorInfo.label}` : undefined}
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
