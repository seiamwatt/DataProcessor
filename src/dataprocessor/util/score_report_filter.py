#!/usr/bin/env python3
"""Score `spider.is_report_url` against the confirmed-report ground truth.

Run this after touching REPORT_KEYWORDS, the tokenizer, or the org+year rule --
the recall/precision numbers quoted in spider.py come from here.

    uv run python src/dataprocessor/util/score_report_filter.py

Ground truth is util/temp_resources/Agg_pilot.xlsx, whose `pdf_url` column
holds 452 confirmed annual reports in three shapes:

  url      -- a real URL the crawler could actually encounter
  name     -- the publisher's original filename
  renamed  -- the researcher's own "<org> <year>.pdf" filing convention

Only `url` measures what the live crawl sees; `renamed` reflects how the pilot
set was catalogued, not how anything is published on the web, so treat its
recall as a lower-value signal and never tune against it alone.

Negatives are hand-built, not sampled -- they exist to stop a "recall
improvement" that is really just the filter saying yes to everything. HARD in
particular is the adversarial set: same-site PDFs carrying BOTH the org name
and a year in the filename, which is exactly what the org+year rule keys on.
"""
import os
import re
import sys
from urllib.parse import urlparse, unquote

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dataprocessor.Spider_section.spider import is_report_url  # noqa: E402

XLSX = os.path.join(os.path.dirname(__file__), "temp_resources", "Agg_pilot.xlsx")

# Ordinary same-site PDFs a nonprofit crawl trips over.
NEGATIVES = [
    ("ACLU", "/sites/default/files/field_document/complaint_filed.pdf"),
    ("ACLU", "/files/assets/staff_bio_jane_doe.pdf"),
    ("RAND", "/content/dam/rand/pubs/research_briefs/RB9823/RAND_RB9823.pdf"),
    ("RAND", "/pubs/periodicals/rand-review/issues/2019/spring.pdf"),
    ("Cato Institute", "/sites/cato.org/files/pubs/pdf/pa-812-updated.pdf"),
    ("Cato Institute", "/files/event-flyer-2015-03-12.pdf"),
    ("Brookings", "/wp-content/uploads/2016/07/gs_20160718_press_release.pdf"),
    ("Brookings", "/wp-content/uploads/2016/06/economic-policy-brief.pdf"),
    ("Heritage Foundation", "/sites/default/files/2020-09/BG3520.pdf"),
    ("Heritage Foundation", "/static/newsletter-june.pdf"),
    ("AARP", "/content/dam/aarp/membership/benefits-discounts.pdf"),
    ("AARP", "/content/dam/aarp/research/surveys_statistics/2018-survey.pdf"),
    ("Hoover Institution", "/sites/default/files/research/docs/policy-seminar.pdf"),
    ("Mellon Foundation", "/media/filer_public/grant-guidelines.pdf"),
    ("Mellon Foundation", "/media/filer_public/form-990-2018.pdf"),
    ("Urban Institute", "/sites/default/files/publication/98765/brief.pdf"),
    ("HRW", "/sites/default/files/media_2020/03/press-statement.pdf"),
    ("ADL", "/sites/default/files/documents/assets/pdf/audit-of-incidents.pdf"),
    ("EPI", "/files/2014/testimony-before-congress.pdf"),
    ("Demos", "/sites/default/files/publications/board-minutes-2019.pdf"),
    ("SPLC", "/sites/default/files/intelligence_report_spring.pdf"),
    ("OSF", "/uploads/financial-statements-2017.pdf"),
    ("BMGF", "/media/files/employee-handbook.pdf"),
    ("AEI", "/wp-content/uploads/2016/05/working-paper-draft.pdf"),
    ("Lilly Endowment", "/wp-content/uploads/grant-application-form.pdf"),
]

