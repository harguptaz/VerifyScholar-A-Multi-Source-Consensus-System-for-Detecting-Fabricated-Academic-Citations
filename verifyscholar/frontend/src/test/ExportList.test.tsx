import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { mockFetch, record } from "./helpers";

const health = { status: "ok", grobid: true, libreoffice: true, embedding_backend_setting: "auto", openalex_api_key_set: true, contact_email_set: true };

describe("session export list (F36) and bibliography export (F35)", () => {
  it("survives a re-render from sessionStorage, de-duplicates, and exports with the chosen style", async () => {
    sessionStorage.setItem("verifyscholar.exportList", JSON.stringify([record({ title: "Stored paper", doi: "10.1/stored" })]));
    const { calls } = mockFetch([
      { method: "GET", path: "/health", handler: () => health },
      { method: "POST", path: "/bibliography/export", handler: () => ({ status: 200, body: "[1] formatted reference" }) },
    ]);
    // jsdom has no object URLs; the download helper needs them
    URL.createObjectURL = () => "blob:fake";
    URL.revokeObjectURL = () => undefined;

    const user = userEvent.setup();
    render(<App />);
    expect(screen.getByRole("button", { name: "Export list (1)" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Export list (1)" }));
    expect(screen.getByText("Stored paper")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Format"), "text");
    await user.selectOptions(screen.getByLabelText("Style"), "ieee");
    expect(screen.getByText(/Numbered styles keep your original citation order/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Export bibliography" }));

    const req = calls.find((c) => c.url.pathname === "/bibliography/export")!;
    const body = req.body as { format: string; style: string; extra_papers: Array<{ doi: string }>; job_id?: string };
    expect(body.format).toBe("text");
    expect(body.style).toBe("ieee");
    expect(body.extra_papers.map((p) => p.doi)).toEqual(["10.1/stored"]);
    expect(body.job_id).toBeUndefined();

    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.getByRole("button", { name: "Export list (0)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export bibliography" })).toBeDisabled();
    expect(JSON.parse(sessionStorage.getItem("verifyscholar.exportList") ?? "[]")).toEqual([]);
  });

  it("surfaces export errors from the API instead of failing silently", async () => {
    sessionStorage.setItem("verifyscholar.exportList", JSON.stringify([record()]));
    mockFetch([
      { method: "GET", path: "/health", handler: () => health },
      { method: "POST", path: "/bibliography/export", handler: () => ({ status: 400, body: { detail: "Nothing to export" } }) },
    ]);
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Export list (1)" }));
    await user.click(screen.getByRole("button", { name: "Export bibliography" }));
    expect(await screen.findByText("Nothing to export")).toBeInTheDocument();
  });
});
