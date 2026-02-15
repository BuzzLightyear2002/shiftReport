#!/usr/bin/env python3
"""
Surveillance Daily Report Builder (Desktop GUI)
- Reads the "Daily Log Detailed List Report" CSV (new printed style)
- High Priority only (toggleable but default True)
- NO date filter
- Details-only output (no Location/Sublocation printed)
- Spacing rules:
    Compact -> Observations (tables only), Jackpots, Parkade Scans, ID Shots
    Spaced  -> All other sections
- Observation routing:
    TABLES: table-games only (Pit in Location, or Sublocation contains BJ/RB/RL/UTH/LIR/POKER)
    CAGE/COUNT: any Observation in Cage/Count Room
    MISC->Other: other Observations (non-table, non-cage/count)
- Procedural Error routing:
    TABLES / CAGE-COUNT / MISC (spaced)
- Headings styled to body size, bold, underlined (spacing preserved)
- Produces HTML and opens a local preview in the default browser
"""

import csv
import io
import os
import sys
import webbrowser
from datetime import datetime
from typing import List, Optional, Dict

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, StringVar, BooleanVar

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


def parse_printed_csv_from_path(path: str) -> pd.DataFrame:
    """Convert the 'printed' CSV (label/value pairs per row) into a tidy DataFrame."""
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

def lines_join_section(lines: List[str], compact: bool) -> str:
    """Compact -> single <br>; Spaced -> double <br>."""
    if not lines:
        return "N/A"
    sep = "<br>" if compact else "<br><br>"
    return sep.join(lines)

def details_only(row: pd.Series) -> str:
    """Return only the Details text (no Location/Sublocation)."""
    return (row.get("Details") or "").strip()

def is_table_games_observation(row: pd.Series) -> bool:
    """
    Observation -> TABLES if:
      - Location mentions 'PIT' (e.g., Pit 1), OR
      - Sublocation has BJ, RB, RL, UTH, LIR, POKER (e.g., BJ10, RB1, UTH2).
    """
    if str(row.get("Topic", "")) != "Observation":
        return False
    loc = (row.get("Location") or "").upper()
    sub = (row.get("Sublocation") or "").upper()
    if "PIT" in loc:
        return True
    table_markers = ("BJ", "RB", "RL", "UTH", "LIR", "POKER")
    return any(m in sub for m in table_markers)


# ----------------------------
# HTML BUILDER
# ----------------------------

