#!/usr/bin/env python3
"""
update_db.py - Autonomous Conference Pipeline Processor

Automates the harvesting of academic conference dates and metadata using a 
2-Phase Cascade LLM Search Strategy (Official Site -> WikiCFP / Aggregators) 
and a Verification Engine with Dynamic Cooldown for Deadline Extensions.

Designed for GitHub Actions daily execution with strict API Quota & Cooldown management.
"""

import os
import re
import sys
import time
import json
import yaml
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse
import pandas as pd

# Force unbuffered output for GitHub Actions logs
sys.stdout.reconfigure(line_buffering=True)

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
CORE_CSV_PATH = os.path.join(BASE_DIR, "CORE_all26.csv")
DB_JSON_PATH = os.path.join(BASE_DIR, "conferences_db.json")
OUTPUT_CSV_PATH = os.path.join(BASE_DIR, "conferences_filtrees.csv")

# Quota Security Settings
MAX_API_CALLS_HARD_LIMIT = 480  # Strict script limit (max 500 allowed per day by API)
KILL_SWITCH_THRESHOLD = 450     # Stop loop cleanly when reached
TARGET_YEAR = 2027              # Default target edition year

# Selection Triage Settings (Max 100 total)
MAX_BATCH_SIZE = 100
MAX_PENDING_SLOTS = 50
MAX_INCOMPLETE_SLOTS = 25
MAX_NOT_FOUND_SLOTS = 25

# Global API Call Counter
api_calls_count = 0


# ==========================================
# CONFIGURATION & API HELPERS
# ==========================================
def load_config():
    """Load configuration from config.yaml and environment variables."""
    config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Warning: Failed to load config.yaml: {e}")
            
    gemini_key = os.environ.get("GEMINI_API_KEY") or config.get("gemini_api_key")
    model_id = os.environ.get("GEMINI_MODEL_ID") or config.get("model_id") or "gemma-4-31b-it"
    
    return {
        "gemini_api_key": gemini_key,
        "model_id": model_id
    }


