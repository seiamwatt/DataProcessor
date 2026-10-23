import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from dataprocessor.config import load_env
from dataprocessor.filter_section import report_filter_llm
from dataprocessor.filter_section import report_filter_pattern
from dataprocessor.filter_section import filter_page
from dataprocessor.analysis_section import analysis
from dataprocessor.analysis_section import analysis_ui
from dataprocessor.analysis_section import prompts
from dataprocessor.propublica_section import propublica_logic
from dataprocessor.propublica_domain_finder_section import domain_finder
from dataprocessor.Spider_section import spider
from dataprocessor.Spider_section import spider_UI
from dataprocessor.wayback_section import wayback
from dataprocessor.wayback_section import wayback_UI
from dataprocessor.links_checker_section import links_checker
from dataprocessor.links_checker_section import links_checker_UI
from dataprocessor.summary_section import summary as summary_logic

# SETUP ---------------------------------------------------------------------------
# GUI versions of the run loops in the terminal pages. They call the same logic
# functions with the same batching and CSV writes, and import the pages' own
# constants/helpers (output columns, batch wrappers) so the two can't drift.
# The only difference: progress and per-row results go to the JobContext
# instead of rich/questionary.

STATES = [
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA",
    "HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
    "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
    "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY",
    "DC","AS","GU","MP","PR","VI","ZZ"]

RPM_CHOICES = wayback_UI.RPM_CHOICES
# ---------------------------------------------------------------------------------


# HELPERS -------------------------------------------------------------------------
def clean_path(raw):
    """Strip quotes and drag-and-drop escaped spaces, expand ~."""
    return os.path.expanduser(str(raw or "").strip().strip("'\"").replace("\\ ", " "))


def to_int(params, key, default=None):
    raw = params.get(key)
    if raw in (None, ""):
        if default is None:
            raise ValueError(f"'{key}' is required.")
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        raise ValueError(f"'{key}' must be a whole number, got '{raw}'.")


def require_csv(loader, path):
    if not path:
        raise ValueError("Choose an input CSV file.")
    if not os.path.isfile(path):
        raise ValueError(f"File not found: {path}")
    df = loader(path)
    if df is None:
        raise ValueError(f"Could not read CSV: {path}")
    return df


def require_column(df, col):
    if col not in df.columns:
        raise ValueError(f"Column '{col}' is not in the file. Available: {', '.join(map(str, df.columns))}")


def output_beside(input_path, output, folder_suffix=None):
    """Same rule as the terminal pages: a relative output lands next to the input.
    With folder_suffix, a folder output becomes <folder>/<input name><suffix>.csv."""
    output = clean_path(output)
    if not output:
        raise ValueError("Choose an output CSV file.")
    path = os.path.join(os.path.dirname(input_path), output)
    if folder_suffix and os.path.isdir(path):
        input_name = os.path.splitext(os.path.basename(input_path))[0]
        return os.path.join(path, f"{input_name}{folder_suffix}.csv")
    return path


def out_folder(raw, csv_path):
    out_dir = clean_path(raw) or os.path.join(os.path.dirname(csv_path), "reports")
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        raise ValueError(f"{out_dir} exists and is not a folder.")
    return out_dir


def row_range(params, n, strict=False):
    start = to_int(params, "start_row", 0)
    end = to_int(params, "end_row", n)
    if strict and not 0 <= start < end <= n:
        raise ValueError(f"Row range must satisfy 0 ≤ start < end ≤ {n}.")
    return start, end


def require_keys(*names):
    load_env()
    keys = {n: os.getenv(n) for n in names}
    missing = [n for n, v in keys.items() if not v]
    if missing:
        raise ValueError(f"Missing API key(s): {', '.join(missing)}. Add them in the Setup tab.")
    return keys


def append_csv(df, path, **kwargs):
    df.to_csv(path, mode="a", header=not os.path.exists(path), **kwargs)


def timed(fn, *args):
    t0 = time.time()
    result = fn(*args)
    return result, time.time() - t0


def text(value, n=90):
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return ""
    s = str(value)
    return s if len(s) <= n else s[: n - 1] + "…"


