
#!/usr/bin/env python3
"""
Daily Report Email Builder (High Priority Only)

Reads SearchResults.csv of daily logs, filters to the chosen date AND
High Priority = TRUE, builds an HTML email body aligned to the template,
and can optionally send via Outlook (Windows + Outlook required).

Author: Your team
"""

import os
from datetime import datetime
from typing import List, Optional

import pandas as pd

# ----------------------------
# CONFIGURATION
# ----------------------------

LOG_CSV = "SearchResults.csv"      # your attached CSV of daily logs
PLAYERS_CSV = "players.csv"        # optional; if missing, PLAYERS -> N/A

# Header fields (edit to suit)
REPORT_DATE = "Monday, December 29th, 2025"
AUTHOR_NAME = "Matt Doyle"
AUTHOR_TITLE = "Surveillance Operator"

# Distribution
TO_LINE = "NSHFX Surveillance Employees"
CC_LINE = "Troy Syms; Stacey Sinclair; Jason MacNeil; Dan Wandless"

# Signature
COMPANY = "CASINO NOVA SCOTIA"
ADDRESS = "1983 Upper Water Street, Halifax, NS B3J 3Y5"
PHONE = "902-496-6649"
EMAIL = "matt.doyle@casinonovascotia.com"

# Red flag topics (you can modify)
RED_FLAG_TOPICS = {
}

FILTER_DATE = "2025-12-30"  # ISO yyyy-mm-dd
USE_OUTLOOK = False          # set True to draft/send via Outlook COM


# ----------------------------
# HELPERS
# ----------------------------

def load_logs(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)

    # Normalize 'Occurred' to date
    df["OccurredDate"] = pd.to_datetime(
        df["Occurred"].astype(str).str.split().str[0],
        format="%m/%d/%Y",
        errors="coerce",
    )

    # Normalize High Priority to boolean
    hp = df.get("High Priority")
    if hp is not None:
        df["HighPriorityBool"] = (
            hp.astype(str).str.strip().str.upper().isin({"TRUE", "T", "YES", "Y", "1"})
        )
    else:
        df["HighPriorityBool"] = False  # if missing, treat as False

    # Ensure text columns are strings to avoid errors
    for c in ["Topic", "Details", "Location", "Sublocation"]:
        if c in df.columns:
            df[c] = df[c].astype(str)
    return df


def only_date(df: pd.DataFrame, iso_date: str) -> pd.DataFrame:
    target = pd.to_datetime(iso_date).date()
    return df[df["OccurredDate"].dt.date == target].copy()


def only_high_priority(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["HighPriorityBool"]].copy()


def rows_by_topics(df: pd.DataFrame, topics: List[str]) -> pd.DataFrame:
    return df[df["Topic"].isin(topics)].copy()


def html_list(items: List[str]) -> str:
    return "<p>N/A</p>" if not items else "<ul>\n" + "\n".join(f"  <li>{x}</li>" for x in items) + "\n</ul>"


def format_details(row: pd.Series) -> str:
    loc = (row.get("Location") or "").strip()
    sub = (row.get("Sublocation") or "").strip()
    det = (row.get("Details") or "").strip()
    if loc and sub:
        return f"<strong>{loc}, {sub}:</strong> {det}"
    elif loc:
        return f"<strong>{loc}:</strong> {det}"
    return det


def extract_id_shots(df: pd.DataFrame) -> List[str]:
    id_rows = df[(df["Topic"].str.contains("Pit Scan", na=False)) | (df["Details"].str.contains("ID Shots", na=False))]
    return [str(r.get("Details", "")).strip() for _, r in id_rows.iterrows()]


def extract_parkade_scans(df: pd.DataFrame) -> List[str]:
    pk_rows = df[df["Topic"].str.contains("Parkade Scan", na=False)]
    return [str(r.get("Details", "")).strip() for _, r in pk_rows.iterrows()]


