import os
import pandas as pd

CORE_CSV_PATH = "CORE_all26.csv"
OUTPUT_CSV_PATH = "conferences_filtrees.csv"
BACKUP_CSV_PATH = "conferences_filtrees_backup.csv"
TERM_LOG_PATH = "copy-sortie-term.text"

# 1. Load CORE database for full names lookup
if os.path.exists(CORE_CSV_PATH):
    df_core = pd.read_csv(CORE_CSV_PATH, header=None, names=["id", "name", "acronym", "source", "rank", "active", "for1", "for2", "for3"])
    # Map lowercase acronym to full name and rank
    core_lookup = {}
    for _, row in df_core.iterrows():
        acr = str(row["acronym"]).strip().lower()
        core_lookup[acr] = {
            "name": row["name"],
            "rank": row["rank"]
        }
else:
    print(f"Error: {CORE_CSV_PATH} not found.")
    core_lookup = {}

# 2. Parse copy-sortie-term.text
parsed_term_rows = []
if os.path.exists(TERM_LOG_PATH):
    with open(TERM_LOG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "Acronym" in line or "=====" in line or "Results successfully" in line or "Total results" in line:
            continue
            
        parts = line.split()
        if len(parts) < 5:
            continue
            
        acronym = parts[0]
        rank = parts[1]
        if rank not in ["A", "B", "A*"]:
            continue
            
        sub_date = parts[2]
        notif_date = parts[3]
        if sub_date in ["None", "NaN", "null"]:
            sub_date = None
        if notif_date in ["None", "NaN", "null"]:
            notif_date = None
            
        rest = parts[4:]
        last_item = rest[-1]
        if last_item.startswith("http") or last_item.startswith("https"):
            url = last_item
            status_parts = rest[:-1]
        elif last_item in ["N/A", "NaN", "None"]:
            url = "N/A"
            status_parts = rest[:-1]
        else:
            url = "N/A"
            status_parts = rest
            
        status = " ".join(status_parts)
        
        parsed_term_rows.append({
            "Acronym": acronym,
            "Submission Deadline": sub_date,
            "Notification Date": notif_date,
            "Status": status,
            "URL": url
        })
    print(f"Parsed {len(parsed_term_rows)} entries from terminal log dump.")
else:
    print(f"No terminal log dump found at {TERM_LOG_PATH}.")

# 3. Load all accumulated results
accumulated = {}

def add_entry(entry):
    acronym = entry.get("Acronym")
    if not acronym or pd.isna(acronym):
        return
    # Standardize acronym and year (we assume year 2027 based on logs, or default to 2027 if missing)
    year = entry.get("Year", 2027)
    try:
        year = int(year)
    except Exception:
        year = 2027
        
    key = (acronym.strip().lower(), year)
    
    # If key already exists, resolve conflicts: prefer the one with successful status
    existing = accumulated.get(key)
    if existing:
        ex_status = str(existing.get("Status", ""))
        new_status = str(entry.get("Status", ""))
        
        # If existing has detailed info, keep it
        if "Success" in ex_status and "Success" not in new_status:
            return
        # If new is success and existing is not, overwrite
        if "Success" in new_status and "Success" not in ex_status:
            pass
        else:
            # If both are same status type, prefer the one with more columns populated
            ex_val_count = sum(1 for v in existing.values() if pd.notna(v) and v != "N/A" and v != "")
            new_val_count = sum(1 for v in entry.values() if pd.notna(v) and v != "N/A" and v != "")
            if ex_val_count >= new_val_count:
                return
                
    # Build complete dict
    core_info = core_lookup.get(acronym.strip().lower(), {"name": entry.get("Name", "N/A"), "rank": entry.get("Rank", "N/A")})
    
    complete_entry = {
        "Acronym": acronym.strip(),
        "Year": year,
        "Name": core_info["name"],
        "Rank": core_info["rank"],
        "URL": entry.get("URL", "N/A"),
        "Abstract Deadline": entry.get("Abstract Deadline") or entry.get("Abstract Deadline") or None,
        "Submission Deadline": entry.get("Submission Deadline") or entry.get("Submission Deadline") or None,
        "Notification Date": entry.get("Notification Date") or entry.get("Notification Date") or None,
        "Timezone": entry.get("Timezone") or None,
        "Topics": entry.get("Topics") or "N/A",
        "Short Description": entry.get("Short Description") or "N/A",
        "Other Tracks": entry.get("Other Tracks") or "N/A",
        "Confidence Score": entry.get("Confidence Score") or 0,
        "Status": entry.get("Status") or "Success"
    }
    accumulated[key] = complete_entry

# Load backup CSV first
if os.path.exists(BACKUP_CSV_PATH):
    try:
        df_backup = pd.read_csv(BACKUP_CSV_PATH)
        for _, row in df_backup.iterrows():
            add_entry(row.to_dict())
        print(f"Loaded records from backup: {BACKUP_CSV_PATH}")
    except Exception as e:
        print(f"Failed to load backup CSV: {e}")

# Load current CSV (if it exists)
if os.path.exists(OUTPUT_CSV_PATH):
    try:
        df_current = pd.read_csv(OUTPUT_CSV_PATH)
        for _, row in df_current.iterrows():
            add_entry(row.to_dict())
        print(f"Loaded records from current output: {OUTPUT_CSV_PATH}")
    except Exception as e:
        print(f"Failed to load current output CSV: {e}")

# Load parsed terminal log entries
for entry in parsed_term_rows:
    add_entry(entry)

# 4. Save to target file
if accumulated:
    df_restored = pd.DataFrame(list(accumulated.values()))
    cols_order = [
        "Acronym", "Year", "Name", "Rank", "URL", 
        "Abstract Deadline", "Submission Deadline", "Notification Date", 
        "Timezone", "Topics", "Short Description", "Other Tracks", 
        "Confidence Score", "Status"
    ]
    actual_cols = [c for c in cols_order if c in df_restored.columns]
    df_restored[actual_cols].to_csv(OUTPUT_CSV_PATH, index=False)
    print(f"Successfully restored and merged database: {len(df_restored)} entries written to {OUTPUT_CSV_PATH}.")
else:
    print("No records found to restore.")
