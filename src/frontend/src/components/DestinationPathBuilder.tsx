import { Dropdown } from './Dropdown';

export interface DestinationRootOption {
  path: string;
  label?: string;
  isDefault?: boolean;
  /** Whether this root already contains the author's folder (shown as a ✓). */
  authorFolderExists?: boolean;
}

interface DestinationPathBuilderProps {
  roots: DestinationRootOption[];
  /** Selected parent folder, or null for the configured default. */
  rootValue: string | null;
  onRootChange: (path: string | null) => void;
  /** Adds a trailing "Browse…" item to the root menu. */
  onBrowse?: () => void;
  /** Locked author chip — the author folder is always created. */
  authorName: string;
  /** Show the optional series-folder chip (the template creates a series folder). */
  showSeriesChip: boolean;
  /** Chip text; defaults to the literal `{Series}` token. */
  seriesLabel?: string;
  seriesEnabled: boolean;
  /** Omit to render the chip read-only. */
  onSeriesToggle?: (next: boolean) => void;
  /** Full resolved path; empty hides the PREVIEW line. */
  previewPath: string;
  loading?: boolean;
  disabled?: boolean;
}

const LockIcon = ({ className }: { className: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <rect x="3" y="11" width="18" height="11" rx="2" />
    <path d="M7 11V7a5 5 0 0 1 10 0v4" />
  </svg>
);

const CheckIcon = ({ className }: { className: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2.5}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

const PlusIcon = ({ className }: { className: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2.5}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </svg>
);

const ChevronIcon = ({ className }: { className: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2.5}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

/**
 * Destination path builder: `[root ▾] / [🔒 Author] / [{Series}]` + PREVIEW line.
 *
 * Shared by the book modal's SAVE TO bar and the Monitor Author dialog so both
 * flows describe the shelf the same way. The author chip is locked because the
 * library layout always creates the author folder; the series chip is a dashed
 * optional toggle. The component is presentational — the caller owns the
 * roots, the selection, and the rendered preview.
 */
export const DestinationPathBuilder = ({
  roots,
  rootValue,
  onRootChange,
  onBrowse,
  authorName,
  showSeriesChip,
  seriesLabel = '{Series}',
  seriesEnabled,
  onSeriesToggle,
  previewPath,
  loading = false,
  disabled = false,
}: DestinationPathBuilderProps) => {
  const chipBase =
    'inline-flex items-center gap-1.5 rounded-[10px] px-3 py-2 text-[13px] font-semibold';
  const iconSize = 'h-3.5 w-3.5';
  const separator = <span className="text-gray-400 select-none dark:text-gray-600">/</span>;

  const defaultRoot = roots.find((r) => r.isDefault) ?? null;
  const selectedPath = rootValue ?? defaultRoot?.path ?? null;
  const rootDisplay = selectedPath ?? (loading ? 'Loading…' : 'Default');

  // A root picked via Browse won't be in the configured list; keep it visible.
  const menuRoots: DestinationRootOption[] =
    rootValue && !roots.some((r) => r.path === rootValue) ? [...roots, { path: rootValue }] : roots;

  const seriesChipClass = seriesEnabled
    ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
    : 'border-gray-400/70 bg-transparent text-gray-500 dark:border-gray-500 dark:text-gray-400';

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <Dropdown
          align="left"
          usePortal
          widthClassName="inline-flex max-w-full"
          panelClassName="z-[1350] min-w-[260px] max-w-[90vw] p-1.5"
          disabled={disabled}
          renderTrigger={({ isOpen, toggle }) => (
            <button
              type="button"
              onClick={toggle}
              disabled={disabled}
              aria-haspopup="listbox"
              aria-expanded={isOpen}
              aria-label="Library root"
              title={selectedPath ?? undefined}
              className={`${chipBase} max-w-[16rem] border border-[var(--border-muted)] bg-black/5 font-mono text-[var(--text)] transition-colors hover:bg-black/10 disabled:cursor-not-allowed disabled:opacity-60 sm:max-w-[22rem] dark:bg-white/10 dark:hover:bg-white/15`}
            >
              <span className="truncate">{rootDisplay}</span>
              <ChevronIcon
                className={`h-3 w-3 shrink-0 text-gray-500 transition-transform duration-150 dark:text-gray-400 ${isOpen ? 'rotate-180' : ''}`}
              />
            </button>
          )}
        >
          {({ close }) => (
            <div role="listbox" aria-label="Library root" className="flex flex-col gap-0.5">
              {menuRoots.length === 0 ? (
                <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
                  {loading ? 'Loading…' : 'No save locations configured'}
                </div>
              ) : null}
              {menuRoots.map((root) => {
                const isSelected = root.path === selectedPath;
                const suffix = [
                  root.authorFolderExists ? '✓' : '',
                  root.isDefault ? '— default' : '',
                ]
                  .filter(Boolean)
                  .join(' ');
                return (
                  <button
                    key={root.path}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => {
                      onRootChange(root.isDefault ? null : root.path);
                      close();
                    }}
                    className={`hover-surface flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left font-mono text-[13px] ${
                      isSelected
                        ? 'font-semibold text-emerald-600 dark:text-emerald-400'
                        : 'text-[var(--text)]'
                    }`}
                    title={
                      root.authorFolderExists ? 'Author folder already exists here' : undefined
                    }
                  >
                    <span className="min-w-0 truncate">
                      {root.path}
                      {suffix ? (
                        <span className="ml-2 text-gray-500 dark:text-gray-400">{suffix}</span>
                      ) : null}
                    </span>
                    {isSelected ? <CheckIcon className="h-3.5 w-3.5 shrink-0" /> : null}
                  </button>
                );
              })}
              {onBrowse ? (
                <>
                  <div className="my-1 border-t border-[var(--border-muted)]" />
                  <button
                    type="button"
                    onClick={() => {
                      close();
                      onBrowse();
                    }}
                    className="hover-surface flex w-full items-center rounded-lg px-3 py-2 text-left text-[13px] text-[var(--text)]"
                  >
                    Browse…
                  </button>
                </>
              ) : null}
            </div>
          )}
        </Dropdown>

        {separator}

        <span
          className={`${chipBase} border border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400`}
          title="Author folder is always created"
          aria-label={`Author folder ${authorName} (always created)`}
        >
          <LockIcon className={iconSize} />
          <span className="max-w-[14rem] truncate">{authorName || 'Author'}</span>
        </span>

        {showSeriesChip ? (
          <>
            {separator}
            {onSeriesToggle ? (
              <button
                type="button"
                role="switch"
                aria-checked={seriesEnabled}
                aria-label="Series folder"
                disabled={disabled}
                onClick={() => onSeriesToggle(!seriesEnabled)}
                title={
                  seriesEnabled
                    ? 'Series folder on — click to turn off'
                    : 'Optional series folder — click to turn on'
                }
                className={`${chipBase} border border-dashed transition-colors hover:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-60 ${seriesChipClass}`}
              >
                {seriesEnabled ? (
                  <CheckIcon className={iconSize} />
                ) : (
                  <PlusIcon className={iconSize} />
                )}
                <span className="max-w-[12rem] truncate">{seriesLabel}</span>
              </button>
            ) : (
              <span
                className={`${chipBase} border border-dashed ${seriesChipClass}`}
                title="Series folder"
              >
                {seriesEnabled ? (
                  <CheckIcon className={iconSize} />
                ) : (
                  <PlusIcon className={iconSize} />
                )}
                <span className="max-w-[12rem] truncate">{seriesLabel}</span>
              </span>
            )}
          </>
        ) : null}
      </div>

      {previewPath ? (
        <div className="flex min-w-0 items-center gap-2 rounded-[9px] border border-[var(--border-muted)] bg-black/[0.04] px-3 py-2 dark:bg-black/30">
          <span className="shrink-0 text-[10.5px] font-bold tracking-[0.08em] text-gray-500 dark:text-gray-400">
            PREVIEW
          </span>
          <span className="min-w-0 font-mono text-xs break-all text-emerald-700 dark:text-emerald-400">
            {previewPath}
          </span>
        </div>
      ) : null}
    </div>
  );
};
