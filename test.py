#!/usr/bin/env python3
"""
Daily Report Email Builder (High Priority Only) -- 'Printed' CSV format
- Parses 'Daily Log Detailed List Report' CSV (label/value pairs per row)
- Detects bare token 'High Priority' as HighPriorityBool=True
- NO DATE FILTER: includes all entries that appear in the CSV
- Outputs HTML with no bullets and without Location/Sublocation
- Adds a 'Not Categorized' section for high-priority items not matching any bucket
"""

import csv
import os
from typing import List, Optional, Dict

from numpy import empty
import pandas as pd

# ----------------------------
# CONFIGURATION
# ----------------------------

PRINTED_CSV = "searchResults.csv"   # <-- your file name here
PLAYERS_CSV = "players.csv"         # optional players table

HIGH_PRIORITY_ONLY = True  # keep True per your requirement

# Optional RED FLAG topics (still High Priority, but shown under a RED FLAGS heading)
RED_FLAG_TOPICS = {
    # e.g., "Straight Flush", "FINTRAC"
}

# ----------------------------
# PARSER FOR PRINTED EXPORT
# ----------------------------

LABEL_MAP = {
    "Log #:": "LogNumber",
    "Department:": "Department",
    "Property:": "Property",
    "Owner:": "Owner",
    "Location:": "Location",
    "Created By:": "Created By",
    "Sublocation:": "Sublocation",
    "Occurred:": "Occurred",
    "End Time:": "End Time",
    "Camera/Monitor:": "Camera/Monitor",
    "Status:": "Status",
    "Duration:": "Duration",
    "Topic:": "Topic",
    "Details:": "Details",
}

IGNORE_TOKENS = {
    "Daily Log Detailed List Report",
    "Page 1 / 1",
}

HIGH_PRIORITY_TOKEN = "High Priority"


