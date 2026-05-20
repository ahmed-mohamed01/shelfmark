import { RowThumbnail } from './RowThumbnail';

export interface StackedThumb {
  url?: string | null;
  alt?: string;
  kind?: 'book' | 'author';
}

interface StackedThumbnailsProps {
  /** Up to 3 thumbs are rendered; extras count is shown as a badge. */
  thumbs: StackedThumb[];
  /** Default 'book'. Used as a fallback for thumbs that omit kind. */
  defaultKind?: 'book' | 'author';
}

export const StackedThumbnails = ({ thumbs, defaultKind = 'book' }: StackedThumbnailsProps) => {
  const visible = thumbs.slice(0, 3);
  const extras = Math.max(0, thumbs.length - visible.length);

  if (visible.length === 0) {
    return (
      <div className="w-12 h-18 shrink-0">
        <RowThumbnail kind={defaultKind} className="w-12 h-18" />
      </div>
    );
  }

  if (visible.length === 1) {
    const t = visible[0];
    return (
      <div className="w-12 h-18 shrink-0">
        <RowThumbnail url={t.url} alt={t.alt} kind={t.kind ?? defaultKind} className="w-12 h-18" />
      </div>
    );
  }

  // 2 or 3 thumbs — stagger them as a fanned stack within the 48×72 slot.
  return (
    <div className="relative w-12 h-18 shrink-0">
      {visible.map((t, i) => {
        const zIndex = visible.length - i;
        const offset = i * 4;
        const rotate = (i - (visible.length - 1) / 2) * 4;
        return (
          <div
            key={i}
            className="absolute"
            style={{
              left: `${offset}px`,
              top: `${i * 2}px`,
              zIndex,
              transform: `rotate(${rotate}deg)`,
            }}
          >
            <RowThumbnail
              url={t.url}
              alt={t.alt}
              kind={t.kind ?? defaultKind}
              className="w-10 h-15"
            />
          </div>
        );
      })}
      {extras > 0 ? (
        <div
          className="absolute -bottom-1 -right-1 rounded-full bg-gray-700 text-white text-[9px] font-semibold px-1.5 py-0.5 shadow"
          style={{ zIndex: 100 }}
        >
          +{extras}
        </div>
      ) : null}
    </div>
  );
};
