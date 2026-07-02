"""
Qualys Scan Schedule Migration Script
--------------------------------------
Exports all scan schedules from a SOURCE Qualys POD and
imports them into a DESTINATION Qualys POD.

Usage:
    1. Fill in SOURCE and DEST credentials below (or use environment variables)
    2. Run:  python migrate_schedules.py --mode export   (saves schedules_export.xml + schedules_export.csv)
             python migrate_schedules.py --mode import   (reads schedules_export.xml and creates in DEST)
             python migrate_schedules.py --mode both     (export then import in one go)
"""

import requests
import xml.etree.ElementTree as ET
import csv
import argparse
import sys
import os
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — update these before running
# ─────────────────────────────────────────────────────────────────────────────

SOURCE = {
    "base_url": os.getenv("SOURCE_URL", "https://qualysguard.qg1.apps.qualys.ae"),
    "username": os.getenv("SOURCE_USER", "YOUR_SOURCE_USERNAME"),
    "password": os.getenv("SOURCE_PASS", "YOUR_SOURCE_PASSWORD"),
}

DEST = {
    "base_url": os.getenv("DEST_URL", "https://YOUR_DEST_POD_URL"),
    "username": os.getenv("DEST_USER", "YOUR_DEST_USERNAME"),
    "password": os.getenv("DEST_PASS", "YOUR_DEST_PASSWORD"),
}

EXPORT_XML_FILE = "schedules_export.xml"
EXPORT_CSV_FILE = "schedules_export.csv"

HEADERS = {"X-Requested-With": "Python-Qualys-Migration-Script"}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def safe_text(element, path, default=""):
    """Return text at XML path, or default if not found."""
    node = element.find(path)
    return node.text.strip() if (node is not None and node.text) else default


def check_credentials(pod_name, pod):
    """Warn if placeholder credentials are still set."""
    if "YOUR_" in pod["username"] or "YOUR_" in pod["password"] or "YOUR_" in pod["base_url"]:
        print(f"\n[ERROR] {pod_name} credentials/URL are still set to placeholder values.")
        print(f"        Edit the SOURCE / DEST dictionaries at the top of this script.\n")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────────────────────────────────────────