def fmt_secs(s):
    m, s = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def summary(title, items, output=None, stopped=False):
    return {"title": title, "items": items, "output": output, "stopped": stopped}


def range_item(start, done_to, stopped, label="Rows processed"):
    return [label, f"{start:,} – {done_to:,}" + (f"  (resume at {done_to})" if stopped else "")]


def manifest_rows(ctx, manifest):
    """One results row per org: document count, year span, formats."""
    if manifest is None or not len(manifest) or "org" not in manifest.columns:
        return
    for org, g in manifest.groupby("org", sort=False):
        years = pd.to_numeric(g["year"], errors="coerce").dropna() if "year" in g.columns else pd.Series(dtype=float)
        fmts = g["format"].value_counts().to_dict() if "format" in g.columns else {}
        ctx.row({
            "org": text(org, 60),
            "domain": text(g["domain"].dropna().iloc[0], 40) if "domain" in g.columns and g["domain"].notna().any() else "",
            "docs": len(g),
            "years": f"{int(years.min())}–{int(years.max())}" if len(years) else "",
            "formats": " ".join(f"{k}:{v}" for k, v in fmts.items()),
        })


def crawl_ticker(ctx, progress, extra=None):
    """Mirror a spider/wayback CrawlProgress into the job every half second."""
    stop = threading.Event()

    def tick():
        while not stop.is_set():
            snap = progress.snapshot()
            if snap["total"]:
                ctx.set_total(snap["total"], "orgs")
                ctx.set_done(snap["done"])
            ctx.active(snap["active"])
            if extra:
                extra()
            stop.wait(0.5)

    threading.Thread(target=tick, daemon=True).start()
    return stop


def crawl_counts(ctx, progress, in_range):
    """Final progress + summary lines; orgs without a domain are skipped by the logic."""
    snap = progress.snapshot()
    ctx.set_total(snap["total"] or in_range, "orgs")
    ctx.set_done(snap["done"])
    items = [["Orgs crawled", f"{snap['done']:,} / {snap['total']:,}"]]
    if in_range > snap["total"]:
        items.append(["Skipped (no domain)", f"{in_range - snap['total']:,}"])
    return items
# ---------------------------------------------------------------------------------


# INSPECTORS (used by the forms) --------------------------------------------------
LOADERS = {
    "filter": report_filter_llm.load_csv,
    "analysis": analysis.load_csv,
    "domain": domain_finder.load_csv,
    "spider": spider.load_csv,
    "wayback": wayback.load_csv,
    "links": pd.read_csv,
    "summary": summary_logic.load_csv,
}


def inspect_csv(tool, path):
    path = clean_path(path)
    df = require_csv(LOADERS.get(tool, pd.read_csv), path)
    return {"path": path, "rows": len(df), "columns": [str(c) for c in df.columns]}
# ---------------------------------------------------------------------------------


