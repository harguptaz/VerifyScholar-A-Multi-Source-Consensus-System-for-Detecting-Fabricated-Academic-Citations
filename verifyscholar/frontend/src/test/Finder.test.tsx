import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { card, mockFetch, record } from "./helpers";

const health = { status: "ok", grobid: true, libreoffice: true, embedding_backend_setting: "auto", openalex_api_key_set: true, contact_email_set: true };

async function openFinder(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Paper Finder" }));
}

describe("Paper Finder (Mode D)", () => {
  it("looks up a DOI and shows the access ladder and abstract, but no verdict or confidence", async () => {
    const { calls } = mockFetch([
      { method: "GET", path: "/health", handler: () => health },
      { method: "GET", path: "/papers/lookup", handler: () => ({ query: "x", query_type: "doi", paper: card(), candidates: [], message: "" }) },
    ]);
    const user = userEvent.setup();
    render(<App />);
    await openFinder(user);
    await user.type(screen.getByLabelText("DOI, link or title"), "https://doi.org/10.1038/nature14539");
    await user.click(screen.getByRole("button", { name: "Look up" }));

    await screen.findByRole("heading", { name: "Deep learning" });
    expect(calls.find((c) => c.url.pathname === "/papers/lookup")!.url.searchParams.get("id")).toBe("https://doi.org/10.1038/nature14539");
    expect(screen.getByText("Read it now")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Free full text" })).toHaveAttribute("href", "https://example.org/dl.pdf");
    expect(screen.getByText(/never hosts or proxies paywalled PDFs/)).toBeInTheDocument();
    expect(screen.getByText(/Deep learning allows computational models/)).toBeInTheDocument();
    // a paper retrieved from the sources exists by definition, so nothing on the card says it was "verified"
    const paper = within(screen.getByRole("heading", { name: "Deep learning" }).closest("article") as HTMLElement);
    expect(paper.queryByText(/Verified|Suspicious|Ghost|Inconclusive/)).not.toBeInTheDocument();
    expect(paper.queryByText(/Confidence/)).not.toBeInTheDocument();
    expect(paper.getByText("Found in")).toBeInTheDocument();
  });

  it("paywalled papers get the publisher page and a library-login note, never a fake full-text link", async () => {
    const closed = record({ is_oa: false, oa_url: null, oa_status: "closed", title: "A paywalled paper", doi: "10.5555/closed", doi_url: "https://doi.org/10.5555/closed" });
    mockFetch([
      { method: "GET", path: "/health", handler: () => health },
      { method: "GET", path: "/papers/lookup", handler: () => ({ query: "x", query_type: "doi", paper: card({ record: closed }), candidates: [], message: "" }) },
    ]);
    const user = userEvent.setup();
    render(<App />);
    await openFinder(user);
    await user.type(screen.getByLabelText("DOI, link or title"), "10.5555/closed");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    await screen.findByRole("heading", { name: "A paywalled paper" });
    expect(screen.getByText("Get the paper")).toBeInTheDocument();
    expect(screen.getByText(/library login may give you access/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Free full text" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open publisher page" })).toHaveAttribute("href", "https://doi.org/10.5555/closed");
  });

  it("loads the citation network tabs lazily and can open a paper from them", async () => {
    const ref = record({ doi: "10.1/ref", title: "A referenced paper", openalex_id: "W222", authors: [{ family: "Lee", given: "Ann", name: "Ann Lee" }], cited_by_count: 12 });
    const { calls } = mockFetch([
      { method: "GET", path: "/health", handler: () => health },
      { method: "GET", path: "/papers/lookup", handler: (url) => (url.searchParams.get("id") === "10.1/ref"
        ? { query: "x", query_type: "doi", paper: card({ record: ref }), candidates: [], message: "" }
        : { query: "x", query_type: "doi", paper: card(), candidates: [], message: "" }) },
      { method: "GET", path: "/papers/W2919115771/references", handler: () => ({ kind: "references", openalex_id: "W2919115771", page: 1, per_page: 10, total: 1, items: [ref] }) },
    ]);
    const user = userEvent.setup();
    render(<App />);
    await openFinder(user);
    await user.type(screen.getByLabelText("DOI, link or title"), "10.1038/nature14539");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    await screen.findByRole("heading", { name: "Deep learning" });

    expect(calls.some((c) => c.url.pathname.includes("/references"))).toBe(false); // not fetched until the tab is opened
    await user.click(screen.getByRole("button", { name: "References" }));
    await screen.findByRole("button", { name: "A referenced paper" });
    expect(screen.getByText(/1 references/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "A referenced paper" }));
    await screen.findByRole("heading", { name: "A referenced paper" });
  });

  it("searches with filters, merges results and lets you add papers to the export list", async () => {
    const a = record({ title: "Deep learning in medicine", doi: "10.1/a", is_oa: true, cited_by_count: 5 });
    const b = record({ title: "Retracted study", doi: "10.1/b", is_oa: false, retraction: { status: "retracted", notices: [{ type: "retraction", source: "openalex" }] } });
    const { calls } = mockFetch([
      { method: "GET", path: "/health", handler: () => health },
      { method: "GET", path: "/papers/search", handler: () => ({ query: "deep", page: 1, per_page: 10, total: 2, items: [a, b], warnings: ["CrossRef did not respond; showing OpenAlex results only."] }) },
    ]);
    const user = userEvent.setup();
    render(<App />);
    await openFinder(user);
    await user.click(screen.getByRole("button", { name: "Search papers" }));
    await user.type(screen.getByLabelText("Search papers", { selector: "input" }), "deep learning");
    await user.type(screen.getByPlaceholderText("2018"), "2019");
    await user.click(screen.getByLabelText(/Open access only/));
    await user.selectOptions(screen.getByLabelText("Sort"), "cited_by");
    await user.click(screen.getByRole("button", { name: "Search", exact: true } as never));

    await screen.findByRole("button", { name: "Deep learning in medicine" });
    const params = calls.find((c) => c.url.pathname === "/papers/search")!.url.searchParams;
    expect(params.get("q")).toBe("deep learning");
    expect(params.get("year_from")).toBe("2019");
    expect(params.get("oa")).toBe("true");
    expect(params.get("sort")).toBe("cited_by");
    expect(screen.getByText(/CrossRef did not respond/)).toBeInTheDocument();
    expect(screen.getByText("Retracted")).toBeInTheDocument(); // retraction badge on the list item

    const items = screen.getAllByRole("listitem");
    await user.click(within(items[0]).getByRole("button", { name: "Add to export list" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Export list (1)" })).toBeInTheDocument());
    expect(within(items[0]).getByRole("button", { name: "✓ In export list" })).toBeInTheDocument();
  });

  it("shows a helpful message when a title search finds candidates only", async () => {
    mockFetch([
      { method: "GET", path: "/health", handler: () => health },
      { method: "GET", path: "/papers/lookup", handler: () => ({ query: "q", query_type: "title", paper: null, candidates: [record({ title: "Candidate one" })], message: "" }) },
    ]);
    const user = userEvent.setup();
    render(<App />);
    await openFinder(user);
    await user.type(screen.getByLabelText("DOI, link or title"), "Deep learning LeCun");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    await screen.findByText("Pick a paper");
    expect(screen.getByRole("button", { name: "Candidate one" })).toBeInTheDocument();
  });
});

describe("Paper Finder layout, tabs and year filters", () => {
  const year = new Date().getFullYear();
  const lookupOk = { method: "GET", path: "/papers/lookup", handler: () => ({ query: "x", query_type: "doi", paper: card(), candidates: [], message: "" }) };

  it("is centred like a search engine until there is something to show, then moves to the top", async () => {
    mockFetch([{ method: "GET", path: "/health", handler: () => health }, lookupOk]);
    const user = userEvent.setup();
    render(<App />);
    await openFinder(user);
    const finder = () => document.querySelector(".finder-card")!;
    expect(finder()).toHaveClass("hero");
    expect(screen.getByRole("heading", { name: "Find a paper" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("DOI, link or title"), "10.1038/nature14539");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    await screen.findByRole("heading", { name: "Deep learning" });
    expect(finder()).not.toHaveClass("hero");
    expect(screen.queryByRole("heading", { name: "Find a paper" })).not.toBeInTheDocument();
  });

  it("keeps lookup results in the lookup tab only, and restores them when you come back", async () => {
    mockFetch([{ method: "GET", path: "/health", handler: () => health }, lookupOk]);
    const user = userEvent.setup();
    render(<App />);
    await openFinder(user);
    await user.type(screen.getByLabelText("DOI, link or title"), "10.1038/nature14539");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    await screen.findByRole("heading", { name: "Deep learning" });

    await user.click(screen.getByRole("button", { name: "Search papers" }));
    expect(screen.queryByRole("heading", { name: "Deep learning" })).not.toBeInTheDocument();
    expect(document.querySelector(".finder-card")).toHaveClass("hero"); // search has nothing to show yet

    await user.click(screen.getByRole("button", { name: "Look up a DOI, link or title" }));
    expect(screen.getByRole("heading", { name: "Deep learning" })).toBeInTheDocument();
  });

  it("validates the year filters and blocks the search while one is wrong", async () => {
    const { calls } = mockFetch([
      { method: "GET", path: "/health", handler: () => health },
      { method: "GET", path: "/papers/search", handler: () => ({ query: "deep", page: 1, per_page: 10, total: 0, items: [], warnings: [] }) },
    ]);
    const user = userEvent.setup();
    render(<App />);
    await openFinder(user);
    await user.click(screen.getByRole("button", { name: "Search papers" }));
    await user.type(screen.getByLabelText("Search papers", { selector: "input" }), "deep learning");
    const from = screen.getByPlaceholderText("2018");
    const to = screen.getByPlaceholderText(String(year));
    const searchBtn = () => screen.getByRole("button", { name: "Search", exact: true } as never);

    // a half-typed year is not flagged while typing, only on leaving the box
    await user.type(from, "20");
    expect(screen.queryByText("Enter a 4-digit year")).not.toBeInTheDocument();
    await user.tab();
    expect(screen.getByText("Enter a 4-digit year")).toBeInTheDocument();
    expect(from).toHaveAttribute("aria-invalid", "true");
    expect(searchBtn()).toBeDisabled();

    // out of range
    await user.clear(from);
    await user.type(from, "1850");
    expect(screen.getByText(`Year must be between 1900 and ${year}`)).toBeInTheDocument();
    await user.clear(from);
    await user.type(from, String(year + 1));
    expect(screen.getByText(`Year must be between 1900 and ${year}`)).toBeInTheDocument();

    // "To" earlier than "From": the error sits under "To"
    await user.clear(from);
    await user.type(from, "2020");
    await user.type(to, "2015");
    const toError = screen.getByText("'To' can't be earlier than 'From'");
    expect(toError).toHaveAttribute("id", "year-to-error");
    expect(to).toHaveAttribute("aria-invalid", "true");
    expect(searchBtn()).toBeDisabled();
    expect(calls.some((c) => c.url.pathname === "/papers/search")).toBe(false);

    // fixed: the search runs with both years
    await user.clear(to);
    await user.type(to, "2024");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(searchBtn());
    await screen.findByText(/No papers matched/);
    const params = calls.find((c) => c.url.pathname === "/papers/search")!.url.searchParams;
    expect(params.get("year_from")).toBe("2020");
    expect(params.get("year_to")).toBe("2024");
  });

  it("pressing Search reveals a half-typed year instead of searching", async () => {
    const { calls } = mockFetch([{ method: "GET", path: "/health", handler: () => health }]);
    const user = userEvent.setup();
    render(<App />);
    await openFinder(user);
    await user.click(screen.getByRole("button", { name: "Search papers" }));
    await user.type(screen.getByLabelText("Search papers", { selector: "input" }), "deep learning");
    await user.type(screen.getByPlaceholderText("2018"), "199{Enter}");
    expect(screen.getByText("Enter a 4-digit year")).toBeInTheDocument();
    expect(calls.some((c) => c.url.pathname === "/papers/search")).toBe(false);
  });

  it("joins the citation style and the copy button into one 'Cite as' control", async () => {
    const { calls } = mockFetch([
      { method: "GET", path: "/health", handler: () => health }, lookupOk,
      { method: "POST", path: "/papers/format", handler: () => ({ text: "LeCun, Y. … Deep learning." }) },
    ]);
    const user = userEvent.setup();
    render(<App />);
    await openFinder(user);
    await user.type(screen.getByLabelText("DOI, link or title"), "10.1038/nature14539");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    await screen.findByRole("heading", { name: "Deep learning" });

    const cite = screen.getByRole("group", { name: "Cite as" });
    await user.selectOptions(within(cite).getByLabelText("Citation style"), "ieee");
    await user.click(within(cite).getByRole("button", { name: "Copy citation" }));
    await screen.findByText("LeCun, Y. … Deep learning.");
    const body = calls.find((c) => c.url.pathname === "/papers/format")!.body as { style: string };
    expect(body.style).toBe("ieee");
    // "Add to export list" is no longer wedged between the style and the copy button
    expect(within(cite).queryByRole("button", { name: /export list/ })).not.toBeInTheDocument();
  });
});
