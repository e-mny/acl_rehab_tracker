import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
export default function Modal({ title, subtitle, close, children, wide = false }: { title: string; subtitle?: string; close: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const el = ref.current!; el.showModal(); document.body.style.overflow = 'hidden'; return () => { document.body.style.overflow = ''; }; }, []);
  return <dialog ref={ref} className={'modal ' + (wide ? 'wide' : '')} onCancel={e => { e.preventDefault(); close(); }} onClick={e => { if (e.target === ref.current) { const r = ref.current.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close(); } }}><div className="modal-heading"><div><span className="eyebrow">YOUR RECOVERY JOURNAL</span><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="icon-button" aria-label="Close dialog" onClick={close}><X size={20}/></button></div>{children}</dialog>;
}
