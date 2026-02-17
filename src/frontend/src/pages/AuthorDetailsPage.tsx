import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Header } from '../components/Header';
import { AuthorModal, AuthorModalAuthor } from '../components/AuthorModal';
import { ActivityStatusCounts } from '../utils/activityBadge';
import { Book, ContentType, ReleasePrimaryAction, StatusData } from '../types';

interface AuthorDetailsPageProps {
  onActivityClick?: () => void;
  onGetReleases?: (
    book: Book,
    contentType: ContentType,
    monitoredEntityId?: number | null,
    actionOverride?: ReleasePrimaryAction,
  ) => Promise<void>;
  defaultReleaseContentType?: ContentType;
  defaultReleaseActionEbook?: ReleasePrimaryAction;
  defaultReleaseActionAudiobook?: ReleasePrimaryAction;
  onBack?: () => void;
  onMonitoredClick?: () => void;
  logoUrl?: string;
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

export const AuthorDetailsPage = ({
  onActivityClick,
  onGetReleases,
  defaultReleaseContentType = 'ebook',
  defaultReleaseActionEbook = 'interactive_search',
  defaultReleaseActionAudiobook = 'interactive_search',
  onBack,
  onMonitoredClick,
  logoUrl,
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
}: AuthorDetailsPageProps) => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const initialBooksQuery = (searchParams.get('initial_query') || '').trim();
  const initialBookProvider = (searchParams.get('initial_provider') || '').trim() || undefined;
  const initialBookProviderId = (searchParams.get('initial_provider_id') || '').trim() || undefined;
  const [headerSearch, setHeaderSearch] = useState('');

  const author = useMemo<AuthorModalAuthor | null>(() => {
    const name = (searchParams.get('name') || '').trim();
    if (!name) return null;
    const provider = (searchParams.get('provider') || '').trim();
    const providerId = (searchParams.get('provider_id') || '').trim();
    const sourceUrl = (searchParams.get('source_url') || '').trim();
    const photoUrl = (searchParams.get('photo_url') || '').trim();

    return {
      name,
      provider: provider || null,
      provider_id: providerId || null,
      source_url: sourceUrl || null,
      photo_url: photoUrl || null,
    };
  }, [searchParams]);

  const monitoredEntityId = useMemo(() => {
    const raw = (searchParams.get('entity_id') || '').trim();
    if (!raw) return null;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : null;
  }, [searchParams]);

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--background-color)', color: 'var(--text-color)' }}>
      <div className="fixed top-0 left-0 right-0 z-40">
        <Header
          showSearch
          logoUrl={logoUrl}
          searchInput={headerSearch}
          searchPlaceholder="Search authors to monitor.."
          onSearchChange={setHeaderSearch}
          onSearch={() => {
            const q = headerSearch.trim();
            navigate(q ? `/monitored?q=${encodeURIComponent(q)}` : '/monitored');
          }}
          onDownloadsClick={onActivityClick}
          onLogoClick={onBack}
          debug={debug}
          onMonitoredClick={onMonitoredClick}
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

      <main className="relative w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 pt-32 lg:pt-24">
        {author ? (
          <AuthorModal
            author={author}
            displayMode="page"
            onClose={() => navigate('/monitored')}
            onGetReleases={onGetReleases}
            defaultReleaseContentType={defaultReleaseContentType}
            defaultReleaseActionEbook={defaultReleaseActionEbook}
            defaultReleaseActionAudiobook={defaultReleaseActionAudiobook}
            initialBooksQuery={initialBooksQuery || undefined}
            initialBookProvider={initialBookProvider}
            initialBookProviderId={initialBookProviderId}
            monitoredEntityId={monitoredEntityId}
            status={status}
          />
        ) : (
          <section className="rounded-2xl border border-black/10 dark:border-white/10 bg-white/80 dark:bg-white/5 p-5">
            <div className="text-sm text-gray-600 dark:text-gray-300">Missing author details in URL.</div>
            <button
              type="button"
              onClick={() => navigate('/monitored')}
              className="mt-3 px-4 py-2 rounded-full bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium"
            >
              Back to Monitored
            </button>
          </section>
        )}
      </main>
    </div>
  );
};
