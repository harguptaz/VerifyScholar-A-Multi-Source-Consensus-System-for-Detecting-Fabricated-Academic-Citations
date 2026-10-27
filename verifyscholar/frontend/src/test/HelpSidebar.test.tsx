import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { mockFetch } from "./helpers";

const health = { status: "ok", grobid: true, grobid_detail: "GROBID is up", libreoffice: true, embedding_backend_setting: "auto", openalex_api_key_set: true, contact_email_set: true };

function setup() {
  mockFetch([
    { method: "GET", path: "/health", handler: () => health },
    { method: "POST", path: "/citations/count", handler: () => ({ count: 0 }) },
  ]);
  const user = userEvent.setup();
  render(<App />);
  return user;
}

/** Pretend the screen is wide (push) or narrow (overlay). */
function screenWidth(wide: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide && query.includes("min-width: 1200px"), media: query,
    addEventListener: () => undefined, removeEventListener: () => undefined,
  }));
}

const helpButton = () => screen.getByRole("button", { name: /How it works/ });
const sidebar = () => screen.queryByRole("dialog", { name: "How it works" });

describe("How it works sidebar", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is closed by default, and the button opens and closes it", async () => {
    const user = setup();
    expect(sidebar()).not.toBeInTheDocument();
    expect(helpButton()).toHaveAttribute("aria-expanded", "false");
    // the old help card is gone from the page
    expect(screen.queryByRole("button", { name: "Test connection to data sources" })).not.toBeInTheDocument();

    await user.click(helpButton());
    expect(sidebar()).toBeInTheDocument();
    expect(helpButton()).toHaveAttribute("aria-expanded", "true");
    expect(helpButton()).toHaveClass("active");

    await user.click(helpButton());
    expect(sidebar()).not.toBeInTheDocument();
  });

  it("toggles with the ? key, but not while typing", async () => {
    const user = setup();
    await user.keyboard("?");
    expect(sidebar()).toBeInTheDocument();
    await user.keyboard("?");
    expect(sidebar()).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Citation list"), "Is this real?");
    expect(sidebar()).not.toBeInTheDocument();
    expect(screen.getByLabelText("Citation list")).toHaveValue("Is this real?");
  });

  it("closes with Escape or the close button and gives focus back to the button", async () => {
    const user = setup();
    await user.click(helpButton());
    expect(sidebar()).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(sidebar()).not.toBeInTheDocument();
    expect(helpButton()).toHaveFocus();

    await user.click(helpButton());
    await user.click(within(sidebar()!).getByRole("button", { name: "Close help" }));
    expect(sidebar()).not.toBeInTheDocument();
    expect(helpButton()).toHaveFocus();
  });

  it("shows help for the tab that is open", async () => {
    const user = setup();
    await user.click(helpButton());
    expect(within(sidebar()!).getByText("Confidence score")).toBeInTheDocument();
    expect(within(sidebar()!).getByText("80 to 100")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Paper Finder" }));
    expect(within(sidebar()!).getByRole("heading", { name: "Look up" })).toBeInTheDocument();
    expect(within(sidebar()!).queryByText("Confidence score")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Export list/ }));
    expect(within(sidebar()!).getByText(/for LaTeX and Overleaf/)).toBeInTheDocument();
  });

  it("on a wide screen it sits beside the page, which stays usable", async () => {
    screenWidth(true);
    const user = setup();
    await user.click(helpButton());
    expect(sidebar()).toHaveClass("push");
    expect(sidebar()).not.toHaveAttribute("aria-modal");
    expect(document.querySelector(".app")).toHaveClass("help-push");
    expect(document.querySelector(".help-backdrop")).toBeNull();
    expect(document.body.style.overflow).toBe("");
    // you can type into the page while it is open
    await user.type(screen.getByLabelText("Citation list"), "x");
    expect(sidebar()).toBeInTheDocument();
  });

  it("on a narrow screen it slides over a dimmed page and a click outside closes it", async () => {
    screenWidth(false);
    const user = setup();
    await user.click(helpButton());
    expect(sidebar()).toHaveClass("overlay");
    expect(sidebar()).toHaveAttribute("aria-modal", "true");
    expect(document.querySelector(".app")).not.toHaveClass("help-push");
    expect(document.body.style.overflow).toBe("hidden");

    await user.click(document.querySelector(".help-backdrop") as HTMLElement);
    expect(sidebar()).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
  });
});
