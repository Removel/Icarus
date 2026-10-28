import { useEffect, useLayoutEffect, useRef } from 'react';
import type { AnimationItem } from 'lottie-web';
import lottie from 'lottie-web/build/player/lottie_light';
import thinkingData from './assets/animations/thinking.json';
import searchData from './assets/animations/search.json';

export function AnimatedToggleIcon({
  kind,
  enabled,
}: {
  kind: 'thinking' | 'search';
  enabled: boolean;
}) {
  const container = useRef<HTMLSpanElement>(null);
  const animation = useRef<AnimationItem | undefined>(undefined);
  const initialEnabled = useRef(enabled);
  const data = kind === 'thinking' ? thinkingData : searchData;

  useLayoutEffect(() => {
    if (!container.current) return;
    const item = lottie.loadAnimation({
      container: container.current,
      renderer: 'svg',
      loop: false,
      autoplay: false,
      animationData: structuredClone(data),
    });
    animation.current = item;
    item.goToAndStop(initialEnabled.current ? data.op - 1 : 0, true);
    return () => {
      item.destroy();
      animation.current = undefined;
    };
  }, [data]);

  useEffect(() => {
    const item = animation.current;
    if (!item) return;
    const target = enabled ? data.op - 1 : 0;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches)
      item.goToAndStop(target, true);
    else if (item.currentFrame !== target)
      item.playSegments([item.firstFrame + item.currentFrame, target], true);
  }, [enabled, data]);

  return <span ref={container} className="toggle-icon" aria-hidden="true" />;
}
