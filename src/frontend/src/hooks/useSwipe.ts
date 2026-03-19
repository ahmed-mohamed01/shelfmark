import { useRef, useCallback, type TouchEventHandler } from 'react';

interface UseSwipeOptions {
  onSwipeLeft?: () => void;
  onSwipeRight?: () => void;
  threshold?: number;
}

export function useSwipe({ onSwipeLeft, onSwipeRight, threshold = 50 }: UseSwipeOptions) {
  const startRef = useRef<{ x: number; y: number } | null>(null);

  const onTouchStart: TouchEventHandler = useCallback((e) => {
    const touch = e.touches[0];
    startRef.current = { x: touch.clientX, y: touch.clientY };
  }, []);

  const onTouchEnd: TouchEventHandler = useCallback((e) => {
    if (!startRef.current) return;
    const touch = e.changedTouches[0];
    const dx = touch.clientX - startRef.current.x;
    const dy = touch.clientY - startRef.current.y;
    startRef.current = null;
    // Only fire if horizontal movement exceeds threshold and dominates vertical
    if (Math.abs(dx) < threshold || Math.abs(dx) < Math.abs(dy)) return;
    if (dx < 0) onSwipeLeft?.();
    else onSwipeRight?.();
  }, [onSwipeLeft, onSwipeRight, threshold]);

  return { onTouchStart, onTouchEnd };
}
