"""Fabrication-pattern detector (F12).

Local rules run on every citation. Network rules (journal range checks, prefix registration, author
existence) run only for citations that did not verify cleanly. Cluster rules look across the whole
bibliography. Every rule produces a HeuristicFlag; flags lower the confidence score, they never decide
a verdict on their own.
"""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from ..schemas import CitationResult, HeuristicFlag, ParsedCitation
from ..utils.text import first_int, normalize
from .adapters.crossref import CrossRefAdapter
from .adapters.datacite import DataCiteAdapter
from .adapters.openalex import OpenAlexAdapter
from .http_client import RequestContext
from .matching import abbreviation_similarity, similarity
from .parsing import looks_like_template
from .scoring import COVERAGE_CODE

# Publisher brand -> DOI prefix. Used to spot "IEEE journal, non-IEEE DOI" combinations.
BRAND_PREFIXES = {"ieee": "10.1109", "acm": "10.1145", "plos": "10.1371"}


_NAME_STOP = {"the", "and", "of", "in", "on", "for", "a"}


def same_journal_name(a: str, b: str) -> bool:
    """Identical after ignoring case, accents, punctuation and filler words ('The', 'and', 'of' ...)."""
    ta = [t for t in normalize(a).split() if t not in _NAME_STOP]
    tb = [t for t in normalize(b).split() if t not in _NAME_STOP]
    return bool(ta) and ta == tb


def _placeholder_suffix(suffix: str) -> bool:
    runs = re.findall(r"\d{5,}", suffix)
    for run in runs:
        if len(set(run)) == 1:  # 00000, 11111
            return True
        if len(run) >= 6 and all((int(run[i + 1]) - int(run[i])) % 10 == 1 for i in range(len(run) - 1)):  # 1234567
            return True
    return False


def local_flags(parsed: ParsedCitation, now_year: Optional[int] = None) -> List[HeuristicFlag]:
    flags: List[HeuristicFlag] = []
    now = now_year or datetime.now().year
    if looks_like_template(parsed.raw):
        flags.append(HeuristicFlag(
            code="template_placeholder", severity="high",
            message="This looks like placeholder text from a citation style guide (for example “Author, A. A.” or “Title of article”), not a real reference",
        ))
    if parsed.doi:
        m = re.match(r"^(10\.\d{4,9})/(.+)$", parsed.doi)
        if not m:
            flags.append(HeuristicFlag(code="doi_syntax", severity="medium", message="The DOI is not syntactically valid"))
        else:
            prefix, suffix = m.group(1), m.group(2)
            if len(suffix) < 3:
                flags.append(HeuristicFlag(code="doi_syntax", severity="low", message="The DOI suffix is unusually short"))
            if prefix != "10.48550" and _placeholder_suffix(suffix):  # 10.48550 = arXiv DOIs, which end in an arXiv id
                flags.append(HeuristicFlag(code="doi_placeholder", severity="medium", message="The DOI suffix looks like a placeholder number (for example a run such as 1234567)"))
            if parsed.journal:
                words = set(normalize(parsed.journal).split())
                for brand, expected in BRAND_PREFIXES.items():
                    if brand in words and prefix != expected:
                        flags.append(HeuristicFlag(
                            code="prefix_journal_mismatch", severity="medium",
                            message=f"The journal name suggests {brand.upper()} but the DOI prefix {prefix} is not {brand.upper()}'s ({expected})",
                        ))
                        break
    if parsed.year:
        if parsed.year > now + 1:
            flags.append(HeuristicFlag(code="year_future", severity="high", message=f"The publication year {parsed.year} is in the future"))
        elif parsed.year < 1665:
            flags.append(HeuristicFlag(code="year_implausible", severity="medium", message=f"The publication year {parsed.year} predates scholarly journals"))
    return flags


