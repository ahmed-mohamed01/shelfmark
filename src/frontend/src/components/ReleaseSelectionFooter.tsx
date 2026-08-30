import { useMemo, useState } from 'react';

import {
  buildQueueItems,
  buildQueueLabel,
  buildSelectionSummary,
  countSelected,
  SELECTION_STEPS,
  type ReleaseSelectionConfig,
} from '../hooks/useReleaseSelection';
import type { ContentType, Release, RequestPolicyMode } from '../types';

interface ReleaseSelectionFooterProps {
  selection: ReleaseSelectionConfig;
  /**
   * Resolved action mode for a picked release, evaluated against that
   * release's OWN content type — request policy is per content type, so the
   * non-active step must not be judged by the active step's type.
   */
  modeForRelease: (release: Release, contentType: ContentType) => RequestPolicyMode;
  /** Header "Multi-book pack" toggle — forces a heuristic split for releases that couldn't be inspected. */
  multiBook?: boolean;
}

const STEP_LABEL: Record<ContentType, string> = { ebook: 'Book', audiobook: 'Audiobook' };

const CheckIcon = () => (
  <svg
    className="h-3 w-3"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={3}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

/**
 * Book / Audiobook step pills (free two-way navigation, both optional), the
 * selection summary, and the single queue action for the book modal.
 */
export const ReleaseSelectionFooter = ({
  selection,
  modeForRelease,
  multiBook = false,
}: ReleaseSelectionFooterProps) => {
  const [queueing, setQueueing] = useState(false);

  const items = useMemo(
    () => buildQueueItems(selection, modeForRelease),
    [selection, modeForRelease],
  );

  const count = countSelected(selection.selected);
  const label = buildQueueLabel(items.map((item) => item.mode));
  const canQueue = count > 0 && !queueing;
  const onBookStep = selection.activeStep === 'ebook';

  const handleQueue = async () => {
    if (!canQueue) return;
    setQueueing(true);
    try {
      await selection.onQueue({ items, organize: selection.organize, multiBook });
    } finally {
      setQueueing(false);
    }
  };

  return (
    <div className="shrink-0 border-t border-(--border-muted) bg-(--bg) px-5 py-3 sm:bg-(--bg-soft)">
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <div className="flex items-center gap-1.5" role="tablist" aria-label="Content type">
          {SELECTION_STEPS.map((step) => {
            const available = selection.stepAvailability[step];
            const active = selection.activeStep === step;
            const picked = Boolean(selection.selected[step]);
            const mode = selection.modes[step];
            let pillClass =
              'border-(--border-muted) bg-transparent text-zinc-600 hover:border-zinc-500 dark:text-zinc-300';
            if (active) {
              pillClass =
                'border-zinc-900 bg-zinc-900 text-white dark:border-white dark:bg-white dark:text-zinc-900';
            } else if (picked) {
              pillClass =
                'border-(--border-muted) bg-transparent text-emerald-600 hover:border-zinc-500 dark:text-emerald-400';
            }
            let unavailableHint: string | undefined;
            if (mode === 'request_book')
              unavailableHint = 'Request only — picking releases is not available for this type';
            else if (mode === 'blocked') unavailableHint = 'Unavailable by policy';
            return (
              <button
                key={step}
                type="button"
                role="tab"
                aria-selected={active}
                disabled={!available}
                title={unavailableHint}
                onClick={() => selection.onStepChange(step)}
                className={`inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-xs font-bold transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${pillClass}`}
              >
                {picked ? <CheckIcon /> : null}
                {STEP_LABEL[step]}
              </button>
            );
          })}
        </div>

        <span className="min-w-0 truncate text-xs text-zinc-500 dark:text-zinc-400">
          {buildSelectionSummary(selection.selected, selection.packPlans)}
        </span>

        <span className="flex-1" />

        {onBookStep && selection.stepAvailability.audiobook ? (
          <button
            type="button"
            onClick={() => selection.onStepChange('audiobook')}
            className="hover-surface rounded-xl border border-(--border-muted) px-3.5 py-2 text-sm font-semibold text-(--text) transition-colors"
          >
            Audiobook &rarr;
          </button>
        ) : null}

        <button
          type="button"
          onClick={() => void handleQueue()}
          disabled={!canQueue}
          aria-disabled={!canQueue}
          className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-bold text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <svg
            className="h-4 w-4"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2.5}
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          {queueing ? 'Queueing…' : label}
        </button>
      </div>
    </div>
  );
};