def parse_printed_csv(path: str) -> pd.DataFrame:
    """
    Convert the 'printed' CSV (label/value pairs on each row) into a tidy DataFrame.
    This export presents 'High Priority' as a bare token in the same row; we flag it.
    """
    records: List[Dict[str, str]] = []

    def flush_record(cur: Dict[str, str]):
        if any(cur.get(k) for k in ["LogNumber", "Topic", "Details"]):
            records.append(cur.copy())

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue

            cur: Dict[str, str] = {k: "" for k in LABEL_MAP.values()}
            cur["HighPriorityBool"] = False

            i, n = 0, len(row)
            while i < n:
                token = (row[i] or "").strip()

                if not token or token in IGNORE_TOKENS:
                    i += 1
                    continue

                if token == HIGH_PRIORITY_TOKEN:
                    cur["HighPriorityBool"] = True
                    i += 1
                    continue

                if token in LABEL_MAP:
                    key = LABEL_MAP[token]
                    value = ""
                    if i + 1 < n:
                        nxt = (row[i + 1] or "").strip()
                        if nxt not in LABEL_MAP and nxt != HIGH_PRIORITY_TOKEN and nxt not in IGNORE_TOKENS:
                            value = nxt
                            i += 1
                    cur[key] = value

                i += 1

            flush_record(cur)

    df = pd.DataFrame.from_records(records)

    # Normalize text fields
    for col in ["Topic", "Details", "Location", "Sublocation", "Status"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    # Ensure flag column exists
    if "HighPriorityBool" not in df.columns:
        df["HighPriorityBool"] = False

    # Keep only meaningful rows
    if "Topic" in df.columns:
        df = df[(df["Topic"].str.len() > 0) | (df["Details"].str.len() > 0) | (df["LogNumber"].str.len() > 0)]

    return df


# ----------------------------
# RENDER HELPERS
# ----------------------------

def rows_by_topics(df: pd.DataFrame, topics: List[str]) -> pd.DataFrame:
    return df[df["Topic"].isin(topics)].copy() if not df.empty else df

def _join_compact(lines: list[str]) -> str:
    """Compact list: single line break between items."""
    return "N/A" if not lines else "<br>".join(lines)

def lines_join_section(lines: list[str], compact: bool) -> str:
    """
    If compact=True  -> single break between items (for Observations, Jackpots, Parkade Scan, ID Shots)
    If compact=False -> add an empty line between items (double break)
    """
    if not lines:
        return "N/A"
    sep = "<br>" if compact else "<br><br>"
    return sep.join(lines)

def details_only(row: pd.Series) -> str:
    """Return only the Details text (no Location/Sublocation)."""
    return (row.get("Details") or "").strip()

def is_table_games_observation(row: pd.Series) -> bool:
    """
    True if Topic == 'Observation' AND it looks like a table-games table.
    Uses Sublocation markers seen in the export (BJ, RB, RL, UTH, LIR, Poker).
    We don't print location/sublocation, but we use them to route.
    """
    if str(row.get("Topic", "")) != "Observation":
        return False
    sub = (row.get("Location") or "").upper()
    table_markers = ("Pit")
    return any(m in sub for m in table_markers)


# ----------------------------
# EMAIL BODY BUILDER
# ----------------------------

def build_email_html(df_src: pd.DataFrame, players_df: Optional[pd.DataFrame]) -> str:
    # High Priority only
    df = df_src.copy()
    if HIGH_PRIORITY_ONLY and "HighPriorityBool" in df.columns:
        df = df[df["HighPriorityBool"]]

    # Topic buckets
    topics_reviews = {"Requested Review", "Requested Observation", "Surveillance Initiated Review", "Service Review"}
    topics_tables  = {"Observation"}  # we'll route only *table-game* Observations here
    topics_slots   = {"Jackpot"}
    topics_removals = {
        "Removal", "Alcohol related removal", "PPA Issued/Violation",
        "Self-Exclusion Violation", "Self-Exclusion Application", "Behaviour Related Removal"
    }
    topics_misc = {
        "FINTRAC", "Information", "Security Escort", "Other", "Access Control",
        "Criminal Activity - Driving under the influence", "Criminal Activity - Theft"
    }
    topics_highlights = {"Straight Flush", "Kings Bounty", "Royal Flush", "Four of a Kind"}
    topics_idshots = {"Pit Scan"}   # ID shots live here
    topics_parkade = {"Parkade Scan"}
    topics_visitors = {"Surveillance Visitor Log"}

    # -----------------------------
    # Observation routing (3-way)
    # -----------------------------
    obs_df = rows_by_topics(df, ["Observation"])

    # (a) TABLES: table-games observations (compact)
    tables_df = obs_df[obs_df.apply(is_table_games_observation, axis=1)]
    tables = lines_join_section(
        [details_only(r) for _, r in tables_df.iterrows()],
        compact=True  # compact for Observations
    )


    # (b) CAGE/COUNT: observations at Cage or Count Room should appear under Cage/Count section
    obs_cage_count_df = obs_df[
        obs_df["Location"].str.contains("Cage|Count Room", case=False, na=False)
    ]

    # (c) NON-TABLE, NON-CAGE/COUNT observations -> go to MISC (Other)
    obs_misc_df = obs_df[
        ~obs_df.index.isin(tables_df.index) &
        ~obs_df.index.isin(obs_cage_count_df.index)
    ]

    # RED FLAGS (spaced)
    red_flags = lines_join_section(
        [details_only(r) for _, r in rows_by_topics(df, list(RED_FLAG_TOPICS)).iterrows()]
        if RED_FLAG_TOPICS else [],
        compact=False
    )

    # REVIEWS/ROBS (spaced)
    reviews =lines_join_section(
        [details_only(r) for _, r in rows_by_topics(df, list(topics_reviews)).iterrows()], 
        compact=False
    )

    # Highlights (spaced)
    highlights = lines_join_section(
        [details_only(r) for _, r in rows_by_topics(df, list(topics_highlights)).iterrows()],
        compact=False
    )

    if highlights == "N/A":
        highlights = ""

    # SLOTS -> Jackpots (compact)
    slots = lines_join_section(
        [details_only(r) for _, r in rows_by_topics(df, list(topics_slots)).iterrows()],
        compact=True
    )

    # REMOVALS/PPA/VSE (spaced)
    removals = lines_join_section(
        [details_only(r) for _, r in rows_by_topics(df, list(topics_removals)).iterrows()],
        compact=False
    )

    # ID Shots (compact)
    idshots = lines_join_section(
        [details_only(r) for _, r in rows_by_topics(df, list(topics_idshots)).iterrows()],
        compact=True
    )

    # Parkade Scans (compact)
    parkade = lines_join_section(
        [details_only(r) for _, r in rows_by_topics(df, list(topics_parkade)).iterrows()],
        compact=True
    )

    # -----------------------------
    # CAGE/COUNT section (spaced)
    # -----------------------------
    cc_topic_rows = df[
        df["Location"].str.contains("Cage|Count Room|TITO Self Redemption|Main Bank", case=False, na=False)
    ]
    cc_combined_df = pd.concat([cc_topic_rows, obs_cage_count_df]).drop_duplicates(ignore_index=True)
    cage_count_html = lines_join_section(
        [details_only(r) for _, r in cc_combined_df.iterrows()],
        compact=False
    )

    # -----------------------------
    # MISC -> Other (spaced) + non-table, non-cage/count Observations
    # -----------------------------
    misc_df = rows_by_topics(df, list(topics_misc))
    misc_combined_df = pd.concat([obs_misc_df, misc_df], ignore_index=True)
    misc_other = lines_join_section(
        [details_only(r) for _, r in misc_combined_df.iterrows()],
        compact=False
    )

    if misc_other == "N/A":
        misc_other = ""

    # VISITORS (spaced)
    visitors = lines_join_section(
        [details_only(r) for _, r in rows_by_topics(df, list(topics_visitors)).iterrows()],
        compact=True
    )

    # NOT CATEGORIZED (spaced) = any HP topics not in handled sets
    handled_topics = (
        topics_reviews | topics_tables | topics_slots | topics_removals |
        topics_misc | topics_highlights | topics_idshots | topics_parkade |
        topics_visitors | set(RED_FLAG_TOPICS)
    )
    not_cat_df = df[~df["Topic"].isin(handled_topics)]
    not_categorized = lines_join_section(
        [details_only(r) for _, r in not_cat_df.iterrows()],
        compact=False
    )

    # PLAYERS (optional; unchanged behavior)
    players_html = "<p>N/A</p>"
    if players_df is not None and not players_df.empty:
        cols = [c for c in ["First Name", "Last Name", "Buy-In", "CasinoWin"] if c in players_df.columns]
        if cols:
            p = players_df[cols].copy().astype(str)
            players_html = "<br>".join(f"{p.iloc[i].to_dict()}" for i in range(len(p)))

    # Final HTML
    html = f"""
<div style="font-family: Segoe UI, Arial, sans-serif; line-height:1.45; font-size:14px;">
  <h3>RED FLAGS</h3>
  {red_flags}

  <h3>PLAYERS</h3>
  {players_html}

  <h3>REVIEWS/ROBS</h3>
  {reviews}

  <h3>TABLES</h3>
  {tables}
<br><br>
  {highlights}

  <h3>SLOTS</h3>
  {slots}

  <h3>CAGE/COUNT</h3>
  {cage_count_html}

  <h3>MISC</h3>
  {parkade}<br>
  <br>{idshots}<br>
  <br>{misc_other}

  <h3>REMOVALS/PPA/VSE</h3>
  {removals}

  <h3>VISITORS</h3>
  {visitors}

  <h3>Not Categorized</h3>
  {not_categorized}
</div>
"""
    return html


def optional_players_table(csv_path: str) -> Optional[pd.DataFrame]:
    if os.path.exists(csv_path):
        try:
            return pd.read_csv(csv_path)
        except Exception:
            return None
    return None


def save_html(html_body: str, filename: str = "DailyReport_HP_AllFromFile.html"):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_body)
    print(f"Saved HTML email body to {filename}")


def main():
    df = parse_printed_csv(PRINTED_CSV)  # parses the new report format (High Priority token per row)
    players_df = optional_players_table(PLAYERS_CSV)
    html_body = build_email_html(df, players_df)
    save_html(html_body)


if __name__ == "__main__":
    main()