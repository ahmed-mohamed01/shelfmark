import { useCallback, useMemo, useReducer, type Reducer } from 'react';

import type { ContentType, Release, RequestPolicyMode } from '../types';

/**
 * Decoupled ebook/audiobook release selection for the book modal.
 *
 * Both steps are optional and freely navigable; rows toggle-select and a
 * single "Queue N downloads" action queues whatever is picked. The reducer is
 * pure and exported so the selection rules are unit-testable without React.
 */

export const SELECTION_STEPS: ContentType[] = ['ebook', 'audiobook'];

export type PerStep<T> = Record<ContentType, T>;

export interface ReleaseSelectionState {
  activeStep: ContentType;
  /** Default policy mode per step (resolved from the request policy). */
  modes: PerStep<RequestPolicyMode>;
  selected: PerStep<Release | null>;
  /** Effective root per step (the default once loaded, or the user's pick). */
  roots: PerStep<string | null>;
  /** "Organize into folders": force organize mode via the global organize template. */
  organize: boolean;
  /** Resolved full path per step, as rendered by the SAVE TO bar (for the toast). */
  previews: PerStep<string | null>;
}

export type ReleaseSelectionAction =
  | { type: 'open'; initialStep: ContentType; modes: PerStep<RequestPolicyMode> }
  | { type: 'setStep'; step: ContentType }
  | { type: 'toggleRelease'; step: ContentType; release: Release }
  | { type: 'setRoot'; step: ContentType; root: string | null }
  | { type: 'setOrganize'; enabled: boolean }
  | { type: 'setPreview'; step: ContentType; preview: string | null }
  | { type: 'reset' };

/** A step is selectable when releases can be picked for it (download or release-level request). */
export const isStepAvailable = (mode: RequestPolicyMode | null | undefined): boolean =>
  mode === 'download' || mode === 'request_release';

export const firstAvailableStep = (
  modes: PerStep<RequestPolicyMode>,
  preferred: ContentType,
): ContentType => {
  if (isStepAvailable(modes[preferred])) return preferred;
  return SELECTION_STEPS.find((step) => isStepAvailable(modes[step])) ?? preferred;
};

const emptyPerStep = <T>(value: T): PerStep<T> => ({ ebook: value, audiobook: value });

export const releaseSelectionReducer: Reducer<
  ReleaseSelectionState | null,
  ReleaseSelectionAction
> = (state, action) => {
  switch (action.type) {
    case 'open':
      return {
        activeStep: firstAvailableStep(action.modes, action.initialStep),
        modes: action.modes,
        selected: emptyPerStep<Release | null>(null),
        roots: emptyPerStep<string | null>(null),
        organize: true,
        previews: emptyPerStep<string | null>(null),
      };
    case 'reset':
      return null;
    case 'setStep': {
      if (!state || state.activeStep === action.step) return state;
      if (!isStepAvailable(state.modes[action.step])) return state;
      return { ...state, activeStep: action.step };
    }
    case 'toggleRelease': {
      if (!state || !isStepAvailable(state.modes[action.step])) return state;
      const current = state.selected[action.step];
      const next =
        current &&
        current.source === action.release.source &&
        current.source_id === action.release.source_id
          ? null
          : action.release;
      return { ...state, selected: { ...state.selected, [action.step]: next } };
    }
    case 'setRoot': {
      if (!state || state.roots[action.step] === action.root) return state;
      return { ...state, roots: { ...state.roots, [action.step]: action.root } };
    }
    case 'setOrganize': {
      if (!state || state.organize === action.enabled) return state;
      return { ...state, organize: action.enabled };
    }
    case 'setPreview': {
      if (!state || state.previews[action.step] === action.preview) return state;
      return { ...state, previews: { ...state.previews, [action.step]: action.preview } };
    }
    default:
      return state;
  }
};

export const availableSteps = (modes: PerStep<RequestPolicyMode>): ContentType[] =>
  SELECTION_STEPS.filter((step) => isStepAvailable(modes[step]));

export const countSelected = (selected: PerStep<Release | null>): number =>
  SELECTION_STEPS.filter((step) => selected[step] != null).length;

/** Header label, e.g. `STEP 1 OF 2 — SELECT BOOK (OPTIONAL)`. */
export const buildStepLabel = (
  activeStep: ContentType,
  modes: PerStep<RequestPolicyMode>,
): string => {
  const steps = availableSteps(modes);
  const what = activeStep === 'audiobook' ? 'AUDIOBOOK' : 'BOOK';
  if (steps.length <= 1) return `SELECT ${what}`;
  const index = Math.max(0, steps.indexOf(activeStep)) + 1;
  return `STEP ${index} OF ${steps.length} — SELECT ${what} (OPTIONAL)`;
};

/**
 * Queue button label from the per-release action modes of the picked releases:
 * all downloads → "Queue N downloads", all requests → "Request N releases",
 * mixed → "Download & Request".
 */
export const buildQueueLabel = (modes: RequestPolicyMode[]): string => {
  const n = modes.length;
  if (n === 0) return 'Queue';
  const requests = modes.filter((m) => m === 'request_release' || m === 'request_book').length;
  if (requests === 0) return `Queue ${n} download${n > 1 ? 's' : ''}`;
  if (requests === n) return `Request ${n} release${n > 1 ? 's' : ''}`;
  return 'Download & Request';
};