# FILTER (filter_section/filter_page.py) ------------------------------------------
def run_filter(ctx, p):
    input_path = clean_path(p.get("input"))
    df = require_csv(report_filter_llm.load_csv, input_path)
    output_path = output_beside(input_path, p.get("output"))
    start_row, end_row = row_range(p, len(df))
    col_name = (p.get("column") or "url").strip()
    require_column(df, col_name)
    use_pattern = p.get("mode") == "pattern"

    if use_pattern:
        fn, extra, BATCH = report_filter_pattern.identify_reports, (), 10
    else:
        api_key = require_keys("DeepSeek_key")["DeepSeek_key"]
        fn, extra, BATCH = report_filter_llm.identify_reports, (api_key,), 100

    df_subset = df.iloc[start_row:end_row]
    ctx.set_total(len(df_subset), "rows")
    ctx.phase(f"Filtering by {'pattern' if use_pattern else 'DeepSeek LLM'} · {len(df_subset):,} rows, {BATCH} at a time → {output_path}")

    counts = {"annual": 0, "not": 0, "error": 0}
    time_start = time.time()
    processed = 0
    stopped = False

    for i in range(0, len(df_subset), BATCH):
        if ctx.cancelled:
            stopped = True
            break
        batch = df_subset.iloc[i:i + BATCH]
        results = [None] * len(batch)

        with ThreadPoolExecutor(max_workers=BATCH) as executor:
            futures = {executor.submit(timed, fn, batch.iloc[j][col_name], *extra): j for j in range(len(batch))}
            for future in as_completed(futures):
                j = futures[future]
                try:
                    result, secs = future.result()
                except Exception as e:
                    ctx.log(f"Row {start_row + i + j}: {e}", "error")
                    result, secs = None, 0
                results[j] = result
                key = "annual" if result == 1 else "not" if result == 0 else "error"
                counts[key] += 1
                src = batch.iloc[j]
                ctx.row({
                    "row": start_row + i + j,
                    "result": key,
                    "secs": round(secs, 1),
                    "org": text(src.get("org"), 40),
                    "url": text(src[col_name], 200),
                })
                ctx.advance()

        rows = []
        for j in range(len(batch)):
            result_row = batch.iloc[j].copy()
            result_row["is_annual_report"] = results[j]
            rows.append(result_row)

        batch_result = pd.DataFrame(rows)
        batch_result["is_annual_report"] = batch_result["is_annual_report"].astype("Int64")
        batch_result = batch_result.reindex(columns=filter_page.OUTPUT_COLUMNS)
        append_csv(batch_result, output_path, index=False)
        processed += len(batch)

    return summary(
        "Run stopped" if stopped else "Run complete",
        [
            ["Run ID", str(uuid.uuid4())],
            range_item(start_row, start_row + processed, stopped),
            ["Annual / impact reports", f"{counts['annual']:,}"],
            ["Not a report", f"{counts['not']:,}"],
            ["Errors", f"{counts['error']:,}"],
            ["Elapsed", fmt_secs(time.time() - time_start)],
            ["Output file", output_path],
        ],
        output_path, stopped,
    )
# ---------------------------------------------------------------------------------


# LLM ANALYSIS (analysis_section/analysis_ui.py) ----------------------------------
MODELS = [("deepseek", "DeepSeek_key"), ("gemini", "Gemini_key"), ("gpt", "GPT_key")]


def first_value(record, field):
    for prefix in analysis_ui.MODEL_PREFIXES.values():
        v = record.get(f"{prefix}_{field}")
        if v not in (None, ""):
            return v
    return ""