def export_schedules():
    """Fetch all scan schedules from SOURCE and save XML + CSV."""
    check_credentials("SOURCE", SOURCE)

    print(f"\n[EXPORT] Connecting to: {SOURCE['base_url']}")
    url = f"{SOURCE['base_url']}/api/2.0/fo/schedule/scan/"
    params = {"action": "list", "active": "0,1"}

    try:
        resp = requests.get(
            url,
            params=params,
            auth=(SOURCE["username"], SOURCE["password"]),
            headers=HEADERS,
            timeout=60,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        print(f"[ERROR] Cannot connect to {SOURCE['base_url']}. Check the URL and your network.")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"[ERROR] HTTP {resp.status_code}: {e}")
        if resp.status_code == 401:
            print("        Authentication failed — check your username and password.")
        sys.exit(1)

    # Save raw XML
    with open(EXPORT_XML_FILE, "w", encoding="utf-8") as f:
        f.write(resp.text)
    print(f"[EXPORT] Raw XML saved → {EXPORT_XML_FILE}")

    # Parse and show summary
    root = ET.fromstring(resp.text)
    schedules = root.findall(".//SCHEDULE")

    if not schedules:
        print("[EXPORT] No schedules found in source POD.")
        return []

    print(f"[EXPORT] Found {len(schedules)} schedule(s).\n")

    rows = []
    for s in schedules:
        # ── Task Title tab (picture 5) ──
        title          = safe_text(s, "TITLE")
        active         = safe_text(s, "ACTIVE", "1")   # 0=deactivated (picture 9 checked)
        user_login     = safe_text(s, "USER_LOGIN")
        option_profile = safe_text(s, "OPTION_PROFILE/TITLE")
        network_id     = safe_text(s, "NETWORK_ID", "0")
        priority       = safe_text(s, "PROCESSING_PRIORITY", "0")
        scanner        = safe_text(s, "ISCANNER_NAME")

        # ── Target Hosts tab (picture 6) ──
        asset_groups   = ", ".join(
            [ag.text.strip() for ag in s.findall(".//ASSET_GROUP_TITLE") if ag.text]
        )
        target_ip      = safe_text(s, "TARGET/IP")
        exclude_ip     = safe_text(s, "TARGET/EXCLUDED_IP")
        fqdns          = safe_text(s, "TARGET/FQDN")

        # ── Scheduling tab (picture 7) ──
        start_date     = safe_text(s, "SCHEDULE/START_DATE")
        start_hour     = safe_text(s, "SCHEDULE/START_HOUR", "0")
        start_minute   = safe_text(s, "SCHEDULE/START_MINUTE", "0")
        timezone       = safe_text(s, "SCHEDULE/TIME_ZONE/TIME_ZONE_CODE")
        dst            = safe_text(s, "SCHEDULE/DST_SELECTED", "0")
        occurrence     = safe_text(s, "SCHEDULE/OCCURRENCE_SELECTED", "")

        # Recurrence type
        recurrence_type = "once"
        for rec in ["DAILY", "WEEKLY", "MONTHLY", "MONTHLY_BY_WEEKDAY"]:
            if s.find(f".//SCHEDULE/{rec}") is not None:
                recurrence_type = rec.lower()
                break

        recurrence_every = safe_text(s, f"SCHEDULE/{recurrence_type.upper()}", "")
        recurrence_node  = s.find(f".//SCHEDULE/{recurrence_type.upper()}")
        recurrence_every = (
            recurrence_node.get("every", "1")
            if recurrence_node is not None else "1"
        )

        # ── Notifications tab (pictures 8-1, 8-2, 8-3) ──
        notify_before_launch = safe_text(s, "NOTIFICATION/BEFORE_LAUNCH/ACTIVE", "0")
        notify_on_complete   = safe_text(s, "NOTIFICATION/ON_FINISH/ACTIVE", "0")
        notify_on_delay      = safe_text(s, "NOTIFICATION/ON_DELAY/ACTIVE", "0")
        notify_on_skip       = safe_text(s, "NOTIFICATION/ON_SKIP/ACTIVE", "0")
        notify_on_deactivate = safe_text(s, "NOTIFICATION/ON_DEACTIVATION/ACTIVE", "0")

        schedule_id    = safe_text(s, "ID")

        row = {
            "id":                    schedule_id,
            "title":                 title,
            "active":                active,            # 0 = deactivated (picture 9)
            "user_login":            user_login,
            "option_profile":        option_profile,
            "network_id":            network_id,
            "processing_priority":   priority,
            "scanner":               scanner,
            "asset_groups":          asset_groups,
            "target_ip":             target_ip,
            "exclude_ip":            exclude_ip,
            "fqdns":                 fqdns,
            "start_date":            start_date,
            "start_hour":            start_hour,
            "start_minute":          start_minute,
            "timezone":              timezone,
            "dst":                   dst,
            "recurrence_type":       recurrence_type,
            "recurrence_every":      recurrence_every,
            "occurrence":            occurrence,
            "notify_before_launch":  notify_before_launch,
            "notify_on_complete":    notify_on_complete,
            "notify_on_delay":       notify_on_delay,
            "notify_on_skip":        notify_on_skip,
            "notify_on_deactivate":  notify_on_deactivate,
        }
        rows.append(row)

        deactivated = "YES (deactivated)" if active == "0" else "no (active)"
        print(f"  [{schedule_id}] {title:<35} | Active: {deactivated}")

    # Save CSV for human review
    with open(EXPORT_CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n[EXPORT] CSV saved for review → {EXPORT_CSV_FILE}")
    print("[EXPORT] Review the CSV before running import!\n")

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# IMPORT
# ─────────────────────────────────────────────────────────────────────────────

def import_schedules(rows=None):
    """Create scan schedules in DEST POD from exported data."""
    check_credentials("DEST", DEST)

    # If rows not passed, read from saved XML
    if rows is None:
        if not os.path.exists(EXPORT_XML_FILE):
            print(f"[ERROR] {EXPORT_XML_FILE} not found. Run --mode export first.")
            sys.exit(1)

        print(f"[IMPORT] Reading from {EXPORT_XML_FILE} ...")
        with open(EXPORT_XML_FILE, "r", encoding="utf-8") as f:
            xml_content = f.read()

        root = ET.fromstring(xml_content)
        schedules = root.findall(".//SCHEDULE")
        print(f"[IMPORT] {len(schedules)} schedule(s) found in export file.\n")

        rows = []
        for s in schedules:
            rows.append({
                "id":                   safe_text(s, "ID"),
                "title":                safe_text(s, "TITLE"),
                "active":               safe_text(s, "ACTIVE", "1"),
                "user_login":           safe_text(s, "USER_LOGIN"),
                "option_profile":       safe_text(s, "OPTION_PROFILE/TITLE"),
                "network_id":           safe_text(s, "NETWORK_ID", "0"),
                "processing_priority":  safe_text(s, "PROCESSING_PRIORITY", "0"),
                "scanner":              safe_text(s, "ISCANNER_NAME"),
                "asset_groups":         ", ".join(
                    [ag.text.strip() for ag in s.findall(".//ASSET_GROUP_TITLE") if ag.text]
                ),
                "target_ip":            safe_text(s, "TARGET/IP"),
                "exclude_ip":           safe_text(s, "TARGET/EXCLUDED_IP"),
                "fqdns":                safe_text(s, "TARGET/FQDN"),
                "start_date":           safe_text(s, "SCHEDULE/START_DATE"),
                "start_hour":           safe_text(s, "SCHEDULE/START_HOUR", "0"),
                "start_minute":         safe_text(s, "SCHEDULE/START_MINUTE", "0"),
                "timezone":             safe_text(s, "SCHEDULE/TIME_ZONE/TIME_ZONE_CODE"),
                "recurrence_type":      "daily",
                "recurrence_every":     "1",
                "occurrence":           safe_text(s, "SCHEDULE/OCCURRENCE_SELECTED", ""),
            })

    print(f"[IMPORT] Connecting to DEST: {DEST['base_url']}\n")
    url = f"{DEST['base_url']}/api/2.0/fo/schedule/scan/"
    success, failed = 0, 0

    for row in rows:
        title = row["title"]

        # Build POST payload — map exported fields to API create parameters
        payload = {
            "action":               "create",
            "title":                title,
            "active":               row["active"],       # 0 or 1 (picture 9)
            "option_title":         row["option_profile"],
            "iscanner_name":        row["scanner"],
            "processing_priority":  row["processing_priority"],
            "network_id":           row["network_id"],
        }

        # Target hosts
        if row["asset_groups"]:
            payload["asset_group_title"] = row["asset_groups"]
        if row["target_ip"]:
            payload["ip"]               = row["target_ip"]
        if row["exclude_ip"]:
            payload["exclude_ip"]       = row["exclude_ip"]
        if row["fqdns"]:
            payload["fqdn"]             = row["fqdns"]

        # Scheduling
        if row["start_date"]:
            payload["start_date"]    = row["start_date"]
        payload["start_hour"]        = row["start_hour"]
        payload["start_minute"]      = row["start_minute"]
        if row.get("timezone"):
            payload["time_zone_code"]= row["timezone"]

        recurrence = row.get("recurrence_type", "daily").lower()
        if recurrence == "daily":
            payload["occurrence"]    = "daily"
            payload["daily_freq"]    = row.get("recurrence_every", "1")
        elif recurrence == "weekly":
            payload["occurrence"]    = "weekly"
        elif recurrence == "monthly":
            payload["occurrence"]    = "monthly"

        if row.get("occurrence"):
            payload["end_after"]     = row["occurrence"]

        try:
            resp = requests.post(
                url,
                data=payload,
                auth=(DEST["username"], DEST["password"]),
                headers=HEADERS,
                timeout=60,
            )
            resp.raise_for_status()
            resp_xml = ET.fromstring(resp.text)
            item_id = resp_xml.findtext(".//VALUE", "?")
            status  = resp_xml.findtext(".//CODE", "OK")
            print(f"  [OK]     {title:<35} → new ID: {item_id}  (status: {status})")
            success += 1
        except requests.exceptions.HTTPError as e:
            print(f"  [FAIL]   {title:<35} → HTTP {resp.status_code}: {resp.text[:200]}")
            failed += 1
        except Exception as e:
            print(f"  [FAIL]   {title:<35} → {e}")
            failed += 1

    print(f"\n[IMPORT] Done — {success} created, {failed} failed.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Qualys Scan Schedule Migration Tool"
    )
    parser.add_argument(
        "--mode",
        choices=["export", "import", "both"],
        default="export",
        help=(
            "export  = fetch schedules from SOURCE and save to XML + CSV\n"
            "import  = read saved XML and create schedules in DEST\n"
            "both    = export then immediately import"
        ),
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Qualys Scan Schedule Migration Tool")
    print(f"  Mode   : {args.mode.upper()}")
    print(f"  Time   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    if args.mode == "export":
        export_schedules()

    elif args.mode == "import":
        import_schedules()

    elif args.mode == "both":
        rows = export_schedules()
        if rows:
            import_schedules(rows)


if __name__ == "__main__":
    main()
