#!/usr/bin/env python3
import os
import re
import sys
import yaml
import time
import json
import argparse
from datetime import datetime
import pandas as pd
import requests
from urllib.parse import urlparse

# --- GLOBAL STATISTICS ---
RUN_STATS = {
    "total_matched": 0,
    "skipped_cache": 0,
    "processed_new": 0,
    "api_429_errors": 0,
    "api_timeouts": 0,
    "api_json_errors": 0,
    "phase1_success": 0,
    "phase2_success": 0,
    "failures_wrong_edition": 0,
    "failures_grounded_search": 0,
    "source_domains": {},
    "missing_submission_date": 0,
    "missing_notification_date": 0,
    "api_response_times": []
}

# Define default paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
CORE_CSV_PATH = os.path.join(BASE_DIR, "CORE_all26.csv")
FOR_CSV_PATH = os.path.join(BASE_DIR, "FoRcode_details.csv")
OUTPUT_CSV_PATH = os.path.join(BASE_DIR, "conferences_filtrees.csv")

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

def get_group_codes_from_theme(theme_query):
    """
    Search FoRcode_details.csv for a list of comma-separated theme queries.
    It can be a 4-digit code, a 6-digit code, or a substring in the name.
    Returns a set of 4-digit/CSE group codes to match against the CORE database.
    """
    if not os.path.exists(FOR_CSV_PATH):
        print(f"Error: {FOR_CSV_PATH} not found. Please run the extraction first.")
        return set()
        
    df = pd.read_csv(FOR_CSV_PATH)
    
    # Split the query by commas
    queries = [q.strip() for q in str(theme_query).split(",") if q.strip()]
    group_codes = set()
    
    for query in queries:
        query_str = query.lower()
        # 1. Check exact match on code
        exact_match = df[df["code"].astype(str).str.lower() == query_str]
        
        # 2. Check substring match on name
        name_match = df[df["name"].astype(str).str.lower().str.contains(query_str, na=False)]
        
        combined = pd.concat([exact_match, name_match]).drop_duplicates()
        
        if combined.empty:
            print(f"Warning: No exact theme matching '{query}' found in {FOR_CSV_PATH}. Using '{query}' raw.")
            group_codes.add(query.upper())
            continue
            
        sub_group_codes = set()
        for _, row in combined.iterrows():
            level = row["level"]
            code = str(row["code"])
            parent = str(row["parent_code"])
            
            if level == "Group":
                sub_group_codes.add(code)
            elif level == "Subarea":
                if parent and parent != "nan" and parent != "None" and parent != "":
                    sub_group_codes.add(parent)
                else:
                    if len(code) == 6 and code.isdigit():
                        sub_group_codes.add(code[:4])
                    else:
                        sub_group_codes.add(code)
                        
        print(f"Theme '{query}' matched the following FoR Group Code(s): {sub_group_codes}")
        group_codes.update(sub_group_codes)
        
    print(f"Combined matching FoR Group Code(s): {group_codes}")
    return group_codes


