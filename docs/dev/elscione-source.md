# Elscione release source: implementation spec

Status: approved and implemented; live-tested on the local dev stack (see section 12).
Base: `main` at `9321096` (upstream calibrain/shelfmark v1.4.0). Fork-only work.

## 0. Goal and scope

Add an `elscione` release source so BookOrbit can search and download EPUB/PDF files
from `https://server.elscione.com` through Shelfmark's existing API:

- search: `GET /api/releases?provider=manual&source=elscione&title=...`
- queue: `POST /api/releases/download` with the release payload
- poll / cancel: `GET /api/status`, `DELETE /api/download/<id>/cancel`

What was proven live on `shelfmark-mod` (host Arda), and what this design relies on:

| Fact | Consequence |
|------|-------------|
| The site is behind Cloudflare and Anubis; the internal bypasser clears both in 26-60 s | Reuse the internal bypasser; no second browser stack |
| With `cf_clearance`, the two `techaro.lol-anubis*` cookies and the stored UA, plain HTTP `GET /index.json` returns 200, 3.95 MB, 13,671 files, in about 4 s | The index comes over plain HTTP (piece D) |
| Chrome's rendered copy of `index.json` is truncated at about 429 KB | Never read the index from the page DOM |
| Plain HTTP file downloads return 403 even with those cookies | Files must come from inside the cleared page (piece C) |
| An in-page `fetch` from the cleared page returned 200, 156,484 bytes, valid zip, in 1.2 s | In-page fetch is the primary download method |
| The cookie store currently drops the Anubis cookies | Piece A |
| Every bypass currently starts and closes its own Chrome | Piece B, so a download does not pay a 26-60 s solve each time |

Non-goals: author monitoring (BookOrbit owns it), any frontend change, any
non-EPUB/PDF format, resume/Range support, running without the internal bypasser.

Users are responsible for the legality of what they download; BookOrbit's Shelfmark guide
carries the disclaimer. This source adds no content of its own.

## 1. File layout and upstream footprint

Everything new lives in fork-only files. Shared upstream files get the smallest possible
hooks, listed here so a future upstream merge knows exactly what to re-apply.

New files (no merge conflicts possible):

| File | Piece | Purpose |
|------|-------|---------|
| `shelfmark/bypass/browser_session.py` | B | Generic cleared-browser reuse (port of `monitored_bypass_session.py`) |
| `shelfmark/bypass/page_fetch.py` | C | "Download this URL inside a cleared page to a temp file" |
| `shelfmark/config/browser_session_settings.py` | B, C | Settings tab for session reuse |
| `shelfmark/release_sources/elscione/__init__.py` | D | Registration imports |
| `shelfmark/release_sources/elscione/index.py` | D | Index fetch, cache, flatten, search |
| `shelfmark/release_sources/elscione/source.py` | D | `ReleaseSource` |
| `shelfmark/release_sources/elscione/handler.py` | D | `DownloadHandler` |
| `shelfmark/release_sources/elscione/validate.py` | D | EPUB/PDF/size validation |
| `shelfmark/release_sources/elscione/settings.py` | D | Settings tab |
| `tests/bypass/test_cookie_store_anubis.py` | A | |
| `tests/bypass/test_browser_session.py` | B | |
| `tests/bypass/test_page_fetch.py` | C | |
| `tests/elscione/...` | D | see section 8 |
| `docs/dev/elscione-source.md` | | this file |

Shared-file edits (the whole upstream diff):

| File | Change | Size |
|------|--------|------|
| `shelfmark/bypass/cookie_store.py` | Piece A: keep Anubis cookies | about 5 lines, upstreamable |
| `shelfmark/bypass/challenge.py` | Piece A2: Anubis challenge marker | 1-2 lines, upstreamable |
| `shelfmark/bypass/internal_bypasser.py` | Piece B: `acquire`/`release`/`discard` swaps in `_run_bypass_in_current_process`, `shutdown` in `_run_child_process`, one line in `_BypassHelper._idle_timeout`. Piece C: 2-line `op` dispatch at the top of `_handle_child_request` | about 30 lines |
| `shelfmark/release_sources/__init__.py` | Add `"shelfmark.release_sources.elscione"` to `_BUILTIN_SOURCE_MODULES` | 1 line |

The settings tab in `browser_session_settings.py` is registered by importing it from
`release_sources/elscione/__init__.py`. That keeps `config/settings.py` untouched and
still works in the bypass helper subprocess, because `Config._load_settings()` imports
`shelfmark.release_sources`, which imports every built-in source package. (Alternative:
one bottom-of-file import in `config/settings.py`, which is the pattern
`feat/monitor-authors` uses. Recommended against here: one more shared-file line for no
functional gain.)

## 2. Piece A: cookie store keeps Anubis cookies

### A1. `cookie_store.py`

Anubis sets `techaro.lol-anubis-auth` (the pass) and `techaro.lol-anubis-cookie-verification`
(seen live), and older deployments use `within.website-x-cmd-anubis-auth`.
`_should_extract_cookie` only keeps Cloudflare, DDoS-Guard and AA names, so these are
dropped and the plain-HTTP index fetch is challenged again.

Change:

```python
# Anubis (https://anubis.techaro.lol) proof-of-work pass. The current release names its
# cookies techaro.lol-anubis-*; older ones used within.website-x-cmd-anubis-*.
ANUBIS_COOKIE_PREFIXES = ("techaro.lol-anubis", "within.website-x-cmd-anubis")
...
    is_anubis = name.startswith(ANUBIS_COOKIE_PREFIXES)
    return is_cf or is_ddg or is_aa or is_anubis
```

Nothing else changes: expiry handling already applies to every cookie, and
`get_cf_cookies_for_domain` returns all live cookies for the base domain
(`elscione.com`). Written so it can go upstream as a standalone PR.

### A2. `challenge.py`: recognise the Anubis interstitial

The Anubis page ("Making sure you're not a bot!") matches none of the current
indicators. `_is_bypassed_content` would then call it "bypassed" once its body passes 50
characters, and cookies could be extracted before the proof of work finishes. The live
test passed, probably because Cloudflare came first and Anubis finished during that
solve, but it should not depend on timing.

