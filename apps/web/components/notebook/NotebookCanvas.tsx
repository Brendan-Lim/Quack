"use client";

import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { strokeBounds, strokeHit } from "@/lib/geometry";
import type { LassoPoint, ScreenRect } from "@/lib/lasso";
import { livePath, strokePath } from "@/lib/ink";
import { PAGE_WIDTH, pageHeight } from "@/lib/notebook";
import type { Marker } from "@/lib/mistakes";
import { randomId } from "@/lib/persist";
import type { NotebookStore } from "@/lib/store";
import type { PenStroke, Point } from "@/lib/types";

export type Tool = "pen" | "eraser" | "lasso";
export type Paper = "grid" | "ruled" | "plain";

const LINE_SPACING = 44;
const ERASER_RADIUS = 10;
const MIN_POINT_DISTANCE = 0.4;
const PEN_SEEN_KEY = "quack.pen-seen";

type Props = {
  store: NotebookStore;
  tool: Tool;
  color: string;
  width: number;
  paper: Paper;
  markers: Marker[];
  onDrawingChange: (drawing: boolean) => void;
  // A loop around some working, in page coordinates. Nothing about it goes into the notes.
  lasso?: readonly LassoPoint[] | null;
  selectedStrokes?: number;
  onLassoStart?: () => void;
  onLasso?: (polygon: LassoPoint[], screen: ScreenRect) => void;
  // Bring this height on the page into view. A new object asks again.
  focus?: { y: number } | null;
};

