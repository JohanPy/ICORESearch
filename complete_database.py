#!/usr/bin/env python3
import os
import re
import sys
import yaml
import time
import json
import argparse
import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
OUTPUT_CSV_PATH = os.path.join(BASE_DIR, "conferences_filtrees.csv")

def load_config():
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

def make_grounded_gemini_api_call(payload, api_key, model, max_retries=4):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=90)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
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
            print(f"  Exception querying API: {e}")
            time.sleep(2)
            
    return None

def parse_extracted_json(raw_json):
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
        print(f"  JSON parsing exception: {e}")
        return None

def extract_from_url(acronym, name, year, target_url, api_key, model):
    prompt1 = f"""[Mode Recherche Internet Actif]
Rends-toi sur l'URL suivante qui est le site de la conférence {acronym} {year} ({name}) : {target_url}

Extrais textuellement les blocs concernant :
- Les dates de la "Main Track" (Abstract, Full Paper, Notification).
- Les autres dates (Workshops, Tutorials).
- Le petit descriptif ou "Call for Papers" listant les thèmes (Topics).

Affiche simplement les extraits bruts.
"""
    
    payload1 = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt1}]}
        ],
        "tools": [{"googleSearch": {}}]
    }
    
    print(f"  Extraction depuis l'URL: {target_url}")
    res_data1 = make_grounded_gemini_api_call(payload1, api_key, model)
    if res_data1:
        candidates = res_data1.get("candidates", [])
        if candidates and candidates[0].get("content", {}).get("parts", []):
            model_response1_parts = candidates[0]["content"]["parts"]
            
            time.sleep(2.0)
            
            prompt2 = f"""Analyse les blocs de texte récupérés. 
Règles strictes :
1. Si l'information ne concerne pas {year}, mets "year_found": false et toutes les dates à null.
2. Ne devine pas les dates. Si c'est TBD ou TBA, mets null.

Génère UNIQUEMENT un objet JSON (sans texte autour) avec la structure suivante :
{{
  "conference": {{"acronym": "{acronym}", "year_found": true/false, "timezone": "ex: AoE" ou null}},
  "scope_and_topics": {{"short_description": "Description courte ou null", "topics": ["thème 1", "thème 2"]}},
  "main_track_dates": {{"abstract_submission": "YYYY-MM-DD" ou null, "paper_submission": "YYYY-MM-DD" ou null, "notification": "YYYY-MM-DD" ou null}},
  "other_tracks": [{{"track_name": "nom du track", "submission_date": "YYYY-MM-DD" ou null}}],
  "confidence_score": 10
}}
"""
            payload2 = {
                "contents": [
                    {"role": "user", "parts": [{"text": prompt1}]},
                    {"role": "model", "parts": model_response1_parts},
                    {"role": "user", "parts": [{"text": prompt2}]}
                ],
                "tools": [{"googleSearch": {}}]
            }
            
            print("  Analyse critique et génération JSON...")
            res_data2 = make_grounded_gemini_api_call(payload2, api_key, model)
            if res_data2:
                candidates2 = res_data2.get("candidates", [])
                if candidates2 and candidates2[0].get("content", {}).get("parts", []):
                    model_response2_parts = candidates2[0]["content"]["parts"]
                    raw_json = model_response2_parts[-1].get("text", "").strip()
                    
                    parsed = parse_extracted_json(raw_json)
                    if parsed:
                        conf_year_found = parsed.get("conference", {}).get("year_found", False)
                        if conf_year_found:
                            print("  -> Extraction réussie depuis l'URL.")
                            parsed["status"] = "Success (Completed via URL)"
                        else:
                            print("  -> Année non confirmée sur l'URL.")
                            parsed["status"] = "Failed (Year mismatch)"
                        return parsed
    return None

def is_missing_data(row):
    # Consider row for completion if important fields are missing
    sub_date = str(row.get("Submission Deadline", "")).strip()
    notif_date = str(row.get("Notification Date", "")).strip()
    topics = str(row.get("Topics", "")).strip()
    
    missing_sub = (not sub_date or sub_date == "nan" or sub_date == "N/A" or sub_date == "None")
    missing_notif = (not notif_date or notif_date == "nan" or notif_date == "N/A" or notif_date == "None")
    missing_topics = (not topics or topics == "nan" or topics == "N/A" or topics == "None")
    
    # We want to complete it if it lacks either submission, notification, or topics
    return missing_sub or missing_notif or missing_topics

def main():
    print("=" * 60)
    print("      ICORE DATABASE COMPLETION SCRIPT")
    print("=" * 60)
    config = load_config()
    
    if not config["gemini_api_key"]:
        print("Error: gemini_api_key is required. Set it in config.yaml or GEMINI_API_KEY environment variable.")
        sys.exit(1)
        
    if not os.path.exists(OUTPUT_CSV_PATH):
        print(f"Error: {OUTPUT_CSV_PATH} not found.")
        sys.exit(1)
        
    df = pd.read_csv(OUTPUT_CSV_PATH)
    
    # We need to preserve the dataframe and update it in-place
    rows_to_update = []
    
    for idx, row in df.iterrows():
        url = str(row.get("URL", "")).strip()
        status = str(row.get("Status", "")).strip()
        
        # Must have a valid URL and not be marked as a Wrong Edition / completely lost
        if url.startswith("http") and "Wrong Edition" not in status and status != "Failed (Year mismatch)":
            if is_missing_data(row):
                rows_to_update.append(idx)
                
    print(f"Found {len(rows_to_update)} rows to complete.")
    
    if not rows_to_update:
        print("No rows need completion. Exiting.")
        sys.exit(0)
        
    for i, idx in enumerate(rows_to_update):
        row = df.iloc[idx]
        acronym = row.get("Acronym", "")
        year = row.get("Year", "")
        name = row.get("Name", "")
        target_url = row.get("URL", "")
        
        print(f"[{i+1}/{len(rows_to_update)}] Completing {acronym} {year} - {name}")
        
        extracted_data = extract_from_url(acronym, name, year, target_url, config["gemini_api_key"], config["model_id"])
        
        if extracted_data and extracted_data.get("status") == "Success (Completed via URL)":
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
            
            # Update only if new data is not empty/null
            main_dates = extracted_data.get("main_track_dates", {})
            
            if main_dates.get("abstract_submission"):
                df.at[idx, "Abstract Deadline"] = main_dates.get("abstract_submission")
            if main_dates.get("paper_submission"):
                df.at[idx, "Submission Deadline"] = main_dates.get("paper_submission")
            if main_dates.get("notification"):
                df.at[idx, "Notification Date"] = main_dates.get("notification")
                
            tz = extracted_data.get("conference", {}).get("timezone")
            if tz:
                df.at[idx, "Timezone"] = tz
                
            if topics and topics != "N/A":
                df.at[idx, "Topics"] = topics
            if short_desc and short_desc != "N/A":
                df.at[idx, "Short Description"] = short_desc
            if other_tracks and other_tracks != "N/A":
                df.at[idx, "Other Tracks"] = other_tracks
                
            df.at[idx, "Status"] = "Success (Completed via URL)"
            
        # Progressive save
        df.to_csv(OUTPUT_CSV_PATH, index=False)
        time.sleep(3.5) # respect rate limits
        
    print("=" * 60)
    print(f"Completion run finished. Updated {OUTPUT_CSV_PATH}")
    print("=" * 60)

if __name__ == "__main__":
    main()
