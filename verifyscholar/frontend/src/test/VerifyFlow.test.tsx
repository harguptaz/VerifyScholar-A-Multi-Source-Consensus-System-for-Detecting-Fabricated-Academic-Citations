import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import App from "../App";
import { FakeEventSource, doneJob, installFakeEventSource, mockFetch } from "./helpers";

const health = { status: "ok", grobid: false, libreoffice: true, embedding_backend_setting: "auto", openalex_api_key_set: true, contact_email_set: true };

function setup() {
  return mockFetch([
    { method: "GET", path: "/health", handler: () => health },
    { method: "POST", path: "/citations/count", handler: () => ({ count: 4 }) },
    { method: "POST", path: "/verify/citations", handler: () => ({ job_id: "job1" }) },
    { method: "GET", path: /^\/jobs\/job1$/, handler: () => doneJob() },
    {
      method: "POST", path: "/papers/format",
      handler: () => ({ style: "apa", text: "LeCun, Y., & Bengio, Y. (2015). Deep learning. Nature, 521(7553), 436–444.", bibtex: "@article{x}", source: "local" }),
    },
  ]);
}

async function runVerification(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Citation list"), "some pasted citations");
  await waitFor(() => expect(screen.getByText(/Citation count detected/).textContent).toContain("4"));
  await user.click(screen.getByRole("button", { name: "Verify Citations" }));
  await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
  const es = FakeEventSource.instances[0];
  es.emit("progress", { stage: "Checking citation 2 of 4 against CrossRef, OpenAlex and DOAJ…", completed: 1, total: 4 });
  await screen.findByText(/Checking citation 2 of 4/);
  expect(screen.getByText("1 of 4 citations checked")).toBeInTheDocument();
  es.emit("done", { status: "done" });
  await screen.findByText(/4 citations checked/);
}