def run_analysis(ctx, p):
    input_path = clean_path(p.get("input"))
    df = require_csv(analysis.load_csv, input_path)
    output_path = output_beside(input_path, p.get("output"), folder_suffix="_analysis")
    start_row, end_row = row_range(p, len(df))
    col_name = (p.get("column") or "url").strip()
    require_column(df, col_name)
    BATCH = to_int(p, "batch_size", 10)
    if BATCH < 1:
        raise ValueError("Batch size must be at least 1.")

    keys = require_keys(*(k for _, k in MODELS))
    runners_by_model = {
        "deepseek": analysis.deepseek_batch,
        "gemini": analysis_ui.gemini_run,
        "gpt": analysis_ui.gpt_run,
    }

    df_subset = df.iloc[start_row:end_row]
    total_batches = (len(df_subset) + BATCH - 1) // BATCH
    ctx.set_total(len(df_subset), "rows")
    ctx.phase(f"Analysing {len(df_subset):,} rows in {total_batches:,} batches of {BATCH} · DeepSeek + Gemini batch + GPT batch")

    ok_totals = {m: 0 for m, _ in MODELS}
    time_start = time.time()
    row_track = start_row
    stopped = False

    for b, i in enumerate(range(0, len(df_subset), BATCH), 1):
        if ctx.cancelled:
            stopped = True
            break
        batch = df_subset.iloc[i:i + BATCH]
        url_links = [batch.iloc[j][col_name] for j in range(len(batch))]

        ctx.phase(f"Batch {b}/{total_batches} · downloading and extracting text for rows {row_track:,}–{row_track + len(batch):,}")
        reports = analysis.create_reports_batch(url_links)
        n_text = sum(1 for r in reports if r[1] is not None)

        # Same three parallel calls as the terminal page, plus a live status per model.
        status = {m: "running" for m, _ in MODELS}

        def show_status():
            ctx.active([{"row": f"b{b}", "lane": m, "name": status[m]} for m, _ in MODELS])

        def call(model):
            try:
                return runners_by_model[model](reports, keys[dict(MODELS)[model]])
            finally:
                status[model] = "finished"
                show_status()

        ctx.phase(f"Batch {b}/{total_batches} · {n_text}/{len(batch)} PDFs have text · waiting on the models (Gemini/GPT batch jobs can take a while)")
        show_status()
        with ThreadPoolExecutor(max_workers=3) as process:
            futures = {m: process.submit(call, m) for m, _ in MODELS}
            model_results = {}
            for m, _ in MODELS:
                try:
                    model_results[m] = futures[m].result()
                except Exception as e:
                    ctx.log(f"{m} failed: {e}", "error")
                    model_results[m] = [None] * len(reports)
        ctx.active([])

        # one output row per report, models side by side as prefixed columns
        rows = []
        for j in range(len(batch)):
            source_row = batch.iloc[j]
            result_row = {}
            for col in analysis_ui.ID_COLUMNS:
                result_row[col] = source_row.get(col, "")
            result_row["url"] = source_row[col_name]
            for model, result_rows in model_results.items():
                prefix = analysis_ui.MODEL_PREFIXES[model]
                result = result_rows[j]
                if not isinstance(result, dict):
                    result = {}
                for col in prompts.ANALYSIS_COLUMNS:
                    result_row[f"{prefix}_{col}"] = result.get(col)
            rows.append(result_row)

            no_text = reports[j][1] is None
            cell = {}
            for model, result_rows in model_results.items():
                ok = isinstance(result_rows[j], dict)
                ok_totals[model] += ok
                cell[analysis_ui.MODEL_PREFIXES[model]] = "—" if no_text else ("OK" if ok else "ERR")
            ctx.row({
                "row": row_track + j,
                **cell,
                "no_text": no_text,
                "org": text(result_row.get("org"), 40),
                "year": text(result_row.get("year"), 6),
                "activity": text(first_value(result_row, "META_PrimaryActivity"), 60),
                "intensity": " / ".join(text(result_row.get(f"{px}_OVERALL_Intensity"), 4) or "·" for px in analysis_ui.MODEL_PREFIXES.values()),
                "url": text(result_row["url"], 200),
            })

        ok_counts = [f"{m} {sum(1 for r in model_results[m] if r is not None)}/{len(batch)}" for m, _ in MODELS]
        ctx.log(f"rows {row_track:,} – {row_track + len(batch):,} → {', '.join(ok_counts)}", "info")
        row_track += len(batch)

        batch_result = pd.DataFrame(rows, columns=analysis_ui.OUTPUT_COLUMNS)
        append_csv(batch_result, output_path, index=False)
        ctx.advance(len(batch))

    processed = row_track - start_row
    return summary(
        "Run stopped" if stopped else "Run complete",
        [
            ["Run ID", str(uuid.uuid4())],
            range_item(start_row, row_track, stopped),
            *[[f"{m} answered", f"{ok_totals[m]:,} / {processed:,}"] for m, _ in MODELS],
            ["Elapsed", fmt_secs(time.time() - time_start)],
            ["Output file", output_path],
        ],
        output_path, stopped,
    )
# ---------------------------------------------------------------------------------