def make_grounded_gemini_api_call(payload, api_key, model, max_retries=4):
    """Query Gemini/Gemma API with Google Search grounding and handle rate limit retries."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    
    for attempt in range(max_retries):
        try:
            # High timeout of 90 seconds because grounding performs external web searches
            start_req = time.time()
            response = requests.post(url, json=payload, headers=headers, timeout=90)
            req_time = round(time.time() - start_req, 2)
            RUN_STATS["api_response_times"].append(req_time)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                RUN_STATS["api_429_errors"] += 1
                retry_after = 5.0
                try:
                    err_data = response.json()
                    message = err_data.get("error", {}).get("message", "")
                    match = re.search(r"retry in ([\d\.]+)s", message)
                    if match:
                        retry_after = float(match.group(1)) + 0.5
                except Exception:
                    pass
                print(f"  API returned 429 (Rate Limited). Retrying in {retry_after:.2f}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(retry_after)
            else:
                print(f"  API error ({response.status_code}): {response.text}")
                if response.status_code >= 500:
                    time.sleep(2)
                else:
                    break
        except Exception as e:
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str:
                RUN_STATS["api_timeouts"] += 1
            print(f"  Exception querying API: {e}")
            time.sleep(2)
            
    return None

def resolve_redirect_url(url, timeout=10):
    """Resolve redirect URLs (like Vertex grounding redirect links) to the final target URL."""
    if not url or url == "N/A" or "grounding-api-redirect" not in url:
        return url
    try:
        # Perform a HEAD request which is fast and does not download the body
        response = requests.head(url, allow_redirects=True, timeout=timeout)
        return response.url
    except Exception:
        # Fallback to GET if HEAD fails
        try:
            response = requests.get(url, allow_redirects=True, timeout=timeout)
            return response.url
        except Exception:
            pass
    return url

def parse_extracted_json(raw_json):
    """Clean markdown backticks and parse JSON string."""
    if not raw_json:
        return None
    cleaned = raw_json.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except Exception as e:
        RUN_STATS["api_json_errors"] += 1
        print(f"  JSON parsing exception: {e}")
        return None

def get_metadata_url(candidate):
    """Extract grounding chunk URL from candidate metadata if available."""
    g_meta = candidate.get("groundingMetadata", {})
    g_chunks = g_meta.get("groundingChunks", [])
    if g_chunks:
        for chunk in g_chunks:
            uri = chunk.get("web", {}).get("uri")
            if uri:
                return uri
    return "N/A"

def extract_dates_cascade(acronym, name, year, api_key, model):
    """
    Implements the Cascade Strategy:
    Phase 1: Query the official website strictly.
    If year_found is True and paper_submission is not None:
        return result.
    Phase 2: Open a new session, query WikiCFP and other aggregators.
    """
    # ------------------ PHASE 1: SITE OFFICIEL ------------------
    prompt1_1 = f"""[Mode Recherche Internet Actif]
Trouve le site officiel de l'édition {year} de la conférence {acronym} ({name}).

RÈGLES DE RECHERCHE STRICTES :
1. Cherche UNIQUEMENT sur le site officiel de la conférence (souvent une URL contenant l'acronyme et l'année).
2. IGNORE totalement les sites comme WikiCFP, Research.com, Call4Papers, ou tout autre annuaire.
3. IGNORE les éditions passées. Nous voulons strictement l'édition {year}.

Une fois le bon site officiel trouvé, extrais textuellement les blocs concernant :
- Les dates de la "Main Track" (Abstract, Full Paper, Notification).
- Les autres dates (Workshops, Tutorials).
- Le petit descriptif ou "Call for Papers" listant les thèmes (Topics).

Affiche simplement les extraits bruts avec l'URL du site officiel en source.
"""
    
    payload1_1 = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt1_1}]}
        ],
        "tools": [{"googleSearch": {}}]
    }
    
    print("  Phase 1 : Recherche sur le site officiel strict...")
    res_data1_1 = make_grounded_gemini_api_call(payload1_1, api_key, model)
    if res_data1_1:
        candidates = res_data1_1.get("candidates", [])
        if candidates and candidates[0].get("content", {}).get("parts", []):
            model_response1_1_parts = candidates[0]["content"]["parts"]
            
            # Short wait before the second prompt to respect rate limits
            time.sleep(2.0)
            
            prompt1_2 = f"""Analyse les blocs de texte récupérés. 
Utilise ton mode de raisonnement (Thinking) pour t'assurer que les informations proviennent bien du site officiel de {year}.

Règles strictes :
1. Si l'information ne concerne pas {year}, ou si tu n'as pas trouvé de site officiel valide, mets "year_found": false et toutes les dates à null.
2. Ne devine pas les dates. Si c'est TBD ou TBA, mets null.