def build_email_html(
    df_src: pd.DataFrame,
    players_df: Optional[pd.DataFrame],
    high_priority_only: bool,
    red_flag_topics: List[str]
) -> str:

    df = df_src.copy()
    if high_priority_only and "HighPriorityBool" in df.columns:
        df = df[df["HighPriorityBool"]]

    # Topic buckets
    topics_reviews = {"Requested Review", "Requested Observation", "Surveillance Initiated Review", "Service Review"}
    topics_slots   = {"Jackpot"}
    topics_removals = {
        "Removal", "Alcohol related removal", "PPA Issued/Violation",
        "Self-Exclusion Violation", "Self-Exclusion Application", "Behaviour Related Removal"
    }
    topics_misc = {
        "FINTRAC", "Information", "Security Escort", "Other", "Access Control",
        "Criminal Activity - Driving under the influence", "Criminal Activity - Theft",
        "Integrity - Unsecured Assets", "Integrity"
    }
    topics_highlights = {"Straight Flush", "Kings Bounty", "Royal Flush", "Four of a Kind"}
    topics_idshots = {"Pit Scan"}   # ID shots live here
    topics_parkade = {"Parkade Scan"}
    topics_visitors = {"Surveillance Visitor Log"}

    # --- Observation routing (3-way) ---
    obs_df = rows_by_topics(df, ["Observation"])

    # TABLES (compact)
    tables_df = obs_df[obs_df.apply(is_table_games_observation, axis=1)]
    tables = lines_join_section([details_only(r) for _, r in tables_df.iterrows()], compact=True)

    # CAGE/COUNT observations
    obs_cage_count_df = obs_df[obs_df["Location"].str.contains("Cage|Count Room", case=False, na=False)]

    # MISC observations (non-table, non-cage/count)
    obs_misc_df = obs_df[~obs_df.index.isin(tables_df.index) & ~obs_df.index.isin(obs_cage_count_df.index)]

    # RED FLAGS (spaced)
    red_flags = lines_join_section(
        [details_only(r) for _, r in rows_by_topics(df, red_flag_topics).iterrows()] if red_flag_topics else [],
        compact=False
    )

    # REVIEWS/ROBS (spaced)
    reviews = lines_join_section([details_only(r) for _, r in rows_by_topics(df, list(topics_reviews)).iterrows()], compact=False)

    # Highlights (spaced)
    highlights = lines_join_section([details_only(r) for _, r in rows_by_topics(df, list(topics_highlights)).iterrows()], compact=False)
    if highlights == "N/A":
        highlights = ""

    # SLOTS -> Jackpots (compact)
    slots = lines_join_section([details_only(r) for _, r in rows_by_topics(df, list(topics_slots)).iterrows()], compact=True)

    # REMOVALS/PPA/VSE (spaced)
    removals = lines_join_section([details_only(r) for _, r in rows_by_topics(df, list(topics_removals)).iterrows()], compact=False)

    # ID Shots (compact)
    idshots = lines_join_section([details_only(r) for _, r in rows_by_topics(df, list(topics_idshots)).iterrows()], compact=True)

    # Parkade Scans (compact)
    parkade = lines_join_section([details_only(r) for _, r in rows_by_topics(df, list(topics_parkade)).iterrows()], compact=True)

    # CAGE/COUNT (spaced) – include all cage/count location topics + obs at cage/count
    cc_topic_rows = df[
        df["Location"].str.contains("Cage|Count Room|TITO Self Redemption|Main Bank", case=False, na=False)
    ]
    cc_combined_df = pd.concat([cc_topic_rows, obs_cage_count_df]).drop_duplicates(ignore_index=True)
    cage_count_html = lines_join_section(
        [details_only(r) for _, r in obs_cage_count_df.iterrows()],
        compact=True
    )

    # MISC -> Other (spaced) + non-table, non-cage/count Observations
    misc_df = rows_by_topics(df, list(topics_misc))
    misc_combined_df = pd.concat([obs_misc_df, misc_df], ignore_index=True)
    misc_other = lines_join_section([details_only(r) for _, r in misc_combined_df.iterrows()], compact=False)
    if misc_other == "N/A":
        misc_other = ""

    # Procedural Error (3-way) -> spaced
    pro_df = rows_by_topics(df, ["Procedural Error"])

    def is_table_games_site(row: pd.Series) -> bool:
        loc = (row.get("Location") or "").upper()
        sub = (row.get("Sublocation") or "").upper()
        if "PIT" in loc:
            return True
        table_markers = ("BJ", "RB", "RL", "UTH", "LIR", "POKER")
        return any(m in sub for m in table_markers)

    pro_cage_count_rows = pro_df[pro_df["Location"].str.contains("Cage|Count Room|Main Bank|TITO Self Redemption", case=False, na=False)]
    pro_tables_rows = pro_df[pro_df.apply(is_table_games_site, axis=1)]
    pro_misc_rows_df = pro_df[~pro_df.index.isin(pro_cage_count_rows.index) & ~pro_df.index.isin(pro_tables_rows.index)]

    pro_cage_count_df = lines_join_section([details_only(r) for _, r in pro_cage_count_rows.iterrows()], compact=False)
    pro_tables_df = lines_join_section([details_only(r) for _, r in pro_tables_rows.iterrows()], compact=False)
    pro_misc_df = lines_join_section([details_only(r) for _, r in pro_misc_rows_df.iterrows()], compact=False)

    # Normalize N/A -> empty string
    if pro_cage_count_df == "N/A":
        pro_cage_count_df = ""
    if pro_tables_df == "N/A":
        pro_tables_df = ""
    if pro_misc_df == "N/A":
        pro_misc_df = ""

    # VISITORS (spaced)
    visitors = lines_join_section([details_only(r) for _, r in rows_by_topics(df, list(topics_visitors)).iterrows()], compact=True)

    # NOT CATEGORIZED (spaced)
    handled_topics = (set(topics_reviews) | {"Observation"} | set(topics_slots) | set(topics_removals) |
                      set(topics_misc) | set(topics_highlights) | set(topics_idshots) | set(topics_parkade) |
                      set(topics_visitors) | {"Procedural Error"} | set(red_flag_topics))
    not_cat_df = df[~df["Topic"].isin(handled_topics)]
    not_categorized = lines_join_section([details_only(r) for _, r in not_cat_df.iterrows()], compact=False)

    # PLAYERS (optional; unchanged behavior)
    players_html = "<p>N/A</p>"
    if players_df is not None and not players_df.empty:
        cols = [c for c in ["First Name", "Last Name", "Buy-In", "CasinoWin"] if c in players_df.columns]
        if cols:
            p = players_df[cols].copy().astype(str)
            players_html = "<br>".join(f"{p.iloc[i].to_dict()}" for i in range(len(p)))

    # Headings style (bold+underlined, body size, spacing preserved)
    H = 'style="font-size:inherit; font-weight:700; text-decoration:underline; margin:16px 0 6px 0;"'

    html = f"""
<div style="font-family: Segoe UI, Arial, sans-serif; line-height:1.45; font-size:14px;">
   <p style="font-size:inherit; font-weight:700; color:red; text-decoration:underline; margin:0;">RED FLAGS</p>
   <br>
   <p style="color:red;  margin:0;">
{red_flags}
</p>
  <br> <br>
 <p style="font-size:inherit; font-weight:700; text-decoration:underline; margin:0;">PLAYERS</p> <br>

  {players_html}

   <br>
<p style="font-size:inherit; font-weight:700; text-decoration:underline; margin:0;">REVIEWS/ROBS</p> <br>

  {reviews}

  <br> <br>
 <p style="font-size:inherit; font-weight:700; text-decoration:underline; margin:0;">TABLES</p> <br>

  {tables}


<br><br>
  {highlights}<br><br>

    {pro_tables_df}

   <br><br>
<p style="font-size:inherit; font-weight:700; text-decoration:underline; margin:0;">SLOTS</p> <br>

  {slots}

  <br> <br>
 <p style="font-size:inherit; font-weight:700; text-decoration:underline; margin:0;">CAGE/COUNT</p> <br>

  {cage_count_html}<br><br>
  {pro_cage_count_df}

   <br>
<p style="font-size:inherit; font-weight:700; text-decoration:underline; margin:0;">MISC</p> <br>

  {parkade}<br>
  <br>{idshots}<br>
  <br>{misc_other}
  {pro_misc_df}

   <br> <br>
<p style="font-size:inherit; font-weight:700; text-decoration:underline; margin:0;">REMOVALS/PPA/VSE</p> <br>

  {removals}

   <br> <br>
<p style="font-size:inherit; font-weight:700; text-decoration:underline; margin:0;">VISITORS</p> <br>

  {visitors}

  <br> <br>
 <p style="font-size:inherit; font-weight:700; text-decoration:underline; margin:0;">Not Categorized</p> <br>

  {not_categorized}
</div>
"""
    return html