Add to `OTHER_CHALLENGE_INDICATORS`: `"making sure you're not a bot"` and the
typographic-apostrophe variant `"making sure you’re not a bot"`. Both are matched
against lowercased title/body text. Anubis needs no clicking; it falls under the
existing 15 s passive-solve wait in `_bypass`. Piece C also has its own readiness
check (section 4.3), so this is defence in depth. Upstreamable with A1.

Tests (`tests/bypass/test_cookie_store_anubis.py`):
- both prefixes kept; an unrelated cookie still dropped; `extract_all` path unchanged;
- `store_extracted_cookies` + `get_cf_cookies_for_domain("server.elscione.com")` returns
  `cf_clearance` plus both Anubis cookies;
- `_is_bypassed_content` is False for an Anubis page snapshot, True for a directory listing.

## 3. Piece B: browser session reuse (`shelfmark/bypass/browser_session.py`)

### 3.1 Why and how it fits upstream's architecture

Upstream v1.4.0 (Docker mode) runs every bypass in a long-lived **helper subprocess**
(`_BypassHelper`). Each request starts and closes its own Chrome. The helper process is
reaped after `BYPASS_BROWSER_IDLE_TIMEOUT` (default 180 s, max 3600). Killing the helper
kills its process group, Chrome included.

The fork's `monitored_bypass_session.py` (branch `feat/monitor-authors`, about 190 lines)
already keeps one cleared driver alive in that helper and hands it to the next bypass.
This piece ports it as a generic module and adds parking, recycling, a long idle
timeout and DEBUG-recording control.

### 3.2 Public interface

```python
def reuse_enabled() -> bool
async def acquire(url: str, create_fn: CreateFn, close_fn: CloseFn) -> Any
async def release(driver: Any, *, keep: bool, close_fn: CloseFn) -> None
async def discard(driver: Any, close_fn: CloseFn) -> None
async def shutdown(close_fn: CloseFn) -> None
def helper_idle_timeout(base_seconds: float) -> float
def snapshot() -> dict[str, Any]   # uses, age, idle, key host; for logs and tests
```

Same semantics as the fork module, with these changes:

1. **Session key = egress only**: `(DNS fingerprint, proxy for url)`, not the hostname.
   `--host-resolver-rules` is built from the AA mirror list at browser start, not per
   URL, so one browser can serve AA and Elscione. Keying on hostname would make
   alternating AA searches and Elscione downloads close and reopen Chrome every time.
   Residual risk: an AA IP change while a browser is cached. A failed job releases with
   `keep=False`, so the cost is at most one failed attempt before a fresh browser.
2. **Liveness probe on reuse**: before returning a cached driver, run
   `main_tab.evaluate("1")` with a 5 s timeout. If it fails, drop the driver and create
   a fresh one.
3. **Park between jobs** (`release(keep=True)`): close every tab except the main one,
   navigate the main tab to `about:blank`, then stop the DEBUG recording (3.4). Cookies
   stay in the incognito context, so clearance survives parking. Parking stops a heavy
   page's timers and drops its DOM (Elscione's listing, AA result pages). If parking
   fails, the driver is dropped.
4. **Recycle**: drop and recreate when `uses >= BYPASS_SESSION_MAX_JOBS` (default 50)
   or `age >= BYPASS_SESSION_MAX_AGE_HOURS` (default 4). This replaces the fork's
   hardcoded 25 uses / 300 s.
5. **Idle close**: `BYPASS_SESSION_IDLE_TIMEOUT` (default 2700 s = 45 min, range
   300-14400). Two layers:
   - Docker mode: the helper process is what holds the browser, so the parent keeps it
     alive. One line in `_BypassHelper._idle_timeout`:
     `return browser_session.helper_idle_timeout(<current value>)`. This returns
     `max(base, BYPASS_SESSION_IDLE_TIMEOUT)` when reuse is on and `base` otherwise. When
     the helper is reaped, the browser goes with it.
   - In-process mode (non-Docker, dev on the host): a `threading.Timer` in
     `browser_session`, re-armed by every `release`. When it fires it tries
     `internal_bypasser.LOCKED.acquire(blocking=False)`. If the lock is free it closes the
     driver on `_CDP_WORKER`. If not, a job is running and it re-arms.
6. **Keep rules unchanged from the fork**: never cache a driver that produced no page,
   that was cancelled, or that hit a CDP error; close before replacing; clear the cached
   reference synchronously before the first `await` in `_drop_cached` (the
   timeout-backstop race documented in the fork module).

Default for `BYPASS_REUSE_BROWSER_SESSION`: **off**, same as the fork, so behaviour
matches upstream until switched on. The Elscione source works with reuse off; each
download then pays a full solve (26-60 s). The Elscione settings tab says so, and the
source logs one INFO line per download when reuse is off.

### 3.3 Hook in `internal_bypasser.py`

Same three call-site swaps the fork already carries (diff shown in the fork:
`session.acquire` replaces `_create_cdp_browser`; `session.discard` + `session.acquire`
on `DRIVER_RESET_ERRORS`; `session.release(keep=...)` in `finally`), the `try/finally`
around the stdin loop in `_run_child_process` that calls `session.shutdown`, and the
idle-timeout line from 3.2 item 5. The import is
`from shelfmark.bypass import browser_session as session`.

### 3.4 DEBUG screen recording must not run forever

`_create_cdp_browser` starts ffmpeg when `DEBUG` is on, and `_close_cdp_driver` stops it.
With a reused browser that would become one endless recording. Rule: **one recording per
job**:

- `release(keep=True)` calls `internal_bypasser._stop_ffmpeg_recording()` after parking;
- `acquire()` returning a cached driver calls
  `internal_bypasser._start_ffmpeg_recording(os.environ.get("DISPLAY", ":0"))` when
  `DEBUG` is on and `DISPLAY["ffmpeg"]` is empty.