# PROPUBLICA ORG FINDER (propublica_section/propublica_UI.py) ---------------------
def run_propublica(ctx, p):
    out = clean_path(p.get("output"))
    if not out:
        raise ValueError("Choose an output CSV file or folder.")
    if os.path.isdir(out):
        out = os.path.join(out, "output.csv")
    elif not os.path.isfile(out):
        if not os.path.isdir(os.path.dirname(out) or "."):
            raise ValueError(f"Folder does not exist: {os.path.dirname(out)}")
        if not out.lower().endswith(".csv"):
            out += ".csv"

    ntee = str(p.get("ntee") or "").strip()
    if not ntee:
        raise ValueError("NTEE code category is required (1–10).")
    num_pages = to_int(p, "num_pages", 500)
    start_state = to_int(p, "start_state", 0)
    end_state = to_int(p, "end_state", 57)
    if not 0 <= start_state < end_state <= len(STATES):
        raise ValueError(f"State range must satisfy 0 ≤ start < end ≤ {len(STATES)}.")

    n_states = end_state - start_state
    ctx.set_total(n_states, "states")
    ctx.phase(f"Searching NTEE {ntee} · states {STATES[start_state]}–{STATES[end_state - 1]} · up to {num_pages} pages each")

    counters = {"obtained": 0, "kept": 0, "found": 0}

    # populate_data only prints, so progress is read from its output.
    def on_line(line):
        m = re.search(r"State Index: (\d+)", line)
        if m:
            i = int(m.group(1))
            ctx.set_done(i - start_state)
            ctx.phase(f"Searching {STATES[i]} ({i - start_state + 1}/{n_states})", log=False)
            return
        m = re.search(r"Found (\d+) total orgs", line)
        if m:
            counters["found"] = int(m.group(1))
            ctx.set_done(n_states)
            ctx.set_total(0, "orgs")
            ctx.phase(f"Filtering {counters['found']:,} orgs (ruling year ≤ 2000)")
            return
        if "organisation data obtained" in line:
            counters["obtained"] += 1
        elif line.startswith("Data appended:"):
            counters["kept"] += 1
        else:
            return
        ctx.phase(f"Filtering {counters['found']:,} orgs · details fetched {counters['obtained']:,} · kept {counters['kept']:,}", log=False)

    ctx.on_line(on_line)
    time_start = time.time()
    results = propublica_logic.populate_data(
        num_pages=num_pages,
        ntee_catagory_id=ntee,
        start_state_index=start_state,
        end_state_index=end_state,
    )

    if results:
        df = pd.DataFrame(results)
        df.to_csv(out, mode="a", header=not os.path.isfile(out), index=False)
        for r in results:
            ctx.row({k: text(v, 60) for k, v in r.items()})
    else:
        ctx.log("No results were found for this configuration. Nothing was written.", "warn")

    return summary(
        "Run complete" if results else "No results",
        [
            ["Orgs found in search", f"{counters['found']:,}"],
            ["Orgs kept (ruled ≤ 2000)", f"{len(results or []):,}"],
            ["Elapsed", fmt_secs(time.time() - time_start)],
            ["Output file", out if results else "(nothing written)"],
        ],
        out if results else None,
    )
# ---------------------------------------------------------------------------------


# PROPUBLICA DOMAIN FINDER (propublica_domain_finder_section/domain_finder_UI.py) --
def run_domain(ctx, p):
    input_path = clean_path(p.get("input"))
    df = require_csv(domain_finder.load_csv, input_path)
    output_path = output_beside(input_path, p.get("output"))
    col_name = (p.get("column") or "ein").strip()
    require_column(df, col_name)
    start_row, end_row = row_range(p, len(df))
    max_filings = to_int(p, "max_filings", 3)
    name_col = next((c for c in df.columns if str(c).lower() in ("name", "org", "org_name", "organization")), None)

    df_subset = df.iloc[start_row:end_row]
    BATCH = 30
    ctx.set_total(len(df_subset), "orgs")
    ctx.phase(f"Finding domains for {len(df_subset):,} orgs · up to {max_filings} Form 990 filings each")

    found_count = 0
    processed = 0
    stopped = False
    time_start = time.time()

    for i in range(0, len(df_subset), BATCH):
        if ctx.cancelled:
            stopped = True
            break
        batch = df_subset.iloc[i:i + BATCH]
        domains = [None] * len(batch)

        with ThreadPoolExecutor(max_workers=BATCH) as executor:
            futures = {executor.submit(timed, domain_finder.find_domain, batch.iloc[j][col_name], max_filings): j
                       for j in range(len(batch))}
            for future in as_completed(futures):
                j = futures[future]
                try:
                    domain, secs = future.result()
                except Exception as e:
                    ctx.log(f"Row {start_row + i + j}: {e}", "error")
                    domain, secs = None, 0
                domains[j] = domain
                found_count += bool(domain)
                ctx.row({
                    "row": start_row + i + j,
                    "ein": text(batch.iloc[j][col_name], 20),
                    "name": text(batch.iloc[j][name_col], 50) if name_col else "",
                    "domain": domain or "",
                    "secs": round(secs, 1),
                })
                ctx.advance()

        rows = []
        for j in range(len(batch)):
            result_row = batch.iloc[j].copy()
            result_row["domain"] = domains[j]
            rows.append(result_row)
        append_csv(pd.DataFrame(rows), output_path, index=False)
        processed += len(batch)

    return summary(
        "Run stopped" if stopped else "Run complete",
        [
            ["Run ID", str(uuid.uuid4())],
            range_item(start_row, start_row + processed, stopped),
            ["Domains found", f"{found_count:,} / {processed:,}" + (f"  ({found_count / processed:.0%})" if processed else "")],
            ["Elapsed", fmt_secs(time.time() - time_start)],
            ["Output file", output_path],
        ],
        output_path, stopped,
    )
