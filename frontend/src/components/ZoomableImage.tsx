import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Maximize2, X, ZoomIn, ZoomOut } from "lucide-react";

export interface ImageMarker {
  key: string | number;
  /** Centro y radio en pixeles naturales de la imagen. */
  x: number;
  y: number;
  r: number;
  color: string;
  label?: string;
  dim?: boolean;
}

/** Rectangulo en pixeles de la imagen original. */
export interface ImageRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ZoomableImageHandle {
  /** Centra y acerca la vista sobre un marcador y fija su tooltip. */
  focusMarker: (key: string | number) => void;
}

interface ZoomableImageProps {
  src: string;
  alt: string;
  /** Clases del contenedor; debe definir su alto (ej. "h-[60vh]" o "absolute inset-0"). */
  className?: string;
  markers?: ImageMarker[];
  renderTooltip?: (key: string | number, pinned: boolean) => ReactNode;
  /** Contenido SVG extra en coordenadas naturales (contornos, etiquetas). */
  overlay?: ReactNode;
  /** Clic sin arrastre sobre la imagen, en coordenadas naturales. */
  onImageClick?: (x: number, y: number) => void;
  crosshair?: boolean;
  onLoad?: (size: { width: number; height: number }) => void;
  /** Recorte mostrado: lo de afuera se atenua. */
  selection?: ImageRect | null;
  /** Con `selectMode` arrastrar dibuja un nuevo rectangulo en vez de mover la imagen. */
  selectMode?: boolean;
  onSelection?: (rect: ImageRect) => void;
}

interface View {
  s: number;
  tx: number;
  ty: number;
}

const MAX_ZOOM = 20;
const TOOLTIP_WIDTH = 272;