`browser_session` imports `internal_bypasser` lazily inside these two helpers (no import
cycle at module load). This depends on two private upstream names, so a test asserts
both exist and are callable. A rename then fails CI instead of silently recording
forever. Result: the number of recordings matches upstream (one per bypass/fetch), and no
recording outlives its job.

### 3.5 Logging

- INFO `Reusing cleared browser (use %d/%d, age %.0f min)` / `Starting fresh browser (reason: none|key-changed|recycle-uses|recycle-age|dead)`
- DEBUG `Parked browser on about:blank` (one per job)
- INFO `Closing idle browser session after %.0f min`
- WARNING on park failure or liveness-probe failure (then drop)

### 3.6 Settings (`config/browser_session_settings.py`)

Tab `browser_session`, display "Browser Session Reuse", `group="direct_download"`,
`order=23` (next to Cloudflare Bypass), all `show_when` internal bypasser:

| Key | Type | Default | Range |
|-----|------|---------|-------|
| `BYPASS_REUSE_BROWSER_SESSION` | Checkbox | False | |
| `BYPASS_SESSION_IDLE_TIMEOUT` | Number (s) | 2700 | 300-14400 |
| `BYPASS_SESSION_MAX_JOBS` | Number | 50 | 1-1000 |
| `BYPASS_SESSION_MAX_AGE_HOURS` | Number | 4 | 1-48 |

Memory note for the description text: a parked Chrome holds roughly 300-500 MB for the
idle window.

## 4. Piece C: in-page download (`shelfmark/bypass/page_fetch.py`)

### 4.1 Interface

```python
class PageFetchError(Exception):
    reason: Literal["unsupported", "blocked", "http_status", "too_large",
                    "timeout", "cancelled", "browser", "short_read"]
    status: int | None

@dataclass(frozen=True)
class PageFetchResult:
    path: Path              # == dest
    size: int
    content_type: str | None
    final_url: str
    method: Literal["fetch", "native"]

def fetch_to_file(
    url: str,
    dest: Path,
    *,
    origin_url: str,          # page to clear first, same origin as url
    max_bytes: int,
    timeout: float,
    cancel_flag: Event | None = None,
    method: Literal["fetch", "native_then_fetch"] = "fetch",
) -> PageFetchResult
```

Preconditions checked in the parent before any work, raising `PageFetchError("unsupported")`:
`USE_CF_BYPASS` on, `USING_EXTERNAL_BYPASSER` off (FlareSolverr cannot run code in a
page), `url` and `origin_url` share scheme+host+port, `dest` lies under `TMP_DIR`.

### 4.2 Dispatch (mirrors `internal_bypasser.get`)

- Takes `internal_bypasser.LOCKED`, so it serialises with every other bypass. A 64 MiB
  fetch therefore holds up a concurrent AA search for its duration. Measured: 156 KB in
  1.2 s. Typical light-novel EPUBs are a few MB.
- Honours `network.host_cooldown_remaining(url)` like `get()`.
- Docker mode: sends `{"op": "page_fetch", "url", "origin_url", "dest", "max_bytes",
  "timeout", "method", "dns_config", "result_path"}` through
  `internal_bypasser._BYPASS_HELPER.run(payload, timeout + 30, cancel_flag)`. In the
  helper, `_handle_child_request` gains, as its first statement after JSON parsing:
  ```python
  if request.get("op") == "page_fetch":
      return page_fetch.handle_child_request(request)
  ```
  The child answers with the same envelope as a bypass (`ok`, `error_type`, `error`,
  `cookies`, `user_agents`) plus `size`, `content_type`, `final_url`, `method`. The
  parent imports the returned cookies into its store, which keeps the plain-HTTP index
  fetch warm as a side effect.
- In-process mode: runs the same coroutine on `_CDP_WORKER.run(coro, timeout)`.
- Cancellation: in Docker mode the existing helper behaviour applies. A cancel kills the
  helper, browser included, so the next job starts cold. In-process: checked between
  every poll and chunk (4.3).

### 4.3 Algorithm (child side)

1. `driver = await session.acquire(origin_url, _create_cdp_browser, _close_cdp_driver)`.
2. **Ensure a cleared page on the origin.** If the main tab's URL is not on the origin
   (always true after parking), `page = await driver.get(origin_url)`, then
   `await _bypass(page, cancel_flag=...)`, then `_wait_for_origin_ready(page)`. That
   function polls every 1 s, for up to 60 s, until the title and body carry no
   Cloudflare, DDoS-Guard or Anubis marker (`challenge.py` lists), and
   `document.readyState == "complete"`. Then `_extract_cookies_from_cdp(driver, page, origin_url)`.
3. **Start the fetch without awaiting it.** One `page.evaluate` installs
   `window.__smFetch[id] = {received, total, done, error, status, contentType, finalUrl, blob}`
   and starts:
   ```js
   const ctl = new AbortController();
   const r = await fetch(url, {credentials: "same-origin", cache: "no-store",
                               redirect: "follow", signal: ctl.signal});
   // record status, content-type, content-length, r.url
   // reject: status != 200; content-length > max; content-type text/html
   // stream r.body.getReader() into chunks, abort when received > max
   // done: state.blob = new Blob(chunks)
   ```
4. **Poll** every 0.5 s with a small `evaluate` that returns the state without the blob.
   Stop on `done`, on `error`, on `cancel_flag` (then `ctl.abort()`), or at the deadline.
5. **Transfer to disk in chunks** (no single huge base64 string): for each 1 MiB slice,
   `evaluate(await_promise=True)` reads `blob.slice(a, b)` through
   `FileReader.readAsDataURL`. The data-URL prefix is stripped, the payload
   base64-decoded and appended to `dest.with_suffix(dest.suffix + ".part")`. Peak cost is
   about 1.4 MB per CDP message, well under SeleniumBase's 256 MiB websocket frame limit.
6. **Finish**: `delete window.__smFetch[id]` in a `finally`, check bytes written ==
   `received` (else `short_read`), rename `.part` to `dest`. On any failure, unlink
   `.part`.