/** Selection summary, e.g. `ebook ✓ · audiobook ✓`. */
export const buildSelectionSummary = (selected: PerStep<Release | null>): string => {
  const parts = SELECTION_STEPS.filter((step) => selected[step]).map((step) => `${step} ✓`);
  return parts.length > 0 ? parts.join(' · ') : 'nothing selected yet — pick either, or both';
};

export interface QueuedSelectionItem {
  contentType: ContentType;
  release: Release;
  /** The release's resolved action mode (download / request_release). */
  mode: RequestPolicyMode;
  /** Effective root the SAVE TO bar showed for this step (null when the bar is hidden). */
  root: string | null;
  /** Resolved full path shown in the PREVIEW line, for the toast. */
  preview: string | null;
}

export interface QueueSelectionsRequest {
  items: QueuedSelectionItem[];
  organize: boolean;
}

/**
 * Build the queue items from the current selection. Each item's action mode is
 * resolved against its OWN content type (request policy is per content type),
 * and the SAVE TO root/preview are attached only for standalone downloads.
 * Pure and exported so the per-step routing is unit-testable.
 */
export const buildQueueItems = (
  selection: Pick<ReleaseSelectionConfig, 'selected' | 'roots' | 'previews' | 'showSaveTo'>,
  modeForRelease: (release: Release, contentType: ContentType) => RequestPolicyMode,
): QueuedSelectionItem[] =>
  SELECTION_STEPS.flatMap((step) => {
    const release = selection.selected[step];
    if (!release) return [];
    return [
      {
        contentType: step,
        release,
        mode: modeForRelease(release, step),
        root: selection.showSaveTo ? selection.roots[step] : null,
        preview: selection.showSaveTo ? selection.previews[step] : null,
      },
    ];
  });

/** What the modal receives: state snapshot + bound actions. Null when not in selection mode. */
export interface ReleaseSelectionConfig {
  activeStep: ContentType;
  modes: PerStep<RequestPolicyMode>;
  stepAvailability: PerStep<boolean>;
  selected: PerStep<Release | null>;
  roots: PerStep<string | null>;
  organize: boolean;
  previews: PerStep<string | null>;
  stepLabel: string;
  /** Show the SAVE TO bar (standalone downloads; monitored ones resolve their own folder). */
  showSaveTo: boolean;
  onStepChange: (step: ContentType) => void;
  onToggleRelease: (step: ContentType, release: Release) => void;
  onRootChange: (step: ContentType, root: string | null) => void;
  onOrganizeChange: (enabled: boolean) => void;
  onPreviewChange: (step: ContentType, preview: string | null) => void;
  onQueue: (request: QueueSelectionsRequest) => void | Promise<void>;
}

interface UseReleaseSelectionParams {
  onQueue: (request: QueueSelectionsRequest) => void | Promise<void>;
  showSaveTo: boolean;
}

export const useReleaseSelection = ({ onQueue, showSaveTo }: UseReleaseSelectionParams) => {
  const [state, dispatch] = useReducer(releaseSelectionReducer, null);

  const open = useCallback(
    (params: { initialStep: ContentType; modes: PerStep<RequestPolicyMode> }) =>
      dispatch({ type: 'open', initialStep: params.initialStep, modes: params.modes }),
    [],
  );
  const reset = useCallback(() => dispatch({ type: 'reset' }), []);

  const onStepChange = useCallback((step: ContentType) => dispatch({ type: 'setStep', step }), []);
  const onToggleRelease = useCallback(
    (step: ContentType, release: Release) => dispatch({ type: 'toggleRelease', step, release }),
    [],
  );
  const onRootChange = useCallback(
    (step: ContentType, root: string | null) => dispatch({ type: 'setRoot', step, root }),
    [],
  );
  const onOrganizeChange = useCallback(
    (enabled: boolean) => dispatch({ type: 'setOrganize', enabled }),
    [],
  );
  const onPreviewChange = useCallback(
    (step: ContentType, preview: string | null) => dispatch({ type: 'setPreview', step, preview }),
    [],
  );

  const config = useMemo<ReleaseSelectionConfig | null>(() => {
    if (!state) return null;
    return {
      activeStep: state.activeStep,
      modes: state.modes,
      stepAvailability: {
        ebook: isStepAvailable(state.modes.ebook),
        audiobook: isStepAvailable(state.modes.audiobook),
      },
      selected: state.selected,
      roots: state.roots,
      organize: state.organize,
      previews: state.previews,
      stepLabel: buildStepLabel(state.activeStep, state.modes),
      showSaveTo,
      onStepChange,
      onToggleRelease,
      onRootChange,
      onOrganizeChange,
      onPreviewChange,
      onQueue,
    };
  }, [
    state,
    showSaveTo,
    onStepChange,
    onToggleRelease,
    onRootChange,
    onOrganizeChange,
    onPreviewChange,
    onQueue,
  ]);

  return useMemo(
    () => ({
      state,
      activeStep: state?.activeStep ?? null,
      open,
      reset,
      config,
    }),
    [state, open, reset, config],
  );
};
