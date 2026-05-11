import { KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react';
import { CustomSettingsFieldRendererProps } from './types';

const TIME_RE = /^(?:[01]\d|2[0-3]):[0-5]\d$/;

const parseTimes = (raw: unknown): string[] => {
  if (typeof raw !== 'string') return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const segment of raw.split(',')) {
    const trimmed = segment.trim();
    if (TIME_RE.test(trimmed) && !seen.has(trimmed)) {
      seen.add(trimmed);
      out.push(trimmed);
    }
  }
  out.sort();
  return out;
};

export const MonitoredRefreshTimesField = ({
  values,
  onChange,
  isDisabled,
}: CustomSettingsFieldRendererProps) => {
  const times = useMemo(() => parseTimes(values.MONITORED_REFRESH_TIMES), [values.MONITORED_REFRESH_TIMES]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
    }
  }, [editing]);

  const commit = (next: string[]) => {
    onChange('MONITORED_REFRESH_TIMES', next.join(','));
  };

  const closeEditor = () => {
    setEditing(false);
    setDraft('');
    setError(null);
  };

  const handleAdd = () => {
    const trimmed = draft.trim();
    if (!trimmed) {
      setError('Pick a time first.');
      return;
    }
    if (!TIME_RE.test(trimmed)) {
      setError(`Invalid time '${trimmed}'. Use 24-hour HH:MM.`);
      return;
    }
    if (times.includes(trimmed)) {
      setError(`${trimmed} is already in the list.`);
      return;
    }
    commit([...times, trimmed].sort());
    closeEditor();
  };

  const handleRemove = (value: string) => {
    commit(times.filter((t) => t !== value));
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleAdd();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeEditor();
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {times.map((time) => (
          <span
            key={time}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-base-300 bg-base-200 text-sm"
          >
            <span className="tabular-nums">{time}</span>
            <button
              type="button"
              onClick={() => handleRemove(time)}
              disabled={isDisabled}
              aria-label={`Remove ${time}`}
              className="opacity-60 hover:opacity-100 disabled:opacity-30 leading-none"
            >
              ×
            </button>
          </span>
        ))}

        {editing ? (
          <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full border border-base-300 bg-base-100">
            <input
              ref={inputRef}
              type="time"
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                if (error) setError(null);
              }}
              onKeyDown={onKeyDown}
              disabled={isDisabled}
              className="bg-transparent text-sm tabular-nums outline-none border-none p-0 m-0 w-[6.5em]"
            />
            <button
              type="button"
              onClick={handleAdd}
              disabled={isDisabled || !draft}
              aria-label="Confirm add"
              className="text-primary opacity-80 hover:opacity-100 disabled:opacity-30 leading-none"
            >
              ✓
            </button>
            <button
              type="button"
              onClick={closeEditor}
              aria-label="Cancel"
              className="opacity-60 hover:opacity-100 leading-none"
            >
              ×
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            disabled={isDisabled}
            aria-label="Add refresh time"
            className="inline-flex items-center justify-center w-7 h-7 rounded-full border border-dashed border-base-300 opacity-70 hover:opacity-100 disabled:opacity-30 leading-none"
          >
            +
          </button>
        )}
      </div>

      {error && <p className="text-xs text-error">{error}</p>}
      {times.length === 0 && !editing && (
        <p className="text-xs opacity-60">No times scheduled — click + to add one.</p>
      )}
    </div>
  );
};