Génère UNIQUEMENT un objet JSON (sans texte autour) avec la structure suivante :
{{
  "conference": {{"acronym": "{acronym}", "year_found": true/false, "timezone": "ex: AoE" ou null}},
  "scope_and_topics": {{"short_description": "Description courte ou null", "topics": ["thème 1", "thème 2"]}},
  "main_track_dates": {{"abstract_submission": "YYYY-MM-DD" ou null, "paper_submission": "YYYY-MM-DD" ou null, "notification": "YYYY-MM-DD" ou null}},
  "other_tracks": [{{"track_name": "nom du track", "submission_date": "YYYY-MM-DD" ou null}}],
  "source_url": "URL du site officiel",
  "confidence_score": 10
}}
"""
            payload1_2 = {
                "contents": [
                    {"role": "user", "parts": [{"text": prompt1_1}]},
                    {"role": "model", "parts": model_response1_1_parts},
                    {"role": "user", "parts": [{"text": prompt1_2}]}
                ],
                "tools": [{"googleSearch": {}}]
            }
            
            print("  Phase 1 : Extraction JSON et analyse critique...")
            res_data1_2 = make_grounded_gemini_api_call(payload1_2, api_key, model)
            if res_data1_2:
                candidates2 = res_data1_2.get("candidates", [])
                if candidates2 and candidates2[0].get("content", {}).get("parts", []):
                    model_response1_2_parts = candidates2[0]["content"]["parts"]
                    raw_json = model_response1_2_parts[-1].get("text", "").strip()
                    
                    parsed = parse_extracted_json(raw_json)
                    if parsed:
                        source_url = parsed.get("source_url")
                        if not source_url or source_url == "N/A" or "grounding-api-redirect" in source_url:
                            source_url = get_metadata_url(candidates2[0])
                        parsed["source_url"] = resolve_redirect_url(source_url)
                        
                        conf_year_found = parsed.get("conference", {}).get("year_found", False)
                        paper_sub = parsed.get("main_track_dates", {}).get("paper_submission")
                        
                        if conf_year_found and paper_sub:
                            print("  -> Succès Phase 1 ! Site officiel et date de soumission trouvés.")
                            parsed["status"] = "Success (Official Site)"
                            RUN_STATS["phase1_success"] += 1
                            domain = urlparse(parsed["source_url"]).netloc
                            if domain:
                                RUN_STATS["source_domains"][domain] = RUN_STATS["source_domains"].get(domain, 0) + 1
                            return parsed
                        else:
                            print("  -> Date non trouvée ou année incorrecte en Phase 1. Lancement de la Phase 2 Fallback...")

    # Short wait before starting Phase 2 (to respect 30 RPM rate limits)
    time.sleep(3.5)
    
    # ------------------ PHASE 2: FALLBACK ------------------
    prompt2_1 = f"""[Mode Recherche Internet Actif]
Nous n'avons pas pu trouver les dates sur le site officiel pour l'édition {year} de la conférence {acronym} ({name}).

Cherche spécifiquement ces informations sur les agrégateurs académiques :
1. Cherche en priorité sur WikiCFP (utilise la requête: site:wikicfp.com "{acronym} {year}").
2. Cherche également sur Research.com ou d'autres annuaires.

Extrais textuellement les blocs mentionnant les dates (Abstract, Paper, Notification) et les thèmes (Topics) pour l'édition {year}. Affiche les blocs et les URLs sources.
"""
    payload2_1 = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt2_1}]}
        ],
        "tools": [{"googleSearch": {}}]
    }
    
    print("  Phase 2 : Recherche sur agrégateurs (WikiCFP)...")
    res_data2_1 = make_grounded_gemini_api_call(payload2_1, api_key, model)
    if res_data2_1:
        candidates = res_data2_1.get("candidates", [])
        if candidates and candidates[0].get("content", {}).get("parts", []):
            model_response2_1_parts = candidates[0]["content"]["parts"]
            
            time.sleep(2.0)
            
            prompt2_2 = f"""Analyse les blocs de texte récupérés. 
Utilise ton mode de raisonnement (Thinking) pour t'assurer que les informations proviennent de sources fiables de l'édition {year}.

Règles strictes :
1. Si l'information ne concerne pas {year}, ou si tu n'as pas trouvé d'informations valides, mets "year_found": false et toutes les dates à null.
2. Ne devine pas les dates. Si c'est TBD ou TBA, mets null.