# ---------------------------------------------------------------------------------


# SPIDER (Spider_section/spider_UI.py) --------------------------------------------
def run_spider(ctx, p):
    csv_path = clean_path(p.get("input"))
    orgs_df = require_csv(spider.load_csv, csv_path)
    if len(orgs_df) == 0:
        raise ValueError("The CSV file contains no rows.")
    out_dir = out_folder(p.get("out_dir"), csv_path)
    depth = to_int(p, "depth", 3)
    start_row, end_row = row_range(p, len(orgs_df), strict=True)
    sources = spider_UI.DEFAULT_SOURCES

    os.makedirs(out_dir, exist_ok=True)
    progress = spider.CrawlProgress()
    ctx.set_total(end_row - start_row, "orgs")
    ctx.phase(f"Crawling live sites for rows {start_row}–{end_row} · depth {depth} → {out_dir}")

    time_start = time.time()
    stop = crawl_ticker(ctx, progress)
    try:
        manifest = spider.populate_data(
            orgs_df=orgs_df, out_dir=out_dir, sources=sources, depth=depth,
            start_row=start_row, end_row=end_row, progress=progress,
        )
    finally:
        stop.set()
        ctx.active([])

    manifest_rows(ctx, manifest)
    n_docs = 0 if manifest is None else len(manifest)
    paths = [os.path.join(out_dir, spider.manifest_name(s)) for s in sources]
    return summary(
        "Crawl complete" if n_docs else "Finished — no documents found",
        [
            *crawl_counts(ctx, progress, end_row - start_row),
            ["Documents", f"{n_docs:,}"],
            ["Elapsed", fmt_secs(time.time() - time_start)],
            *[[f"Manifest ({s})", m if os.path.exists(m) else "nothing collected"] for s, m in zip(sources, paths)],
        ],
        next((m for m in paths if os.path.exists(m)), out_dir),
    )
# ---------------------------------------------------------------------------------


