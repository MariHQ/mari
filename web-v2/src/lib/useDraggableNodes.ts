import { PointerEvent as ReactPointerEvent, useRef, useState } from "react";

export type NodePosition = { x: number; y: number };

type ProjectionArgs = {
  event: ReactPointerEvent<HTMLDivElement>;
  rect: DOMRect;
  origin: NodePosition;
  dx: number;
  dy: number;
};

type DragState = {
  id: string;
  pointerId: number;
  startX: number;
  startY: number;
  origin: NodePosition;
  moved: boolean;
};

type Options = {
  initialPositions: Record<string, NodePosition>;
  projectPointer: (args: ProjectionArgs) => NodePosition;
  onActivate: (id: string) => void;
  movementThreshold?: number;
};

export function useDraggableNodes({ initialPositions, projectPointer, onActivate, movementThreshold = 4 }: Options) {
  const [positions, setPositions] = useState<Record<string, NodePosition>>(() => initialPositions);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);

  const beginDrag = (event: ReactPointerEvent<HTMLDivElement>, id: string) => {
    if (event.button !== 0) return;
    const origin = positions[id];
    if (!origin) return;
    dragRef.current = {
      id,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin,
      moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.focus({ preventScroll: true });
    setDraggingId(id);
    event.preventDefault();
  };

  const moveDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    const rect = surfaceRef.current?.getBoundingClientRect();
    if (!drag || drag.pointerId !== event.pointerId || !rect) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) <= movementThreshold) return;
    drag.moved = true;
    setPositions((current) => ({
      ...current,
      [drag.id]: projectPointer({ event, rect, origin: drag.origin, dx, dy }),
    }));
  };

  const endDrag = (event: ReactPointerEvent<HTMLDivElement>, cancelled = false) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    dragRef.current = null;
    setDraggingId(null);
    if (!cancelled && !drag.moved) onActivate(drag.id);
  };

  return { positions, draggingId, surfaceRef, beginDrag, moveDrag, endDrag };
}
