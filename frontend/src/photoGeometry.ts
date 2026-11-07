import type { ExtensionDirection, PhotoMode, Point } from './types';

export function guideArms(): Point[] { return [{ x: .18, y: .55 }, { x: .5, y: .55 }, { x: .82, y: .55 }]; }
export function kneeBend(points: Point[], width: number, height: number): number | null {
  if (points.length !== 3) return null;
  const [a,b,c] = points;
  const u = [(a.x-b.x)*width, (a.y-b.y)*height], v = [(c.x-b.x)*width, (c.y-b.y)*height];
  const length = Math.hypot(...u)*Math.hypot(...v);
  if (length < 1) return null;
  return +(180 - Math.acos(Math.min(1, Math.max(-1, (u[0]*v[0]+u[1]*v[1])/length))) * 180/Math.PI).toFixed(1);
}
export function signedValue(angle: number | null, mode: PhotoMode, direction: ExtensionDirection): number | null {
  if (angle == null || mode === 'unclassified' || (mode === 'extension' && !direction)) return null;
  return mode === 'extension' && direction === 'hyperextension' && angle !== 0 ? -angle : angle;
}
export function moveArm(points: Point[], index: number, x: number, y: number): Point[] {
  x = Math.max(.015, Math.min(.985, x)); y = Math.max(.015, Math.min(.985, y));
  if (index !== 1) return points.map((p,i) => i === index ? { x, y } : p);
  const pivot = points[1], vectors = points.map(p => ({ x: p.x-pivot.x, y: p.y-pivot.y }));
  let scale = 1;
  for (const v of vectors) {
    if (v.x > 0) scale = Math.min(scale, (1-x)/v.x);
    if (v.x < 0) scale = Math.min(scale, -x/v.x);
    if (v.y > 0) scale = Math.min(scale, (1-y)/v.y);
    if (v.y < 0) scale = Math.min(scale, -y/v.y);
  }
  return vectors.map(v => ({ x: x+v.x*scale, y: y+v.y*scale }));
}
