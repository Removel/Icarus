import { useEffect, useRef, useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export default function ChatContent({
  text,
  live,
  plain = false,
  onGrowth,
}: {
  text: string;
  live: boolean;
  plain?: boolean;
  onGrowth: () => void;
}) {
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  );
  const animate = live && !reducedMotion;
  const [visible, setVisible] = useState(() => (animate ? '' : text));
  const displayed = useRef(visible);
  const target = useRef(text);
  target.current = text;
  const frame = useRef<number | undefined>(undefined);
  const lastFrame = useRef(0);
  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const change = () => setReducedMotion(preference.matches);
    preference.addEventListener('change', change);
    return () => preference.removeEventListener('change', change);
  }, []);
  useEffect(() => {
    if (!animate || !text.startsWith(displayed.current)) {
      if (frame.current !== undefined) cancelAnimationFrame(frame.current);
      frame.current = undefined;
      displayed.current = text;
      setVisible(text);
      return;
    }
    if (frame.current !== undefined || displayed.current === text) return;
    lastFrame.current = performance.now();
    function advance(now: number) {
      const current = target.current;
      const elapsed = Math.min(64, Math.max(1, now - lastFrame.current));
      lastFrame.current = now;
      const remaining = current.length - displayed.current.length;
      // Catch up faster for a burst, keeping only a short presentation delay.
      const count = Math.max(1, Math.ceil(elapsed * 0.15), Math.ceil((remaining * elapsed) / 80));
      let end = Math.min(current.length, displayed.current.length + count);
      if (end < current.length && /[\uD800-\uDBFF]/.test(current[end - 1])) end += 1;
      displayed.current = current.slice(0, end);
      setVisible(displayed.current);
      frame.current = end < current.length ? requestAnimationFrame(advance) : undefined;
    }
    frame.current = requestAnimationFrame(advance);
  }, [text, animate]);
  useEffect(
    () => () => {
      if (frame.current !== undefined) cancelAnimationFrame(frame.current);
      frame.current = undefined;
    },
    [],
  );
  useEffect(onGrowth, [visible, onGrowth]);
  // History, corrected final text and reduced-motion mode are never replayed.
  const content = animate ? visible : text;
  return plain ? (
    <pre data-streaming={content !== text || undefined}>{content}</pre>
  ) : (
    <div className="chat-markdown" data-streaming={content !== text || undefined}>
      <Markdown remarkPlugins={[remarkGfm]} skipHtml>
        {content}
      </Markdown>
    </div>
  );
}