Génère UNIQUEMENT un objet JSON (sans texte autour) avec la structure suivante :
{{
  "conference": {{"acronym": "{acronym}", "year_found": true/false, "timezone": "ex: AoE" ou null}},
  "scope_and_topics": {{"short_description": "Description courte ou null", "topics": ["thème 1", "thème 2"]}},
  "main_track_dates": {{"abstract_submission": "YYYY-MM-DD" ou null, "paper_submission": "YYYY-MM-DD" ou null, "notification": "YYYY-MM-DD" ou null}},
  "other_tracks": [{{"track_name": "nom du track", "submission_date": "YYYY-MM-DD" ou null}}],
  "source_url": "URL de la source trouvée",
  "confidence_score": 10
}}
"""
            payload2_2 = {
                "contents": [
                    {"role": "user", "parts": [{"text": prompt2_1}]},
                    {"role": "model", "parts": model_response2_1_parts},
                    {"role": "user", "parts": [{"text": prompt2_2}]}
                ],
                "tools": [{"googleSearch": {}}]
            }
            
            print("  Phase 2 : Extraction JSON et analyse critique...")
            res_data2_2 = make_grounded_gemini_api_call(payload2_2, api_key, model)
            if res_data2_2:
                candidates2 = res_data2_2.get("candidates", [])
                if candidates2 and candidates2[0].get("content", {}).get("parts", []):
                    model_response2_2_parts = candidates2[0]["content"]["parts"]
                    raw_json = model_response2_2_parts[-1].get("text", "").strip()
                    
                    parsed = parse_extracted_json(raw_json)
                    if parsed:
                        source_url = parsed.get("source_url")
                        if not source_url or source_url == "N/A" or "grounding-api-redirect" in source_url:
                            source_url = get_metadata_url(candidates2[0])
                        parsed["source_url"] = resolve_redirect_url(source_url)
                        
                        conf_year_found = parsed.get("conference", {}).get("year_found", False)
                        paper_sub = parsed.get("main_track_dates", {}).get("paper_submission")
                        
                        if conf_year_found and paper_sub:
                            print("  -> Succès Phase 2 ! Dates trouvées sur agrégateur.")
                            parsed["status"] = "Success (Fallback Aggregators)"
                            RUN_STATS["phase2_success"] += 1
                            domain = urlparse(parsed["source_url"]).netloc
                            if domain:
                                RUN_STATS["source_domains"][domain] = RUN_STATS["source_domains"].get(domain, 0) + 1
                        else:
                            print("  -> Date introuvable sur agrégateurs en Phase 2.")
                            parsed["status"] = "Wrong Edition / Date Not Found"
                            RUN_STATS["failures_wrong_edition"] += 1
                        return parsed
                        
    return None

def main():
    parser = argparse.ArgumentParser(description="Automated Academic Conference Search (ETL using Google Grounding)")
    parser.add_argument("--year", type=int, default=2026, help="Target Year (default: 2026)")
    parser.add_argument("--theme", type=str, required=True, help="FoR Theme Code or Name (e.g. 4602, 'Artificial intelligence', 'Software engineering')")
    parser.add_argument("--rank", type=str, default="A,A*", help="Comma-separated ranks to filter (default: 'A,A*')")
    parser.add_argument("--date-after", type=str, default=None, help="Filter out submissions before this date (YYYY-MM-DD). Defaults to today's date.")
    
    args = parser.parse_args()
    
    if args.date_after is None:
        args.date_after = datetime.today().strftime("%Y-%m-%d")
        
    print("=" * 60)
    print("      ICORE GROUNDED CASCADE TWO-PROMPT SEARCH")
    print("=" * 60)
    config = load_config()
    print(f"Model ID:             {config['model_id']}")
    print(f"Target Year:          {args.year}")
    print(f"Theme Query:          {args.theme}")
    print(f"Target Ranks:         {args.rank}")
    print(f"Deadline Filter >=    {args.date_after}")
    print("-" * 60)
    
    # 1. Load Configurations
    if not config["gemini_api_key"]:
        print("Error: gemini_api_key is required. Set it in config.yaml or GEMINI_API_KEY environment variable.")
        sys.exit(1)
        
    # 2. Map Theme
    target_groups = get_group_codes_from_theme(args.theme)
    if not target_groups:
        print("Error: Could not resolve the theme to any valid FoR code.")
        sys.exit(1)
        
    # 3. Read & Filter CORE Database
    if not os.path.exists(CORE_CSV_PATH):
        print(f"Error: {CORE_CSV_PATH} not found.")
        sys.exit(1)
        
    df_core = pd.read_csv(CORE_CSV_PATH, header=None, names=["id", "name", "acronym", "source", "rank", "active", "for1", "for2", "for3"])
    
    # Filter Ranks
    target_ranks = [r.strip() for r in args.rank.split(",") if r.strip()]
    df_filtered = df_core[df_core["rank"].isin(target_ranks)]
    
    # Filter Theme
    def matches_theme(row):
        for1 = str(row["for1"]).strip()
        for2 = str(row["for2"]).strip()
        for3 = str(row["for3"]).strip()
        return for1 in target_groups or for2 in target_groups or for3 in target_groups

    df_filtered = df_filtered[df_filtered.apply(matches_theme, axis=1)]
    
    total_matches = len(df_filtered)
    print(f"Found {total_matches} conferences matching Rank {args.rank} and Theme {args.theme}.")
    
    if total_matches == 0:
        print("No conferences match the criteria. Exiting.")
        sys.exit(0)
        
    # 4. Load all existing results to build a cumulative database
    accumulated_results = {}
    if os.path.exists(OUTPUT_CSV_PATH):
        try:
            df_existing = pd.read_csv(OUTPUT_CSV_PATH)
            for _, r_exist in df_existing.iterrows():
                acronym = r_exist.get("Acronym")
                
                # Support schema transition: retrieve Year if present, otherwise default to current target year
                year_val = r_exist.get("Year") if "Year" in r_exist else args.year
                if pd.isna(year_val):
                    year_val = args.year
                try:
                    year_key = int(year_val)
                except ValueError:
                    year_key = str(year_val)
                    
                if acronym and pd.notna(acronym):
                    entry_dict = r_exist.to_dict()
                    # Ensure new schema fields are present
                    entry_dict.setdefault("Year", year_key)
                    entry_dict.setdefault("Topics", "N/A")
                    entry_dict.setdefault("Short Description", "N/A")
                    entry_dict.setdefault("Other Tracks", "N/A")
                    
                    # Key is (Acronym, Year)
                    accumulated_results[(acronym, year_key)] = entry_dict
            print(f"Loaded {len(accumulated_results)} existing conference editions from {OUTPUT_CSV_PATH} for cumulative database.")
        except Exception as e:
            print(f"Warning: Could not read existing results from {OUTPUT_CSV_PATH}: {e}")
            
    print("-" * 60)
    
    results = []
    current_year = int(args.year)
    
    RUN_STATS["total_matched"] = total_matches
    # 5. Query the grounded model for each conference
    for idx, row in df_filtered.iterrows():
        acronym = row["acronym"]
        name = row["name"]
        rank = row["rank"]
        
        cache_key = (acronym, current_year)
        
        # Check cache: skip if it's already in the cache (including previous failures)
        if cache_key in accumulated_results:
            print(f"[{len(results)+1}/{total_matches}] Skipping {acronym} (Already in Cache for Year {current_year})")
            results.append(accumulated_results[cache_key])
            RUN_STATS["skipped_cache"] += 1
            continue
            
        print(f"[{len(results)+1}/{total_matches}] Querying {acronym} - {name} (Rank: {rank})...")
        RUN_STATS["processed_new"] += 1
        
        extracted_data = extract_dates_cascade(acronym, name, args.year, config["gemini_api_key"], config["model_id"])
        
        if extracted_data:
            print(f"  Extracted Data: {extracted_data}")
            status = extracted_data.get("status", "Success")
            
            sub_date = extracted_data.get("main_track_dates", {}).get("paper_submission")
            notif_date = extracted_data.get("main_track_dates", {}).get("notification")
            if not sub_date or sub_date == "N/A":
                RUN_STATS["missing_submission_date"] += 1
            if not notif_date or notif_date == "N/A":
                RUN_STATS["missing_notification_date"] += 1
            
            # Extract new fields
            scope_and_topics = extracted_data.get("scope_and_topics", {})
            short_desc = scope_and_topics.get("short_description", "N/A") if scope_and_topics else "N/A"
            topics_list = scope_and_topics.get("topics", []) if scope_and_topics else []
            topics = ", ".join(topics_list) if isinstance(topics_list, list) else "N/A"
            
            other_tracks_list = []
            for track in extracted_data.get("other_tracks", []):
                if isinstance(track, dict):
                    track_name = track.get("track_name", "N/A")
                    sub_date = track.get("submission_date", "N/A")
                    other_tracks_list.append(f"{track_name}: {sub_date}")
            other_tracks = " | ".join(other_tracks_list) if other_tracks_list else "N/A"
            
            res_entry = {
                "Acronym": acronym,
                "Year": current_year,
                "Name": name,
                "Rank": rank,
                "URL": extracted_data.get("source_url", "N/A"),
                "Abstract Deadline": extracted_data.get("main_track_dates", {}).get("abstract_submission"),
                "Submission Deadline": extracted_data.get("main_track_dates", {}).get("paper_submission"),
                "Notification Date": extracted_data.get("main_track_dates", {}).get("notification"),
                "Timezone": extracted_data.get("conference", {}).get("timezone"),
                "Topics": topics,
                "Short Description": short_desc,
                "Other Tracks": other_tracks,
                "Confidence Score": extracted_data.get("confidence_score", 0),
                "Status": status
            }
        else:
            print("  Failed to extract data or parse API response.")
            res_entry = {
                "Acronym": acronym,
                "Year": current_year,
                "Name": name,
                "Rank": rank,
                "URL": "N/A",
                "Abstract Deadline": "N/A",
                "Submission Deadline": "N/A",
                "Notification Date": "N/A",
                "Timezone": "N/A",
                "Topics": "N/A",
                "Short Description": "N/A",
                "Other Tracks": "N/A",
                "Confidence Score": 0,
                "Status": "Grounded Search Failed"
            }
            RUN_STATS["failures_grounded_search"] += 1
            
        results.append(res_entry)
        accumulated_results[cache_key] = res_entry
        
        # Progressive save: write all accumulated results to CSV atomically
        try:
            df_save = pd.DataFrame(list(accumulated_results.values()))
            cols_order = [
                "Acronym", "Year", "Name", "Rank", "URL", 
                "Abstract Deadline", "Submission Deadline", "Notification Date", 
                "Timezone", "Topics", "Short Description", "Other Tracks", 
                "Confidence Score", "Status"
            ]
            actual_cols = [c for c in cols_order if c in df_save.columns]
            tmp_path = OUTPUT_CSV_PATH + ".tmp"
            df_save[actual_cols].to_csv(tmp_path, index=False)
            os.replace(tmp_path, OUTPUT_CSV_PATH)
        except Exception as save_err:
            print(f"  Warning: Progressive save failed: {save_err}")
            
        print()
        # Sleep for 3.5 seconds to respect Gemma's 30 RPM limits safely
        time.sleep(3.5)
        
    # 6. Filter current run results by submission date for console display only
    filtered_results = []
    
    for res in results:
        sub_date_str = res.get("Submission Deadline")
        if pd.isna(sub_date_str) or sub_date_str == "N/A" or sub_date_str is None:
            keep = True
        else:
            try:
                sub_date = datetime.strptime(str(sub_date_str), "%Y-%m-%d")
                filter_date = datetime.strptime(args.date_after, "%Y-%m-%d")
                keep = (sub_date >= filter_date)
            except ValueError:
                keep = True
                
        if keep:
            filtered_results.append(res)
            
    df_final = pd.DataFrame(filtered_results)
    
    print("=" * 60)
    print(f"Incremental run completed. Master database saved to {OUTPUT_CSV_PATH}")
    print(f"Current run matching results: {len(df_final)}")
    print("=" * 60)
    
    # Print a beautiful summary table
    if not df_final.empty:
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 1000)
        display_cols = ["Acronym", "Rank", "Submission Deadline", "Notification Date", "Status", "URL"]
        display_cols = [c for c in display_cols if c in df_final.columns]
        print(df_final[display_cols].to_string(index=False))
    else:
        print("No conferences in this run matched the submission deadline criteria.")
        
    # --- SAVE LOGS ---
    end_time = time.time()
    RUN_STATS["execution_time_seconds"] = round(end_time - start_time, 2)
    RUN_STATS["timestamp"] = datetime.now().isoformat()
    RUN_STATS["theme_query"] = args.theme
    
    log_file = os.path.join(BASE_DIR, "execution_logs.jsonl")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(RUN_STATS) + "\n")
        print(f"\n[INFO] Statistiques d'exécution sauvegardées dans {log_file}")
    except Exception as e:
        print(f"\n[ERREUR] Impossible de sauvegarder les logs : {e}")

if __name__ == "__main__":
    start_time = time.time()
    main()