async def network_flags(
    parsed: ParsedCitation,
    crossref: CrossRefAdapter,
    openalex: OpenAlexAdapter,
    ctx: RequestContext,
    doi_not_found: bool,
    skip_journal: bool = False,
    datacite: Optional[DataCiteAdapter] = None,
) -> List[HeuristicFlag]:
    """Checks that need data from CrossRef / OpenAlex. Failures are swallowed: no data, no flag.

    With `datacite`, a DOI prefix that CrossRef does not know is also looked up there (arXiv, Zenodo and figshare DOIs are
    registered with DataCite) and is only flagged when neither registration agency has it.
    """
    flags: List[HeuristicFlag] = []

    async def prefix_rule() -> None:
        if not (parsed.doi and doi_not_found):
            return
        prefix = parsed.doi.split("/")[0]
        resp = await crossref.prefix_registered(prefix)
        ctx.log(crossref.label, resp.query, "Match found" if resp.status == "ok" else ("No record" if resp.status == "not_found" else f"Source unavailable ({resp.error})"))
        if resp.status != "not_found":
            return
        if datacite is None:
            flags.append(HeuristicFlag(
                code="prefix_not_registered", severity="medium",
                message=f"The DOI prefix {prefix} is not registered with CrossRef (it may belong to another registration agency, or not exist)",
            ))
            return
        dc = await datacite.prefix_registered(prefix)
        ctx.log(datacite.label, dc.query, "Match found" if dc.status == "ok" else ("No record" if dc.status == "not_found" else f"Source unavailable ({dc.error})"))
        if dc.status == "not_found":  # an unavailable DataCite proves nothing, so only a definite "no" raises the flag
            flags.append(HeuristicFlag(
                code="prefix_not_registered", severity="medium",
                message=f"The DOI prefix {prefix} is not registered with CrossRef or DataCite (it may belong to another registration agency, or not exist)",
            ))

    async def journal_rules() -> None:
        if not parsed.journal or skip_journal:
            return
        resp = await crossref.find_journal(parsed.journal)
        ctx.log(crossref.label, resp.query, "Match found" if resp.status == "ok" else ("No record" if resp.status == "not_found" else f"Source unavailable ({resp.error})"))
        if resp.status == "unavailable":
            return
        best_sim, best = 0.0, None
        for rec in resp.records:
            s = max(similarity(parsed.journal, rec.journal), abbreviation_similarity(parsed.journal, rec.journal or ""))
            if s > best_sim:
                best_sim, best = s, rec
        if best is None or best_sim < 0.75:
            flags.append(HeuristicFlag(code="journal_unknown", severity="low", message=f"No journal with a name similar to “{parsed.journal}” was found in CrossRef"))
            return
        exact = same_journal_name(parsed.journal, best.journal or "") or abbreviation_similarity(parsed.journal, best.journal or "") >= 0.99
        if not exact:
            flags.append(HeuristicFlag(code="journal_near_miss", severity="medium", message=f"The journal name resembles “{best.journal}” but is not identical to it"))
            return
        if not best.issn:
            return
        issn = best.issn[0]
        oldest, newest = await asyncio.gather(crossref.journal_edge(issn, newest=False), crossref.journal_edge(issn, newest=True))
        for r in (oldest, newest):
            ctx.log(crossref.label, r.query, "Match found" if r.status == "ok" else ("No record" if r.status == "not_found" else f"Source unavailable ({r.error})"))
        first = oldest.records[0] if oldest.records else None
        last = newest.records[0] if newest.records else None
        if parsed.year and first and first.year and parsed.year < first.year:
            flags.append(HeuristicFlag(
                code="year_before_journal_start", severity="medium",
                message=f"The earliest article CrossRef holds for “{best.journal}” is from {first.year}, but the citation says {parsed.year}",
            ))
        vol, last_vol = first_int(parsed.volume), first_int(last.volume if last else None)
        if vol and last_vol and vol > last_vol + 2:
            flags.append(HeuristicFlag(
                code="volume_exceeds_range", severity="high",
                message=f"Volume {vol} is higher than the latest volume ({last_vol}) CrossRef holds for “{best.journal}”",
            ))

    async def author_rule() -> None:
        if not parsed.authors:
            return
        a = parsed.authors[0]
        given = re.sub(r"[^A-Za-z\u00c0-\u024f\- ]", "", a.given or "").strip()
        if len(given.replace(" ", "")) < 3 or not a.family:  # initials only: too ambiguous to search
            return
        resp = await openalex.author_exists(f"{given} {a.family}")
        ctx.log(openalex.label, resp.query, "Match found" if resp.status == "ok" else ("No record" if resp.status == "not_found" else f"Source unavailable ({resp.error})"))
        if resp.status == "not_found":
            flags.append(HeuristicFlag(code="author_not_found", severity="low", message=f"No researcher named {given} {a.family} was found in OpenAlex"))

    await asyncio.gather(*(asyncio.create_task(_safe(c())) for c in (prefix_rule, journal_rules, author_rule)))
    return flags


