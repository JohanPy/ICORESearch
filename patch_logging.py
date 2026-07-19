import os

filepath = "/home/killersky4/Documents/IUT/codeOnGit/ICORESearch/search_conferences.py"
with open(filepath, "r") as f:
    content = f.read()

replacements = [
    (
"""import requests

# Define default paths""",
"""import requests
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
    "missing_notification_date": 0
}

# Define default paths"""
    ),
    (
"""            elif response.status_code == 429:
                retry_after = 5.0""",
"""            elif response.status_code == 429:
                RUN_STATS["api_429_errors"] += 1
                retry_after = 5.0"""
    ),
    (
"""        except Exception as e:
            print(f"  Exception querying API: {e}")
            time.sleep(2)""",
"""        except Exception as e:
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str:
                RUN_STATS["api_timeouts"] += 1
            print(f"  Exception querying API: {e}")
            time.sleep(2)"""
    ),
    (
"""    try:
        return json.loads(cleaned)
    except Exception as e:
        print(f"  JSON parsing exception: {e}")
        return None""",
"""    try:
        return json.loads(cleaned)
    except Exception as e:
        RUN_STATS["api_json_errors"] += 1
        print(f"  JSON parsing exception: {e}")
        return None"""
    ),
    (
"""                        if conf_year_found and paper_sub:
                            print("  -> Succès Phase 1 ! Site officiel et date de soumission trouvés.")
                            parsed["status"] = "Success (Official Site)"
                            return parsed""",
"""                        if conf_year_found and paper_sub:
                            print("  -> Succès Phase 1 ! Site officiel et date de soumission trouvés.")
                            parsed["status"] = "Success (Official Site)"
                            RUN_STATS["phase1_success"] += 1
                            domain = urlparse(parsed["source_url"]).netloc
                            if domain:
                                RUN_STATS["source_domains"][domain] = RUN_STATS["source_domains"].get(domain, 0) + 1
                            return parsed"""
    ),
    (
"""                        if conf_year_found and paper_sub:
                            print("  -> Succès Phase 2 ! Dates trouvées sur agrégateur.")
                            parsed["status"] = "Success (Fallback Aggregators)"
                        else:
                            print("  -> Date introuvable sur agrégateurs en Phase 2.")
                            parsed["status"] = "Wrong Edition / Date Not Found"
                        return parsed""",
"""                        if conf_year_found and paper_sub:
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
                        return parsed"""
    ),
    (
"""    # 5. Query the grounded model for each conference
    for idx, row in df_filtered.iterrows():""",
"""    RUN_STATS["total_matched"] = total_matches
    # 5. Query the grounded model for each conference
    for idx, row in df_filtered.iterrows():"""
    ),
    (
"""        if cache_key in accumulated_results and accumulated_results[cache_key].get("Status") not in ["Grounded Search Failed", "Wrong Edition / Date Not Found"]:
            print(f"[{len(results)+1}/{total_matches}] Skipping {acronym} (Already in Cache for Year {current_year})")
            results.append(accumulated_results[cache_key])
            continue
            
        print(f"[{len(results)+1}/{total_matches}] Querying {acronym} - {name} (Rank: {rank})...")""",
"""        if cache_key in accumulated_results and accumulated_results[cache_key].get("Status") not in ["Grounded Search Failed", "Wrong Edition / Date Not Found"]:
            print(f"[{len(results)+1}/{total_matches}] Skipping {acronym} (Already in Cache for Year {current_year})")
            results.append(accumulated_results[cache_key])
            RUN_STATS["skipped_cache"] += 1
            continue
            
        print(f"[{len(results)+1}/{total_matches}] Querying {acronym} - {name} (Rank: {rank})...")
        RUN_STATS["processed_new"] += 1"""
    ),
    (
"""        if extracted_data:
            print(f"  Extracted Data: {extracted_data}")
            status = extracted_data.get("status", "Success")""",
"""        if extracted_data:
            print(f"  Extracted Data: {extracted_data}")
            status = extracted_data.get("status", "Success")
            
            sub_date = extracted_data.get("main_track_dates", {}).get("paper_submission")
            notif_date = extracted_data.get("main_track_dates", {}).get("notification")
            if not sub_date or sub_date == "N/A":
                RUN_STATS["missing_submission_date"] += 1
            if not notif_date or notif_date == "N/A":
                RUN_STATS["missing_notification_date"] += 1"""
    ),
    (
"""                "Confidence Score": 0,
                "Status": "Grounded Search Failed"
            }
            
        results.append(res_entry)""",
"""                "Confidence Score": 0,
                "Status": "Grounded Search Failed"
            }
            RUN_STATS["failures_grounded_search"] += 1
            
        results.append(res_entry)"""
    ),
    (
"""    else:
        print("No conferences in this run matched the submission deadline criteria.")

if __name__ == "__main__":
    main()""",
"""    else:
        print("No conferences in this run matched the submission deadline criteria.")
        
    # --- SAVE LOGS ---
    end_time = time.time()
    RUN_STATS["execution_time_seconds"] = round(end_time - start_time, 2)
    RUN_STATS["timestamp"] = datetime.now().isoformat()
    RUN_STATS["theme_query"] = args.theme
    
    log_file = os.path.join(BASE_DIR, "execution_logs.jsonl")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(RUN_STATS) + "\\n")
        print(f"\\n[INFO] Statistiques d'exécution sauvegardées dans {log_file}")
    except Exception as e:
        print(f"\\n[ERREUR] Impossible de sauvegarder les logs : {e}")

if __name__ == "__main__":
    start_time = time.time()
    main()"""
    )
]

new_content = content
for old_str, new_str in replacements:
    if old_str in new_content:
        new_content = new_content.replace(old_str, new_str)
    else:
        print(f"Failed to find: {old_str[:50]}...")

with open(filepath, "w") as f:
    f.write(new_content)
print("Done modifying search_conferences.py")
