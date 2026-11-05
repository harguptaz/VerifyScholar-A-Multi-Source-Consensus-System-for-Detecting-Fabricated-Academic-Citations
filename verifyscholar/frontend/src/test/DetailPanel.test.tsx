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

describe("ORCID authorship row", () => {
  const person = (orcid: string, name: string, matched: boolean) => ({ orcid, given: "", family: "", name, other_names: [], url: `https://orcid.org/${orcid}`, matched });

  it("shows confirmed authors with links to their ORCID profiles, cited authors first", () => {
    show(citation(7, "verified", {
      sources: [{
        source: "orcid", label: "ORCID Public API", kind: "author", status: "match", method: "doi", score: 100, fields: {},
        note: "2 of 3 cited authors list this paper on their ORCID record",
        researchers: [person("0000-0001-0000-0001", "Yann LeCun", true), person("0000-0009-0000-0009", "Priya Raman", false)],
      }],
    }));
    const row = screen.getByText("ORCID Public API").closest("tr")!;
    expect(row).toHaveTextContent("Authors confirmed");
    expect(row).toHaveTextContent("2 of 3 cited authors list this paper on their ORCID record");
    expect(screen.getByRole("link", { name: "Yann LeCun" })).toHaveAttribute("href", "https://orcid.org/0000-0001-0000-0001");
    expect(screen.getByRole("link", { name: "Priya Raman" })).toBeInTheDocument();
  });

  it("says 'Listed by others' and names them when the paper belongs to people who were not cited", () => {
    show(citation(8, "suspicious", {
      sources: [{
        source: "orcid", label: "ORCID Public API", kind: "author", status: "partial", method: "doi", fields: {},
        note: "2 researchers list this paper on ORCID; none of them is a cited author",
        researchers: [person("0000-0001-0000-0001", "Yann LeCun", false), person("0000-0001-0000-0003", "Geoffrey Hinton", false)],
      }],
    }));
    const row = screen.getByText("ORCID Public API").closest("tr")!;
    expect(row).toHaveTextContent("Listed by others");
    expect(row).toHaveTextContent("none of them is a cited author");
    expect(row.querySelector(".chip")).toHaveClass("src-partial");
    expect(screen.getByRole("link", { name: "Geoffrey Hinton" })).toHaveAttribute("href", "https://orcid.org/0000-0001-0000-0003");
    expect(row).not.toHaveTextContent("✓");
  });
});