def optional_players_table(csv_path: str) -> Optional[pd.DataFrame]:
    if os.path.exists(csv_path):
        p = pd.read_csv(csv_path)
        return p
    return None


def players_html_table(p: Optional[pd.DataFrame]) -> str:
    if p is None or p.empty:
        return "<p>N/A</p>"
    cols = ["First Name", "Last Name", "Buy-In", "CasinoWin"]
    p = p[[c for c in cols if c in p.columns]].copy()
    for c in ["Buy-In", "CasinoWin"]:
        if c in p.columns:
            p[c] = p[c].astype(str)
    header = "<tr>" + "".join(f"<th>{c}</th>" for c in p.columns) + "</tr>"
    rows = "\n".join("<tr>" + "".join(f"<td>{val}</td>" for val in p.iloc[i]) + "</tr>" for i in range(len(p)))
    return f'<table border="1" cellspacing="0" cellpadding="6">\n{header}\n{rows}\n</table>'


def build_email_html(
    report_date: str,
    author_name: str,
    to_line: str,
    cc_line: str,
    df_hp_day: pd.DataFrame,
    players_df: Optional[pd.DataFrame],
) -> str:
    # RED FLAGS (from high priority rows only)
    red_flags_df = rows_by_topics(df_hp_day, list(RED_FLAG_TOPICS))
    red_flags_items = [format_details(r) for _, r in red_flags_df.iterrows()]
    red_flags_html = html_list(red_flags_items)

    # REVIEWS / ROBS
    reviews_df = rows_by_topics(df_hp_day, ["Requested Review", "Requested Observation", "Surveillance Initiated Review"])
    reviews_items = [format_details(r) for _, r in reviews_df.iterrows()]
    reviews_html = html_list(reviews_items)

    # TABLES (Observations at table sublocations)
    tables_df = rows_by_topics(df_hp_day, ["Observation"])
    tables_df = tables_df[tables_df["Sublocation"].str.contains("|".join(["BJ", "RB", "RL", "UTH"]), na=False)]
    tables_items = [format_details(r) for _, r in tables_df.iterrows()]
    tables_html = html_list(tables_items)

    # Highlights (High Action / Straight Flush)
    highlight_df = rows_by_topics(df_hp_day, ["High Action", "Straight Flush"])
    highlight_items = [format_details(r) for _, r in highlight_df.iterrows()]
    highlight_html = html_list(highlight_items)

    # SLOTS (Jackpot + slot tech observations) – high priority only
    slots_df = rows_by_topics(df_hp_day, ["Jackpot", "Observation"])
    slots_df = slots_df[
        slots_df["Location"].str.contains("Slot", na=False) |
        slots_df["Details"].str.contains("Slot Technician", na=False)
    ]
    slots_items = [format_details(r) for _, r in slots_df.iterrows()]
    slots_html = html_list(slots_items)

    # CAGE / COUNT
    cc_df = df_hp_day[
        (df_hp_day["Location"].str.contains("Cage", na=False) |
         df_hp_day["Location"].str.contains("Count Room", na=False))
    ]
    cc_items = [format_details(r) for _, r in cc_df.iterrows()]
    cc_html = html_list(cc_items)

    # REMOVALS / PPA / VSE
    removals_df = rows_by_topics(df_hp_day, ["Removal", "Alcohol related removal"])
    removals_items = [format_details(r) for _, r in removals_df.iterrows()]
    removals_html = html_list(removals_items)

    # MISC (ID shots + Parkade scans + other high-priority)
    id_shots_items = extract_id_shots(df_hp_day)
    parkade_items = extract_parkade_scans(df_hp_day)
    misc_df = rows_by_topics(df_hp_day, ["Emergency BV Drop", "Information", "Armored Escort", "Security Escort"])
    misc_items = [format_details(r) for _, r in misc_df.iterrows()]
    misc_html = (
        "<h4>ID Shots</h4>" + html_list(id_shots_items) +
        "<h4>Parkade Scans</h4>" + html_list(parkade_items) +
        "<h4>Other</h4>" + html_list(misc_items)
    )

    # VISITORS
    visitors_df = rows_by_topics(df_hp_day, ["Surveillance Visitor Log"])
    visitors_items = [format_details(r) for _, r in visitors_df.iterrows()]
    visitors_html = html_list(visitors_items)

    # PLAYERS
    players_html = players_html_table(players_df)

    html = f"""
<div style="font-family: Segoe UI, Arial, sans-serif; line-height:1.45;">
  <p><strong>{REPORT_DATE}</strong></p>

  <p>📣<br>
  <strong>{author_name}</strong><br>
  {to_line}<br>
  {cc_line}</p>

  <h3>RED FLAGS (High Priority)</h3>
  {red_flags_html}

  <h3>PLAYERS</h3>
  {players_html}

  <h3>REVIEWS/ROBS (High Priority)</h3>
  {reviews_html}

  <h3>TABLES (High Priority Observations)</h3>
  {tables_html}

  <h3>Highlights (High Action / Straight Flush)</h3>
  {highlight_html}

  <h3>SLOTS (High Priority)</h3>
  {slots_html}

  <h3>CAGE/COUNT (High Priority)</h3>
  {cc_html}

  <h3>REMOVALS/PPA/VSE (High Priority)</h3>
  {removals_html}

  <h3>MISC (High Priority)</h3>
  {misc_html}

  <h3>VISITORS (High Priority)</h3>
  {visitors_html}

  <p><strong>D:</strong> <!-- add initials here e.g., PD/EG/SK: RC/MD/PK --></p>

  <br>
  <p><strong>{author_name}</strong><br>
  {AUTHOR_TITLE}</p>

  <p><strong>{COMPANY}</strong><br>
  {ADDRESS}<br>
  Phone: {PHONE}<br>
  Email: {EMAIL}</p>

  <p><em>GO FOR GREAT™</em></p>

  <hr>
  <small>
  This E-mail message (including attachments, if any) is intended for the use of the individual or entity to which it is addressed and may contain
  information that is privileged, proprietary, confidential and exempt from disclosure. If you are not the intended recipient, you are notified that any
  dissemination, distribution or copying of this communication is strictly prohibited. If you have received this communication in error, please notify the
  sender and erase this E-mail message immediately.
  </small>
</div>
"""
    return html