7. **Retry policy** (one retry total): on `blocked` (401/403/503, an HTML body, or a
   challenge marker in a small body), or "Execution context was destroyed" (the page
   navigated mid-fetch, which the prototype also saw), re-run step 2 with a fresh
   navigation and fetch once more. No retry for `http_status` 404/410, `too_large` or
   `cancelled`.
8. `session.release(driver, keep=...)`: keep on success, and on clean file-level failures
   (404, too large). Drop on `blocked`, `browser`, `timeout`, `cancelled`.

### 4.4 Optional native download (`method="native_then_fetch"`)

Not yet proven. Specified so it can be tried behind a setting; not built in v1 unless you
want it (see open question 1).

1. Get the main tab's `browserContextId` with `Target.getTargetInfo` (the browser is
   incognito, so the default-context form of `Browser.setDownloadBehavior` does not
   apply).
2. `Browser.setDownloadBehavior(behavior="allowAndName", browserContextId=ctx,
   downloadPath=TMP_DIR/page_fetch/<id>/, eventsEnabled=True)`.
3. Trigger it from the cleared page with a same-origin `<a href=url download>` click.
4. Follow `Browser.downloadWillBegin` and `Browser.downloadProgress` for that guid. Call
   `Browser.cancelDownload` past `max_bytes`, on cancel, or at the deadline. On
   `completed`, move `<guid>` to `dest`.
5. Any failure falls back to the in-page fetch above, with one INFO log line.
6. Reset the download behaviour to `default` before parking.

Upside: streams to disk with native progress and no base64 overhead. Downside: more CDP
surface, and untested against Anubis's fetch-metadata checks (`Sec-Fetch-Dest: document`
rather than `empty`).

### 4.5 Limits

| Limit | Value | Where |
|-------|-------|-------|
| Max file size | `ELSCIONE_MAX_FILE_MB` default 64 (range 1-512) | handler passes `max_bytes` |
| Per-download timeout | `ELSCIONE_DOWNLOAD_TIMEOUT` default 300 s (60-900) | helper deadline is this + 30 s |
| Origin readiness wait | 60 s | constant |
| Chunk size | 1 MiB | constant |
| Poll interval | 0.5 s | constant |

### 4.6 Logging

- INFO `Page fetch %s via %s (reuse=%s)` at start and
  `Page fetch done: %d bytes in %.1fs (%s)` at end. URLs are logged as path only
  (no query, no cookies).
- WARNING with `reason` and status on every failure; DEBUG for each retry decision.
- Never log cookie values (same rule as `cookie_store`).

### 4.7 Progress

In-process mode reports `received/total` to an optional callback on every poll. In
Docker mode the helper protocol has one result per request, so the handler shows an
indeterminate "Downloading in browser" status. Typical files finish in seconds. Live
progress across the process boundary is a follow-up, not v1.

## 5. Piece D: `release_sources/elscione/`

### 5.1 Constants and mirrors

```python
DEFAULT_MIRRORS = ["https://server.elscione.com", "https://server.elsci.one"]
ALLOWED_FORMATS = {"epub", "pdf"}
```

Every URL this piece builds comes from the configured mirror list (section 13), never
from a release payload, so a payload can still never redirect a download to another
host. `origin_url(base)` is the page cleared before an in-page fetch;
`index_url(base)` is where a mirror publishes its catalogue.

### 5.2 Index (`index.py`)

**Format** (from the live index): a node is `{name, path, folders: [...], files: [{name, path, size}]}`.
The top level is one root node (`name: "Officially Translated Light Novels"`,
`path: "."`). A top-level list of roots is also accepted. File paths are relative to
their root. Download URL:

```python
build_download_url(entry, base)  # base + "/" + "/".join(quote(seg, safe="") for seg in [root_name, *path.split("/")])
```

`quote(..., safe="")` encodes `!'()*` too. That matches the proven URL
(`%21Yaoi/...`) and the prototype's encoder.

**Flatten** (iterative walk with an explicit stack, so a deeply nested tree cannot hit the
recursion limit; depth capped at 64):

- A node that is not a dict with `folders` and `files` lists: skip it, count `bad_node`.
- File entry: `name` and `path` must be `str`, and `size` an `int` that is not a `bool` and
  is `>= 0`. Otherwise count `bad_entry`.
- Format from the extension of the last path segment, lowercased. Only `epub`/`pdf`;
  everything else (zip, mobi, doc, fb2, `.bat`, `.dll`, ...) is counted `other_format` and
  can never become a release.
- `size == 0`: count `empty`. `size > max_bytes`: count `too_large` (not offered,
  since it could never download).