# Adversarial: org name AND year in the filename, but not annual reports.
HARD = [
    ("ACLU", "/files/aclu-2019-tax-return-990.pdf"),
    ("ACLU", "/files/aclu_2018_form990.pdf"),
    ("ACLU", "/files/aclu-2020-financial-statements.pdf"),
    ("ACLU", "/files/aclu_2021_membership_survey.pdf"),
    ("RAND", "/pubs/rand_2019_testimony_ct512.pdf"),
    ("RAND", "/pubs/RAND_TR890-2005.pdf"),
    ("RAND", "/pubs/RAND_WR1234.pdf"),
    ("Cato Institute", "/files/cato-2016-policy-analysis-799.pdf"),
    ("Cato Institute", "/files/cato-handbook-2017.pdf"),
    ("Brookings", "/uploads/brookings-2015-working-paper.pdf"),
    ("Brookings", "/uploads/brookings_2020_press_kit.pdf"),
    ("Heritage Foundation", "/files/hf-2019-budget-blueprint.pdf"),
    ("Heritage Foundation", "/files/heritage-2018-index-of-freedom.pdf"),
    ("AARP", "/dam/aarp-2019-caregiving-survey.pdf"),
    ("AARP", "/dam/aarp_2020_bulletin_march.pdf"),
    ("HRW", "/media/hrw-2018-world-report-syria.pdf"),
    ("Hoover Institution", "/docs/hoover-2016-policy-seminar-agenda.pdf"),
    ("Urban Institute", "/publication/urban-institute-2017-brief-fiscal.pdf"),
    ("Mellon Foundation", "/media/mellon-2019-grant-guidelines.pdf"),
    ("EPI", "/files/epi-2014-minimum-wage-study.pdf"),
    ("Demos", "/files/demos_2019_press_release.pdf"),
    ("ADL", "/docs/adl-2020-audit-antisemitic-incidents.pdf"),
    ("SPLC", "/docs/splc-2018-intelligence-files.pdf"),
    ("AEI", "/uploads/aei-2016-working-paper-tax.pdf"),
    ("OSF", "/uploads/osf-2015-grantee-list.pdf"),
    ("BMGF", "/media/gates-foundation-2017-strategy-overview.pdf"),
    ("MacArthur Foundation", "/media/macf-2019-grant-list.pdf"),
    ("Lilly Endowment", "/files/le-2018-community-initiative.pdf"),
    ("Hudson Institute", "/files/hudson-2020-event-transcript.pdf"),
    ("CEIP", "/files/carnegie-2016-nuclear-policy-paper.pdf"),
]


def load_truth() -> list[tuple[str, str, str]]:
    """(org, path, kind) for each unique confirmed report."""
    d = pd.read_excel(XLSX, sheet_name="part2_withdupcorrect")
    d = d[["org_id", "pdf_url"]].dropna().astype(str)
    d["pdf_url"] = d["pdf_url"].str.strip()
    d = d.drop_duplicates("pdf_url")
    rows = []
    for org, raw in zip(d["org_id"], d["pdf_url"]):
        low = raw.lower()
        if low.startswith("http"):
            path, kind = unquote(urlparse(raw).path), "url"
        elif low.startswith("web.archive.org") or "/" in raw:
            path, kind = unquote(urlparse("http://" + raw).path), "url"
        else:
            path, kind = "/" + unquote(raw), "name"
        if kind == "name" and " " in raw and re.search(r"(19|20)\d{2}", raw):
            kind = "renamed"
        rows.append((org.strip(), path, kind))
    return rows


def hit(org: str, path: str) -> bool:
    return is_report_url("http://example.org" + path, org)


def main() -> None:
    rows = load_truth()
    print(f"ground truth: {len(rows)} unique confirmed reports\n")
    for kind in ("url", "name", "renamed"):
        sub = [(o, p) for o, p, k in rows if k == kind]
        h = sum(1 for o, p in sub if hit(o, p))
        print(f"  recall {kind:8s} {h:4d}/{len(sub):<4d} {100 * h / len(sub):5.1f}%")
    h = sum(1 for o, p, _ in rows if hit(o, p))
    print(f"  recall {'TOTAL':8s} {h:4d}/{len(rows):<4d} {100 * h / len(rows):5.1f}%\n")

    for label, neg in (("ordinary", NEGATIVES), ("adversarial", HARD)):
        fp = [(o, p) for o, p in neg if hit(o, p)]
        print(f"  false positives ({label}): {len(fp)}/{len(neg)}")
        for o, p in fp:
            print(f"      {o}: {p}")

    print("\n  misses on real URLs:")
    for o, p, k in rows:
        if k == "url" and not hit(o, p):
            print(f"      {o}: {p}")


if __name__ == "__main__":
    main()
