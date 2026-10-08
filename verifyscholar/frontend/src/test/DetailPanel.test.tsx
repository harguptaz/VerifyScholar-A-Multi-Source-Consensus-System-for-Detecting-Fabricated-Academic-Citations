import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DetailPanel } from "../components/DetailPanel";
import { ExportListProvider } from "../exportList";
import { citation } from "./helpers";

function show(result: ReturnType<typeof citation>) {
  render(
    <ExportListProvider>
      <DetailPanel result={result} onClose={() => undefined} onOpenFinder={() => undefined} />
    </ExportListProvider>,
  );
}

describe("detail panel wording", () => {
  it("does not claim 'no matching record' when a source simply did not respond", () => {
    show(citation(5, "inconclusive", {
      confidence: null, record: null, reason: "Verification could not be completed because OpenAlex did not respond.",
      parsed: { index: 5, raw: "x", authors: [], parse_method: "heuristic", kind: "conference" },
    }));
    expect(screen.getByText(/No comparison was possible/)).toBeInTheDocument();
    expect(screen.queryByText(/nothing to compare against/)).not.toBeInTheDocument();
  });

  it("says what kind of work a reference was read as, with correct grammar", () => {
    show(citation(6, "ghost", { parsed: { index: 6, raw: "x", authors: [], parse_method: "heuristic", kind: "conference" } }));
    expect(screen.getByText(/Read as a conference paper/)).toBeInTheDocument();
  });
});
