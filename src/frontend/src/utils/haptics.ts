/** Light haptic pulse for interaction feedback. No-op on unsupported browsers. */
export function hapticTap() {
  try { navigator?.vibrate?.(1); } catch { /* unsupported */ }
}
