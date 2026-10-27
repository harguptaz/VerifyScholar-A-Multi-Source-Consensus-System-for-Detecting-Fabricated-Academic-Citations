const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/** Keyboard focus targets inside `container`, skipping controls hidden in a collapsed <details> (its <summary> stays reachable). */
function focusables(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => {
    const closed = el.closest("details:not([open])");
    return !closed || (el.tagName === "SUMMARY" && el.parentElement === closed);
  });
}

/** Call from a Tab keydown handler: keeps focus cycling inside `container`. */
export function trapTab(e: KeyboardEvent, container: HTMLElement) {
  if (e.key !== "Tab") return;
  const items = focusables(container);
  if (items.length === 0) {
    e.preventDefault();
    container.focus();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (!container.contains(active)) {
    e.preventDefault();
    first.focus();
  } else if (e.shiftKey && (active === first || active === container)) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault();
    first.focus();
  }
}

/** True while the user is typing or picking from a menu, so single-key shortcuts should stay out of the way. */
export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName);
}

/** True while a citation detail modal is open; it owns Escape and the arrow keys then. */
export function modalIsOpen(): boolean {
  return document.querySelector(".modal-card") !== null;
}
