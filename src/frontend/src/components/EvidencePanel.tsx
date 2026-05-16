import type { AttributionEvidence } from '../services/monitoredApi';

// ---------------------------------------------------------------------------
// Friendly label maps
// ---------------------------------------------------------------------------

// Suffix → human-readable label. Used for both `embedded_*` and `source_<label>_*`
// names — we strip the prefix and look up the trailing concept. The qualifier
// (e.g. "from ABS") is appended by friendlyEvidenceLabel.
const SIGNAL_SUFFIX_LABELS: Record<string, string> = {
  identifier: 'ISBN / ASIN match',
  title_agree: 'Title matches',
  title_agree_med: 'Title is similar',
  title_agree_low: 'Title is loosely similar',
  author_agree: 'Author matches',
  series_agree: 'Series matches',
  position_disagree: 'Book number conflicts',
};

// Exact-match labels for path-derived signals.
const EVIDENCE_LABELS: Record<string, string> = {
  title_core_high: 'Title matches strongly',
  title_core_med: 'Title matches',
  title_core_low: 'Title matches weakly',
  author_folder: 'Author folder matches',
  author_trailer: 'Author name in filename',
  series_folder: 'Series folder matches',
  series_in_filename: 'Series name in filename',
  position_agree_high: 'Book number matches',
  position_agree_med: 'Book number likely matches',
  position_disagree_high: 'Book number conflicts',
  position_disagree_med: 'Book number unclear',
  wrong_author_folder: 'Folder belongs to a different author',
};

const POSITION_SOURCE_LABELS: Record<string, string> = {
  leading_num: 'leading number',
  explicit_marker: 'explicit Book/Vol marker',
  after_series_name: 'number after series name',
  bare_number: 'bare number',
  decimal: 'decimal position',
  roman_marker: 'roman numeral',
  word_number_marker: 'word-number marker',
};

const SOURCE_QUALIFIER_LABELS: Record<string, string> = {
  abs: 'from AudioBookShelf',
  booklore: 'from Booklore',
};

/** Translate algorithm-internal positive/penalty names into UI text. */
const friendlyEvidenceLabel = (name: string): string => {
  if (EVIDENCE_LABELS[name]) return EVIDENCE_LABELS[name];

  // Strip "embedded_" or "source_<label>_" prefix and try the suffix map.
  // (Source-supplied evidence carries a source label so the UI can distinguish
  // "ABS told us the title matches" from "the EPUB itself does".)
  let suffix = name;
  let qualifier = '';
  if (name.startsWith('embedded_')) {
    suffix = name.slice('embedded_'.length);
    qualifier = ' from file tags';
  } else if (name.startsWith('source_')) {
    const rest = name.slice('source_'.length);
    const underscore = rest.indexOf('_');
    if (underscore > 0) {
      const sourceLabel = rest.slice(0, underscore);
      suffix = rest.slice(underscore + 1);
      qualifier = ` ${SOURCE_QUALIFIER_LABELS[sourceLabel] ?? `from ${sourceLabel}`}`;
    } else {
      suffix = rest;
    }
  }
  const base = SIGNAL_SUFFIX_LABELS[suffix];
  if (base) return base + qualifier;
  return name;
};

const friendlyPositionSource = (s: string): string =>
  POSITION_SOURCE_LABELS[s] ?? s;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type EmbeddedData = AttributionEvidence['embedded_data'];

const MetadataDl = ({ title, data }: { title: string; data: EmbeddedData | undefined }) => {
  if (!data || Object.keys(data).length === 0) {
    return (
      <section>
        <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400">
          {title}
        </h4>
        <div className="text-gray-500 dark:text-gray-400 italic">
          (no fields)
        </div>
      </section>
    );
  }
  return (
    <section>
      <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400">
        {title}
      </h4>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-gray-700 dark:text-gray-300">
        {data.title ? (
          <>
            <dt className="text-gray-500 dark:text-gray-400">Title</dt>
            <dd>{data.title}</dd>
          </>
        ) : null}
        {data.authors && data.authors.length > 0 ? (
          <>
            <dt className="text-gray-500 dark:text-gray-400">
              {data.authors.length > 1 ? 'Authors' : 'Author'}
            </dt>
            <dd>{data.authors.join(', ')}</dd>
          </>
        ) : null}
        {data.series_name ? (
          <>
            <dt className="text-gray-500 dark:text-gray-400">Series</dt>
            <dd>
              {data.series_name}
              {data.series_position != null ? ` #${data.series_position}` : ''}
            </dd>
          </>
        ) : null}
        {data.isbn_13 ? (
          <>
            <dt className="text-gray-500 dark:text-gray-400">ISBN-13</dt>
            <dd className="font-mono">{data.isbn_13}</dd>
          </>
        ) : null}
        {data.isbn_10 ? (
          <>
            <dt className="text-gray-500 dark:text-gray-400">ISBN-10</dt>
            <dd className="font-mono">{data.isbn_10}</dd>
          </>
        ) : null}
        {data.asin ? (
          <>
            <dt className="text-gray-500 dark:text-gray-400">ASIN</dt>
            <dd className="font-mono">{data.asin}</dd>
          </>
        ) : null}
        {data.year ? (
          <>
            <dt className="text-gray-500 dark:text-gray-400">Year</dt>
            <dd>{data.year}</dd>
          </>
        ) : null}
      </dl>
    </section>
  );
};

