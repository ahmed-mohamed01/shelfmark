import { useCallback, useEffect, useMemo, useState } from 'react';
import { Header } from '../components/Header';
import { ActivityStatusCounts } from '../utils/activityBadge';
import {
  createMonitoredEntity,
  deleteMonitoredEntity,
  listMonitoredEntities,
  getSelfUserEditContext,
  fsListDirectories,
  updateSelfUser,
  MetadataAuthor,
  MonitoredEntity,
  searchMetadata,
  searchMetadataAuthors,
} from '../services/api';
import { AuthorModal } from '../components/AuthorModal';
import { FolderBrowserModal } from '../components/FolderBrowserModal';
import { AuthorCardView } from '../components/resultsViews/AuthorCardView';
import { AuthorCompactView } from '../components/resultsViews/AuthorCompactView';
import { Book, ContentType, StatusData } from '../types';

interface MonitoredAuthor {
  id: number;
  name: string;
  provider?: string;
  provider_id?: string;
  photo_url?: string;
  books_count?: number;
  cached_bio?: string;
  cached_source_url?: string;
}

interface MonitoredPageProps {
  onActivityClick?: () => void;
  onGetReleases?: (book: Book, contentType: ContentType, monitoredEntityId?: number | null) => Promise<void>;
  onBack?: () => void;

  status?: StatusData;

  debug?: boolean;
  onSettingsClick?: () => void;
  statusCounts?: ActivityStatusCounts;
  isAdmin?: boolean;
  canAccessSettings?: boolean;
  authRequired?: boolean;
  isAuthenticated?: boolean;
  username?: string | null;
  displayName?: string | null;
  onLogout?: () => void;
}

const normalizeAuthor = (value: string): string => {
  return value
    .split(/\s+/)
    .join(' ')
    .trim();
};

const GRID_CLASSES = {
  mobile: 'grid-cols-1 items-start',
  card: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 items-stretch',
  compact: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-2 xl:grid-cols-3 items-start',
} as const;

const AuthorRowThumbnail = ({ photo_url, name }: { photo_url?: string; name: string }) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);

  if (!photo_url || imageError) {
    return (
      <div
        className="w-7 h-10 sm:w-10 sm:h-14 rounded bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-[8px] sm:text-[9px] font-medium text-gray-500 dark:text-gray-300"
        aria-label="No photo available"
      >
        No Photo
      </div>
    );
  }

  return (
    <div className="relative w-7 h-10 sm:w-10 sm:h-14 rounded overflow-hidden bg-gray-100 dark:bg-gray-800 border border-white/40 dark:border-gray-700/70">
      {!imageLoaded && (
        <div className="absolute inset-0 bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
      )}
      <img
        src={photo_url}
        alt={name}
        className="w-full h-full object-cover object-top"
        loading="lazy"
        onLoad={() => setImageLoaded(true)}
        onError={() => setImageError(true)}
        style={{ opacity: imageLoaded ? 1 : 0, transition: 'opacity 0.2s ease-in-out' }}
      />
    </div>
  );
};