def try_send_outlook(subject: str, html_body: str, to_line: str, cc_line: str):
    try:
        import win32com.client as win32
        outlook = win32.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)
        mail.To = to_line
        mail.CC = cc_line
        mail.Subject = subject
        mail.HTMLBody = html_body
        mail.Display()  # or mail.Send()
        print("Draft opened in Outlook.")
    except Exception as e:
        print("Outlook send failed (is Outlook available?).")
        print(e)


def save_html(html_body: str, filename: str = "DailyReport_HP_2025-12-29.html"):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_body)
    print(f"Saved HTML email body to {filename}")


# ----------------------------
# MAIN
# ----------------------------

def main():
    df = load_logs(LOG_CSV)
    df_day = only_date(df, FILTER_DATE)
    df_hp_day = only_high_priority(df_day)   # <<< key filter

    players_df = optional_players_table(PLAYERS_CSV)

    html_body = build_email_html(
        report_date=REPORT_DATE,
        author_name=AUTHOR_NAME,
        to_line=TO_LINE,
        cc_line=CC_LINE,
        df_hp_day=df_hp_day,
        players_df=players_df,
    )

    save_html(html_body)

    if USE_OUTLOOK:
        subject = f"Surveillance Daily Report (High Priority) – {REPORT_DATE}"
        try_send_outlook(subject, html_body, TO_LINE, CC_LINE)


if __name__ == "__main__":
    main()
