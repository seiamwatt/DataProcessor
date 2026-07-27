"""
run_seed_crawl.py
-----------------
Extend a previous crawl DEEPER without re-crawling homepages, using a seed CSV
built by `prep_seed_urls.py`.

It imports the crawler machinery from spider.py (without modifying it), builds a
Crawler, enqueues every seed URL as a depth-0 live seed that expands, then runs
the BFS. With `--depth 3`, the crawler goes 3 hops out from each seed directory,
so reports sitting deeper than the original crawl reached get discovered.

Seeds CSV columns (from prep_seed_urls.py): url[, org, domain].

Because ContentStorage resumes any manifest already in the output dir and dedups
on (org, source, url), pointing --out at your previous crawl's folder appends
only the NEW finds.

Usage:
    python -m dataprocessor.util.run_seed_crawl <seeds.csv> [--out DIR] [--depth N]
    python -m dataprocessor.util.run_seed_crawl seeds.csv --out ./reports --depth 3
"""

import argparse
from dataclasses import replace

import pandas as pd
from urllib.parse import urlparse

from dataprocessor.Spider_section.spider import (
    Config, Crawler, URLFilter, WorkItem, console, registered_domain,
)


def seed_crawler(crawler: Crawler, csv_path: str) -> int:
    """Enqueue every URL in the seed CSV as a depth-0, expanding live seed.

    Mirrors what spider's live seeder does per org: register one same-site
    URLFilter per org (anchored to its domain), then push the seed WorkItem.
    Returns the number of URLs actually queued (dedup drops repeats).
    """
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    if "url" not in df.columns:
        console.print(f"[red]seed CSV {csv_path} has no 'url' column[/red]")
        return 0

    queued = 0
    for _, row in df.iterrows():
        url = (row.get("url") or "").strip()
        if not url:
            continue
        netloc = urlparse(url).netloc
        domain = (row.get("domain") or "").strip() or netloc
        org = (row.get("org") or "").strip() or registered_domain(netloc)
        # One same-site filter per org; _expand_html looks this up by org name.
        if org not in crawler._filters:
            crawler._filters[org] = URLFilter(domain, crawler.cfg.all_pdfs)
        if crawler.frontier.add(WorkItem(url, source="live", org=org,
                                         depth=0, expand=True)):
            queued += 1
    return queued


def main() -> None:
    env_cfg = Config.from_env()
    ap = argparse.ArgumentParser(
        description="Crawl deeper from a seed-URL CSV (built by prep_seed_urls.py).")
    ap.add_argument("seeds", help="Seed CSV path (columns: url[, org, domain])")
    ap.add_argument("--out", default=env_cfg.output_dir, help="Output directory")
    ap.add_argument("--depth", type=int, default=env_cfg.max_depth,
                    help="Hops to crawl out from each seed")
    ap.add_argument("--all-pdfs", action="store_true",
                    help="Keep every PDF, not just ones whose URL looks like a report")
    ap.add_argument("--download", action="store_true",
                    help="Download and save PDFs instead of only collecting links")
    ap.add_argument("--ocr", action="store_true",
                    help="OCR scanned PDFs (needs ocrmypdf+tesseract)")
    args = ap.parse_args()

    # Only the live crawl applies to URL seeds -- 990/wayback are org-based.
    cfg = replace(env_cfg, max_depth=args.depth, output_dir=args.out,
                  sources=("live",), all_pdfs=args.all_pdfs,
                  do_ocr=args.ocr, links_only=not args.download)

    crawler = Crawler(cfg)
    queued = seed_crawler(crawler, args.seeds)
    console.print(f"[bold]Seeded {queued} URL(s); crawling {args.depth} hop(s) "
                  f"out from each -> {args.out}[/bold]")
    if queued == 0:
        console.print("[yellow]Nothing to crawl.[/yellow]")
        return

    df = crawler.run()
    if df is not None:
        console.print(f"\n[green]Done. Manifest now holds {len(df)} documents.[/green]")


if __name__ == "__main__":
    main()