def make_grounded_gemini_api_call(payload, api_key, model, max_retries=3):
    """
    Query Gemini / Gemma API with Google Search Grounding.
    Tracks global api_calls_count and includes robust error handling.
    """
    global api_calls_count
    api_calls_count += 1
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    
    print(f"    [API Call #{api_calls_count}] Executing request...")

    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=120)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                print(f"    [Warning] API returned 429 (Rate Limited). Sleeping 10s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(10)
            elif response.status_code >= 500:
                print(f"    [Warning] Server Error ({response.status_code}). Sleeping 10s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(10)
            else:
                print(f"    [Error] API Call failed with HTTP {response.status_code}: {response.text}")
                time.sleep(5)
                break
        except Exception as e:
            print(f"    [Exception] API error: {e}. Sleeping 10s...")
            time.sleep(10)

    return None


def parse_extracted_json(raw_json):
    """Clean markdown codeblocks and parse raw JSON string."""
    if not raw_json:
        return None
    cleaned = raw_json.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except Exception as e:
        print(f"    JSON Parsing Exception: {e}")
        return None


def get_metadata_url(candidate):
    """Extract grounding URL from candidate metadata if available."""
    try:
        grounding_metadata = candidate.get("groundingMetadata", {})
        chunks = grounding_metadata.get("groundingChunks", [])
        for chunk in chunks:
            web_info = chunk.get("web", {})
            uri = web_info.get("uri")
            if uri:
                return uri
    except Exception:
        pass
    return "N/A"


def resolve_redirect_url(url, timeout=10):
    """Resolve redirect links to absolute target URLs with standard browser headers."""
    if not url or url == "N/A":
        return "N/A"
    if "grounding-api-redirect" not in url and "vertexai" not in url:
        return url
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    try:
        response = requests.head(url, headers=headers, allow_redirects=True, timeout=timeout)
        if "grounding-api-redirect" not in response.url and "vertexai" not in response.url:
            return response.url
    except Exception:
        try:
            response = requests.get(url, headers=headers, allow_redirects=True, timeout=timeout)
            if "grounding-api-redirect" not in response.url and "vertexai" not in response.url:
                return response.url
        except Exception:
            pass
    return "N/A"


def sanitize_extracted_dates(parsed, target_year=None):
    """
    Validates and cleans extracted dates upstream to prevent anomalies:
    1. Enforces year sanity bounds: year must be between target_year - 1 and target_year + 1.
    2. If abstract_deadline > submission_deadline -> clear abstract_deadline.
    3. If submission_deadline > notification_date -> clear notification_date.
    """
    if not parsed or not isinstance(parsed, dict):
        return parsed

    main_dates = parsed.get("main_track_dates")
    if not isinstance(main_dates, dict):
        return parsed

    abs_str = main_dates.get("abstract_submission")
    sub_str = main_dates.get("paper_submission")
    notif_str = main_dates.get("notification")

    def parse_d(d_str):
        if not d_str or d_str in ["N/A", "null", "None"]:
            return None
        try:
            return datetime.strptime(d_str, "%Y-%m-%d").date()
        except ValueError:
            return None

    abs_d = parse_d(abs_str)
    sub_d = parse_d(sub_str)
    notif_d = parse_d(notif_str)

    # Rule 1: Year sanity check against target_year bounds
    target_year_int = None
    if target_year is not None:
        try:
            target_year_int = int(target_year)
        except (ValueError, TypeError):
            pass

    if target_year_int is not None:
        if sub_d and (sub_d.year < target_year_int - 1 or sub_d.year > target_year_int + 1):
            print(f"    [Sanity Filter] Rejected submission date {sub_str} (year out of bounds for target {target_year_int})")
            main_dates["paper_submission"] = None
            sub_d = None
        if abs_d and (abs_d.year < target_year_int - 1 or abs_d.year > target_year_int + 1):
            print(f"    [Sanity Filter] Rejected abstract date {abs_str} (year out of bounds for target {target_year_int})")
            main_dates["abstract_submission"] = None
            abs_d = None
        if notif_d and (notif_d.year < target_year_int - 1 or notif_d.year > target_year_int + 2):
            print(f"    [Sanity Filter] Rejected notification date {notif_str} (year out of bounds for target {target_year_int})")
            main_dates["notification"] = None
            notif_d = None

    # Rule 2: Abstract deadline cannot be after submission deadline
    if abs_d and sub_d and abs_d > sub_d:
        print(f"    [Sanity Filter] Abstract deadline ({abs_str}) > Submission deadline ({sub_str}). Clearing abstract deadline.")
        main_dates["abstract_submission"] = None

    # Rule 3: Submission deadline cannot be after notification date
    if sub_d and notif_d and sub_d > notif_d:
        print(f"    [Sanity Filter] Submission deadline ({sub_str}) > Notification date ({notif_str}). Clearing notification date.")
        main_dates["notification"] = None

    parsed["main_track_dates"] = main_dates
    return parsed


# ==========================================
# DATA STRUCTURE & JSON DB HANDLING
# ==========================================
def load_database():
    """
    Load conferences_db.json.
    Structure: Dictionary indexed by Acronym.
    """
    if os.path.exists(DB_JSON_PATH):
        try:
            with open(DB_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    print(f"Loaded database with {len(data)} entries from {DB_JSON_PATH}")
                    return data
        except Exception as e:
            print(f"Warning: Failed to load {DB_JSON_PATH}: {e}")
    return {}


def save_database(db):
    """Atomic save of database dictionary to conferences_db.json and export to CSV."""
    tmp_json = DB_JSON_PATH + ".tmp"
    try:
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        os.replace(tmp_json, DB_JSON_PATH)
        print(f"Database successfully saved to {DB_JSON_PATH} ({len(db)} entries).")
    except Exception as e:
        print(f"Error saving database JSON: {e}")

    # Export to conferences_filtrees.csv for frontend integration
    try:
        rows = []
        for acronym, entry in db.items():
            row = {
                "Acronym": entry.get("acronym", acronym),
                "Year": entry.get("target_year", entry.get("year", TARGET_YEAR)),
                "Name": entry.get("name", "N/A"),
                "Rank": entry.get("rank", "N/A"),
                "URL": entry.get("url", "N/A"),
                "Abstract Deadline": entry.get("abstract_deadline") or "N/A",
                "Submission Deadline": entry.get("submission_deadline") or "N/A",
                "Notification Date": entry.get("notification_date") or "N/A",
                "Timezone": entry.get("timezone") or "N/A",
                "Topics": entry.get("topics") or "N/A",
                "Short Description": entry.get("short_description") or "N/A",
                "Other Tracks": entry.get("other_tracks") or "N/A",
                "Confidence Score": entry.get("confidence_score", 0),
                "Status": entry.get("status_detail", entry.get("status", "N/A"))
            }
            rows.append(row)

        if rows:
            df = pd.DataFrame(rows)
            cols_order = [
                "Acronym", "Year", "Name", "Rank", "URL", 
                "Abstract Deadline", "Submission Deadline", "Notification Date", 
                "Timezone", "Topics", "Short Description", "Other Tracks", 
                "Confidence Score", "Status"
            ]
            actual_cols = [c for c in cols_order if c in df.columns]
            tmp_csv = OUTPUT_CSV_PATH + ".tmp"
            df[actual_cols].to_csv(tmp_csv, index=False)
            os.replace(tmp_csv, OUTPUT_CSV_PATH)
            print(f"Exported {len(rows)} records to {OUTPUT_CSV_PATH}")
    except Exception as e:
        print(f"Warning: Exporting CSV failed: {e}")


# ==========================================
# TODO 2: YEARLY ROLLOVER & HIBERNATION
# ==========================================
from datetime import timedelta

def process_yearly_rollover(db_data):
    """
    Applies Year Rollover and Hibernation rules before batch selection.
    Modifies db_data directly.
    """
    today = datetime.now(timezone.utc).date()
    
    for acronym, entry in db_data.items():
        if entry.get("manual_override") is True:
            continue
            
        status = entry.get("status")
        # Ensure target_year is integer
        try:
            target_year = int(entry.get("target_year", TARGET_YEAR))
        except (ValueError, TypeError):
            target_year = TARGET_YEAR
            
        entry["target_year"] = target_year
        
        # Rule 1: Transition from DONE
        if status == "DONE":
            notif_str = entry.get("notification_date")
            paper_str = entry.get("submission_deadline")
            threshold_date = None
            
            if notif_str and notif_str != "N/A" and notif_str != "null":
                try:
                    threshold_date = datetime.strptime(notif_str, "%Y-%m-%d").date()
                except ValueError:
                    pass
            
            if not threshold_date and paper_str and paper_str != "N/A" and paper_str != "null":
                try:
                    paper_date = datetime.strptime(paper_str, "%Y-%m-%d").date()
                    threshold_date = paper_date + timedelta(days=60)
                except ValueError:
                    pass
                    
            if threshold_date and today > threshold_date:
                print(f"[Rollover] {acronym}: Edition {target_year} finished. Rolling over to {target_year + 1} and hibernating for 75 days.")
                entry["target_year"] = target_year + 1
                entry["status"] = "PENDING"
                entry["status_detail"] = f"Edition {target_year} finished (Waiting for {target_year + 1})"
                entry["hibernate_until"] = (today + timedelta(days=75)).strftime("%Y-%m-%d")
                entry["last_checked"] = None
                # Reset old deadline dates to prevent date mismatch in UI while preserving domain URL
                entry["abstract_deadline"] = None
                entry["submission_deadline"] = None
                entry["notification_date"] = None
                
        # Rule 2: Transition from NOT_FOUND / INCOMPLETE
        elif status in ["NOT_FOUND", "INCOMPLETE"]:
            try:
                nov_1st = datetime(target_year, 11, 1).date()
                if today > nov_1st:
                    print(f"[Rollover] {acronym}: Edition {target_year} missed (timeout). Rolling over to {target_year + 1}.")
                    entry["target_year"] = target_year + 1
                    entry["status"] = "PENDING"
                    entry["status_detail"] = f"Edition {target_year} not found (Waiting for {target_year + 1})"
                    entry["hibernate_until"] = None
                    entry["last_checked"] = None
                    # Reset old deadline dates to avoid invalid date persistence
                    entry["abstract_deadline"] = None
                    entry["submission_deadline"] = None
                    entry["notification_date"] = None
            except ValueError:
                pass


# ==========================================
# TODO 1: DYNAMIC COOLDOWN FOR VERIFICATION
# ==========================================
def is_done_eligible_for_verification(entry, today):
    """
    Evaluates if a DONE conference is eligible for re-verification based on dynamic cooldown rules.
    Returns: (is_eligible: bool, days_to_deadline: int)
    """
    sub_deadline_str = entry.get("submission_deadline")
    last_checked_str = entry.get("last_checked")

    if not sub_deadline_str or sub_deadline_str == "N/A":
        return False, 999

    try:
        sub_date = datetime.strptime(sub_deadline_str, "%Y-%m-%d").date()
    except ValueError:
        return False, 999

    days_to_deadline = (sub_date - today).days

    # Rule 1: Deadline has passed -> Ignore (no longer checked)
    if days_to_deadline < 0:
        return False, days_to_deadline

    # Calculate days since last check
    if not last_checked_str:
        days_since_last_check = 999
    else:
        try:
            last_checked_date = datetime.strptime(last_checked_str, "%Y-%m-%d").date()
            days_since_last_check = (today - last_checked_date).days
        except ValueError:
            days_since_last_check = 999

    # Rule 2: > 90 days remaining -> Eligible if > 30 days since last check
    if days_to_deadline > 90:
        return (days_since_last_check > 30), days_to_deadline

    # Rule 3: 31 to 90 days remaining -> Eligible if > 14 days since last check
    if 31 <= days_to_deadline <= 90:
        return (days_since_last_check > 14), days_to_deadline

    # Rule 4: 8 to 30 days remaining -> Eligible if > 5 days since last check
    if 8 <= days_to_deadline <= 30:
        return (days_since_last_check > 5), days_to_deadline

    # Rule 5: 0 to 7 days remaining -> Eligible if >= 2 days since last check
    if 0 <= days_to_deadline <= 7:
        return (days_since_last_check >= 2), days_to_deadline

    return False, days_to_deadline


# ==========================================
# TODO 2: TRIAGE & VASES COMMUNICANTS QUEUE
# ==========================================
def select_batch_to_process(db, core_csv_path, max_batch=MAX_BATCH_SIZE):
    """
    Selects a batch of up to 120 conferences following priority rules:
      1. PENDING (max 60)
      2. INCOMPLETE (max 20, >10 days since last check)
      3. NOT_FOUND (max 20, >21 days since last check)
      4. DONE (Verification, sorted by days_to_deadline ASC to fill remaining quota up to 120)
      5. Surplus spillover from PENDING / INCOMPLETE / NOT_FOUND if remaining capacity exists.
    """
    if not os.path.exists(core_csv_path):
        print(f"Error: {core_csv_path} not found.")
        return []

    df_core = pd.read_csv(core_csv_path, header=None, names=["id", "name", "acronym", "source", "rank", "active", "for1", "for2", "for3"])
    today = datetime.now(timezone.utc).date()

    candidates_pending = []
    candidates_incomplete = []
    candidates_not_found = []
    candidates_done_eligible = []

    # 1. Classify all conferences
    for _, row in df_core.iterrows():
        acronym = str(row["acronym"]).strip()
        name = str(row["name"]).strip()
        rank = str(row["rank"]).strip()

        if not acronym or acronym == "nan":
            continue

        if acronym not in db:
            current_year = today.year
            default_target = current_year + 1 if today.month >= 9 else current_year
            item_info = {"acronym": acronym, "name": name, "rank": rank, "year": default_target}
            candidates_pending.append((item_info, "PENDING (New)", "HARVEST"))
        else:
            entry = db[acronym]

            # Rule: If manual_override is True, totally ignore (never select for search or verification)
            if entry.get("manual_override") is True:
                print(f"  [Manual Override] Skipping {acronym} (manual_override=True)")
                continue

            # Hibernation check
            hibernate_str = entry.get("hibernate_until")
            if hibernate_str and hibernate_str not in ["null", "N/A"]:
                try:
                    hibernate_date = datetime.strptime(hibernate_str, "%Y-%m-%d").date()
                    if today < hibernate_date:
                        continue
                except ValueError:
                    pass

            target_year = entry.get("target_year", TARGET_YEAR)
            item_info = {"acronym": acronym, "name": name, "rank": rank, "year": target_year}

            status = entry.get("status", "PENDING")
            last_checked_str = entry.get("last_checked")

            if status == "PENDING" or not last_checked_str:
                candidates_pending.append((item_info, "PENDING", "HARVEST"))
            elif status == "INCOMPLETE":
                try:
                    last_checked_date = datetime.strptime(last_checked_str, "%Y-%m-%d").date()
                    if (today - last_checked_date).days > 10:
                        candidates_incomplete.append((item_info, f"INCOMPLETE (Checked {(today - last_checked_date).days}d ago)", "HARVEST"))
                except ValueError:
                    candidates_incomplete.append((item_info, "INCOMPLETE (Invalid Date)", "HARVEST"))
            elif status == "NOT_FOUND":
                try:
                    last_checked_date = datetime.strptime(last_checked_str, "%Y-%m-%d").date()
                    if (today - last_checked_date).days > 21:
                        candidates_not_found.append((item_info, f"NOT_FOUND (Checked {(today - last_checked_date).days}d ago)", "HARVEST"))
                except ValueError:
                    candidates_not_found.append((item_info, "NOT_FOUND (Invalid Date)", "HARVEST"))
            elif status == "DONE":
                is_eligible, days_to_dl = is_done_eligible_for_verification(entry, today)
                if is_eligible:
                    item_info["days_to_deadline"] = days_to_dl
                    candidates_done_eligible.append((item_info, f"VERIFY_DONE ({days_to_dl}d to deadline)", "VERIFY"))

    # 2. Sort DONE verification candidates by days_to_deadline ASCENDING
    candidates_done_eligible.sort(key=lambda x: x[0].get("days_to_deadline", 999))

    # 3. Select allocations
    selected_pending = candidates_pending[:MAX_PENDING_SLOTS]         # Max 60
    selected_incomplete = candidates_incomplete[:MAX_INCOMPLETE_SLOTS] # Max 20
    selected_not_found = candidates_not_found[:MAX_NOT_FOUND_SLOTS]   # Max 20

    batch = selected_pending + selected_incomplete + selected_not_found

    # 4. Fill remaining slots with eligible DONE verification candidates
    remaining_capacity = max_batch - len(batch)
    if remaining_capacity > 0:
        selected_done = candidates_done_eligible[:remaining_capacity]
        batch += selected_done
    else:
        selected_done = []

    # 5. Spillover: If still below max_batch, grab any surplus from PENDING, INCOMPLETE, NOT_FOUND
    if len(batch) < max_batch:
        used_acronyms = {item[0]["acronym"] for item in batch}
        surplus_candidates = [
            c for c in (candidates_pending + candidates_incomplete + candidates_not_found)
            if c[0]["acronym"] not in used_acronyms
        ]
        spillover_capacity = max_batch - len(batch)
        batch += surplus_candidates[:spillover_capacity]

    print("=" * 60)
    print(f"BATCH SELECTION TRIAGE (Max: {max_batch}):")
    print(f"  1. PENDING (New)      : {len(selected_pending)} selected (Pool: {len(candidates_pending)})")
    print(f"  2. INCOMPLETE (>10d)  : {len(selected_incomplete)} selected (Pool: {len(candidates_incomplete)})")
    print(f"  3. NOT_FOUND (>21d)   : {len(selected_not_found)} selected (Pool: {len(candidates_not_found)})")
    print(f"  4. DONE (Verification): {len(selected_done)} selected (Pool: {len(candidates_done_eligible)})")
    print(f"Total Selected Batch Size : {len(batch)}")
    print("=" * 60)

    return batch


# ==========================================
# CASCADE HARVESTING STRATEGY (Phase 1 & 2)
# ==========================================
def extract_dates_cascade(acronym, name, year, api_key, model):
    """
    Executes the 2-Phase Cascade LLM Search Strategy for new/incomplete conferences.
    """
    # Phase 1: Official Site
    prompt1_1 = f"""[Active Internet Search Mode]
Find the official website for edition {year} of conference {acronym} ({name}).

STRICT SEARCH RULES:
1. Search ONLY on the official conference website (URL containing acronym and target year).
2. IGNORE aggregator sites like WikiCFP, Research.com, Call4Papers.
3. IGNORE past editions. We strictly want edition {year}.

Extract textual blocks concerning:
- Main Track dates (Abstract, Full Paper, Notification).
- Short scope description or "Call for Papers" listing topics.

Display raw extracts with the official site URL as source.
"""
    payload1_1 = {
        "contents": [{"role": "user", "parts": [{"text": prompt1_1}]}],
        "tools": [{"googleSearch": {}}]
    }
    
    print(f"  [Harvest Phase 1] Querying official website for {acronym} {year}...")
    res1_1 = make_grounded_gemini_api_call(payload1_1, api_key, model)
    
    if res1_1:
        candidates = res1_1.get("candidates", [])
        if candidates and candidates[0].get("content", {}).get("parts", []):
            model_parts1_1 = candidates[0]["content"]["parts"]
            time.sleep(2.5)
            
            prompt1_2 = f"""Analyze the retrieved text blocks.
Use your reasoning capability to ensure the information originates from the official website of edition {year}.

Strict Rules:
1. If the information does not concern {year}, or if no valid official website was found, set "year_found": false and all dates to null.
2. EXCEPTION: If the text explicitly states that dates concern edition {year} + 1, and there is no trace of edition {year}, accept the data, but return "year_found" as an integer (e.g. {year + 1}) instead of a boolean.
3. Do not guess dates. If TBD or TBA, set to null.

Generate ONLY a JSON object (no surrounding text) with the following structure:
{{
  "conference": {{"acronym": "{acronym}", "year_found": true/false or integer (e.g. {year + 1}), "timezone": "e.g. AoE" or null}},
  "scope_and_topics": {{"short_description": "Short description or null", "topics": ["topic 1", "topic 2"]}},
  "main_track_dates": {{"abstract_submission": "YYYY-MM-DD" or null, "paper_submission": "YYYY-MM-DD" or null, "notification": "YYYY-MM-DD" or null}},
  "other_tracks": [{{"track_name": "track name", "submission_date": "YYYY-MM-DD" or null}}],
  "source_url": "URL du site officiel",
  "confidence_score": 10
}}
"""
            payload1_2 = {
                "contents": [
                    {"role": "user", "parts": [{"text": prompt1_1}]},
                    {"role": "model", "parts": model_parts1_1},
                    {"role": "user", "parts": [{"text": prompt1_2}]}
                ],
                "tools": [{"googleSearch": {}}]
            }
            
            print(f"  [Harvest Phase 1] JSON Extraction & Analysis...")
            res1_2 = make_grounded_gemini_api_call(payload1_2, api_key, model)
            if res1_2:
                candidates2 = res1_2.get("candidates", [])
                if candidates2 and candidates2[0].get("content", {}).get("parts", []):
                    raw_json = candidates2[0]["content"]["parts"][-1].get("text", "").strip()
                    parsed = parse_extracted_json(raw_json)
                    if parsed:
                        source_url = parsed.get("source_url")
                        if not source_url or source_url == "N/A" or "grounding-api-redirect" in source_url:
                            source_url = get_metadata_url(candidates2[0])
                        parsed["source_url"] = resolve_redirect_url(source_url)
                        parsed = sanitize_extracted_dates(parsed, year)
                        
                        conf_year_found = parsed.get("conference", {}).get("year_found", False)
                        paper_sub = parsed.get("main_track_dates", {}).get("paper_submission")
                        
                        if conf_year_found and paper_sub:
                            print(f"  -> SUCCESS (Harvest Phase 1): Official site and submission deadline found!")
                            parsed["status_detail"] = "Success (Official Site)"
                            return parsed

    time.sleep(3.0)

    # Phase 2: Aggregators
    prompt2_1 = f"""[Active Internet Search Mode]
We could not find the dates on the official website for edition {year} of conference {acronym} ({name}).

Search specifically on academic aggregators:
1. Primary: WikiCFP (query: site:wikicfp.com "{acronym} {year}").
2. Secondary: Research.com or other academic directories.

Extract textual blocks mentioning dates (Abstract, Paper, Notification) and topics for edition {year}.
"""
    payload2_1 = {
        "contents": [{"role": "user", "parts": [{"text": prompt2_1}]}],
        "tools": [{"googleSearch": {}}]
    }
    
    print(f"  [Harvest Phase 2] Querying aggregators (WikiCFP)...")
    res2_1 = make_grounded_gemini_api_call(payload2_1, api_key, model)
    if res2_1:
        candidates = res2_1.get("candidates", [])
        if candidates and candidates[0].get("content", {}).get("parts", []):
            model_parts2_1 = candidates[0]["content"]["parts"]
            time.sleep(2.5)
            
            prompt2_2 = f"""Analyze the retrieved text blocks.
Strict Rules:
1. If the information does not concern {year}, set "year_found": false and all dates to null.
2. EXCEPTION: If the text explicitly states that dates concern edition {year} + 1, and there is no trace of edition {year}, accept the data, but return "year_found" as an integer (e.g. {year + 1}) instead of a boolean.
3. Do not guess dates. If TBD or TBA, set to null.

Generate ONLY a JSON object (no surrounding text) with the following structure:
{{
  "conference": {{"acronym": "{acronym}", "year_found": true/false or integer, "timezone": "e.g. AoE" or null}},
  "scope_and_topics": {{"short_description": "Short description or null", "topics": ["topic 1", "topic 2"]}},
  "main_track_dates": {{"abstract_submission": "YYYY-MM-DD" or null, "paper_submission": "YYYY-MM-DD" or null, "notification": "YYYY-MM-DD" or null}},
  "other_tracks": [{{"track_name": "track name", "submission_date": "YYYY-MM-DD" or null}}],
  "source_url": "URL of the found source",
  "confidence_score": 10
}}
"""
            payload2_2 = {
                "contents": [
                    {"role": "user", "parts": [{"text": prompt2_1}]},
                    {"role": "model", "parts": model_parts2_1},
                    {"role": "user", "parts": [{"text": prompt2_2}]}
                ],
                "tools": [{"googleSearch": {}}]
            }
            
            print(f"  [Harvest Phase 2] JSON Extraction & Analysis...")
            res2_2 = make_grounded_gemini_api_call(payload2_2, api_key, model)
            if res2_2:
                candidates2 = res2_2.get("candidates", [])
                if candidates2 and candidates2[0].get("content", {}).get("parts", []):
                    raw_json = candidates2[0]["content"]["parts"][-1].get("text", "").strip()
                    parsed = parse_extracted_json(raw_json)
                    if parsed:
                        source_url = parsed.get("source_url")
                        if not source_url or source_url == "N/A" or "grounding-api-redirect" in source_url:
                            source_url = get_metadata_url(candidates2[0])
                        parsed["source_url"] = resolve_redirect_url(source_url)
                        parsed = sanitize_extracted_dates(parsed, year)
                        
                        conf_year_found = parsed.get("conference", {}).get("year_found", False)
                        paper_sub = parsed.get("main_track_dates", {}).get("paper_submission")
                        
                        if conf_year_found and paper_sub:
                            print(f"  -> SUCCESS (Harvest Phase 2): Found on aggregator.")
                            parsed["status_detail"] = "Success (Fallback Aggregators)"
                        else:
                            print(f"  -> INCOMPLETE / NOT FOUND: Dates not found.")
                            parsed["status_detail"] = "Wrong Edition / Date Not Found"
                        return parsed

    return None


# ==========================================
# TODO 3 & 5: VERIFICATION VIA LLM DELEGATION
# ==========================================
def verify_done_conference(acronym, entry, target_year, api_key, model):
    """
    Executes TODO 3 & 5: Delegates webpage inspection directly to Gemma's browsing capability.
    Uses exact prompt to check for deadline extensions on source_url.
    """
    source_url = entry.get("url", "N/A")
    if not source_url or source_url == "N/A" or not source_url.startswith("http"):
        print(f"  -> Verification impossible for {acronym}: URL missing or invalid ({source_url}). Demoted to INCOMPLETE.")
        entry["status"] = "INCOMPLETE"
        entry["status_detail"] = "URL missing for verification"
        entry["last_checked"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        return {"url_invalid_demoted": True}

    old_abstract_date = entry.get("abstract_deadline") or "N/A"
    old_paper_date = entry.get("submission_deadline") or "N/A"

    prompt_verification = f"""[Active Internet Search Mode]
Use your web browsing capabilities to inspect this specific page: {source_url}
This is the official website for conference {acronym} {target_year}.

Previously extracted Main Track dates:
- Abstract: {old_abstract_date}
- Full Paper: {old_paper_date}

Your mission is to read the page to verify if these dates are still current or if a deadline extension has been announced (often indicated by "Deadline Extended" or "Firm Deadline").

Rules:
1. If the dates displayed on the page match the old ones, return them as is.
2. If the text mentions extended deadlines, return the NEW dates.
3. Do not guess. If the page fails to load or the edition year differs, return null.

Return ONLY strict JSON (no markdown fences or explanatory text) with the following structure:
{{
  "main_track_dates": {{
    "abstract_submission": "YYYY-MM-DD" or null,
    "paper_submission": "YYYY-MM-DD" or null
  }},
  "is_extended": true/false,
  "confidence_score": 10
}}
"""
    payload_verify = {
        "contents": [{"role": "user", "parts": [{"text": prompt_verification}]}],
        "tools": [{"googleSearch": {}}]
    }

    print(f"  [Verify LLM] Querying Gemma browsing for {acronym} ({source_url})...")
    res = make_grounded_gemini_api_call(payload_verify, api_key, model)

    if res:
        candidates = res.get("candidates", [])
        if candidates and candidates[0].get("content", {}).get("parts", []):
            raw_json = candidates[0]["content"]["parts"][-1].get("text", "").strip()
            parsed = parse_extracted_json(raw_json)
            return sanitize_extracted_dates(parsed, target_year)

    return None


# ==========================================
# TODO 4: CONFLICT RESOLUTION LOGIC
# ==========================================
def process_verification_result(entry, verify_res, today_str):
    """
    Applies TODO 4 Conflict Resolution Rules:
      - New == Old -> Update last_checked
      - New > Old  -> Update deadline dates, set extended_deadline=True
      - New < Old or New is null -> Keep DB dates, set needs_manual_review=True
    """
    old_paper_str = entry.get("submission_deadline")
    
    if isinstance(verify_res, dict) and verify_res.get("url_invalid_demoted"):
        # Entry status was already updated to INCOMPLETE in verify_done_conference
        return entry

    if not verify_res:
        print("  -> Verification response invalid. Flagging for manual review.")
        entry["needs_manual_review"] = True
        entry["last_checked"] = today_str
        return entry

    main_dates = verify_res.get("main_track_dates", {})
    new_paper_str = main_dates.get("paper_submission")
    new_abstract_str = main_dates.get("abstract_submission")

    # If new paper date is missing or null
    if not new_paper_str or new_paper_str == "null" or new_paper_str == "N/A":
        print(f"  -> Verification returned null/missing date. Keeping old date ({old_paper_str}) and flagging needs_manual_review.")
        entry["needs_manual_review"] = True
        entry["last_checked"] = today_str
        return entry

    # Parse and compare dates
    try:
        old_paper_date = datetime.strptime(old_paper_str, "%Y-%m-%d").date() if old_paper_str else None
        new_paper_date = datetime.strptime(new_paper_str, "%Y-%m-%d").date()
    except ValueError:
        print(f"  -> Date parsing error (Old: {old_paper_str}, New: {new_paper_str}). Flagging needs_manual_review.")
        entry["needs_manual_review"] = True
        entry["last_checked"] = today_str
        return entry

    if old_paper_date is None:
        # DB had no submission date, set new date
        entry["submission_deadline"] = new_paper_str
        if new_abstract_str and new_abstract_str != "null":
            entry["abstract_deadline"] = new_abstract_str
        entry["last_checked"] = today_str
        return entry

    if new_paper_date == old_paper_date:
        print(f"  -> Verification CONFIRMED: Submission deadline remains {old_paper_str}.")
        entry["last_checked"] = today_str
    elif new_paper_date > old_paper_date:
        print(f"  -> EXTENSION DETECTED! Deadline extended from {old_paper_str} to {new_paper_str}.")
        entry["submission_deadline"] = new_paper_str
        if new_abstract_str and new_abstract_str != "null":
            entry["abstract_deadline"] = new_abstract_str
        entry["extended_deadline"] = True
        entry["last_checked"] = today_str
    else: # new_paper_date < old_paper_date
        print(f"  -> DISCREPANCY DETECTED: New date ({new_paper_str}) is earlier than Old date ({old_paper_str}). Flagging needs_manual_review.")
        entry["needs_manual_review"] = True
        entry["last_checked"] = today_str

    return entry


# ==========================================
# MAIN EXECUTION PIPELINE
# ==========================================
def main():
    config = load_config()
    api_key = config.get("gemini_api_key")
    model = config.get("model_id")

    if not api_key:
        print("Error: GEMINI_API_KEY environment variable or config key is missing.")
        sys.exit(1)

    print("=" * 60)
    print("STARTING AUTONOMOUS CONFERENCE DATABASE PIPELINE")
    print(f"Timestamp   : {datetime.now(timezone.utc).isoformat()}")
    print(f"Model ID    : {model}")
    print(f"API Target  : Max {MAX_API_CALLS_HARD_LIMIT} calls (Kill switch at {KILL_SWITCH_THRESHOLD})")
    print("=" * 60)

    # Load Database
    db = load_database()

    # Process yearly rollover before selecting batch
    process_yearly_rollover(db)

    # Select batch to process today
    batch = select_batch_to_process(db, CORE_CSV_PATH, max_batch=MAX_BATCH_SIZE)

    if not batch:
        print("No conferences to process today. Exiting.")
        sys.exit(0)

    processed_count = 0

    for item, reason, action_type in batch:
        acronym = item["acronym"]
        name = item["name"]
        rank = item["rank"]
        year = item["year"]

        processed_count += 1
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        print(f"\n[{processed_count}/{len(batch)}] Processing {acronym} ({name}, Rank {rank}) [{reason}]...")

        if action_type == "VERIFY":
            # Execution of TODO 3 & 4 for DONE conferences
            entry = db.get(acronym, {})
            verify_res = verify_done_conference(acronym, entry, year, api_key, model)
            db[acronym] = process_verification_result(entry, verify_res, today_str)

        else: # HARVEST mode (PENDING, INCOMPLETE, NOT_FOUND)
            extracted = extract_dates_cascade(acronym, name, year, api_key, model)

            if extracted:
                conf_year_found = extracted.get("conference", {}).get("year_found", False)
                paper_sub = extracted.get("main_track_dates", {}).get("paper_submission")
                
                topics_list = extracted.get("scope_and_topics", {}).get("topics", [])
                topics_str = ", ".join(topics_list) if isinstance(topics_list, list) else "N/A"

                # Check for year > target_year detection (TODO 5)
                if isinstance(conf_year_found, int) and conf_year_found > year:
                    print(f"  -> Anticipated Detection: Found edition {conf_year_found} instead of {year}!")
                    year = conf_year_found
                    conf_year_found = True

                valid_url = (extracted.get("source_url") not in [None, "N/A", "null", ""]) and str(extracted.get("source_url", "")).startswith("http")
                if conf_year_found and paper_sub and valid_url:
                    state_status = "DONE"
                elif conf_year_found or paper_sub or valid_url or extracted.get("source_url") not in [None, "N/A"]:
                    state_status = "INCOMPLETE"
                else:
                    state_status = "NOT_FOUND"

                entry = {
                    "status": state_status,
                    "status_detail": extracted.get("status_detail", state_status),
                    "last_checked": today_str,
                    "manual_override": db.get(acronym, {}).get("manual_override", False),
                    "hibernate_until": db.get(acronym, {}).get("hibernate_until"),
                    "acronym": acronym,
                    "target_year": year,
                    "year": year,
                    "name": name,
                    "rank": rank,
                    "url": extracted.get("source_url", "N/A"),
                    "abstract_deadline": extracted.get("main_track_dates", {}).get("abstract_submission"),
                    "submission_deadline": paper_sub,
                    "notification_date": extracted.get("main_track_dates", {}).get("notification"),
                    "timezone": extracted.get("conference", {}).get("timezone"),
                    "topics": topics_str,
                    "short_description": extracted.get("scope_and_topics", {}).get("short_description", "N/A"),
                    "other_tracks": str(extracted.get("other_tracks", [])),
                    "confidence_score": extracted.get("confidence_score", 0)
                }
            else:
                state_status = "NOT_FOUND"
                entry = {
                    "status": state_status,
                    "status_detail": "Grounded Search Failed",
                    "last_checked": today_str,
                    "manual_override": db.get(acronym, {}).get("manual_override", False),
                    "hibernate_until": db.get(acronym, {}).get("hibernate_until"),
                    "acronym": acronym,
                    "target_year": year,
                    "year": year,
                    "name": name,
                    "rank": rank,
                    "url": "N/A",
                    "abstract_deadline": None,
                    "submission_deadline": None,
                    "notification_date": None,
                    "timezone": None,
                    "topics": "N/A",
                    "short_description": "N/A",
                    "other_tracks": "N/A",
                    "confidence_score": 0
                }

            db[acronym] = entry

        # Progressive atomic save after every conference
        save_database(db)

        # KILL SWITCH CHECK
        if api_calls_count >= KILL_SWITCH_THRESHOLD:
            print("\n" + "!" * 60)
            print(f"[KILL SWITCH ACTIVATED] API Calls count ({api_calls_count}) reached threshold ({KILL_SWITCH_THRESHOLD}).")
            print("Cleanly stopping batch loop and saving state...")
            print("!" * 60)
            break

        time.sleep(3.5)  # Rate limit safety delay

    # Final Save
    save_database(db)
    print("\n" + "=" * 60)
    print("FINISHED DAILY PIPELINE BATCH")
    print(f"Total API Calls Executed : {api_calls_count}")
    print(f"Total Database Entries   : {len(db)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