async def journal_coverage_flag(
    parsed: ParsedCitation, crossref: CrossRefAdapter, ctx: RequestContext, in_doaj: bool, max_year_works: int,
) -> Optional[HeuristicFlag]:
    """Is the cited journal real but so thinly indexed that "paper not found" says little?

    Only for a citation that was not found at all. The journal counts as real when CrossRef knows it under the SAME name
    (or its standard abbreviation), or when DOAJ lists it. The flag is raised when CrossRef holds at most `max_year_works`
    articles from the cited year (or does not index the journal at all). A well-covered journal returns nothing: a paper that
    is absent from a journal CrossRef covers properly is exactly the fabrication pattern, so it stays a Ghost.
    """
    if not parsed.journal or not parsed.year or parsed.kind in ("book", "conference", "preprint"):
        return None
    resp = await crossref.find_journal(parsed.journal)
    ctx.log(crossref.label, resp.query, "Match found" if resp.status == "ok" else ("No record" if resp.status == "not_found" else f"Source unavailable ({resp.error})"))
    if resp.status == "unavailable":
        return None
    known = None
    for rec in resp.records:
        name = rec.journal or ""
        if same_journal_name(parsed.journal, name) or abbreviation_similarity(parsed.journal, name) >= 0.99:
            known = rec
            break
    if known is None and not in_doaj:
        return None  # the journal was not shown to exist: that is a different (stronger) signal, not a coverage gap
    shown = known.journal if known and known.journal else parsed.journal
    if known is None or not known.issn:
        return HeuristicFlag(
            code=COVERAGE_CODE, severity="low",
            message=f"The journal “{shown}” is listed in DOAJ but was not found in CrossRef under that name, so a missing paper is weak evidence of fabrication",
        )
    count = await crossref.journal_year_count(known.issn[0], parsed.year)
    ctx.log(crossref.label, count.query, "Match found" if count.status == "ok" else f"Source unavailable ({count.error})")
    if count.status != "ok" or count.total is None or count.total > max_year_works:
        return None
    return HeuristicFlag(
        code=COVERAGE_CODE, severity="low",
        message=f"The journal “{shown}” exists, but CrossRef holds only {count.total} article{'s' if count.total != 1 else ''} from {parsed.year}, so a missing paper is weak evidence of fabrication",
    )


async def _safe(coro) -> None:
    try:
        await coro
    except Exception:  # a failed heuristic must never break verification
        return


# ------------------------------------------------------------------------------------------
# Cluster rules (whole bibliography)
# ------------------------------------------------------------------------------------------
def _add(result: CitationResult, flag: HeuristicFlag) -> bool:
    if any(f.code == flag.code for f in result.flags):
        return False
    result.flags.append(flag)
    return True


def apply_cluster_flags(results: List[CitationResult]) -> List[int]:
    """Add bibliography-level flags. Returns the indices of results that changed."""
    suspects = [r for r in results if r.verdict in ("suspicious", "ghost")]
    changed: set[int] = set()

    by_journal: Dict[str, List[CitationResult]] = defaultdict(list)
    for r in suspects:
        if r.parsed.journal:
            by_journal[normalize(r.parsed.journal)].append(r)
    for name, group in by_journal.items():
        if name and len(group) >= 3:
            for r in group:
                if _add(r, HeuristicFlag(code="cluster_repeated_journal", severity="medium",
                                         message=f"{len(group)} unverified citations in this list cite the same journal (“{r.parsed.journal}”)")):
                    changed.add(r.index)

    by_prefix: Dict[str, List[tuple[int, CitationResult]]] = defaultdict(list)
    for r in suspects:
        if r.parsed.doi and "/" in r.parsed.doi:
            prefix = r.parsed.doi.split("/")[0]
            tail = re.search(r"(\d+)\s*$", r.parsed.doi)
            if tail:
                by_prefix[prefix].append((int(tail.group(1)), r))
    for items in by_prefix.values():
        items.sort(key=lambda t: t[0])
        run = [items[0]] if items else []
        for cur in items[1:]:
            if cur[0] - run[-1][0] <= 2 and cur[0] != run[-1][0]:
                run.append(cur)
            else:
                if len(run) >= 3:
                    for _, r in run:
                        if _add(r, HeuristicFlag(code="cluster_sequential_doi", severity="medium", message="Several unverified citations have consecutive DOI numbers")):
                            changed.add(r.index)
                run = [cur]
        if len(run) >= 3:
            for _, r in run:
                if _add(r, HeuristicFlag(code="cluster_sequential_doi", severity="medium", message="Several unverified citations have consecutive DOI numbers")):
                    changed.add(r.index)

    by_author: Dict[str, List[CitationResult]] = defaultdict(list)
    for r in suspects:
        if r.parsed.authors and r.parsed.authors[0].family:
            by_author[normalize(r.parsed.authors[0].family)].append(r)
    for fam, group in by_author.items():
        if fam and len(group) >= 3:
            for r in group:
                if _add(r, HeuristicFlag(code="cluster_repeated_author", severity="low",
                                         message=f"The same first author ({r.parsed.authors[0].family}) appears on {len(group)} unverified citations")):
                    changed.add(r.index)
    return sorted(changed)