# ----------------------------
# TKINTER UI
# ----------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Surveillance Daily Report Builder")
        self.geometry("780x320")
        self.resizable(False, False)

        self.input_csv = StringVar()
        self.players_csv = StringVar()
        self.red_flags = StringVar(value="")   # e.g., Straight Flush, FINTRAC
        self.hp_only = BooleanVar(value=True)  # default True per your SOP

        # Row 0: main CSV
        tk.Label(self, text="Daily Log Detailed List CSV:").grid(row=0, column=0, sticky="w", padx=12, pady=10)
        tk.Entry(self, textvariable=self.input_csv, width=80).grid(row=0, column=1, padx=6)
        tk.Button(self, text="Browse", command=self.pick_main_csv).grid(row=0, column=2, padx=6)

        # Row 1: players CSV (optional)
        tk.Label(self, text="players.csv (optional):").grid(row=1, column=0, sticky="w", padx=12, pady=6)
        tk.Entry(self, textvariable=self.players_csv, width=80).grid(row=1, column=1, padx=6)
        tk.Button(self, text="Browse", command=self.pick_players_csv).grid(row=1, column=2, padx=6)

        # Row 2: HP only checkbox
        tk.Checkbutton(self, text="Include only High Priority rows", variable=self.hp_only).grid(row=2, column=1, sticky="w", padx=6, pady=6)

        # Row 3: red flags
        tk.Label(self, text="RED FLAG topics (comma-separated):").grid(row=3, column=0, sticky="w", padx=12, pady=6)
        tk.Entry(self, textvariable=self.red_flags, width=80).grid(row=3, column=1, padx=6)

        # Row 4: buttons
        tk.Button(self, text="Generate HTML", command=self.generate).grid(row=4, column=1, pady=18)

        # Footer
        tk.Label(self, text="Output: DailyReport_HP_AllFromFile.html (saved next to the app or selected CSV)").grid(row=5, column=1, pady=4)

    def pick_main_csv(self):
        path = filedialog.askopenfilename(
            title="Select 'Daily Log Detailed List Report' CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path:
            self.input_csv.set(path)

    def pick_players_csv(self):
        path = filedialog.askopenfilename(
            title="Select players.csv (optional)",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path:
            self.players_csv.set(path)

    def generate(self):
        in_csv = self.input_csv.get().strip()
        if not in_csv or not os.path.isfile(in_csv):
            messagebox.showerror("Missing file", "Please select the Daily Log Detailed List Report CSV.")
            return

        try:
            df = parse_printed_csv_from_path(in_csv)
        except Exception as e:
            messagebox.showerror("Error reading CSV", str(e))
            return

        players_df = None
        p_csv = self.players_csv.get().strip()
        if p_csv and os.path.isfile(p_csv):
            try:
                players_df = pd.read_csv(p_csv)
            except Exception as e:
                messagebox.showwarning("players.csv", f"Could not read players.csv: {e}")

        red_flags = [t.strip() for t in self.red_flags.get().split(",") if t.strip()]

        try:
            html_body = build_email_html(
                df_src=df,
                players_df=players_df,
                high_priority_only=self.hp_only.get(),
                red_flag_topics=red_flags
            )
        except Exception as e:
            messagebox.showerror("Build error", str(e))
            return

        # Save HTML next to the input CSV by default
        out_dir = os.path.dirname(in_csv) or os.getcwd()
        out_path = os.path.join(out_dir, "DailyReport_HP_AllFromFile.html")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html_body)
        except Exception as e:
            messagebox.showerror("Write error", f"Could not save HTML file:\n{e}")
            return

        # Open in default browser
        webbrowser.open_new_tab(f"file:///{out_path}")
        messagebox.showinfo("Success", f"Report generated:\n{out_path}")


if __name__ == "__main__":
    App().mainloop()