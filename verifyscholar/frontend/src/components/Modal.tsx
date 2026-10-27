import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { trapTab } from "../a11y";

interface Props {
  /** Accessible name announced by screen readers when the dialog opens. */
  label: string;
  onClose: () => void;
  children: ReactNode;
}

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
      trapTab(e, card);
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