# WAYBACK (wayback_section/wayback_UI.py) -----------------------------------------
def run_wayback(ctx, p):
    csv_path = clean_path(p.get("input"))
    orgs_df = require_csv(wayback.load_csv, csv_path)
    if len(orgs_df) == 0:
        raise ValueError("The CSV file contains no rows.")
    out_dir = out_folder(p.get("out_dir"), csv_path)
    years = to_int(p, "years", 0)
    rpm = to_int(p, "rpm", next(iter(RPM_CHOICES.values())))
    start_row, end_row = row_range(p, len(orgs_df), strict=True)
    links_only = not p.get("download", False)
    workers = wayback.WaybackConfig(requests_per_minute=rpm).effective_workers

    os.makedirs(out_dir, exist_ok=True)
    limiter = wayback.RateLimiter(rpm)
    progress = wayback.CrawlProgress()
    ctx.set_total(end_row - start_row, "orgs")
    window = "whole archive (1996–now)" if years == 0 else f"last {years} years"
    base_phase = f"Sweeping the archive for rows {start_row}–{end_row} · {window} · {rpm} req/min · {workers} workers"
    ctx.phase(base_phase)

    def rate():
        s = limiter.stats()
        line = f"archive {s['in_window']}/{s['rpm_limit']} per min · {s['total']:,} requests"
        if s["paused_for"] > 0:
            line += f" · paused {s['paused_for']:.0f}s (archive pushed back)"
        elif s["throttled"]:
            line += f" · throttled {s['throttled']}x"
        ctx.phase(line, log=False)

    time_start = time.time()
    stop = crawl_ticker(ctx, progress, extra=rate)
    try:
        manifest = wayback.populate_data(
            orgs_df=orgs_df, out_dir=out_dir, years=years,
            start_row=start_row, end_row=end_row, links_only=links_only,
            requests_per_minute=rpm, max_workers=workers,
            progress=progress, limiter=limiter,
        )
    finally:
        stop.set()
        ctx.active([])

    manifest_rows(ctx, manifest)
    n_docs = 0 if manifest is None else len(manifest)
    s = limiter.stats()
    path = os.path.join(os.path.abspath(out_dir), wayback.MANIFEST_NAME)
    items = [
        *crawl_counts(ctx, progress, end_row - start_row),
        ["Documents", f"{n_docs:,}"],
        ["Archive requests", f"{s['total']:,} at {s['rpm_limit']}/min" + (f" · {s['throttled']} throttled" if s["throttled"] else "")],
        ["Elapsed", fmt_secs(time.time() - time_start)],
        ["Manifest", path if os.path.exists(path) else "nothing collected"],
    ]
    if manifest is not None and "format" in manifest.columns:
        items.insert(2, ["By format", "  ".join(f"{k} {v}" for k, v in manifest["format"].value_counts().items())])
    return summary(
        "Sweep complete" if n_docs else "Finished — no archived documents found",
        items,
        path if os.path.exists(path) else out_dir,
    )
# ---------------------------------------------------------------------------------