describe("verify flow (Mode A)", () => {
  beforeEach(() => installFakeEventSource());

  it("shows the live counter, streams progress, then renders the report with the summary row", async () => {
    setup();
    const user = userEvent.setup();
    render(<App />);
    await runVerification(user);

    const summary = screen.getByText(/4 citations checked/).closest("p")!;
    expect(summary).toHaveTextContent("2 Verified");
    expect(summary).toHaveTextContent("1 Suspicious");
    expect(summary).toHaveTextContent("1 Ghost");
    expect(summary).toHaveTextContent("1 retracted");
    expect(screen.getByText(/built-in parser was used/)).toBeInTheDocument();
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });

  it("filters and sorts the table, and shows a retraction badge beside the verdict", async () => {
    setup();
    const user = userEvent.setup();
    render(<App />);
    await runVerification(user);

    const bodyRows = () => screen.getAllByRole("row").slice(1);
    // default sort puts reds first
    expect(within(bodyRows()[0]).getByText("Ghost")).toBeInTheDocument();
    expect(within(bodyRows()[1]).getByText("Suspicious")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Ghost (1)" }));
    expect(bodyRows()).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Retracted (1)" }));
    expect(bodyRows()).toHaveLength(1);
    expect(within(bodyRows()[0]).getByText("Verified")).toBeInTheDocument();
    expect(within(bodyRows()[0]).getByText("Retracted")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "All (4)" }));
    expect(bodyRows()).toHaveLength(4);
  });

  it("opens the detail modal with a field-by-field comparison and copies a corrected citation", async () => {
    const { calls } = setup();
    const user = userEvent.setup();
    render(<App />);
    await runVerification(user);

    const suspiciousRow = screen.getAllByRole("row").find((r) => r.textContent?.includes("Raw citation text number 2"))!;
    await user.click(within(suspiciousRow).getByRole("button", { name: "View detail" }));

    const panel = screen.getByRole("dialog", { name: "Citation 2 detail" });
    expect(within(panel).getByText(/your citation differs from it/)).toBeInTheDocument();
    const year = within(panel).getByText("Year").closest("tr")!;
    expect(year).toHaveTextContent("2018");
    expect(year).toHaveTextContent("2015");
    expect(year).toHaveTextContent("Mismatch");
    expect(within(panel).getByText("Title").closest("tr")).toHaveTextContent("Match");
    expect(within(panel).getByText("DOAJ")).toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "Copy corrected citation" }));
    await waitFor(() => expect(within(panel).getByText(/LeCun, Y., & Bengio, Y\. \(2015\)/)).toBeInTheDocument());
    const format = calls.find((c) => c.url.pathname === "/papers/format")!;
    expect((format.body as { style: string }).style).toBe("apa");

    await user.click(within(panel).getByRole("button", { name: "Add to export list" }));
    expect(screen.getByRole("button", { name: "Export list (1)" })).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Close details" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("ghost citations offer 'Find a replacement' and show their flags; no corrected citation is offered", async () => {
    setup();
    const user = userEvent.setup();
    render(<App />);
    await runVerification(user);

    const ghostRow = screen.getAllByRole("row").find((r) => r.textContent?.includes("Raw citation text number 3"))!;
    await user.click(within(ghostRow).getByRole("button", { name: "View detail" }));
    const panel = screen.getByRole("dialog", { name: "Citation 3 detail" });
    expect(within(panel).getByText(/placeholder/)).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Copy corrected citation" })).not.toBeInTheDocument();
    expect(within(panel).getByText(/No matching record was found/)).toBeInTheDocument();
    // the closest search results are listed with the reason each was rejected, so a wrong verdict can be explained
    await user.click(within(panel).getByText(/1 closest search result considered/));
    expect(within(panel).getByText("Layer Normalization in Transformers")).toBeInTheDocument();
    expect(within(panel).getByText(/not accepted: generic title and no author, journal or year in common/)).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Find a replacement" }));
    // jumps to the Paper Finder tab in search mode with the title pre-filled, and the modal does not stay on top
    expect(screen.getByRole("button", { name: "Paper Finder" })).toHaveClass("active");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes the detail modal with Escape or a backdrop click and gives focus back to the row", async () => {
    setup();
    const user = userEvent.setup();
    render(<App />);
    await runVerification(user);

    const row = screen.getAllByRole("row").find((r) => r.textContent?.includes("Raw citation text number 2"))!;
    const viewDetail = within(row).getByRole("button", { name: "View detail" });
    await user.click(viewDetail);
    const dialog = screen.getByRole("dialog", { name: "Citation 2 detail" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(document.body.style.overflow).toBe("hidden");

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
    expect(viewDetail).toHaveFocus();

    await user.click(viewDetail);
    // a click inside the card keeps it open; a click on the backdrop closes it
    await user.click(within(screen.getByRole("dialog")).getByText(/your citation differs from it/));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(document.querySelector(".modal-backdrop") as HTMLElement);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("steps through citations in table order with the arrow buttons and keys", async () => {
    setup();
    const user = userEvent.setup();
    render(<App />);
    await runVerification(user);

    // default sort is "reds first": the Ghost (#3) comes first, then the Suspicious (#2)
    const first = screen.getAllByRole("row")[1];
    await user.click(within(first).getByRole("button", { name: "View detail" }));
    let dialog = screen.getByRole("dialog", { name: "Citation 3 detail" });
    expect(dialog).toHaveTextContent("1 of 4");
    expect(within(dialog).getByRole("button", { name: "Previous citation" })).toBeDisabled();

    await user.click(within(dialog).getByRole("button", { name: "Next citation" }));
    dialog = screen.getByRole("dialog", { name: "Citation 2 detail" });
    expect(dialog).toHaveTextContent("2 of 4");

    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("dialog")).toHaveTextContent("3 of 4");
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("dialog", { name: "Citation 2 detail" })).toBeInTheDocument();

    // closing returns focus to the row being shown now, not the one first opened
    await user.keyboard("{Escape}");
    const row2 = screen.getAllByRole("row").find((r) => r.textContent?.includes("Raw citation text number 2"))!;
    expect(within(row2).getByRole("button", { name: "View detail" })).toHaveFocus();
  });

  it("Escape closes only the detail modal when the help sidebar is open behind it", async () => {
    setup();
    const user = userEvent.setup();
    render(<App />);
    await runVerification(user);

    await user.click(screen.getByRole("button", { name: /How it works/ }));
    const row = screen.getAllByRole("row").find((r) => r.textContent?.includes("Raw citation text number 2"))!;
    await user.click(within(row).getByRole("button", { name: "View detail" }));
    expect(screen.getByRole("dialog", { name: "Citation 2 detail" })).toBeInTheDocument();

    await user.keyboard("?"); // ignored while the modal is open
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Citation 2 detail" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "How it works" })).toBeInTheDocument();
  });

  it("shows an error when the job fails", async () => {
    mockFetch([
      { method: "GET", path: "/health", handler: () => health },
      { method: "POST", path: "/citations/count", handler: () => ({ count: 1 }) },
      { method: "POST", path: "/verify/citations", handler: () => ({ job_id: "job9" }) },
      { method: "GET", path: /^\/jobs\/job9$/, handler: () => ({ id: "job9", mode: "citations", status: "error", stage: "Failed", total: 0, completed: 0, error: "No citations were found in the pasted text." }) },
    ]);
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Citation list"), "x");
    await user.click(screen.getByRole("button", { name: "Verify Citations" }));
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    FakeEventSource.instances[0].emit("failed", {});
    await screen.findByText("No citations were found in the pasted text.");
  });

  it("falls back to polling when the event stream breaks", async () => {
    setup();
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Citation list"), "x");
    await user.click(screen.getByRole("button", { name: "Verify Citations" }));
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    FakeEventSource.instances[0].onerror?.();
    await screen.findByText(/4 citations checked/, undefined, { timeout: 4000 });
  });
});