export function NotebookCanvas({ store, tool, color, width, paper, markers, onDrawingChange, lasso, selectedStrokes = 0, onLassoStart, onLasso, focus }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const baseCanvas = useRef<HTMLCanvasElement>(null);
  const liveCanvas = useRef<HTMLCanvasElement>(null);
  const [view, setView] = useState({ width: 0, height: 0 });
  const [densityChanges, setDensityChanges] = useState(0);
  const [contentHeight, setContentHeight] = useState(0);
  const [info, setInfo] = useState({ version: 0, strokes: 0 });

  const scale = view.width / PAGE_WIDTH || 1;

  // Pointer state is kept in refs: it changes on every move and must not re-render.
  const current = useRef<{ pointerId: number; pointerType: string; points: Point[] } | null>(null);
  const erasing = useRef<{ pointerId: number; pointerType: string; ids: Set<string>; last: { x: number; y: number } } | null>(null);
  const looping = useRef<{ pointerId: number; pointerType: string; points: LassoPoint[] } | null>(null);
  const scrolling = useRef<{ pointerId: number; lastY: number } | null>(null);
  const penSeen = useRef(false);
  const touches = useRef(new Set<number>());
  const frame = useRef<number | null>(null);
  const eraserAt = useRef<{ x: number; y: number } | null>(null);
  const pendingWorldTop = useRef<number | null>(null);
  // Where the view's top was in the page, while shown, and whether it's hidden now.
  const shownTop = useRef(0);
  const hidden = useRef(false);

  const propsRef = useRef({ tool, color, width, paper, markers, scale, view, lasso });
  useEffect(() => {
    propsRef.current = { tool, color, width, paper, markers, scale, view, lasso };
  });

  const paint = useCallback(() => {
    frame.current = null;
    const base = baseCanvas.current;
    const live = liveCanvas.current;
    const area = scroller.current;
    if (!base || !live || !area) return;
    const { view, scale, paper, markers, color, width, lasso } = propsRef.current;
    if (!view.width) return;
    const dpr = window.devicePixelRatio || 1;
    const scrollTop = area.scrollTop;
    const worldTop = scrollTop / scale;
    const worldBottom = worldTop + view.height / scale;

    const context = base.getContext("2d");
    if (context) {
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.fillStyle = "#FFFCF4";
      context.fillRect(0, 0, view.width, view.height);
      context.setTransform(dpr * scale, 0, 0, dpr * scale, 0, -scrollTop * dpr);

      if (paper !== "plain") {
        context.strokeStyle = paper === "grid" ? "#F0E9D6" : "#E9E2CE";
        context.lineWidth = 1 / scale;
        context.beginPath();
        for (let y = Math.ceil(worldTop / LINE_SPACING) * LINE_SPACING; y <= worldBottom; y += LINE_SPACING) {
          context.moveTo(0, y);
          context.lineTo(PAGE_WIDTH, y);
        }
        if (paper === "grid") {
          for (let x = LINE_SPACING; x < PAGE_WIDTH; x += LINE_SPACING) {
            context.moveTo(x, worldTop);
            context.lineTo(x, worldBottom);
          }
        }
        context.stroke();
      }

      for (const { bounds, kind } of markers) {
        context.beginPath();
        context.roundRect(bounds.minX, bounds.minY, bounds.maxX - bounds.minX, bounds.maxY - bounds.minY, 10);
        if (kind === "unread") {
          // Slate, the colour of "can't read it yet", never the mistake colour.
          context.strokeStyle = "rgba(70, 97, 127, 0.75)";
          context.lineWidth = 1.75 / scale;
          context.setLineDash([6 / scale, 4 / scale]);
          context.stroke();
          context.setLineDash([]);
        } else if (kind === "pending") {
          // Its ink changed. Not a verdict either way, just that Quack is looking again.
          context.strokeStyle = "rgba(244, 163, 64, 0.55)";
          context.lineWidth = 1.5 / scale;
          context.setLineDash([5 / scale, 4 / scale]);
          context.stroke();
          context.setLineDash([]);
        } else {
          context.fillStyle = kind === "selected" ? "rgba(244, 163, 64, 0.24)" : "rgba(244, 163, 64, 0.1)";
          context.fill();
        }
      }

      const hidden = erasing.current?.ids;
      for (const stroke of store.getState().strokes) {
        if (hidden?.has(stroke.id)) continue;
        const bounds = strokeBounds(stroke);
        if (bounds.maxY < worldTop || bounds.minY > worldBottom) continue;
        context.fillStyle = stroke.color;
        context.fill(strokePath(stroke));
      }
    }

    const overlay = live.getContext("2d");
    if (overlay) {
      overlay.setTransform(dpr, 0, 0, dpr, 0, 0);
      overlay.clearRect(0, 0, view.width, view.height);
      overlay.setTransform(dpr * scale, 0, 0, dpr * scale, 0, -scrollTop * dpr);
      if (current.current) {
        overlay.fillStyle = color;
        overlay.fill(livePath(current.current.points, width));
      }
      if (eraserAt.current) {
        overlay.strokeStyle = "#B8B0A0";
        overlay.lineWidth = 1 / scale;
        overlay.beginPath();
        overlay.arc(eraserAt.current.x, eraserAt.current.y, ERASER_RADIUS, 0, Math.PI * 2);
        overlay.stroke();
      }
      const loop = looping.current?.points ?? lasso;
      if (loop?.length) {
        overlay.beginPath();
        loop.forEach((point, index) => (index ? overlay.lineTo(point.x, point.y) : overlay.moveTo(point.x, point.y)));
        if (!looping.current) {
          overlay.closePath();
          overlay.fillStyle = "rgba(255, 217, 90, 0.16)";
          overlay.fill();
        }
        overlay.strokeStyle = "#F4A340";
        overlay.lineWidth = 1.5 / scale;
        overlay.setLineDash([6 / scale, 5 / scale]);
        overlay.stroke();
        overlay.setLineDash([]);
      }
    }
  }, [store]);

  const invalidate = useCallback(() => {
    if (frame.current === null) frame.current = requestAnimationFrame(paint);
  }, [paint]);

  const reportViewport = useCallback(() => {
    const area = scroller.current;
    const { scale, view } = propsRef.current;
    if (!area || !view.width) return;
    store.setViewport({
      minX: 0,
      maxX: PAGE_WIDTH,
      minY: area.scrollTop / scale,
      maxY: (area.scrollTop + view.height) / scale,
    });
  }, [store]);

  useEffect(() => {
    const area = scroller.current;
    if (!area) return;
    penSeen.current = localStorage.getItem(PEN_SEEN_KEY) === "1";
    const measure = () => {
      // Hidden, as a lesson is while the notebook is in front: nothing to measure, and a hidden scroller forgets
      // where it was. Keep the last view, and on coming back, go back to the same writing.
      if (area.clientWidth === 0) {
        hidden.current = true;
        return;
      }
      if (hidden.current) {
        hidden.current = false;
        area.scrollTop = shownTop.current * propsRef.current.scale;
      }
      // Rotating or resizing changes the scale, so keep the same writing at the top of the view.
      const previous = propsRef.current;
      if (previous.view.width && area.clientWidth !== previous.view.width) {
        const worldTop = area.scrollTop / previous.scale;
        pendingWorldTop.current = worldTop;
      }
      setView({ width: area.clientWidth, height: area.clientHeight });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(area);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const area = scroller.current;
    if (area && pendingWorldTop.current !== null && contentHeight) {
      area.scrollTop = pendingWorldTop.current * scale;
      pendingWorldTop.current = null;
    }
    reportViewport();
    invalidate();
  }, [scale, contentHeight, reportViewport, invalidate]);

  useEffect(() => {
    const dpr = window.devicePixelRatio || 1;
    for (const canvas of [baseCanvas.current, liveCanvas.current]) {
      if (!canvas) continue;
      canvas.width = Math.round(view.width * dpr);
      canvas.height = Math.round(view.height * dpr);
    }
    invalidate();
    // Moving to a screen with a different density changes the pixels but not the layout.
    const density = window.matchMedia(`(resolution: ${dpr}dppx)`);
    const redraw = () => setDensityChanges((count) => count + 1);
    density.addEventListener("change", redraw);
    return () => density.removeEventListener("change", redraw);
  }, [view, invalidate, densityChanges]);

  useEffect(() => {
    const update = () => {
      const { strokes, version } = store.getState();
      const { scale, view } = propsRef.current;
      setContentHeight(pageHeight(strokes, view.height / scale) * scale);
      setInfo({ version, strokes: strokes.length });
      invalidate();
    };
    update();
    return store.subscribe(update);
  }, [store, invalidate, view, scale]);

  useEffect(invalidate, [paper, markers, lasso, invalidate]);

  useEffect(() => {
    const area = scroller.current;
    if (area && focus) area.scrollTop = focus.y * propsRef.current.scale - area.clientHeight / 3;
  }, [focus]);

  // Safari ignores `touch-action` for some gestures unless touchmove is cancelled, and React
  // registers passive touch listeners, so this one is attached by hand.
  useEffect(() => {
    const canvas = liveCanvas.current;
    if (!canvas) return;
    const prevent = (event: TouchEvent) => event.preventDefault();
    canvas.addEventListener("touchmove", prevent, { passive: false });
    canvas.addEventListener("touchstart", prevent, { passive: false });
    return () => {
      canvas.removeEventListener("touchmove", prevent);
      canvas.removeEventListener("touchstart", prevent);
    };
  }, []);

  const toWorld = (event: { clientX: number; clientY: number }) => {
    const rect = liveCanvas.current!.getBoundingClientRect();
    const { scale } = propsRef.current;
    return {
      x: (event.clientX - rect.left) / scale,
      y: (event.clientY - rect.top + scroller.current!.scrollTop) / scale,
    };
  };

  const toPoint = (event: PointerEvent, pointerType: string): Point => ({
    ...toWorld(event),
    // Mouse and finger report placeholder pressure, so only a real pen's value is kept.
    ...(pointerType === "pen" ? { pressure: event.pressure } : {}),
    timestamp: event.timeStamp,
  });

  const eraseTo = (position: { x: number; y: number }) => {
    const active = erasing.current;
    if (!active) return;
    // Test along the path, since a fast swipe can jump right over a thin stroke.
    const distance = Math.hypot(position.x - active.last.x, position.y - active.last.y);
    const steps = Math.max(1, Math.ceil(distance / ERASER_RADIUS));
    for (let step = 1; step <= steps; step++) {
      const at = {
        x: active.last.x + ((position.x - active.last.x) * step) / steps,
        y: active.last.y + ((position.y - active.last.y) * step) / steps,
      };
      for (const stroke of store.getState().strokes) {
        if (!active.ids.has(stroke.id) && strokeHit(stroke, at, ERASER_RADIUS)) active.ids.add(stroke.id);
      }
    }
    active.last = position;
  };

  const discardGesture = () => {
    current.current = null;
    looping.current = null;
    if (erasing.current) {
      erasing.current = null;
      eraserAt.current = null;
    }
    onDrawingChange(false);
    invalidate();
  };

  const screenRectOf = (points: readonly LassoPoint[]): ScreenRect => {
    const rect = liveCanvas.current!.getBoundingClientRect();
    const { scale } = propsRef.current;
    const scrollTop = scroller.current!.scrollTop;
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    return {
      left: Math.min(...xs) * scale + rect.left,
      right: Math.max(...xs) * scale + rect.left,
      top: Math.min(...ys) * scale - scrollTop + rect.top,
      bottom: Math.max(...ys) * scale - scrollTop + rect.top,
    };
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (event.pointerType === "pen" && !penSeen.current) {
      penSeen.current = true;
      localStorage.setItem(PEN_SEEN_KEY, "1");
    }
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const canvas = event.currentTarget;
    const active = current.current ?? erasing.current ?? looping.current;
    const penOrMouseActive = active !== null && active.pointerType !== "touch";

    if (event.pointerType === "touch") {
      touches.current.add(event.pointerId);
      // A touch never disturbs a pen in use. It's a palm, so it does nothing at all.
      if (penOrMouseActive) return;
      canvas.setPointerCapture(event.pointerId);
      // Once a pen has been used, fingers scroll, and a resting palm can't leave marks.
      // Without a pen, one finger draws and two scroll.
      if (penSeen.current || touches.current.size >= 2) {
        if (active) discardGesture();
        scrolling.current ??= { pointerId: event.pointerId, lastY: event.clientY };
        return;
      }
    } else {
      canvas.setPointerCapture(event.pointerId);
      // A pen landing beats whatever a palm was doing, even on the first stroke after a load.
      if (event.pointerType === "pen") {
        if (active?.pointerType === "touch") discardGesture();
        scrolling.current = null;
      } else if (active) {
        return;
      }
    }
    if (current.current || erasing.current || looping.current) return;

    if (propsRef.current.tool === "lasso") {
      onDrawingChange(true);
      onLassoStart?.();
      looping.current = { pointerId: event.pointerId, pointerType: event.pointerType, points: [toWorld(event)] };
      invalidate();
      return;
    }

    // The eraser end of a stylus reports button 5 (mask 32).
    const useEraser = propsRef.current.tool === "eraser" || (event.buttons & 32) !== 0;
    onDrawingChange(true);
    if (useEraser) {
      const at = toWorld(event);
      erasing.current = { pointerId: event.pointerId, pointerType: event.pointerType, ids: new Set(), last: at };
      eraserAt.current = at;
      eraseTo(at);
    } else {
      current.current = {
        pointerId: event.pointerId,
        pointerType: event.pointerType,
        points: [toPoint(event.nativeEvent, event.pointerType)],
      };
    }
    invalidate();
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const scroll = scrolling.current;
    if (scroll?.pointerId === event.pointerId) {
      scroller.current!.scrollTop += scroll.lastY - event.clientY;
      scroll.lastY = event.clientY;
      return;
    }
    const native = event.nativeEvent;
    const samples = native.getCoalescedEvents?.() ?? [];
    const events = samples.length ? samples : [native];

    if (erasing.current?.pointerId === event.pointerId) {
      for (const sample of events) {
        eraserAt.current = toWorld(sample);
        eraseTo(eraserAt.current);
      }
      invalidate();
      return;
    }
    const loop = looping.current;
    if (loop?.pointerId === event.pointerId) {
      for (const sample of events) {
        const point = toWorld(sample);
        const last = loop.points.at(-1)!;
        if (Math.hypot(point.x - last.x, point.y - last.y) >= MIN_POINT_DISTANCE) loop.points.push(point);
      }
      invalidate();
      return;
    }
    const drawing = current.current;
    if (drawing?.pointerId !== event.pointerId) return;
    for (const sample of events) {
      const point = toPoint(sample, event.pointerType);
      const last = drawing.points.at(-1)!;
      if (Math.hypot(point.x - last.x, point.y - last.y) >= MIN_POINT_DISTANCE) drawing.points.push(point);
    }
    invalidate();
  };

  const finish = (event: ReactPointerEvent<HTMLCanvasElement>, cancelled: boolean) => {
    touches.current.delete(event.pointerId);
    if (scrolling.current?.pointerId === event.pointerId) {
      scrolling.current = null;
      return;
    }
    const eraser = erasing.current;
    if (eraser?.pointerId === event.pointerId) {
      erasing.current = null;
      eraserAt.current = null;
      onDrawingChange(false);
      if (!cancelled && eraser.ids.size) store.remove(eraser.ids);
      invalidate();
      return;
    }
    const loop = looping.current;
    if (loop?.pointerId === event.pointerId) {
      looping.current = null;
      onDrawingChange(false);
      // Unlike a stroke, nothing is lost by dropping a loop that was cut short.
      if (!cancelled) onLasso?.(loop.points, screenRectOf(loop.points));
      invalidate();
      return;
    }
    const drawing = current.current;
    if (drawing?.pointerId !== event.pointerId) return;
    current.current = null;
    onDrawingChange(false);
    // A cancelled touch is usually a palm the system recognised late, so it's dropped. A
    // cancelled pen stroke is the student's writing, so it's kept if there is enough of it.
    const keep = cancelled ? event.pointerType !== "touch" && drawing.points.length > 1 : true;
    if (keep) {
      const { color, width } = propsRef.current;
      const stroke: PenStroke = {
        id: randomId(),
        points: drawing.points,
        color,
        width,
        createdAt: Date.now(),
      };
      store.add(stroke);
    }
    invalidate();
  };

  return (
    <div
      ref={scroller}
      onScroll={() => {
        if (scroller.current && scroller.current.clientWidth) shownTop.current = scroller.current.scrollTop / propsRef.current.scale;
        reportViewport();
        invalidate();
      }}
      data-testid="notebook"
      data-version={info.version}
      data-strokes={info.strokes}
      data-scale={scale}
      data-markers={JSON.stringify(markers.map(({ id, kind, bounds }) => ({ id, kind, ...bounds })))}
      data-selected-strokes={selectedStrokes}
      className="notebook-scroll relative min-h-0 flex-1 overflow-x-hidden overflow-y-auto"
    >
      <div style={{ height: contentHeight }} className="relative">
        <div className="sticky top-0" style={{ height: view.height }}>
          <canvas
            ref={baseCanvas}
            aria-hidden
            className="absolute inset-0"
            style={{ width: view.width, height: view.height }}
          />
          <canvas
            ref={liveCanvas}
            role="img"
            aria-label={`Notebook page with ${info.strokes} pen strokes`}
            data-testid="ink"
            className={`absolute inset-0 touch-none select-none ${tool === "eraser" ? "cursor-cell" : tool === "lasso" ? "cursor-alias" : "cursor-crosshair"}`}
            style={{ width: view.width, height: view.height, WebkitTouchCallout: "none", WebkitUserSelect: "none" }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={(event) => finish(event, false)}
            onPointerCancel={(event) => finish(event, true)}
          />
        </div>
      </div>
    </div>
  );
}
