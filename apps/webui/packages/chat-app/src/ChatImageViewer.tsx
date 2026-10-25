import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Minus, Plus, RotateCcw, X } from 'lucide-react';
import ChatDialog from './ChatDialog';

export type ViewerImage = { src: string; alt: string };

export default function ChatImageViewer({
  images,
  initial,
  onClose,
}: {
  images: ViewerImage[];
  initial: number;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(initial);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [failed, setFailed] = useState(false);
  const stage = useRef<HTMLDivElement>(null);
  const picture = useRef<HTMLImageElement>(null);
  const drag = useRef<{
    x: number;
    y: number;
    left: number;
    top: number;
    moved: boolean;
    image: boolean;
  } | null>(null);
  const dragged = useRef(false);
  const maximum = () => {
    const node = picture.current;
    return node?.naturalWidth && node.clientWidth
      ? Math.max(4, (2 * node.naturalWidth) / node.clientWidth)
      : 4;
  };
  const changeZoom = (value: number) => {
    setZoom(Math.min(maximum(), Math.max(1, value)));
    setOffset({ x: 0, y: 0 });
  };
  const move = (next: number) => {
    if (next < 0 || next >= images.length) return;
    setIndex(next);
    setZoom(1);
    setOffset({ x: 0, y: 0 });
    setFailed(false);
  };
  useEffect(() => {
    const node = stage.current!;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      setZoom((value) =>
        Math.min(maximum(), Math.max(1, value * (event.deltaY < 0 ? 1.15 : 1 / 1.15))),
      );
      setOffset({ x: 0, y: 0 });
    };
    const resize = new ResizeObserver(() => {
      setZoom(1);
      setOffset({ x: 0, y: 0 });
    });
    node.addEventListener('wheel', wheel, { passive: false });
    resize.observe(node);
    return () => {
      node.removeEventListener('wheel', wheel);
      resize.disconnect();
    };
  }, []);
  return (
    <ChatDialog open label="图片查看器" onClose={onClose} className="chat-image-viewer-host">
      <div
        className="chat-image-viewer"
        onKeyDown={(event) => {
          if (event.key === 'ArrowLeft') {
            event.preventDefault();
            move(index - 1);
          }
          if (event.key === 'ArrowRight') {
            event.preventDefault();
            move(index + 1);
          }
          if (event.key === '+' || event.key === '=') changeZoom(zoom * 1.25);
          if (event.key === '-') changeZoom(zoom / 1.25);
          if (event.key === '0') changeZoom(1);
        }}
      >
        <div className="chat-image-viewer-toolbar">
          <span aria-live="polite">
            {index + 1} / {images.length}
          </span>
          <button
            aria-label="缩小图片"
            disabled={zoom <= 1 || failed}
            onClick={() => changeZoom(zoom / 1.25)}
          >
            <Minus size={20} />
          </button>
          <button aria-label="还原图片" onClick={() => changeZoom(1)}>
            <RotateCcw size={18} />
            <span>{Math.round(zoom * 100)}%</span>
          </button>
          <button
            aria-label="放大图片"
            disabled={zoom >= maximum() || failed}
            onClick={() => changeZoom(zoom * 1.25)}
          >
            <Plus size={20} />
          </button>
          <button aria-label="关闭图片查看器" onClick={onClose}>
            <X size={22} />
          </button>
        </div>
        <div
          className="chat-image-viewer-stage"
          ref={stage}
          data-zoomed={zoom > 1 || undefined}
          onClick={(event) => {
            if (event.target === event.currentTarget && !dragged.current) onClose();
            dragged.current = false;
          }}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            drag.current = {
              x: event.clientX,
              y: event.clientY,
              left: offset.x,
              top: offset.y,
              moved: false,
              image: event.target === picture.current,
            };
            if (zoom > 1) event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            const start = drag.current;
            const img = picture.current;
            const node = stage.current;
            if (!start || !img || !node) return;
            const dx = event.clientX - start.x,
              dy = event.clientY - start.y;
            if (Math.abs(dx) + Math.abs(dy) > 4) start.moved = true;
            if (zoom <= 1) return;
            const limitX = Math.max(0, (img.clientWidth * zoom - node.clientWidth) / 2);
            const limitY = Math.max(0, (img.clientHeight * zoom - node.clientHeight) / 2);
            setOffset({
              x: Math.max(-limitX, Math.min(limitX, start.left + dx)),
              y: Math.max(-limitY, Math.min(limitY, start.top + dy)),
            });
          }}
          onPointerUp={(event) => {
            const start = drag.current;
            drag.current = null;
            dragged.current = Boolean(start?.moved || start?.image);
            if (start && !start.moved && start.image) changeZoom(zoom > 1 ? 1 : 2);
            if (event.currentTarget.hasPointerCapture(event.pointerId))
              event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onPointerCancel={() => {
            drag.current = null;
          }}
        >
          {failed ? (
            <p role="status">图片无法加载，请关闭后重试。</p>
          ) : (
            <img
              key={images[index].src}
              ref={picture}
              src={images[index].src}
              alt={images[index].alt}
              draggable={false}
              onError={() => setFailed(true)}
              style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})` }}
            />
          )}
        </div>
        {images.length > 1 && (
          <>
            <button
              className="chat-image-previous"
              aria-label="上一张图片"
              disabled={index === 0}
              onClick={() => move(index - 1)}
            >
              <ChevronLeft />
            </button>
            <button
              className="chat-image-next"
              aria-label="下一张图片"
              disabled={index === images.length - 1}
              onClick={() => move(index + 1)}
            >
              <ChevronRight />
            </button>
            <div className="chat-image-viewer-thumbs" aria-label="图片缩略图">
              {images.map((image, i) => (
                <button
                  key={image.src + i}
                  aria-label={`切换到图片 ${i + 1}`}
                  aria-pressed={index === i}
                  onClick={() => move(i)}
                >
                  <img src={image.src} alt="" />
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </ChatDialog>
  );
}