const ZoomableImage = forwardRef<ZoomableImageHandle, ZoomableImageProps>(function ZoomableImage(
  { src, alt, className = "", markers = [], renderTooltip, overlay, onImageClick, crosshair, onLoad, selection, selectMode, onSelection },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  const [natural, setNatural] = useState<{ width: number; height: number }>();
  const [view, setView] = useState<View>({ s: 1, tx: 0, ty: 0 });
  const [hovered, setHovered] = useState<string | number | null>(null);
  const [pinned, setPinned] = useState<string | number | null>(null);
  const [dragging, setDragging] = useState(false);
  const [draft, setDraft] = useState<ImageRect | null>(null);
  const selectStart = useRef<{ x: number; y: number } | null>(null);

  const tooltipRef = useRef<HTMLDivElement>(null);
  const [tooltipHeight, setTooltipHeight] = useState(160);

  const viewRef = useRef(view);
  viewRef.current = view;
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<{ moved: number; lastDistance?: number; lastMid?: { x: number; y: number } }>({ moved: 0 });

  const fit = natural && box.width && box.height
    ? Math.min(box.width / natural.width, box.height / natural.height)
    : 1;

  const reset = useCallback(() => {
    if (!natural || !box.width) return;
    setView({
      s: fit,
      tx: (box.width - natural.width * fit) / 2,
      ty: (box.height - natural.height * fit) / 2,
    });
  }, [natural, box.width, box.height, fit]);

  useLayoutEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setBox({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setPinned(null);
    setHovered(null);
  }, [src]);

  useEffect(reset, [reset]);

  const zoomAt = useCallback(
    (factor: number, cx: number, cy: number) => {
      setView((current) => {
        const s = Math.min(fit * MAX_ZOOM, Math.max(fit, current.s * factor));
        if (s === fit && natural) {
          return { s, tx: (box.width - natural.width * s) / 2, ty: (box.height - natural.height * s) / 2 };
        }
        const k = s / current.s;
        return { s, tx: cx - (cx - current.tx) * k, ty: cy - (cy - current.ty) * k };
      });
    },
    [fit, natural, box.width, box.height],
  );

  // Listener nativo no pasivo: evita que la rueda o el pellizco hagan zoom/scroll a la pagina.
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      zoomAt(Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0015)), event.clientX - rect.left, event.clientY - rect.top);
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  useEffect(() => {
    if (pinned === null) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setPinned(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pinned]);

  useImperativeHandle(
    ref,
    () => ({
      focusMarker(key) {
        const marker = markers.find((item) => item.key === key);
        if (!marker || !box.width) return;
        const s = Math.max(viewRef.current.s, Math.min(fit * MAX_ZOOM, fit * 3));
        setView({ s, tx: box.width / 2 - marker.x * s, ty: box.height / 2 - marker.y * s });
        setPinned(key);
      },
    }),
    [markers, box.width, box.height, fit],
  );

  function localPoint(event: React.PointerEvent) {
    const rect = containerRef.current!.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  /** Punto del puntero en coordenadas de la imagen, limitado a sus bordes. */
  function naturalPoint(event: React.PointerEvent) {
    const point = localPoint(event);
    const current = viewRef.current;
    return {
      x: Math.max(0, Math.min(natural?.width ?? 0, (point.x - current.tx) / current.s)),
      y: Math.max(0, Math.min(natural?.height ?? 0, (point.y - current.ty) / current.s)),
    };
  }

  function rectBetween(a: { x: number; y: number }, b: { x: number; y: number }): ImageRect {
    return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y) };
  }

  function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    if (selectMode && natural && pointers.current.size === 0) {
      event.currentTarget.setPointerCapture(event.pointerId);
      selectStart.current = naturalPoint(event);
      setDraft({ ...selectStart.current, w: 0, h: 0 });
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    pointers.current.set(event.pointerId, localPoint(event));
    gesture.current = { moved: 0 };
    setDragging(true);
  }

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (selectStart.current) {
      setDraft(rectBetween(selectStart.current, naturalPoint(event)));
      return;
    }
    const previous = pointers.current.get(event.pointerId);
    if (!previous) return;
    const point = localPoint(event);
    pointers.current.set(event.pointerId, point);

    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const { lastDistance, lastMid } = gesture.current;
      if (lastDistance && lastMid) {
        zoomAt(distance / lastDistance, mid.x, mid.y);
        setView((current) => ({ ...current, tx: current.tx + mid.x - lastMid.x, ty: current.ty + mid.y - lastMid.y }));
      }
      gesture.current = { moved: gesture.current.moved + 10, lastDistance: distance, lastMid: mid };
      return;
    }

    const dx = point.x - previous.x;
    const dy = point.y - previous.y;
    gesture.current.moved += Math.abs(dx) + Math.abs(dy);
    if (viewRef.current.s > fit + 1e-6) {
      setView((current) => ({ ...current, tx: current.tx + dx, ty: current.ty + dy }));
    }
  }

  function handlePointerUp(event: React.PointerEvent<HTMLDivElement>) {
    if (selectStart.current) {
      const rect = rectBetween(selectStart.current, naturalPoint(event));
      selectStart.current = null;
      setDraft(null);
      // Un clic sin arrastre no cambia el recorte.
      if (rect.w >= 20 && rect.h >= 20) {
        onSelection?.({ x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.w), h: Math.round(rect.h) });
      }
      return;
    }
    if (!pointers.current.has(event.pointerId)) return;
    pointers.current.delete(event.pointerId);
    if (pointers.current.size > 0) return;
    setDragging(false);
    if (gesture.current.moved > 5) return;
    // Clic simple sobre el fondo.
    if (pinned !== null) {
      setPinned(null);
      return;
    }
    if (onImageClick && natural) {
      const point = localPoint(event);
      const x = (point.x - view.tx) / view.s;
      const y = (point.y - view.ty) / view.s;
      if (x >= 0 && y >= 0 && x <= natural.width && y <= natural.height) onImageClick(x, y);
    }
  }

  function handleDoubleClick(event: React.MouseEvent<HTMLDivElement>) {
    const rect = containerRef.current!.getBoundingClientRect();
    if (view.s > fit * 3.5) reset();
    else zoomAt(2, event.clientX - rect.left, event.clientY - rect.top);
  }

  const activeKey = pinned ?? (dragging ? null : hovered);
  const activeMarker = activeKey === null ? undefined : markers.find((item) => item.key === activeKey);
  const tooltip = activeMarker && renderTooltip ? renderTooltip(activeMarker.key, pinned !== null) : null;

  // La altura real del tooltip se mide tras pintarlo para que quepa siempre dentro del recuadro.
  useLayoutEffect(() => {
    const height = tooltipRef.current?.offsetHeight;
    if (height && height !== tooltipHeight) setTooltipHeight(height);
  });

  let tooltipStyle: React.CSSProperties | undefined;
  if (activeMarker && tooltip) {
    const sx = view.tx + activeMarker.x * view.s;
    const top = view.ty + (activeMarker.y - activeMarker.r) * view.s;
    const bottom = view.ty + (activeMarker.y + activeMarker.r) * view.s;
    const spaceBelow = box.height - bottom - 10;
    const spaceAbove = top - 10;
    const below = spaceBelow >= tooltipHeight || spaceBelow >= spaceAbove;
    const wanted = below ? bottom + 10 : top - 10 - tooltipHeight;
    const left = Math.min(Math.max(sx, TOOLTIP_WIDTH / 2 + 8), box.width - TOOLTIP_WIDTH / 2 - 8);
    tooltipStyle = {
      left,
      top: Math.max(8, Math.min(wanted, box.height - tooltipHeight - 8)),
      width: TOOLTIP_WIDTH,
      transform: "translateX(-50%)",
    };
  }

  const zoomPercent = Math.round((view.s / fit) * 100);

  return (
    <div
      ref={containerRef}
      className={`${/\b(absolute|fixed)\b/.test(className) ? "" : "relative"} touch-none select-none overflow-hidden bg-black ${
        crosshair || selectMode ? "cursor-crosshair" : view.s > fit + 1e-6 ? (dragging ? "cursor-grabbing" : "cursor-grab") : ""
      } ${className}`}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      onDoubleClick={handleDoubleClick}
    >
      <div
        className="absolute left-0 top-0 origin-top-left"
        style={{
          width: natural?.width,
          height: natural?.height,
          transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.s})`,
          visibility: natural ? "visible" : "hidden",
        }}
      >
        <img
          src={src}
          alt={alt}
          draggable={false}
          className="block h-full w-full max-w-none"
          style={{ imageRendering: view.s > 2 ? "pixelated" : "auto" }}
          onLoad={(event) => {
            const size = { width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight };
            setNatural(size);
            onLoad?.(size);
          }}
        />
        {natural && (overlay || markers.length > 0 || draft || selection) && (
          <svg viewBox={`0 0 ${natural.width} ${natural.height}`} className="absolute inset-0 h-full w-full overflow-visible">
            {overlay}
            {(draft ?? selection) && (
              <g pointerEvents="none">
                {(() => {
                  const r = (draft ?? selection)!;
                  return (
                    <>
                      <path
                        d={`M0 0H${natural.width}V${natural.height}H0Z M${r.x} ${r.y}h${r.w}v${r.h}h${-r.w}Z`}
                        fill="rgba(0,0,0,0.6)"
                        fillRule="evenodd"
                      />
                      <rect
                        x={r.x}
                        y={r.y}
                        width={r.w}
                        height={r.h}
                        fill="none"
                        stroke="#34d399"
                        strokeWidth={2}
                        strokeDasharray="8 5"
                        vectorEffect="non-scaling-stroke"
                      />
                      <text
                        x={r.x + 4 / view.s}
                        y={r.y + 16 / view.s}
                        fill="#34d399"
                        stroke="#000"
                        strokeWidth={3 / view.s}
                        paintOrder="stroke"
                        fontSize={13 / view.s}
                        fontWeight={700}
                      >
                        {Math.round(r.w)} × {Math.round(r.h)} px
                      </text>
                    </>
                  );
                })()}
              </g>
            )}
            {markers.map((marker) => {
              const active = marker.key === activeKey;
              return (
                <g
                  key={marker.key}
                  className="cursor-pointer"
                  opacity={marker.dim ? 0.55 : 1}
                  onPointerDown={(event) => event.stopPropagation()}
                  onPointerEnter={() => setHovered(marker.key)}
                  onPointerLeave={() => setHovered((current) => (current === marker.key ? null : current))}
                  onClick={(event) => {
                    event.stopPropagation();
                    setPinned((current) => (current === marker.key ? null : marker.key));
                  }}
                  onDoubleClick={(event) => event.stopPropagation()}
                >
                  <circle
                    cx={marker.x}
                    cy={marker.y}
                    r={marker.r}
                    fill={active ? `${marker.color}33` : "transparent"}
                    stroke={marker.color}
                    strokeWidth={active ? 3.5 : 2}
                    vectorEffect="non-scaling-stroke"
                  />
                  {marker.label && (
                    <text
                      x={marker.x + marker.r + 3 / view.s}
                      y={marker.y - marker.r}
                      fill="#fafafa"
                      stroke="#000"
                      strokeWidth={3 / view.s}
                      paintOrder="stroke"
                      fontSize={12 / view.s}
                      fontWeight={700}
                    >
                      {marker.label}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        )}
      </div>

      {tooltip && tooltipStyle && (
        <div
          ref={tooltipRef}
          className={`absolute z-20 rounded-xl border border-zinc-700 bg-zinc-950/95 p-3 text-xs text-zinc-200 shadow-2xl backdrop-blur ${
            pinned !== null ? "" : "pointer-events-none"
          }`}
          style={tooltipStyle}
          onPointerDown={(event) => event.stopPropagation()}
          onDoubleClick={(event) => event.stopPropagation()}
        >
          {pinned !== null && (
            <button
              type="button"
              aria-label="Cerrar"
              className="absolute right-2 top-2 text-zinc-500 hover:text-white"
              onClick={() => setPinned(null)}
            >
              <X size={14} />
            </button>
          )}
          {tooltip}
          {pinned === null && <p className="mt-2 text-[10px] text-zinc-500">Clic para fijar</p>}
        </div>
      )}

      <div
        className="absolute bottom-3 right-3 z-10 flex items-center gap-1 rounded-lg border border-zinc-700 bg-zinc-950/85 p-1 text-zinc-300 backdrop-blur"
        onPointerDown={(event) => event.stopPropagation()}
        onDoubleClick={(event) => event.stopPropagation()}
      >
        <button type="button" aria-label="Alejar" className="grid h-7 w-7 place-items-center rounded hover:bg-zinc-800" onClick={() => zoomAt(1 / 1.5, box.width / 2, box.height / 2)}>
          <ZoomOut size={15} />
        </button>
        <span className="w-12 text-center text-[11px] tabular-nums">{zoomPercent}%</span>
        <button type="button" aria-label="Acercar" className="grid h-7 w-7 place-items-center rounded hover:bg-zinc-800" onClick={() => zoomAt(1.5, box.width / 2, box.height / 2)}>
          <ZoomIn size={15} />
        </button>
        <button type="button" aria-label="Ajustar" className="grid h-7 w-7 place-items-center rounded hover:bg-zinc-800" onClick={reset}>
          <Maximize2 size={14} />
        </button>
      </div>
      {natural && zoomPercent === 100 && (
        <div className="pointer-events-none absolute bottom-3 left-3 z-10 rounded-md bg-zinc-950/70 px-2 py-1 text-[10px] text-zinc-400">
          Rueda o pellizco: zoom · Arrastrar: mover · Doble clic: acercar
        </div>
      )}
    </div>
  );
});

export default ZoomableImage;
