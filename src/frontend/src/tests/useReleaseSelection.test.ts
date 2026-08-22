import { describe, expect, it } from 'vitest';

import {
  buildQueueItems,
  buildQueueLabel,
  buildSelectionSummary,
  buildStepLabel,
  countSelected,
  firstAvailableStep,
  isStepAvailable,
  releaseSelectionReducer,
  type PerStep,
  type ReleaseSelectionConfig,
  type ReleaseSelectionState,
} from '../hooks/useReleaseSelection';
import type { ContentType, Release, RequestPolicyMode } from '../types';

const release = (id: string, source = 'direct_download'): Release =>
  ({ source, source_id: id, title: `Release ${id}` }) as Release;

const open = (
  modes: PerStep<RequestPolicyMode> = { ebook: 'download', audiobook: 'download' },
): ReleaseSelectionState =>
  releaseSelectionReducer(null, { type: 'open', initialStep: 'ebook', modes })!;

describe('useReleaseSelection reducer', () => {
  it('opens on the requested step with nothing selected and the series folder on', () => {
    const state = open();
    expect(state.activeStep).toBe('ebook');
    expect(state.selected).toEqual({ ebook: null, audiobook: null });
    expect(state.organize).toBe(true);
  });

  it('falls back to the first available step when the requested one is unavailable', () => {
    const state = releaseSelectionReducer(null, {
      type: 'open',
      initialStep: 'ebook',
      modes: { ebook: 'request_book', audiobook: 'download' },
    })!;
    expect(state.activeStep).toBe('audiobook');
    expect(firstAvailableStep({ ebook: 'blocked', audiobook: 'request_release' }, 'ebook')).toBe(
      'audiobook',
    );
  });

  it('toggles a release on and off for a step', () => {
    let state = open();
    state = releaseSelectionReducer(state, {
      type: 'toggleRelease',
      step: 'ebook',
      release: release('a'),
    })!;
    expect(state.selected.ebook?.source_id).toBe('a');
    state = releaseSelectionReducer(state, {
      type: 'toggleRelease',
      step: 'ebook',
      release: release('a'),
    })!;
    expect(state.selected.ebook).toBeNull();
  });

  it('replaces the selection when a different release is picked', () => {
    let state = open();
    state = releaseSelectionReducer(state, {
      type: 'toggleRelease',
      step: 'ebook',
      release: release('a'),
    })!;
    state = releaseSelectionReducer(state, {
      type: 'toggleRelease',
      step: 'ebook',
      release: release('b'),
    })!;
    expect(state.selected.ebook?.source_id).toBe('b');
  });

  it('distinguishes releases with the same id from different sources', () => {
    let state = open();
    state = releaseSelectionReducer(state, {
      type: 'toggleRelease',
      step: 'ebook',
      release: release('a', 'x'),
    })!;
    state = releaseSelectionReducer(state, {
      type: 'toggleRelease',
      step: 'ebook',
      release: release('a', 'y'),
    })!;
    expect(state.selected.ebook?.source).toBe('y');
  });

  it('keeps the other step selection when switching steps', () => {
    let state = open();
    state = releaseSelectionReducer(state, {
      type: 'toggleRelease',
      step: 'ebook',
      release: release('a'),
    })!;
    state = releaseSelectionReducer(state, { type: 'setStep', step: 'audiobook' })!;
    state = releaseSelectionReducer(state, {
      type: 'toggleRelease',
      step: 'audiobook',
      release: release('b'),
    })!;
    state = releaseSelectionReducer(state, { type: 'setStep', step: 'ebook' })!;
    expect(state.selected).toMatchObject({
      ebook: { source_id: 'a' },
      audiobook: { source_id: 'b' },
    });
    expect(countSelected(state.selected)).toBe(2);
  });

  it('refuses to switch to or select on an unavailable step', () => {
    let state = open({ ebook: 'download', audiobook: 'request_book' });
    const before = state;
    state = releaseSelectionReducer(state, { type: 'setStep', step: 'audiobook' })!;
    expect(state).toBe(before);
    state = releaseSelectionReducer(state, {
      type: 'toggleRelease',
      step: 'audiobook',
      release: release('b'),
    })!;
    expect(state.selected.audiobook).toBeNull();
  });

  it('returns the same state for no-op root/preview/series updates (no render loops)', () => {
    let state = open();
    state = releaseSelectionReducer(state, { type: 'setRoot', step: 'ebook', root: '/books' })!;
    const after = state;
    expect(releaseSelectionReducer(state, { type: 'setRoot', step: 'ebook', root: '/books' })).toBe(
      after,
    );
    expect(releaseSelectionReducer(state, { type: 'setOrganize', enabled: true })).toBe(after);
    expect(
      releaseSelectionReducer(state, { type: 'setPreview', step: 'ebook', preview: null }),
    ).toBe(after);
  });

  it('resets to null and ignores actions while closed', () => {
    const state = open();
    expect(releaseSelectionReducer(state, { type: 'reset' })).toBeNull();
    expect(releaseSelectionReducer(null, { type: 'setStep', step: 'audiobook' })).toBeNull();
  });
});

