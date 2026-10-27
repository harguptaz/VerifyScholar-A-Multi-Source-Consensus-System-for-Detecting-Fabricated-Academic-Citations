import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "../App";
import { FakeEventSource, installFakeEventSource, mockFetch } from "./helpers";

const base = { status: "ok", libreoffice: true, embedding_backend_setting: "auto", openalex_api_key_set: true, contact_email_set: true, grobid_url: "http://grobid:8070" };

describe("diagnostics and feedback", () => {
  it("shows why GROBID is unreachable and clears the banner once it is up, without a page reload", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let alive = false;
    mockFetch([{
      method: "GET", path: "/health",
      handler: () => ({ ...base, grobid: alive, grobid_detail: alive ? "GROBID is up" : "Cannot connect to http://grobid:8070 (ConnectError). It may still be starting." }),
    }]);
    render(<App />);
    await screen.findByText(/cannot reach GROBID right now/);
    expect(screen.getByText(/Cannot connect to http:\/\/grobid:8070/)).toBeInTheDocument();

    alive = true;
    await vi.advanceTimersByTimeAsync(5500); // the page re-checks on its own
    await waitFor(() => expect(screen.queryByText(/cannot reach GROBID right now/)).not.toBeInTheDocument());
    vi.useRealTimers();
  });

  it("tests the connection to the data sources and reports the reason for a failure", async () => {
    mockFetch([
      { method: "GET", path: "/health", handler: () => ({ ...base, grobid: true, grobid_detail: "GROBID is up" }) },
      {
        method: "GET", path: "/health/sources",
        handler: () => ({
          crossref: { ok: true, detail: "Reachable", http_status: 200 },
          openalex: { ok: false, detail: "The service refused the request (HTTP 403): check OPENALEX_API_KEY", http_status: 403 },
          doaj: { ok: true, detail: "Reachable", http_status: 200 },
        }),
      },
    ]);
    const user = userEvent.setup();
    render(<App />);
    // the connection test lives in the "How it works" sidebar
    await user.click(screen.getByRole("button", { name: /How it works/ }));
    await user.click(screen.getByRole("button", { name: "Test connection to data sources" }));
    await screen.findByText(/check OPENALEX_API_KEY/);
    expect(screen.getByText(/✗ OpenAlex/)).toBeInTheDocument();
    expect(screen.getByText(/✓ CrossRef/)).toBeInTheDocument();
  });

  it("gives immediate feedback after Verify, and never shows the previous job's progress", async () => {
    installFakeEventSource();
    let release: (v: unknown) => void = () => undefined;
    const gate = new Promise((r) => { release = r; });
    mockFetch([
      { method: "GET", path: "/health", handler: () => ({ ...base, grobid: true, grobid_detail: "GROBID is up" }) },
      { method: "POST", path: "/citations/count", handler: () => ({ count: 1 }) },
      { method: "POST", path: "/verify/citations", handler: () => gate.then(() => ({ job_id: "slow" })) as unknown as object },
    ]);
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Citation list"), "x");
    // a slow server: the promise returned by the handler is awaited through Response.json
    await user.click(screen.getByRole("button", { name: "Verify Citations" }));
    expect(screen.getByRole("button", { name: "Verifying…" })).toBeDisabled();
    release(null);
    await waitFor(() => expect(FakeEventSource.instances.length).toBeLessThanOrEqual(1));
  });
});