# LINK CHECKER (links_checker_section/links_checker_UI.py) ------------------------
def run_links(ctx, p):
    SOURCE_COL = links_checker_UI.SOURCE_COL
    RESULT_COL = links_checker_UI.RESULT_COL

    input_path = clean_path(p.get("input"))
    df = require_csv(pd.read_csv, input_path)
    output_path = output_beside(input_path, p.get("output"), folder_suffix="_links")
    start_row, end_row = row_range(p, len(df))
    col_name = (p.get("column") or "url").strip()
    BATCH = to_int(p, "batch_size", 5)
    missing_cols = [c for c in (col_name, SOURCE_COL) if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing column(s): {', '.join(missing_cols)}. Available: {', '.join(map(str, df.columns))}")
    if BATCH < 1:
        raise ValueError("Batch size must be at least 1.")

    df_subset = df.iloc[start_row:end_row]
    ctx.set_total(len(df_subset), "links")
    ctx.phase(f"Checking {len(df_subset):,} links, {BATCH} at a time · wayback links capped at 50/min")

    # shared across batches so the wayback budget holds for the whole run
    limiter = links_checker.rate_limiter(window_size=60, max_request=50)
    counts = {"dead": 0, "live": 0, "unknown": 0}
    row_track = start_row
    stopped = False
    time_start = time.time()

    for i in range(0, len(df_subset), BATCH):
        if ctx.cancelled:
            stopped = True
            break
        batch = df_subset.iloc[i:i + BATCH]
        futures = []
        with ThreadPoolExecutor(max_workers=BATCH) as executor:
            for j in range(len(batch)):
                url = batch.iloc[j][col_name]
                source = batch.iloc[j][SOURCE_COL]
                if source == "wayback":
                    while not limiter.allow_request():
                        time.sleep(1)
                futures.append(executor.submit(timed, links_checker.check_dead, url, source, limiter))

        rows = []
        for j in range(len(batch)):
            result_row = batch.iloc[j].copy()
            try:
                dead, secs = futures[j].result()
            except Exception as e:
                ctx.log(f"row {row_track + j:,} → {e}", "error")
                dead, secs = None, 0
            result_row[RESULT_COL] = dead
            rows.append(result_row)
            key = "dead" if dead is True else "live" if dead is False else "unknown"
            counts[key] += 1
            ctx.row({
                "row": row_track + j,
                "status": key,
                "source": text(batch.iloc[j][SOURCE_COL], 10),
                "secs": round(secs, 1),
                "org": text(batch.iloc[j].get("org"), 40),
                "url": text(batch.iloc[j][col_name], 200),
            })
            ctx.advance()

        batch_result = pd.DataFrame(rows)
        row_track += len(batch)
        append_csv(batch_result, output_path, index=False)

    return summary(
        "Run stopped" if stopped else "Run complete",
        [
            ["Run ID", str(uuid.uuid4())],
            range_item(start_row, row_track, stopped),
            ["Dead links", f"{counts['dead']:,}"],
            ["Live links", f"{counts['live']:,}"],
            ["Unknown (archive refused)", f"{counts['unknown']:,}"],
            ["Elapsed", fmt_secs(time.time() - time_start)],
            ["Output file", output_path],
        ],
        output_path, stopped,
    )
# ---------------------------------------------------------------------------------


# SUMMARY (summary_section/summary_UI.py) -----------------------------------------
def run_summary(ctx, p):
    input_path = clean_path(p.get("input"))
    df = require_csv(summary_logic.load_csv, input_path)
    if len(df) == 0:
        raise ValueError("The CSV file contains no rows.")
    for col in summary_logic.REQUIRED_COLS:
        require_column(df, col)
    year_col = summary_logic.find_year_col(df)
    if year_col is None:
        raise ValueError(f"No 'year' column in the file. Available: {', '.join(map(str, df.columns))}")

    # blank output → <input>_matrix.csv next to the input, same as the terminal page
    input_name = os.path.splitext(os.path.basename(input_path))[0]
    output_path = output_beside(input_path, p.get("output") or f"{input_name}_matrix.csv")
    if not output_path.lower().endswith(".csv"):
        output_path += ".csv"
    if os.path.abspath(output_path) == os.path.abspath(input_path):
        raise ValueError("The output is the input file. Pick another name.")

    ctx.phase(f"Building the org × year matrix from {len(df):,} rows")
    time_start = time.time()
    out, stats_df, stats = summary_logic.build_matrix(df, year_col)
    out.to_csv(output_path, index=False)
    stats_df.to_csv(summary_logic.stats_path(output_path), index=False)

    # one results row per org, the Yrly Sum row last
    year_cols = [c for c in out.columns if c not in ("Name", "domain", "Org Sum", "Consecutive Yrs")]
    ctx.set_total(len(out), "orgs")
    for _, r in out.iterrows():
        present = [c for c in year_cols if r[c] > 0]
        ctx.row({
            "name": text(r["Name"], 60),
            "domain": text(r["domain"], 40),
            "years": int(r["Org Sum"]),
            "streak": int(r["Consecutive Yrs"]),
            "span": f"{present[0]}–{present[-1]}" if present else "",
            "total": r["Name"] == "Yrly Sum",
        })
    ctx.set_done(len(out))

    return summary(
        "Run complete",
        [
            ["Unique years", f"{stats['unique_years']:,}"],
            ["Longest streak", f"{stats['longest_run']:,} consecutive years"],
            ["Annual reports", f"{stats['reports']:,}  ({stats['duplicates']:,} duplicate org-year rows merged)"],
            ["Orgs", f"{stats['orgs']:,}"],
            ["No domain", f"{stats['no_domain']:,} orgs"],
            ["Years", f"{stats['first_year']} – {stats['last_year']}  ({stats['year_columns']} columns)"],
            ["Rows dropped", f"{stats['dropped_undated']:,} undated, {stats['dropped_out_of_range']:,} outside {summary_logic.MIN_YEAR}-{summary_logic.MAX_YEAR}"],
            ["Elapsed", fmt_secs(time.time() - time_start)],
            ["Output file", output_path],
            ["Stats file", summary_logic.stats_path(output_path)],
        ],
        output_path,
    )
# ---------------------------------------------------------------------------------


RUNNERS = {
    "filter": run_filter,
    "analysis": run_analysis,
    "propublica": run_propublica,
    "domain": run_domain,
    "spider": run_spider,
    "wayback": run_wayback,
    "links": run_links,
    "summary": run_summary,
}

# Tools whose logic runs in one blocking call with no place to stop between steps.
NOT_CANCELLABLE = {"propublica", "spider", "wayback", "summary"}
