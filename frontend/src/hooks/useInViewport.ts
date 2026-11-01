import { useEffect, useRef, useState } from 'react';

/**
 * true, пока элемент (почти) в viewport. rootMargin держит превью
 * смонтированным чуть раньше появления на экране.
 * setState только при реальной смене — иначе Virtuoso remasure ↔ IO дают #185.
 */
export function useInViewport<T extends Element>(
  rootMargin = '240px 0px',
  initiallyVisible = true,
): { ref: React.RefObject<T | null>; inView: boolean } {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(initiallyVisible);
  const inViewRef = useRef(inView);
  inViewRef.current = inView;

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    // Стриминг: считаем элемент видимым, IO не подключаем — иначе Virtuoso remeasure ↔ IO → #185.
    if (initiallyVisible) return;

    const obs = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (!entry) return;
        const next = entry.isIntersecting;
        if (next === inViewRef.current) return;
        inViewRef.current = next;
        setInView(next);
      },
      { root: null, rootMargin, threshold: 0 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [rootMargin, initiallyVisible]);

  return { ref, inView };
}