export const EvidencePanel = ({ evidence }: { evidence: AttributionEvidence }) => {
  const confidencePct = Math.round((evidence.confidence ?? 0) * 100);
  const accepted = !!evidence.accept;

  return (
    <div className="mt-2 rounded-lg border border-[var(--border-muted)] bg-black/5 dark:bg-white/5 p-3 text-xs space-y-3">
      {/* Header — overall decision */}
      <div className="flex items-baseline justify-between gap-3">
        <div className="font-medium text-gray-800 dark:text-gray-200">
          {evidence.hard_reject
            ? 'Rejected — identifier mismatch'
            : accepted ? 'Match accepted' : 'Match below threshold'}
        </div>
        <div className="text-gray-500 dark:text-gray-400">
          {confidencePct}% confidence
          <span className="ml-1 opacity-60">(score {(evidence.net_score ?? 0).toFixed(2)})</span>
        </div>
      </div>

      {evidence.hard_reject && evidence.hard_reject_reason ? (
        <div className="text-red-600 dark:text-red-400">
          Reason: {evidence.hard_reject_reason}
        </div>
      ) : null}

      {/* Positives */}
      {evidence.positives && evidence.positives.length > 0 ? (
        <section>
          <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-green-700 dark:text-green-400">
            Evidence found
          </h4>
          <ul className="space-y-1 text-gray-700 dark:text-gray-300">
            {evidence.positives.map((p, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="text-green-600 dark:text-green-400" aria-hidden>✓</span>
                <span className="flex-1">
                  <span>{friendlyEvidenceLabel(p.name)}</span>
                  {p.detail ? (
                    <span className="ml-1 text-gray-500 dark:text-gray-400">— {p.detail}</span>
                  ) : null}
                </span>
                <span className="tabular-nums text-gray-500 dark:text-gray-400">
                  +{p.weight.toFixed(2)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Penalties */}
      {evidence.penalties && evidence.penalties.length > 0 ? (
        <section>
          <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-red-700 dark:text-red-400">
            Conflicts
          </h4>
          <ul className="space-y-1 text-gray-700 dark:text-gray-300">
            {evidence.penalties.map((p, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="text-red-600 dark:text-red-400" aria-hidden>✗</span>
                <span className="flex-1">
                  <span>{friendlyEvidenceLabel(p.name)}</span>
                  {p.detail ? (
                    <span className="ml-1 text-gray-500 dark:text-gray-400">— {p.detail}</span>
                  ) : null}
                </span>
                <span className="tabular-nums text-gray-500 dark:text-gray-400">
                  {p.weight.toFixed(2)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Position signals */}
      {evidence.position_votes && evidence.position_votes.length > 0 ? (
        <section>
          <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400">
            Book numbers detected in path
          </h4>
          <ul className="space-y-0.5 text-gray-700 dark:text-gray-300">
            {evidence.position_votes.map((v, i) => (
              <li key={i}>
                Book #{v.value}
                <span className="ml-1 text-gray-500 dark:text-gray-400">
                  ({friendlyPositionSource(v.source)}, {v.weight} confidence)
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Embedded file metadata (EPUB tags / M4B tags) */}
      {evidence.embedded_metadata_used ? (
        <MetadataDl title="Metadata from file tags" data={evidence.embedded_data} />
      ) : null}

      {/* External source metadata (ABS / Booklore API) */}
      {evidence.source_metadata_used ? (
        <MetadataDl
          title={(() => {
            const lbl = evidence.source_data?.source_label as string | undefined;
            const pretty = lbl ? (SOURCE_QUALIFIER_LABELS[lbl] ?? `from ${lbl}`) : '';
            return pretty ? `Metadata ${pretty}` : 'Source metadata';
          })()}
          data={evidence.source_data}
        />
      ) : null}
    </div>
  );
};
