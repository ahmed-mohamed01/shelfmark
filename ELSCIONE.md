# Shelfmark with the Elscione source (fork build)

This branch (`claude/elscione-source-195a12`) is upstream Shelfmark v1.4.0 plus an
**Elscione** release source: search and download officially translated light novels
(EPUB and PDF) from server.elscione.com and its mirror server.elsci.one. It is meant to be
used by BookOrbit through Shelfmark's API, and works from Shelfmark's own UI as well.

Design and test notes: [docs/dev/elscione-source.md](docs/dev/elscione-source.md).

## Image

| Tag | What it is |
|-----|------------|
| `ghcr.io/ahmed-mohamed01/shelfmark:elscione` | Latest build of this branch. Moves with every push. |
| `ghcr.io/ahmed-mohamed01/shelfmark:elscione-<commit>` | One fixed build, for pinning or rollback (e.g. `elscione-3890560`). |

Built by [.github/workflows/build-elscione.yml](.github/workflows/build-elscione.yml) on
every push to this branch, or by hand from the Actions tab ("Build Elscione image").
linux/amd64 only.

## Deploy

1. Point the container at `ghcr.io/ahmed-mohamed01/shelfmark:elscione` (Unraid: Docker tab,
   Edit, Repository, Apply). Keep the existing `/config` mount; nothing in it is migrated.
2. In Shelfmark's settings:
   - **Release Sources > Direct Download > Browser Session Reuse:** turn on
     "Reuse Cleared Browser" so downloads after the first skip the site's bot check.
   - **Release Sources > Elscione:** turn on "Enable Elscione Search". The mirror list,
     result limit, file size cap (64 MB) and download timeout live on the same tab.
   - The internal bypasser must be in use (not FlareSolverr): files are downloaded from
     inside the cleared browser page.
3. The first Elscione search builds the index (about a minute, "Elscione index is being
   built"). It is cached in `/config/elscione/` and refreshed every 24 hours in the background.

Updates: Unraid shows "update available" when a new `:elscione` build is out.
Rollback: set Repository to an earlier `:elscione-<commit>` tag.

## BookOrbit

BookOrbit `monitored` (commit `a2d9d6f5`) knows the `elscione` source. Add `elscione` to
the Shelfmark indexer's sources under Settings > System > Requests > Direct feeds, for
example `direct_download,elscione`. Shelfmark's output should be Folder mode into a
downloads folder that BookOrbit can read, with a path mapping on BookOrbit's Shelfmark
download client (see BookOrbit's `docs/SHELFMARK.md`).

## Release notes

### elscione-3890560 (2026-10-17)

- Stable image tag: this branch now builds `:elscione` and `:elscione-<commit>` on every push,
  so deployments no longer need a new `sha-*` tag per build.

### ea6cac4 (2026-10-17)

- Search understands metadata-style titles. "7th Time Loop: The Villainess ... (Light Novel),
  Vol. 1" now finds "7th Time Loop - Volume 01" instead of nothing.
  - Bracketed tags such as "(Light Novel)" are ignored.
  - A requested volume or range ("Vol. 1", "Volume 01", "Book 3", "Vol. 1-3") keeps only files
    whose volume, or volume range such as "Volume 01-17", covers it.
  - Long titles fall back to the series name before ": " or " - ", only when that keeps at
    least two words, and never for a search typed by hand.

### 2b0c31d (2026-10-17)

- New **Elscione** release source (ebooks): cached index with background refresh, local
  search, browser downloads, EPUB/PDF validation, normal post-processing and import.
- Mirror support: server.elscione.com and server.elsci.one, tried in order, falling over
  when one fails; the last working mirror is preferred.
- **Browser session reuse** (off by default): one cleared browser is kept between bypass
  jobs and shared by Anna's Archive and Elscione. It is recycled after 50 jobs or 4 hours
  and closed after 45 idle minutes.
- Anubis bot-check cookies are kept and its interstitial page is recognised.
- Fixed a hang where the bypass helper loaded a second copy of itself and waited forever on
  the reused browser.

## Known limits

- Download speed is set by the site, about 500 KB/s per file (a 10 MB volume takes about 20 s).
  The first download after the browser closes adds a 5 to 30 second bot-check solve.
- Elscione has no author or language data; releases show title, format and size only.
- Files over the size cap (64 MB by default) are not offered.

## Keeping in sync with upstream

Everything Elscione-specific lives in new files. The upstream files touched are small hooks:
`shelfmark/bypass/cookie_store.py`, `shelfmark/bypass/challenge.py`,
`shelfmark/bypass/internal_bypasser.py` and one line in `shelfmark/release_sources/__init__.py`.
Re-apply those when merging a new upstream release.
