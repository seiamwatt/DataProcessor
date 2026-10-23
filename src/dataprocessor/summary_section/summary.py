import os

import pandas as pd

YEAR_COLS = ("year","report_year")
REQUIRED_COLS = ("org","domain")

# years outside this window are out of scope
MIN_YEAR = 1995
MAX_YEAR = 2026

# streak lengths counted in the stats file, an org counts if its longest streak is at least this
STREAKS = (5,10,15,20,30)

def load_csv(file_path):
    try:
        file = pd.read_csv(file_path,encoding="utf-8")
        return file
    except Exception as e:
        print(e)
        return None

def find_year_col(df):
    for c in YEAR_COLS:
        if c in df.columns:
            return c
    return None

# org -> domain straight from the input, first non blank domain wins
def org_domains(df):
    pair = df[["org","domain"]].dropna().astype(str)
    out = {}
    for org,domain in zip(pair["org"],pair["domain"]):
        if domain.strip():
            out.setdefault(org,domain.strip())
    return out

# longest run of 1s in a row of year flags
def longest_run(flags):
    best = 0
    cur = 0
    for f in flags:
        if f:
            cur += 1
        else:
            cur = 0
        best = max(best,cur)
    return best

# output name -> name of the stats file next to it, matrix.csv -> matrix_stats.csv
def stats_path(output_path):
    base,ext = os.path.splitext(output_path)
    return f"{base}_stats{ext or '.csv'}"

# start year -> how many orgs start there, then streak length -> how many orgs reach it
def build_stats(m,year_cols):
    rows = []

    # first year each org has a report, gap years in between kept at 0
    first = [year_cols[list(row).index(1)] for row in m[year_cols].values]
    starts = pd.Series(first).value_counts()
    for y in year_cols:
        rows.append(["Start Year",y,int(starts.get(y,0))])

    for n in STREAKS:
        rows.append(["Consecutive Yrs",f"{n}+",int((m["Consecutive Yrs"] >= n).sum())])

    return pd.DataFrame(rows,columns=["Stat","Value","Orgs"])

# org x year matrix, 1 = at least one report that year, 0 = none
# returns (matrix, stats file, run stats)
def build_matrix(df,year_col):
    d = df.copy()

    # drop blank / non numeric years
    d["ry"] = pd.to_numeric(d[year_col],errors="coerce")
    dropped = int(d["ry"].isna().sum())
    d = d.dropna(subset=["ry"])
    if d.empty:
        raise ValueError(f"no row in `{year_col}` holds a usable year")
    d["ry"] = d["ry"].astype(int)

    # drop years outside the window
    in_range = d["ry"].between(MIN_YEAR,MAX_YEAR)
    out_of_range = int((~in_range).sum())
    d = d[in_range]
    if d.empty:
        raise ValueError(f"no row falls in {MIN_YEAR}-{MAX_YEAR}")

    # presence not counts, and fill in missing years so there are no gaps
    m = (pd.crosstab(d["org"],d["ry"]) > 0).astype(int)
    m = m.reindex(columns=range(d["ry"].min(),d["ry"].max() + 1),fill_value=0)
    m.index.name = "Name"
    m.columns = [str(c) for c in m.columns]

    year_cols = list(m.columns)
    m["Org Sum"] = m[year_cols].sum(axis=1)
    m["Consecutive Yrs"] = [longest_run(row) for row in m[year_cols].values]

    # bottom row: orgs per year, then unique years covered (gap years don't count)
    # and the longest streak of covered years
    year_totals = m[year_cols].sum(axis=0)
    covered = (year_totals > 0).astype(int).values
    unique_years = int(covered.sum())

    out = m.copy()
    out.loc["Yrly Sum"] = list(year_totals.values) + [unique_years,longest_run(covered)]

    # org goes from the index to the Name column, domain next to it
    out = out.reset_index()
    domains = org_domains(df)
    org_domain = [domains.get(str(org),"") for org in m.index]
    out.insert(1,"domain",org_domain + [""])

    reports = int(m["Org Sum"].sum())
    stats = {
        "unique_years": unique_years,
        "longest_run": longest_run(covered),
        "reports": reports,
        "duplicates": len(d) - reports,
        "orgs": len(m),
        "no_domain": org_domain.count(""),
        "first_year": year_cols[0],
        "last_year": year_cols[-1],
        "year_columns": len(year_cols),
        "dropped_undated": dropped,
        "dropped_out_of_range": out_of_range,
    }
    return out,build_stats(m,year_cols),stats