describe('useReleaseSelection labels', () => {
  it('marks only download and release-level request steps as available', () => {
    expect(isStepAvailable('download')).toBe(true);
    expect(isStepAvailable('request_release')).toBe(true);
    expect(isStepAvailable('request_book')).toBe(false);
    expect(isStepAvailable('blocked')).toBe(false);
    expect(isStepAvailable(undefined)).toBe(false);
  });

  it('builds the step label from the available steps', () => {
    expect(buildStepLabel('ebook', { ebook: 'download', audiobook: 'download' })).toBe(
      'STEP 1 OF 2 — SELECT BOOK (OPTIONAL)',
    );
    expect(buildStepLabel('audiobook', { ebook: 'download', audiobook: 'download' })).toBe(
      'STEP 2 OF 2 — SELECT AUDIOBOOK (OPTIONAL)',
    );
    expect(buildStepLabel('audiobook', { ebook: 'request_book', audiobook: 'download' })).toBe(
      'SELECT AUDIOBOOK',
    );
  });

  it('builds the queue label from the picked releases’ modes', () => {
    expect(buildQueueLabel([])).toBe('Queue');
    expect(buildQueueLabel(['download'])).toBe('Queue 1 download');
    expect(buildQueueLabel(['download', 'download'])).toBe('Queue 2 downloads');
    expect(buildQueueLabel(['request_release'])).toBe('Request 1 release');
    expect(buildQueueLabel(['download', 'request_release'])).toBe('Download & Request');
  });

  it('summarises the selection', () => {
    expect(buildSelectionSummary({ ebook: null, audiobook: null })).toBe(
      'nothing selected yet — pick either, or both',
    );
    expect(buildSelectionSummary({ ebook: release('a'), audiobook: release('b') })).toBe(
      'ebook ✓ · audiobook ✓',
    );
    expect(buildSelectionSummary({ ebook: null, audiobook: release('b') })).toBe('audiobook ✓');
  });
});

describe('buildQueueItems', () => {
  const selectionOf = (
    ebook: Release | null,
    audiobook: Release | null,
    showSaveTo = true,
  ): Pick<ReleaseSelectionConfig, 'selected' | 'roots' | 'previews' | 'showSaveTo'> => ({
    selected: { ebook, audiobook },
    roots: { ebook: '/books/ebooks', audiobook: '/books/audiobooks' },
    previews: { ebook: '/books/ebooks/A/x.epub', audiobook: '/books/audiobooks/A/y.m4b' },
    showSaveTo,
  });

  it('resolves each item mode against its OWN content type, not the active step', () => {
    const modeForRelease = (_r: Release, ct: ContentType): RequestPolicyMode =>
      ct === 'audiobook' ? 'request_release' : 'download';
    const items = buildQueueItems(selectionOf(release('e'), release('a')), modeForRelease);
    const byType = Object.fromEntries(items.map((i) => [i.contentType, i.mode]));
    expect(byType).toEqual({ ebook: 'download', audiobook: 'request_release' });
  });

  it('only includes selected steps and attaches root/preview for standalone', () => {
    const items = buildQueueItems(selectionOf(null, release('a')), () => 'download');
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      contentType: 'audiobook',
      root: '/books/audiobooks',
      preview: '/books/audiobooks/A/y.m4b',
    });
  });

  it('omits root/preview when the SAVE TO bar is hidden (monitored downloads)', () => {
    const items = buildQueueItems(selectionOf(release('e'), null, false), () => 'download');
    expect(items[0].root).toBeNull();
    expect(items[0].preview).toBeNull();
  });
});