export const MonitoredPage = ({
  onActivityClick,
  onGetReleases,
  onBack,
  status,
  debug,
  onSettingsClick,
  statusCounts,
  isAdmin,
  canAccessSettings,
  authRequired,
  isAuthenticated,
  username,
  displayName,
  onLogout,
}: MonitoredPageProps) => {
  const [view, setView] = useState<'landing' | 'search'>('landing');
  const [authorQuery, setAuthorQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [monitoredError, setMonitoredError] = useState<string | null>(null);
  const [rootsError, setRootsError] = useState<string | null>(null);
  const [authorResults, setAuthorResults] = useState<string[]>([]);
  const [authorCards, setAuthorCards] = useState<MetadataAuthor[]>([]);
  const [authorViewMode, setAuthorViewMode] = useState<'card' | 'compact' | 'list'>(() => {
    const saved = localStorage.getItem('authorViewMode');
    return saved === 'card' || saved === 'compact' || saved === 'list' ? saved : 'card';
  });
  const [monitoredViewMode, setMonitoredViewMode] = useState<'card' | 'compact' | 'list'>(() => {
    const saved = localStorage.getItem('monitoredAuthorViewMode');
    return saved === 'card' || saved === 'compact' || saved === 'list' ? saved : 'compact';
  });
  const [monitored, setMonitored] = useState<MonitoredAuthor[]>([]);
  const [activeAuthor, setActiveAuthor] = useState<{
    name: string;
    provider?: string | null;
    provider_id?: string | null;
    source_url?: string | null;
    photo_url?: string | null;
    monitoredEntityId?: number | null;
  } | null>(null);

  const [monitorModalState, setMonitorModalState] = useState<{
    open: boolean;
    author: { name: string; provider?: string; provider_id?: string; photo_url?: string; books_count?: number } | null;
    ebookAuthorDir: string;
    audiobookAuthorDir: string;
  }>(() => ({
    open: false,
    author: null,
    ebookAuthorDir: '',
    audiobookAuthorDir: '',
  }));

  const [monitoredEbookRoots, setMonitoredEbookRoots] = useState<string[]>([]);
  const [monitoredAudiobookRoots, setMonitoredAudiobookRoots] = useState<string[]>([]);

  const [folderBrowserState, setFolderBrowserState] = useState<{
    open: boolean;
    kind: 'ebook' | 'audiobook' | null;
    initialPath: string | null;
  }>({ open: false, kind: null, initialPath: null });

  const [pathSuggestState, setPathSuggestState] = useState<{
    kind: 'ebook' | 'audiobook' | null;
    open: boolean;
    loading: boolean;
    parent: string | null;
    entries: { name: string; path: string }[];
    error: string | null;
  }>({
    kind: null,
    open: false,
    loading: false,
    parent: null,
    entries: [],
    error: null,
  });

  const [editingMonitorPathKind, setEditingMonitorPathKind] = useState<'ebook' | 'audiobook' | null>(null);

  const [isDesktop, setIsDesktop] = useState(false);
  useEffect(() => {
    let timeoutId: number;

    const checkDesktop = () => {
      clearTimeout(timeoutId);
      timeoutId = window.setTimeout(() => {
        setIsDesktop(window.innerWidth >= 640);
      }, 100);
    };

    setIsDesktop(window.innerWidth >= 640);
    window.addEventListener('resize', checkDesktop);
    return () => {
      clearTimeout(timeoutId);
      window.removeEventListener('resize', checkDesktop);
    };
  }, []);

  useEffect(() => {
    let alive = true;

    const toMonitoredAuthor = (entity: MonitoredEntity): MonitoredAuthor | null => {
      if (entity.kind !== 'author') {
        return null;
      }
      const name = normalizeAuthor(entity.name);
      if (!name) {
        return null;
      }

      const settings = entity.settings && typeof entity.settings === 'object' ? entity.settings : {};
      const photo_url = typeof (settings as Record<string, unknown>).photo_url === 'string'
        ? ((settings as Record<string, unknown>).photo_url as string)
        : undefined;
      const books_count = typeof (settings as Record<string, unknown>).books_count === 'number'
        ? ((settings as Record<string, unknown>).books_count as number)
        : undefined;

      return {
        id: entity.id,
        name,
        provider: entity.provider || undefined,
        provider_id: entity.provider_id || undefined,
        photo_url,
        books_count,
        cached_bio: entity.cached_bio || undefined,
        cached_source_url: entity.cached_source_url || undefined,
      };
    };

    const load = async () => {
      setMonitoredError(null);
      try {
        const entities = await listMonitoredEntities();
        const next = entities
          .map(toMonitoredAuthor)
          .filter((item): item is MonitoredAuthor => item !== null);
        if (!alive) {
          return;
        }
        setMonitored(next);
      } catch (e) {
        if (!alive) {
          return;
        }
        const message = e instanceof Error ? e.message : 'Failed to load monitored authors';
        setMonitoredError(message);
        setMonitored([]);
      }
    };

    void load();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;

    const loadRoots = async () => {
      setRootsError(null);
      try {
        const ctx = await getSelfUserEditContext();
        const overrides = ctx?.deliveryPreferences?.userOverrides ?? {};
        const ebook = overrides.MONITORED_EBOOK_ROOTS;
        const audio = overrides.MONITORED_AUDIOBOOK_ROOTS;
        setMonitoredEbookRoots(Array.isArray(ebook) ? ebook.filter((v): v is string => typeof v === 'string' && Boolean(v.trim())) : []);
        setMonitoredAudiobookRoots(Array.isArray(audio) ? audio.filter((v): v is string => typeof v === 'string' && Boolean(v.trim())) : []);
      } catch (e) {
        if (!alive) return;
        const message = e instanceof Error ? e.message : 'Failed to load folder suggestions';
        setRootsError(message);
        setMonitoredEbookRoots([]);
        setMonitoredAudiobookRoots([]);
      }
    };

    void loadRoots();
    return () => {
      alive = false;
    };
  }, []);

  const joinPath = useCallback((root: string, authorName: string): string => {
    const r = (root || '').trim().replace(/\/+$/g, '');
    if (!r) return '';
    return `${r}/${authorName}`;
  }, []);

  const normalizeAbsolutePath = useCallback((value: string): string => {
    const v = (value || '').trim();
    if (!v) return '';
    return v.replace(/\/+$/g, '');
  }, []);

  const stripTrailingAuthorName = useCallback((fullPath: string, authorName: string): string => {
    const normalized = normalizeAbsolutePath(fullPath);
    const a = (authorName || '').trim();
    if (!normalized || !a) return normalized;
    const suffix = `/${a}`;
    if (normalized.endsWith(suffix)) {
      return normalized.slice(0, -suffix.length) || '/';
    }
    return normalized;
  }, [normalizeAbsolutePath]);

  const deriveRootFromAuthorDir = useCallback((authorDir: string): string => {
    const normalized = normalizeAbsolutePath(authorDir);
    if (!normalized || !normalized.startsWith('/')) return '';
    const idx = normalized.lastIndexOf('/');
    if (idx <= 0) return '';
    return normalized.slice(0, idx);
  }, [normalizeAbsolutePath]);

  const persistLearnedRoots = useCallback(async (nextEbookRoot: string, nextAudiobookRoot: string) => {
    const ebookRoot = normalizeAbsolutePath(nextEbookRoot);
    const audioRoot = normalizeAbsolutePath(nextAudiobookRoot);

    if (!ebookRoot && !audioRoot) {
      return;
    }

    const nextSettings: Record<string, unknown> = {};

    if (ebookRoot) {
      const merged = [ebookRoot, ...monitoredEbookRoots].filter(Boolean);
      const unique = Array.from(new Set(merged));
      nextSettings.MONITORED_EBOOK_ROOTS = unique;
      setMonitoredEbookRoots(unique);
    }

    if (audioRoot) {
      const merged = [audioRoot, ...monitoredAudiobookRoots].filter(Boolean);
      const unique = Array.from(new Set(merged));
      nextSettings.MONITORED_AUDIOBOOK_ROOTS = unique;
      setMonitoredAudiobookRoots(unique);
    }

    try {
      await updateSelfUser({ settings: nextSettings });
    } catch {
      // Best-effort persistence; ignore.
    }
  }, [monitoredEbookRoots, monitoredAudiobookRoots, normalizeAbsolutePath]);

  const monitoredAuthorsForCards: MetadataAuthor[] = useMemo(() => {
    return monitored.map((item) => ({
      provider: item.provider || 'hardcover',
      provider_id: item.provider_id || item.name,
      name: item.name,
      photo_url: item.photo_url,
      source_url: item.cached_source_url || null,
      bio: item.cached_bio || null,
      stats: {
        books_count: typeof item.books_count === 'number' ? item.books_count : null,
      },
    }));
  }, [monitored]);

  useEffect(() => {
    try {
      localStorage.setItem('authorViewMode', authorViewMode);
    } catch {
      // ignore
    }
  }, [authorViewMode]);

  useEffect(() => {
    try {
      localStorage.setItem('monitoredAuthorViewMode', monitoredViewMode);
    } catch {
      // ignore
    }
  }, [monitoredViewMode]);

  const monitoredNames = useMemo(() => new Set(monitored.map((a) => a.name.toLowerCase())), [monitored]);

  const monitoredEntityIdByName = useMemo(() => {
    const map = new Map<string, number>();
    for (const item of monitored) {
      map.set(item.name.toLowerCase(), item.id);
    }
    return map;
  }, [monitored]);

  const runAuthorSearch = useCallback(async () => {
    const q = normalizeAuthor(authorQuery);
    setSearchError(null);
    setAuthorResults([]);
    setAuthorCards([]);

    if (!q) {
      return;
    }

    setIsSearching(true);
    setView('search');
    try {
      const authorResponse = await searchMetadataAuthors(q, 20, 1, 'ebook');

      if (authorResponse.supportsAuthors && authorResponse.authors.length > 0) {
        setAuthorCards(authorResponse.authors);
        setAuthorResults(authorResponse.authors.map((a) => a.name));
        return;
      }

      const result = await searchMetadata('', 40, 'relevance', { author: q }, 1, 'ebook');
      const unique = new Map<string, string>();

      result.books.forEach((book) => {
        (book.author || '')
          .split(',')
          .map((name) => normalizeAuthor(name))
          .filter(Boolean)
          .forEach((name) => {
            const key = name.toLowerCase();
            if (!unique.has(key)) {
              unique.set(key, name);
            }
          });
      });

      const results = Array.from(unique.values()).sort((a, b) => a.localeCompare(b));
      setAuthorResults(results);
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to search authors';
      setSearchError(message);
    } finally {
      setIsSearching(false);
    }
  }, [authorQuery]);

  const openMonitorModal = useCallback((payload: { name: string; provider?: string; provider_id?: string; photo_url?: string; books_count?: number }) => {
    const normalized = normalizeAuthor(payload.name);
    if (!normalized) return;

    const ebookSuggestion = monitoredEbookRoots.length > 0 ? joinPath(monitoredEbookRoots[0], normalized) : '';
    const audioSuggestion = monitoredAudiobookRoots.length > 0 ? joinPath(monitoredAudiobookRoots[0], normalized) : '';

    setMonitorModalState({
      open: true,
      author: { ...payload, name: normalized },
      ebookAuthorDir: ebookSuggestion,
      audiobookAuthorDir: audioSuggestion,
    });
    setPathSuggestState({ kind: null, open: false, loading: false, parent: null, entries: [], error: null });
    setEditingMonitorPathKind(null);
  }, [joinPath, monitoredAudiobookRoots, monitoredEbookRoots]);

  const closeMonitorModal = useCallback(() => {
    setMonitorModalState({
      open: false,
      author: null,
      ebookAuthorDir: '',
      audiobookAuthorDir: '',
    });
    setPathSuggestState({ kind: null, open: false, loading: false, parent: null, entries: [], error: null });
    setEditingMonitorPathKind(null);
  }, []);

  const splitPathForSuggest = useCallback((raw: string): { parent: string | null; prefix: string } => {
    const value = raw || '';
    if (!value.startsWith('/')) {
      return { parent: null, prefix: '' };
    }
    const lastSlash = value.lastIndexOf('/');
    if (lastSlash <= 0) {
      return { parent: '/', prefix: value.slice(1) };
    }
    const parent = value.slice(0, lastSlash) || '/';
    const prefix = value.slice(lastSlash + 1);
    return { parent, prefix };
  }, []);

  const refreshPathSuggestions = useCallback(async (kind: 'ebook' | 'audiobook', rawValue: string) => {
    const { parent, prefix } = splitPathForSuggest(rawValue);
    if (!parent) {
      setPathSuggestState((prev) => ({ ...prev, kind, open: false, loading: false, parent: null, entries: [], error: null }));
      return;
    }

    setPathSuggestState((prev) => ({ ...prev, kind, open: true, loading: true, parent, entries: [], error: null }));
    try {
      const res = await fsListDirectories(parent);
      const entries = (res.directories || [])
        .filter((d) => !prefix || d.name.toLowerCase().startsWith(prefix.toLowerCase()))
        .slice(0, 12);
      setPathSuggestState((prev) => ({ ...prev, kind, open: true, loading: false, parent, entries, error: null }));
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to list folders';
      setPathSuggestState((prev) => ({ ...prev, kind, open: true, loading: false, parent, entries: [], error: message }));
    }
  }, [splitPathForSuggest]);

  const confirmMonitorAuthor = useCallback(async () => {
    const payload = monitorModalState.author;
    if (!payload) return;

    const normalized = normalizeAuthor(payload.name);
    if (!normalized) return;

    const ebookAuthorDir = normalizeAbsolutePath(monitorModalState.ebookAuthorDir);
    const audiobookAuthorDir = normalizeAbsolutePath(monitorModalState.audiobookAuthorDir);

    if (!ebookAuthorDir && !audiobookAuthorDir) {
      setMonitoredError('Please set an Ebook folder or Audiobook folder.');
      return;
    }

    setMonitoredError(null);
    try {
      const created = await createMonitoredEntity({
        kind: 'author',
        name: normalized,
        provider: payload.provider,
        provider_id: payload.provider_id,
        settings: {
          photo_url: payload.photo_url,
          books_count: payload.books_count,
          ebook_author_dir: ebookAuthorDir || undefined,
          audiobook_author_dir: audiobookAuthorDir || undefined,
        },
      });

      const learnedEbookRoot = ebookAuthorDir ? deriveRootFromAuthorDir(ebookAuthorDir) : '';
      const learnedAudioRoot = audiobookAuthorDir ? deriveRootFromAuthorDir(audiobookAuthorDir) : '';
      void persistLearnedRoots(learnedEbookRoot, learnedAudioRoot);

      setMonitored((prev) => {
        const next = prev.filter((item) => item.id !== created.id);
        next.unshift({
          id: created.id,
          name: normalized,
          provider: created.provider || payload.provider,
          provider_id: created.provider_id || payload.provider_id,
          photo_url: payload.photo_url,
          books_count: payload.books_count,
        });
        return next;
      });
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to monitor author';
      setMonitoredError(message);
      return;
    }

    closeMonitorModal();
    setAuthorQuery('');
    setAuthorResults([]);
    setAuthorCards([]);
    setSearchError(null);
    setView('landing');
  }, [closeMonitorModal, deriveRootFromAuthorDir, monitorModalState, normalizeAbsolutePath, persistLearnedRoots]);

  const removeMonitored = useCallback(async (name: string) => {
    const normalized = normalizeAuthor(name);
    const match = monitored.find((item) => item.name.toLowerCase() === normalized.toLowerCase());
    if (!match) {
      return;
    }

    setMonitoredError(null);
    try {
      await deleteMonitoredEntity(match.id);
      setMonitored((prev) => prev.filter((item) => item.id !== match.id));
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to remove monitored author';
      setMonitoredError(message);
    }
  }, [monitored]);

  const openAuthorModal = useCallback((payload: { name: string; provider?: string | null; provider_id?: string | null; source_url?: string | null; photo_url?: string | null; monitoredEntityId?: number | null }) => {
    const normalized = normalizeAuthor(payload.name);
    if (!normalized) {
      return;
    }
    setActiveAuthor({
      name: normalized,
      provider: payload.provider || null,
      provider_id: payload.provider_id || null,
      source_url: payload.source_url || null,
      photo_url: payload.photo_url || null,
      monitoredEntityId: payload.monitoredEntityId ?? null,
    });
  }, []);

  const clearSearchAndReturn = useCallback(() => {
    setAuthorQuery('');
    setAuthorResults([]);
    setAuthorCards([]);
    setSearchError(null);
    setView('landing');
  }, []);

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--background-color)', color: 'var(--text-color)' }}>
      <div className="fixed top-0 left-0 right-0 z-40">
        <Header
          showSearch={false}
          onDownloadsClick={onActivityClick}
          onLogoClick={onBack}
          debug={debug}
          onMonitoredClick={undefined}
          onSettingsClick={onSettingsClick}
          statusCounts={statusCounts}
          isAdmin={isAdmin}
          canAccessSettings={canAccessSettings}
          authRequired={authRequired}
          isAuthenticated={isAuthenticated}
          username={username}
          displayName={displayName}
          onLogout={onLogout}
        />
      </div>

      <main className="relative w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6" style={{ paddingTop: '5rem' }}>
        <div className="flex flex-col gap-6">
          <div>
            <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Monitored</h1>
          </div>

          <div className="flex flex-col gap-3">
            <div className="flex flex-col sm:flex-row gap-2">
              <input
                value={authorQuery}
                onChange={(e) => setAuthorQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    void runAuthorSearch();
                  }
                }}
                placeholder="Search authors (Hardcover)"
                className="w-full sm:flex-1 px-4 py-2 rounded-full bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-gray-900 dark:text-gray-100"
              />
              <button
                onClick={() => void runAuthorSearch()}
                disabled={isSearching}
                className="px-4 py-2 rounded-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white font-medium"
              >
                {isSearching ? 'Searching…' : 'Search'}
              </button>
              {view === 'search' && (
                <button
                  onClick={clearSearchAndReturn}
                  className="px-4 py-2 rounded-full bg-white/70 hover:bg-white text-gray-900 font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                >
                  Back
                </button>
              )}
            </div>

            {searchError && (
              <div className="text-sm text-red-500">{searchError}</div>
            )}

            {monitoredError && (
              <div className="text-sm text-red-500">{monitoredError}</div>
            )}

            {rootsError && (
              <div className="text-sm text-red-500">{rootsError}</div>
            )}
          </div>

          {view === 'landing' ? (
            monitored.length === 0 ? (
              <div className="rounded-2xl bg-white/0 dark:bg-white/0 py-10">
                <div className="mx-auto max-w-md text-center">
                  <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-black/5 dark:bg-white/10">
                    <svg
                      className="h-6 w-6 text-gray-500 dark:text-gray-400"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                      strokeWidth={1.5}
                      aria-hidden="true"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z"
                      />
                    </svg>
                  </div>
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">No monitored authors</div>
                  <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">Search for an author above to start monitoring.</div>
                </div>
              </div>
            ) : (
              <section className="rounded-2xl border border-black/10 dark:border-white/10 bg-white/80 dark:bg-white/5 p-4">
                <div className="flex items-center justify-between mb-3 relative z-10">
                  <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Monitored Authors</h2>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setMonitoredViewMode('card')}
                      className={`p-2 rounded-full transition-all duration-200 ${
                        monitoredViewMode === 'card'
                          ? 'text-white bg-emerald-600 hover:bg-emerald-700'
                          : 'hover-action text-gray-900 dark:text-gray-100'
                      }`}
                      title="Card view"
                      aria-label="Card view"
                      aria-pressed={monitoredViewMode === 'card'}
                    >
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.5">
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25A2.25 2.25 0 0 1 13.5 18v-2.25Z"
                        />
                      </svg>
                    </button>
                    <button
                      type="button"
                      onClick={() => setMonitoredViewMode('compact')}
                      className={`p-2 rounded-full transition-all duration-200 ${
                        monitoredViewMode === 'compact'
                          ? 'text-white bg-emerald-600 hover:bg-emerald-700'
                          : 'hover-action text-gray-900 dark:text-gray-100'
                      }`}
                      title="Compact view"
                      aria-label="Compact view"
                      aria-pressed={monitoredViewMode === 'compact'}
                    >
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.5">
                        <rect x="3.75" y="4.5" width="6" height="6" rx="1.125" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6h8.25M12 8.25h6" />
                        <rect x="3.75" y="13.5" width="6" height="6" rx="1.125" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 15h8.25M12 17.25h6" />
                      </svg>
                    </button>
                    <button
                      type="button"
                      onClick={() => setMonitoredViewMode('list')}
                      className={`p-2 rounded-full transition-all duration-200 ${
                        monitoredViewMode === 'list'
                          ? 'text-white bg-emerald-600 hover:bg-emerald-700'
                          : 'hover-action text-gray-900 dark:text-gray-100'
                      }`}
                      title="List view"
                      aria-label="List view"
                      aria-pressed={monitoredViewMode === 'list'}
                    >
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.5">
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0ZM3.75 12h.007v.008H3.75V12Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm-.375 5.25h.007v.008H3.75v-.008Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z"
                        />
                      </svg>
                    </button>
                  </div>
                </div>

                {monitoredViewMode === 'list' ? (
                  <div className="flex flex-col gap-2">
                    {monitoredAuthorsForCards.map((author) => {
                      return (
                        <div
                          key={`${author.provider}:${author.provider_id}`}
                          className="flex items-center justify-between gap-3 rounded-xl px-3 py-2 bg-black/5 dark:bg-white/5"
                        >
                          <div className="flex items-center gap-2 sm:gap-3 flex-1 min-w-0">
                            <AuthorRowThumbnail photo_url={author.photo_url || undefined} name={author.name} />
                            <button
                              type="button"
                              onClick={() => openAuthorModal({ ...author, monitoredEntityId: monitoredEntityIdByName.get(author.name.toLowerCase()) ?? null })}
                              className="text-left flex-1 min-w-0"
                            >
                              <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{author.name}</div>
                              <div className="text-xs text-gray-500 dark:text-gray-400 truncate">
                                {typeof author.stats?.books_count === 'number' ? `${author.stats?.books_count} books` : 'Unknown'}
                                {author.provider ? ` • ${author.provider}` : ''}
                              </div>
                            </button>
                          </div>
                          <button
                            type="button"
                            onClick={() => removeMonitored(author.name)}
                            className="px-3 py-1 rounded-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white text-xs font-medium"
                          >
                            Remove
                          </button>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className={`grid gap-8 ${!isDesktop ? GRID_CLASSES.mobile : GRID_CLASSES[monitoredViewMode]}`}>
                    {monitoredAuthorsForCards.map((author, index) => {
                      const shouldUseCardLayout = isDesktop && monitoredViewMode === 'card';
                      return shouldUseCardLayout ? (
                        <AuthorCardView
                          key={`${author.provider}:${author.provider_id}`}
                          author={author}
                          actionLabel="Remove"
                          actionDisabled={false}
                          onAction={() => removeMonitored(author.name)}
                          onOpen={() => openAuthorModal({ ...author, monitoredEntityId: monitoredEntityIdByName.get(author.name.toLowerCase()) ?? null })}
                          onRemove={() => removeMonitored(author.name)}
                          animationDelay={index * 50}
                        />
                      ) : (
                        <AuthorCompactView
                          key={`${author.provider}:${author.provider_id}`}
                          author={author}
                          actionLabel="Remove"
                          actionDisabled={false}
                          onAction={() => removeMonitored(author.name)}
                          onOpen={() => openAuthorModal({ ...author, monitoredEntityId: monitoredEntityIdByName.get(author.name.toLowerCase()) ?? null })}
                          animationDelay={index * 50}
                        />
                      );
                    })}
                  </div>
                )}
              </section>
            )
          ) : (
            <section className="rounded-2xl border border-black/10 dark:border-white/10 bg-white/80 dark:bg-white/5 p-4">
              <div className="flex items-center justify-between mb-3 relative z-10">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="px-3 py-1.5 rounded-full text-xs font-medium bg-white/70 hover:bg-white text-gray-900 dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                    title="Sort"
                    aria-label="Sort"
                  >
                    Most relevant
                  </button>
                  <div className="text-xs text-gray-500 dark:text-gray-400">{authorResults.length}</div>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setAuthorViewMode('card')}
                    className={`p-2 rounded-full transition-all duration-200 ${
                      authorViewMode === 'card'
                        ? 'text-white bg-emerald-600 hover:bg-emerald-700'
                        : 'hover-action text-gray-900 dark:text-gray-100'
                    }`}
                    title="Card view"
                    aria-label="Card view"
                    aria-pressed={authorViewMode === 'card'}
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.5">
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25A2.25 2.25 0 0 1 13.5 18v-2.25Z"
                      />
                    </svg>
                  </button>
                  <button
                    type="button"
                    onClick={() => setAuthorViewMode('compact')}
                    className={`p-2 rounded-full transition-all duration-200 ${
                      authorViewMode === 'compact'
                        ? 'text-white bg-emerald-600 hover:bg-emerald-700'
                        : 'hover-action text-gray-900 dark:text-gray-100'
                    }`}
                    title="Compact view"
                    aria-label="Compact view"
                    aria-pressed={authorViewMode === 'compact'}
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.5">
                      <rect x="3.75" y="4.5" width="6" height="6" rx="1.125" />
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 6h8.25M12 8.25h6" />
                      <rect x="3.75" y="13.5" width="6" height="6" rx="1.125" />
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 15h8.25M12 17.25h6" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    onClick={() => setAuthorViewMode('list')}
                    className={`p-2 rounded-full transition-all duration-200 ${
                      authorViewMode === 'list'
                        ? 'text-white bg-emerald-600 hover:bg-emerald-700'
                        : 'hover-action text-gray-900 dark:text-gray-100'
                    }`}
                    title="List view"
                    aria-label="List view"
                    aria-pressed={authorViewMode === 'list'}
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.5">
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0ZM3.75 12h.007v.008H3.75V12Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm-.375 5.25h.007v.008H3.75v-.008Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z"
                      />
                    </svg>
                  </button>
                </div>
              </div>

              {authorResults.length === 0 ? (
                <div className="text-sm text-gray-500 dark:text-gray-400">Search for an author to add.</div>
              ) : (
                authorViewMode === 'list' ? (
                  <div className="flex flex-col gap-2">
                    {(authorCards.length > 0
                      ? authorCards
                      : authorResults.map((name) => ({
                          provider: 'hardcover',
                          provider_id: name,
                          name,
                          stats: { books_count: null },
                        } as MetadataAuthor))
                    ).map((author) => {
                      const name = author.name;
                      const isMonitored = monitoredNames.has(name.toLowerCase());
                      const booksCount = author.stats?.books_count;
                      return (
                        <div
                          key={`${author.provider}:${author.provider_id}`}
                          className="flex items-center justify-between gap-3 rounded-xl px-3 py-2 bg-black/5 dark:bg-white/5"
                        >
                          <div className="flex items-center gap-2 sm:gap-3 flex-1 min-w-0">
                            <AuthorRowThumbnail photo_url={author.photo_url || undefined} name={name} />
                            <button
                              type="button"
                              onClick={() => openAuthorModal(author)}
                              className="text-left flex-1 min-w-0"
                            >
                              <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{name}</div>
                              <div className="text-xs text-gray-500 dark:text-gray-400 truncate">
                                {typeof booksCount === 'number' ? `${booksCount} books` : 'Unknown'}
                                {author.provider ? ` • ${author.provider}` : ''}
                              </div>
                            </button>
                          </div>
                          <button
                            type="button"
                            onClick={() => openMonitorModal({
                              name,
                              provider: author.provider,
                              provider_id: author.provider_id,
                              photo_url: author.photo_url || undefined,
                              books_count: typeof author.stats?.books_count === 'number' ? author.stats?.books_count : undefined,
                            })}
                            disabled={isMonitored}
                            className="px-3 py-1 rounded-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white text-xs font-medium"
                          >
                            {isMonitored ? 'Monitored' : 'Monitor'}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className={`grid gap-8 ${!isDesktop ? GRID_CLASSES.mobile : GRID_CLASSES[authorViewMode]}`}>
                    {(authorCards.length > 0
                      ? authorCards
                      : authorResults.map((name) => ({
                          provider: 'hardcover',
                          provider_id: name,
                          name,
                          stats: { books_count: null },
                        } as MetadataAuthor))
                    ).map((author, index) => {
                      const name = author.name;
                      const isMonitored = monitoredNames.has(name.toLowerCase());
                      const shouldUseCardLayout = isDesktop && authorViewMode === 'card';

                      return shouldUseCardLayout ? (
                        <AuthorCardView
                          key={`${author.provider}:${author.provider_id}`}
                          author={author}
                          actionLabel={isMonitored ? 'Monitored' : 'Monitor'}
                          actionDisabled={isMonitored}
                          onAction={() => openMonitorModal({
                            name,
                            provider: author.provider,
                            provider_id: author.provider_id,
                            photo_url: author.photo_url || undefined,
                            books_count: typeof author.stats?.books_count === 'number' ? author.stats?.books_count : undefined,
                          })}
                          onOpen={() => openAuthorModal(author)}
                          animationDelay={index * 50}
                        />
                      ) : (
                        <AuthorCompactView
                          key={`${author.provider}:${author.provider_id}`}
                          author={author}
                          actionLabel={isMonitored ? 'Monitored' : 'Monitor'}
                          actionDisabled={isMonitored}
                          onAction={() => openMonitorModal({
                            name,
                            provider: author.provider,
                            provider_id: author.provider_id,
                            photo_url: author.photo_url || undefined,
                            books_count: typeof author.stats?.books_count === 'number' ? author.stats?.books_count : undefined,
                          })}
                          onOpen={() => openAuthorModal(author)}
                          animationDelay={index * 50}
                        />
                      );
                    })}
                  </div>
                )
              )}
            </section>
          )}
        </div>
      </main>

      {activeAuthor && (
        <AuthorModal
          author={activeAuthor}
          onGetReleases={onGetReleases}
          onClose={() => setActiveAuthor(null)}
          monitoredEntityId={activeAuthor.monitoredEntityId}
          status={status}
        />
      )}

      {monitorModalState.open && monitorModalState.author && (
        <div
          className="modal-overlay active sm:px-6 sm:py-6"
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              closeMonitorModal();
            }
          }}
        >
          <div
            className="details-container w-full max-w-lg h-auto settings-modal-enter"
            role="dialog"
            aria-modal="true"
            aria-label="Monitor author folders"
          >
            <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-2xl overflow-hidden">
              <header className="flex items-start justify-between gap-3 border-b border-[var(--border-muted)] px-5 py-4">
                <div className="min-w-0">
                  <div className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Monitor author</div>
                  <div className="mt-1 text-base font-semibold truncate">{monitorModalState.author.name}</div>
                </div>
                <button
                  type="button"
                  onClick={closeMonitorModal}
                  className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
                  aria-label="Close"
                >
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </header>

              <div className="px-5 py-4 space-y-4">
                <div className="space-y-2">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Ebook folder</div>
                  <div className="space-y-2">
                    {(() => {
                      const authorName = monitorModalState.author?.name || '';
                      const rootValue = stripTrailingAuthorName(monitorModalState.ebookAuthorDir, authorName);
                      const suffix = authorName ? `/${authorName}` : '';
                      return (
                        <>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => {
                                setFolderBrowserState({
                                  open: true,
                                  kind: 'ebook',
                                  initialPath: rootValue || null,
                                });
                              }}
                              className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                            >
                              Browse
                            </button>
                            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">Pick the final author folder.</div>
                          </div>
                          {editingMonitorPathKind === 'ebook' ? (
                            <div className="relative">
                              <input
                                value={rootValue}
                                onChange={(e) => {
                                  const value = e.target.value;
                                  const nextFull = authorName ? joinPath(value, authorName) : value;
                                  setMonitorModalState((prev) => ({ ...prev, ebookAuthorDir: nextFull }));
                                  void refreshPathSuggestions('ebook', value);
                                }}
                                onFocus={() => void refreshPathSuggestions('ebook', rootValue)}
                                onBlur={() => {
                                  window.setTimeout(() => {
                                    setPathSuggestState((prev) => ({ ...prev, open: false }));
                                  }, 150);
                                }}
                                placeholder="/books/ebooks"
                                className="w-full px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm"
                              />
                              {pathSuggestState.open && pathSuggestState.kind === 'ebook' ? (
                                <div className="absolute z-10 mt-1 w-full rounded-xl border border-[var(--border-muted)] bg-[var(--bg)] shadow-lg overflow-hidden">
                                  {pathSuggestState.loading ? (
                                    <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">Loading…</div>
                                  ) : pathSuggestState.error ? (
                                    <div className="px-3 py-2 text-xs text-red-500">{pathSuggestState.error}</div>
                                  ) : pathSuggestState.entries.length === 0 ? (
                                    <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">No folders</div>
                                  ) : (
                                    <div className="max-h-56 overflow-auto">
                                      {pathSuggestState.entries.map((entry) => (
                                        <button
                                          key={entry.path}
                                          type="button"
                                          onMouseDown={(e) => e.preventDefault()}
                                          onClick={() => {
                                            const nextFull = authorName ? joinPath(entry.path, authorName) : entry.path;
                                            setMonitorModalState((prev) => ({ ...prev, ebookAuthorDir: nextFull }));
                                            setPathSuggestState((prev) => ({ ...prev, open: false }));
                                          }}
                                          className="w-full text-left px-3 py-2 text-sm hover:bg-black/5 dark:hover:bg-white/10"
                                        >
                                          {entry.path}
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              ) : null}
                            </div>
                          ) : (
                            <div className="flex items-center gap-2">
                              <div className="flex-1 px-3 py-2 rounded-xl bg-white/60 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm break-all">
                                <span className="text-gray-900 dark:text-gray-100">{rootValue || '—'}</span>
                                {suffix ? <span className="text-gray-400 dark:text-gray-500">{suffix}</span> : null}
                              </div>
                              <button
                                type="button"
                                onClick={() => setEditingMonitorPathKind('ebook')}
                                className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                              >
                                Edit
                              </button>
                            </div>
                          )}
                        </>
                      );
                    })()}
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Audiobook folder</div>
                  <div className="space-y-2">
                    {(() => {
                      const authorName = monitorModalState.author?.name || '';
                      const rootValue = stripTrailingAuthorName(monitorModalState.audiobookAuthorDir, authorName);
                      const suffix = authorName ? `/${authorName}` : '';
                      return (
                        <>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => {
                                setFolderBrowserState({
                                  open: true,
                                  kind: 'audiobook',
                                  initialPath: rootValue || null,
                                });
                              }}
                              className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                            >
                              Browse
                            </button>
                            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">Pick the final author folder.</div>
                          </div>
                          {editingMonitorPathKind === 'audiobook' ? (
                            <div className="relative">
                              <input
                                value={rootValue}
                                onChange={(e) => {
                                  const value = e.target.value;
                                  const nextFull = authorName ? joinPath(value, authorName) : value;
                                  setMonitorModalState((prev) => ({ ...prev, audiobookAuthorDir: nextFull }));
                                  void refreshPathSuggestions('audiobook', value);
                                }}
                                onFocus={() => void refreshPathSuggestions('audiobook', rootValue)}
                                onBlur={() => {
                                  window.setTimeout(() => {
                                    setPathSuggestState((prev) => ({ ...prev, open: false }));
                                  }, 150);
                                }}
                                placeholder="/books/audiobooks"
                                className="w-full px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm"
                              />
                              {pathSuggestState.open && pathSuggestState.kind === 'audiobook' ? (
                                <div className="absolute z-10 mt-1 w-full rounded-xl border border-[var(--border-muted)] bg-[var(--bg)] shadow-lg overflow-hidden">
                                  {pathSuggestState.loading ? (
                                    <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">Loading…</div>
                                  ) : pathSuggestState.error ? (
                                    <div className="px-3 py-2 text-xs text-red-500">{pathSuggestState.error}</div>
                                  ) : pathSuggestState.entries.length === 0 ? (
                                    <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">No folders</div>
                                  ) : (
                                    <div className="max-h-56 overflow-auto">
                                      {pathSuggestState.entries.map((entry) => (
                                        <button
                                          key={entry.path}
                                          type="button"
                                          onMouseDown={(e) => e.preventDefault()}
                                          onClick={() => {
                                            const nextFull = authorName ? joinPath(entry.path, authorName) : entry.path;
                                            setMonitorModalState((prev) => ({ ...prev, audiobookAuthorDir: nextFull }));
                                            setPathSuggestState((prev) => ({ ...prev, open: false }));
                                          }}
                                          className="w-full text-left px-3 py-2 text-sm hover:bg-black/5 dark:hover:bg-white/10"
                                        >
                                          {entry.path}
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              ) : null}
                            </div>
                          ) : (
                            <div className="flex items-center gap-2">
                              <div className="flex-1 px-3 py-2 rounded-xl bg-white/60 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm break-all">
                                <span className="text-gray-900 dark:text-gray-100">{rootValue || '—'}</span>
                                {suffix ? <span className="text-gray-400 dark:text-gray-500">{suffix}</span> : null}
                              </div>
                              <button
                                type="button"
                                onClick={() => setEditingMonitorPathKind('audiobook')}
                                className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                              >
                                Edit
                              </button>
                            </div>
                          )}
                        </>
                      );
                    })()}
                  </div>
                </div>
              </div>

              <footer className="flex items-center justify-end gap-2 border-t border-[var(--border-muted)] px-5 py-4 bg-[var(--bg)] sm:bg-[var(--bg-soft)]">
                <button
                  type="button"
                  onClick={closeMonitorModal}
                  className="px-4 py-2 rounded-full bg-white/70 hover:bg-white text-gray-900 font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void confirmMonitorAuthor()}
                  className="px-4 py-2 rounded-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white font-medium"
                >
                  Monitor
                </button>
              </footer>
            </div>
          </div>
        </div>
      )}

      <FolderBrowserModal
        open={folderBrowserState.open}
        title={folderBrowserState.kind === 'audiobook' ? 'Select audiobook folder' : 'Select ebook folder'}
        initialPath={folderBrowserState.initialPath}
        onClose={() => setFolderBrowserState({ open: false, kind: null, initialPath: null })}
        onSelect={(path) => {
          const authorName = monitorModalState.author?.name || '';
          const suggested = authorName ? joinPath(path, authorName) : path;
          if (folderBrowserState.kind === 'audiobook') {
            setMonitorModalState((prev) => ({
              ...prev,
              audiobookAuthorDir: suggested,
            }));
          } else {
            setMonitorModalState((prev) => ({
              ...prev,
              ebookAuthorDir: suggested,
            }));
          }
        }}
      />
    </div>
  );
};
