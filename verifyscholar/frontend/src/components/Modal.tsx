import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface Props {
  /** Accessible name announced by screen readers when the dialog opens. */
  label: string;
  onClose: () => void;
  children: ReactNode;
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/**
 * A centered modal card over a blurred, dimmed backdrop.
 * Closes on Escape or a click on the backdrop, locks page scroll, keeps keyboard focus inside the card,
 * and gives focus back to whatever was focused before it opened.
 */
export function Modal({ label, onClose, children }: Props) {
  const cardRef = useRef<HTMLDivElement>(null);
  const pressedOnBackdrop = useRef(false);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  // focus in / focus back, and lock page scroll
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    cardRef.current?.focus();

    const body = document.body;
    const prev = { overflow: body.style.overflow, paddingRight: body.style.paddingRight };
    const scrollbar = window.innerWidth - document.documentElement.clientWidth;
    body.style.overflow = "hidden";
    if (scrollbar > 0) body.style.paddingRight = `${scrollbar}px`; // stop the page jumping sideways

    return () => {
      body.style.overflow = prev.overflow;
      body.style.paddingRight = prev.paddingRight;
      if (opener && document.contains(opener)) opener.focus();
    };
  }, []);

  // Escape closes; Tab cycles through the card only
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const card = cardRef.current;
      if (!card) return;
      if (e.key === "Escape") {
        e.preventDefault();
        closeRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      // skip controls hidden inside a collapsed <details> (its <summary> stays reachable)
      const items = Array.from(card.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => {
        const closed = el.closest("details:not([open])");
        return !closed || (el.tagName === "SUMMARY" && el.parentElement === closed);
      });
      if (items.length === 0) {
        e.preventDefault();
        card.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (!card.contains(active)) {
        e.preventDefault();
        first.focus();
      } else if (e.shiftKey && (active === first || active === card)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return createPortal(
    <div
      className="modal-backdrop"
      // close only when the click both starts and ends on the backdrop (not after dragging out of the card)
      onMouseDown={(e) => { pressedOnBackdrop.current = e.target === e.currentTarget; }}
      onClick={(e) => {
        if (pressedOnBackdrop.current && e.target === e.currentTarget) onClose();
        pressedOnBackdrop.current = false;
      }}
    >
      <div ref={cardRef} className="modal-card" role="dialog" aria-modal="true" aria-label={label} tabIndex={-1}>
        {children}
      </div>
    </div>,
    document.body,
  );
}