- Path segments (and the root name): reject if any segment is empty, `.`, `..`, contains
  `\`, or contains a control character (`\x00-\x1f`, `\x7f`). Count `bad_path`.
- Duplicate path: keep the first, count `duplicate`.
- **Skip the bad entry, never fail the refresh** for any of the above.
- Entry: `IndexEntry(path, root, name, folder, format, size_bytes, haystack)`.
  `folder` is the name of the node holding the file, or the file stem for files sitting
  directly in the root. `haystack` is precomputed for search.

Live distribution as a sanity reference: pdf 7034, epub 6397, so about 13.4k offered entries.

**Refresh** (`refresh_index()`, single-flight behind a lock, runs in a daemon thread):

1. Per mirror, in order (section 13): `requests.get(index_url(base), stream=True)` with
   cookies `cookie_store.get_cf_cookies_for_domain(mirror_host(base))`, the stored UA,
   header `Accept: application/json`, `network.get_proxies`, `network.get_ssl_verify`,
   timeout `(10, 60)`. Stream with a 32 MiB cap. Send `If-None-Match`/`If-Modified-Since`
   only to the mirror whose response produced them (`etag_mirror` in the metadata) and
   only while an index is actually served; a 304 only bumps `fetched_at` and records
   the mirror, and a 304 with nothing usable in memory is that mirror's failure.
2. Accept only status 200 with a body that parses as JSON. A 403/429/503, an HTML body, or
   a `challenge_marker(...)` hit counts as **blocked**: call
   `shelfmark.download.http.get_bypassed_page(origin_url(base))`. That dispatches to the
   internal or external bypasser and stores the cookies (with piece A they include Anubis).
   Then retry step 1 once. If plain HTTP is still blocked after the solve, fetch
   `index.json` inside the cleared page with piece C (`page_fetch.fetch_to_file`
   into TMP_DIR, read, delete). A 429 calls `network.note_rate_limited` and stops the
   whole refresh. Any other exhausted mirror falls over to the next one; when all are
   exhausted, `last_error` names each mirror's reason and one backoff is recorded.
3. Flatten. **Sanity guard**: if the new index has 0 entries, or fewer than 50% of the
   current index's entries, keep the old index and record the error. This protects against
   a partial or garbage index, like the catalogue-wipe guard in BookOrbit.
4. Persist atomically: raw JSON to `CONFIG_DIR/elscione/index.json` (`.tmp` + `replace`),
   metadata to `CONFIG_DIR/elscione/index_meta.json`: `fetched_at`, `mirror`, `etag`,
   `etag_mirror`, `last_modified`, `entries`, `skipped` counts, `last_attempt_at`,
   `last_error`. The raw
   file is stored so a flatten-rule change applies on the next load without a re-download.
   `CONFIG_DIR` rather than `TMP_DIR`, because the index must survive container restarts.
5. Swap the in-memory index in one assignment. Readers never see a half-built index.

**On failure**: keep the old index (memory and disk), record `last_error` and
`last_attempt_at`, and back off: 30 min, doubling to at most 6 h. No retry storms.

**Triggers** (no startup work and no timer, so a disabled source causes no egress):

- `search()` with no index loaded: load from disk if present. If nothing is on disk, start
  a background refresh and raise `SourceUnavailableError("Elscione index is being built,
  retry in about a minute")`.
- `search()` when `age > ELSCIONE_INDEX_REFRESH_HOURS`: start a background refresh and
  answer from the current index. Search never waits on the network, so it stays well
  inside the shared search deadline.
- Settings button "Refresh index now".

**Staleness reporting**:

- `index_status()` returns `{entries, fetched_at, age_hours, refreshing, last_error, last_attempt_at, skipped}`.
- Settings button "Show index status" returns that as its message.
- WARNING log once per hour while `age > 3 x refresh interval`:
  `Serving Elscione index from %s (%.0f h old); last refresh error: %s`.
- Every release carries `extra.index_fetched_at` (ISO 8601) so a client can show it.

### 5.3 Search (`source.py`)

- `content_type != "ebook"`: return `[]`.
- Queries, in order, first non-empty result wins: `plan.manual_query`; else
  `plan.title_variants[i].title` for each variant; else `book.title`. Each query
  carries an `allow_fallback` flag into `index.search`: a manual query is exactly
  what the user typed and is never shortened to a series name; metadata titles are.
  **No author terms**: the index has no author field, so adding the author would hide
  every match.
- Query parsing (`index.parse_query`), because metadata providers send titles like
  `7th Time Loop: The Villainess ... (Light Novel), Vol. 1` while the files are named
  `7th Time Loop - Volume 01 ...`:
  - The input is stripped, its whitespace runs collapsed to single spaces and the
    text capped at 512 characters before any regex runs: a padded or runaway title
    cannot make the marker scans quadratic. The trailing-number lookup itself scans
    back from the end in one pass, never a right-anchored regex search.
  - Drop bracketed segments (`(...)`, `[...]`, `{...}`) anywhere in the query, then
    collapse whitespace again before volume matching, so an annotation after the
    volume (`Spice and Wolf, Book 3 (Light Novel)`) cannot leave the marker half
    removed.
  - Volume markers, case-insensitive: `Volume 01`, `Vol. 3`, `Vol 3`, `Vol.1`,
    `v1` as a separate word, and ranges `Vol. 1-3` / `Volumes 1-3` / `Volume 01 - 03`.
    A range is a requested `(lo, hi)`; a scalar marker is `(v, v)`. Every number needs
    an end boundary (`v1beta` is not volume 1), and the number cannot backtrack to a
    shorter prefix to dodge it.
  - Marker precedence: a `Volume`/`Vol`/`v` marker anywhere is the volume, and any
    `Book`/`Part` markers stay in the title text (`Series Volume 1 Part 2` is volume
    1; `Part 2 Volume 4` is volume 4 with `Part 2` kept, so it still matches
    `Part 2 ... v04` file names). `Book`/`Part` are volume markers only when no
    `Volume`/`Vol`/`v` marker exists. A bare trailing `, 1` / ` 1` counts only when
    no explicit marker does; when an explicit marker and a bare trailing number
    overlap, the explicit marker wins. Among markers of one tier the last in the text
    wins, and the marker is removed from the text. A title that is only a marker
    (`42`, `Vol. 1`) keeps it: stripping the only term would make the query
    unsearchable.
  - Series variants, longest first, de-duplicated by their normalised form and dropped
    when empty: the cleaned full title; the part before the first `": "`; the part
    before the first `" - "`. The colon split needs the space, so `Re:Zero` stays
    intact. The cleaned full title is always tried; a shorter variant is tried only
    when it keeps at least 2 normalised terms, so `Magic: Missing Subtitle` never
    falls back to every `Magic ...` series.
- Entry volumes, precomputed at flatten time as `IndexEntry.volumes`, an inclusive
  `(low, high)` range or `None`: parsed case-insensitively from the file name's
  `Volume 01`, `Vol. 3`, `Vol 3`, `v03` and ranges like `Volume 01-17` (`A-B` gives
  `(A, B)`), with the same boundary, range and marker-precedence rules as the query
  (`Part 2 v04` is volume 4; `Part 2` alone is volume 2); `None` when the name
  declares no volume.
- Match: for each series variant in order, every distinct term must be a substring of
  the entry's `haystack` (`normalise(folder + " " + file name)`); the first variant
  with hits wins, so a query never widens past what the metadata title itself matched.
  Linear scan over about 13.4k precomputed strings takes milliseconds.
- Volume filter: when the query asked for volumes `(lo, hi)`, keep only entries whose
  `volumes` range overlaps it (`entry.low <= hi and lo <= entry.high`). Entries with
  no declared volume (a `Complete` omnibus, extras) are excluded rather than guessed.
  With no requested volume the behaviour is the plain all-terms rule.
- Normalise: `unicodedata.normalize("NFKD")`, drop combining marks, `casefold()`, replace
  every run of non-alphanumerics with one space, strip. Same as the prototype.
- INFO log per lookup: `Searching Elscione for: %s (volume %s, %d hits)` with the
  variant that produced the hits and the requested volume (`3.0`, or `1.0-3.0` for a
  range).
- Order: folder, then file name with a natural sort key (so `Volume 2` sorts before
  `Volume 10`). Cap at `ELSCIONE_MAX_RESULTS` (default 50, range 1-200).
- `plan.languages` is ignored: the index has no language data. `language` stays `None`;
  nothing is guessed.

### 5.4 Release mapping

```python
Release(
    source="elscione",
    source_id=entry.path,                 # relative path inside its root, as requested
    title=stem(entry.name),               # file name without extension (naming adds it)
    format=entry.format,                  # "epub" | "pdf"
    size=human_size(entry.size_bytes),
    size_bytes=entry.size_bytes,
    download_url=None,                    # built by the handler from the index entry
    info_url=None,
    protocol=ReleaseProtocol.HTTP,
    indexer="Elscione",
    content_type="ebook",
    extra={"book_title": entry.folder, "root": entry.root,
           "index_fetched_at": "...", "language": None},
)
```

- `search_results_are_releases()` returns True. `get_record()` returns a `BrowseRecord`
  for a path present in the index, else None.
- Columns: format and size badges, the libgen layout without the language column.
  `supported_filters=["format"]`.
- `source_id` contains `/` and spaces. The cancel and retry routes use
  `<path:book_id>`, so this works, but BookOrbit must percent-encode the id when it builds
  `/api/download/<id>/cancel`. (Alternative: namespace as `elscione:<path>` like libgen's
  `libgen:<md5>`. Not needed for collision safety, since a path cannot look like an md5 or
  a GUID, so kept as you specified.)
- `is_available()`: `ELSCIONE_ENABLED is True` and `USE_CF_BYPASS` and not
  `USING_EXTERNAL_BYPASSER`. With an external bypasser the index could still be fetched,
  but no download could ever succeed, so the source hides itself instead of offering
  dead releases.

### 5.5 Handler (`handler.py`)

```python
@register_handler("elscione")
class ElscioneHandler(DownloadHandler):
    def download(self, task, cancel_flag, progress_callback, status_callback) -> str | None
    def cancel(self, task_id) -> bool   # False; cancel_flag does the work
```

1. **Trust nothing from the queue payload.** Look up `task.task_id` in the current
   index. If it is missing, report error "No longer in the Elscione index; search again".
   This is the only way a path reaches a URL, so a crafted `source_id` (`../`, another
   host, a `.bat`) cannot be downloaded. Format and expected size come from the index
   entry, not from the task.
2. Staging name: `build_filename(task.title, task.author, task.year, entry.format)`, or the
   sanitised index file name when `FILE_ORGANIZATION == "none"`. Same rule as libgen.
   Path `TMP_DIR / name`.
3. `status_callback("resolving", "Opening Elscione in browser")`, then per mirror, in
   `index.download_order()`:
   `page_fetch.fetch_to_file(index.build_download_url(entry, base), part_path, origin_url=index.origin_url(base), max_bytes=..., timeout=..., cancel_flag=..., method=...)`.
   `status_callback("downloading", "Downloading in browser")` is sent before the first
   mirror and `("downloading", "Retrying on <host>")` before each later one
   (Docker mode has no live progress, see 4.7). Mirror fallover is specified in
   section 13.
4. `validate.check(path, entry, result)` (5.6). On failure, unlink the file and report the
   reason as the error.
5. Return the path. The orchestrator runs normal post-processing (naming template,
   output mode, library import). Nothing Elscione-specific happens after this point.
6. Error mapping to user-facing messages:
   `unsupported` "Elscione downloads need the internal bypasser";
   `blocked` "Elscione challenge could not be cleared";
   `http_status` "Elscione returned HTTP <n>";
   `too_large` "File exceeds the <n> MiB limit";
   `timeout` "Timed out after <n> s";
   `cancelled` returns None after `status_callback("cancelled", ...)`.

### 5.6 Validation (`validate.py`)

| Check | Rule | On failure |
|-------|------|------------|
| Bytes written | must equal the fetch's `received` and any `Content-Length` | fail |
| Index size | must equal `entry.size_bytes` | fail with "size differs from index (index may be stale)" and start a background index refresh |
| EPUB | starts with `PK\x03\x04`; `zipfile.ZipFile` opens; `testzip()` is None (CRC over the whole file catches truncation); `META-INF/container.xml` present | fail |
| EPUB mimetype | `mimetype` entry equals `application/epub+zip` | WARNING only (some converted files get this wrong) |
| PDF | `%PDF-` within the first 1024 bytes | fail |
| PDF trailer | `%%EOF` within the last 2048 bytes | WARNING only (size equality already catches truncation) |
| Content type | `text/html` rejected inside page_fetch, before writing | fail (`blocked`) |

### 5.7 Settings (`settings.py`)

Tab `elscione_config`, "Elscione", `icon="download"`, `order=47` (after Libgen):

| Key | Type | Default | Range |
|-----|------|---------|-------|
| `ELSCIONE_ENABLED` | Checkbox | False | |
| `ELSCIONE_MAX_RESULTS` | Number | 50 | 1-200 |
| `ELSCIONE_INDEX_REFRESH_HOURS` | Number | 24 | 6-168 |
| `ELSCIONE_MAX_FILE_MB` | Number | 64 | 1-512 |
| `ELSCIONE_DOWNLOAD_TIMEOUT` | Number (s) | 300 | 60-900 |
| `ELSCIONE_DOWNLOAD_METHOD` | Select `fetch` / `native_then_fetch` | `fetch` | only if 4.4 is built |
| `elscione_refresh_index` | ActionButton "Refresh index now" | | |
| `elscione_index_status` | ActionButton "Show index status" | | |

The enable checkbox description states that downloads need the internal bypasser, and
recommends turning on Browser Session Reuse so downloads skip the 26-60 s solve.

### 5.8 Logging (logger `shelfmark.release_sources.elscione`)

- INFO `Elscione index refreshed: %d entries (%s skipped) in %.1fs`; the skipped counts are
  shown per reason.
- WARNING `Elscione index refresh failed (%s); keeping index from %s`.
- INFO `Elscione index blocked (%s); solving challenge` at the bypass hand-off.
- DEBUG one line per skipped entry reason sample (first 5 per reason, then counts only).
- INFO `Searching Elscione for: %s (%d hits)`.
- INFO/WARNING per download as in 4.6, plus the validation outcome.

## 6. Settings summary

Ten new keys in two fork-only tabs (3.6 and 5.7). No existing key changes default or
meaning. `BYPASS_BROWSER_IDLE_TIMEOUT` keeps its upstream meaning. When reuse is on, the
effective helper idle time is the larger of it and `BYPASS_SESSION_IDLE_TIMEOUT`.

## 7. Failure handling summary

| Situation | Behaviour |
|-----------|-----------|
| No index yet | `SourceUnavailableError`, background build |
| Refresh blocked | Solve via bypasser once, retry once, then fall over to the next mirror; all mirrors out means keep old index and back off |
| Refresh returns garbage or a much smaller index | Keep old index, record error |
| Bad entries in index | Skipped and counted, refresh still succeeds |
| Download path not in index | Error, nothing fetched |
| Challenge during download | Re-clear the page once, retry once, then error; cached browser dropped |
| HTTP 404 on download | Fall over to the next mirror; only when every mirror says 404/410: error "HTTP 404", index refresh started, browser kept |
| Oversize | Rejected from Content-Length before the body, else at the cap |
| Validation fails | File deleted, error with reason |
| Cancel | Fetch aborted (in-process) or helper killed (Docker) |
| External bypasser configured | Source not available |
| Helper or browser crash | Upstream helper restart logic; next job starts cold |

## 8. Tests

All offline (no network, no Chrome); run with `uv run pytest tests/`.

- `tests/bypass/test_cookie_store_anubis.py`: section 2.
- `tests/bypass/test_browser_session.py`: port `tests/bypass/test_session_reuse.py` from
  `feat/monitor-authors`, adapted to the egress-only key, plus:
  - reuse across two hosts with the same egress; fresh browser on a DNS or proxy change;
  - recycle on max jobs and on max age (monkeypatched clock);
  - park closes extra tabs and navigates to `about:blank`; a park failure drops the driver;
  - liveness-probe failure creates a fresh browser;
  - DEBUG: recording stopped on park, started on reuse, never started when DEBUG is off;
    the two private ffmpeg helpers exist;
  - `helper_idle_timeout` returns `max(base, idle)` with reuse on and `base` with it off;
  - in-process idle timer closes only when `LOCKED` is free.
- `tests/bypass/test_page_fetch.py` with a fake tab whose `evaluate` simulates the JS state:
  - happy path writes the exact bytes in several 1 MiB chunks and renames `.part`;
  - Content-Length over the cap, streamed over the cap, 403 (retry then `blocked`),
    404 (no retry), HTML body, short read, cancel mid-transfer, timeout;
  - `.part` removed on every failure;
  - preconditions (external bypasser, cross-origin URL, dest outside `TMP_DIR`);
  - helper protocol: `op=page_fetch` routed to `page_fetch.handle_child_request`, and the
    result envelope round-trips cookies into the parent store.
- `tests/elscione/`:
  - `sample_index.py`: a small tree with every entry kind (epub, pdf, zip, bat, dll,
    size 0, `..`, `\`, empty segment, control character, duplicate, non-int size,
    bool size, root-level file, nested folders, list-of-roots form);
  - `test_index.py`: flatten counts per reason; URL encoding for the proven Amazing Grace
    path equals the live URL exactly; persistence round-trip; sanity guard keeps the old
    index; backoff; stale warning; 304 handling; blocked leads to one bypass and one retry;
  - `test_source.py`: query selection (manual, variants, title; never author), unicode
    normalisation (accents, full-width, punctuation), all-terms rule, natural ordering,
    cap, audiobook returns `[]`, `is_available` matrix, no-index raises
    `SourceUnavailableError` and starts exactly one refresh;
  - `test_handler.py`: unknown path rejected without calling `page_fetch`; error mapping;
    staging name rules; validation failures delete the file; cancel;
  - `test_validate.py`: valid EPUB and PDF, truncated zip (CRC failure), missing
    container.xml, wrong mimetype (warning only), PDF without header, size mismatches.
- Lint/type clean on new files: `ruff check`, `ruff format --check`, and pyright as CI
  runs them.

## 9. Live verification plan (on `shelfmark-mod`, before calling it done)

1. Enable the internal bypasser, Browser Session Reuse, and Elscione.
2. Search "Amazing Grace" through `/api/releases?provider=manual&source=elscione&title=Amazing%20Grace`.
   Expect a "building" error first, then results within about a minute; check
   `CONFIG_DIR/elscione/index_meta.json`.
3. Queue the `[enjugroup][Kindle].epub` release. Expect 156,484 bytes, validation passes,
   and the file lands in the library through normal post-processing.
4. Queue a second file within the idle window. The log must show "Reusing cleared
   browser" and no new solve.
5. With `DEBUG` on, confirm one recording per job and no recording running while parked.
6. Wait past the idle timeout: helper and Chrome gone (`ps`).
7. Run an AA search between two Elscione downloads: the same browser is reused (egress key).

## 10. BookOrbit follow-up (separate repo, not part of this change)

After this lands: add `elscione` to `SHELFMARK_SOURCES` (label "Elscione", content type
ebook only) and a row in BookOrbit's `docs/SHELFMARK.md`. BookOrbit must percent-encode
`source_id` (it contains `/` and spaces) when it calls `/api/download/<id>/cancel`.

## 11. Open questions for review

1. **Native CDP download (4.4)**: build it in v1 behind `ELSCIONE_DOWNLOAD_METHOD`
   (default `fetch`), or leave it out until in-page fetch shows a real limitation?
   Recommendation: leave it out of v1; the proven method covers files up to the 64 MiB cap.
2. **Session key**: egress-only (recommended, one browser for AA and Elscione) or the
   fork's per-host key?
3. **Index size mismatch**: hard fail plus refresh (specified), or warn and accept when
   the signature checks pass?
4. **Search matching**: term-as-substring (specified, same as the prototype; "1" matches
   "01" but "of" also matches "professor") or whole-token matching?
5. **`source_id`**: bare path (specified) or `elscione:<path>`?
6. **Reuse default**: keep `BYPASS_REUSE_BROWSER_SESSION` default off (specified), or on in
   this fork?

## 12. Changes from live testing (2026-10-17, local dev stack)

Found by end-to-end runs against server.elscione.com and fixed:

1. **Helper module duplication (hang).** The bypass helper runs `internal_bypasser` as
   `__main__`; a lazy `import shelfmark.bypass.internal_bypasser` from `browser_session`
   or `page_fetch` loaded a second copy with its own CDP loop, so awaiting the reused
   browser from it hung until the 5-minute stall cancel, and the import itself cost
   about 5 s per job. Fix: the `__main__` block registers itself under the real module
   name (one line in the upstream file).
2. **Reused-browser fast path.** On a reused browser the origin page is checked for
   readiness first (up to 3 s); the challenge solver runs only when that fails.
3. **No park for page_fetch.** `browser_session.release(..., park=False)` keeps the light
   origin page loaded, so the next download skips both the about:blank park and the
   reopen. Bypass jobs (AA) still park on about:blank.
4. **Poll interval** 0.5 s to 0.1 s; per-step DEBUG timings added to page_fetch.
5. **Release title** is the file name without its extension; Shelfmark's naming appends
   `format`, which produced `.epub.epub` library files.
6. **Index in-page fallback** (section 5.2 step 2): from a home IP, Cloudflare kept
   answering 403 to plain HTTP even right after a solve, while Chrome was let through.
7. **Settings tab visibility.** `show_when` is evaluated against the current tab only, so
   the reuse checkbox no longer references `USING_EXTERNAL_BYPASSER` (another tab).

Measured: cold solve 5.6 s (direct) to 26 s (WireGuard); warm download overhead about
1 s (open, cookies, copy); throughput is set by the site at about 500 KB/s per file,
matching a normal desktop browser (30 MB in about 65 s).

## 13. Mirrors

Verified live: `https://server.elsci.one` is a full mirror of
`https://server.elscione.com` - same Cloudflare plus Anubis protection, byte-identical
`index.json`, same file bytes per path. Each host needs its own challenge solve.

**Configuration.** `ELSCIONE_MIRRORS` (tag list; default both hosts) holds the mirror
bases, tried in order, first preferred. Entries arrive as a list from the tag input or as
a comma-separated string from a hand-edited config; both parse. Each entry is normalised
to `https://<host>`: non-https schemes, interior whitespace or control characters,
userinfo (even empty, `https://@host`), any port other than none/443, any path other
than ""/"/", query, fragment and IP literals are rejected, and the host is canonicalised
(lowercase, one trailing dot stripped, IDNA-encoded) so the Unicode and punycode
spellings of one host fold. Entries that make the parser itself raise (`https://[`,
fullwidth characters) are dropped like any other invalid entry, without aborting the
rest of the list. Invalid entries are dropped with one WARNING. When nothing valid
remains, the two defaults stand in, so the source never loses its hosts. Duplicates fold.

**Index refresh** walks `download_order()` and gives each mirror the full chain from
5.2 (plain HTTP, one solve plus retry, in-page fallback). The first mirror yielding an
installed index or a 304 wins. `index_meta.json` records `mirror` (who served) and
`etag_mirror` (whose validator we hold); conditional headers go only to that mirror and
only while an index is actually served, since another mirror's ETag names a different
document and a validator for a cache that flattens to nothing can only produce a wrong
answer. A 304 that arrives with nothing usable in memory is that mirror's failure, not
a success. When every mirror fails, `last_error` lists each mirror's reason and one
backoff is recorded for the whole refresh, not per mirror. The 50% sanity guard also
stays whole-refresh. Persisting moves the old `index.json` aside first, so a metadata
replace failure puts the previous catalogue back rather than leaving new bytes on disk
under a failed refresh.

**Downloads** walk the same `download_order()`. Each attempt streams into its own
uniquely named temp file in TMP_DIR (`.elscione-<hex>.<format>`) that the call owns;
only a file that passed validation is moved onto the staging name, and any failed
attempt's temp file (and its `.part`) is deleted, whatever the fetch layer left behind.
A failure falls over to the next mirror when the reason is mirror-specific (`blocked`,
`browser`, `timeout`, `http_status` with any status, `short_read`); `cancelled`,
`unsupported` and `too_large` would repeat identically on every mirror, so they fail at
once, as does a validation failure. The last error is reported through the existing
mapping. The 404/410 background refresh runs only when every mirror in the order was
actually attempted and each answered 404/410. One wall-clock budget covers the task:
each mirror gets the configured download timeout, and no further mirror is started once
two of those timeouts have elapsed since the handler began. Status texts are
"Downloading in browser" for the first mirror and "Retrying on <host>" for later ones.
Each attempt declares an activity grace of the per-mirror timeout plus 60 s to the
orchestrator's stall detector (released when the attempt ends), so a slow fetch or a
fallover is not cancelled mid-flight.

**Sticky favourite.** The mirror that served an index or a download is remembered in
memory and tried first next time; `download_order()` drops it if it is no longer
configured. Each mirror's clearance cookies stay keyed to its own host
(`mirror_host(base)`), so a solve on one never leaks to the other.

**Logging.** INFO `Elscione mirror %s failed (%s); trying %s` per fallover, in both the
index refresh and the download handler; INFO naming the mirror that served the index;
INFO which mirrors were tried when all fail.
