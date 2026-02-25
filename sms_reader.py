#!/usr/bin/env python3
"""
SMS Reader CLI - Windows Phone Link (Link to Windows) Edition
Reads SMS messages from the local Phone Link SQLite database.
No USB, no drivers, no ADB needed!

Requirements:
    pip install rich

Setup:
    1. Install "Link to Windows" app on your Xiaomi phone
    2. Open "Phone Link" on your Windows PC and pair your phone
    3. Make sure Messages are synced (Phone Link → Messages tab)
    4. Run this script!
"""

import sqlite3
import os
import glob
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path


# ── Locate Phone Link database ────────────────────────────────────────────────

def find_phonelink_db() -> list[str]:
    """Search common locations for the Phone Link / Your Phone SQLite database."""
    username = os.environ.get("USERNAME", "*")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")

    search_paths = [
        # Windows 11 Phone Link
        rf"C:\Users\{username}\AppData\Local\Packages\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\LocalCache\Indexed\**\*.db",
        rf"C:\Users\{username}\AppData\Local\Packages\Microsoft.YourPhone_8wekyb3d8bbwe\LocalCache\Indexed\**\*.db",
        rf"C:\Users\{username}\AppData\Local\Packages\Microsoft.YourPhone_8wekyb3d8bbwe\LocalState\**\*.db",
        # Phone Link newer package name
        rf"C:\Users\{username}\AppData\Local\Packages\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\LocalState\**\*.db",
        # Wildcard fallback
        rf"C:\Users\{username}\AppData\Local\Packages\*YourPhone*\**\*.db",
        rf"C:\Users\{username}\AppData\Local\Packages\*PhoneLink*\**\*.db",
        rf"C:\Users\{username}\AppData\Local\Packages\*CBS*\**\*.db",
    ]

    found = []
    for pattern in search_paths:
        matches = glob.glob(pattern, recursive=True)
        for m in matches:
            if m not in found and os.path.getsize(m) > 1024:  # skip empty DBs
                found.append(m)
    return found


def find_sms_db() -> str | None:
    """Find the specific DB file that contains SMS/messages tables."""
    candidates = find_phonelink_db()
    for db_path in candidates:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0].lower() for row in cursor.fetchall()]
            conn.close()
            # Look for message-related tables
            if any(t in tables for t in ["messages", "message", "sms", "conversations", "threads"]):
                return db_path
        except Exception:
            continue
    return None


# ── Inspect DB schema ─────────────────────────────────────────────────────────

def get_tables(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in c.fetchall()]
    conn.close()
    return tables


def get_columns(db_path: str, table: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in c.fetchall()]
    conn.close()
    return cols


# ── Read SMS ──────────────────────────────────────────────────────────────────

def read_messages(db_path: str, limit: int = 3) -> list[dict]:
    """Read messages from the Phone Link database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    tables = get_tables(db_path)
    tables_lower = [t.lower() for t in tables]

    messages = []

    # Try different known table/column naming schemes
    candidates = [
        # (table, id_col, sender_col, body_col, date_col, direction_col)
        ("Messages",      "RowId",      "Sender",       "MessageText",  "Timestamp",   "MessageType"),
        ("messages",      "id",         "address",      "body",         "date",        "type"),
        ("message",       "id",         "sender",       "body",         "timestamp",   "direction"),
        ("SMSMessages",   "MessageId",  "SenderNumber", "Body",         "ReceivedTime","Direction"),
        ("Conversations", "id",         "recipient",    "snippet",      "last_message_timestamp", None),
    ]

    for table, id_col, sender_col, body_col, date_col, dir_col in candidates:
        if table.lower() not in tables_lower:
            continue

        # Get actual table name (preserve case)
        actual_table = tables[[t.lower() for t in tables].index(table.lower())]
        cols = [c.lower() for c in get_columns(db_path, actual_table)]

        # Map to actual column names
        def find_col(candidates_list):
            for candidate in candidates_list:
                if candidate.lower() in cols:
                    return get_columns(db_path, actual_table)[cols.index(candidate.lower())]
            return None

        s_col = find_col([sender_col, "address", "sender", "from_address", "number", "phone_number"])
        b_col = find_col([body_col, "body", "text", "messagetext", "message_text", "content"])
        d_col = find_col([date_col, "timestamp", "date", "received_time", "created_at", "date_sent"])

        if not (s_col and b_col):
            continue

        try:
            order = f"ORDER BY {d_col} DESC" if d_col else ""
            query = f"SELECT * FROM {actual_table} {order} LIMIT {limit}"
            c.execute(query)
            rows = c.fetchall()

            for row in rows:
                row_dict = dict(row)
                msg = {
                    "sender": str(row_dict.get(s_col, row_dict.get(s_col.lower(), "Unknown"))),
                    "body":   str(row_dict.get(b_col, row_dict.get(b_col.lower(), ""))),
                    "raw":    row_dict,
                }
                # Timestamp
                if d_col and d_col in row_dict:
                    ts_raw = row_dict[d_col]
                    msg["timestamp"] = format_timestamp(ts_raw)
                else:
                    msg["timestamp"] = "Unknown"

                # Direction (incoming=1 or "incoming")
                if dir_col:
                    dc = find_col([dir_col])
                    if dc and dc in row_dict:
                        msg["direction"] = str(row_dict[dc])
                    else:
                        msg["direction"] = "?"
                else:
                    msg["direction"] = "Inbox"

                messages.append(msg)

            if messages:
                break  # Found messages, stop trying other tables

        except Exception as e:
            continue

    conn.close()
    return messages


def format_timestamp(ts_raw) -> str:
    """Try to parse various timestamp formats."""
    if ts_raw is None:
        return "Unknown"
    try:
        ts = int(ts_raw)
        # Could be seconds or milliseconds
        if ts > 1e12:
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    # Try string datetime
    try:
        return str(ts_raw)
    except Exception:
        return "Unknown"


# ── Get SIM / device info from Phone Link ─────────────────────────────────────

def get_device_info(db_path: str) -> dict:
    """Try to extract device/SIM info from Phone Link DB."""
    info = {"sim_number": "Unknown", "device_name": "Unknown"}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        tables = [t.lower() for t in get_tables(db_path)]

        for table in ["devices", "device", "phoneinfo", "phone_info", "settings", "deviceinfo"]:
            if table in tables:
                c.execute(f"SELECT * FROM {table} LIMIT 5")
                rows = c.fetchall()
                for row in rows:
                    d = dict(row)
                    for k, v in d.items():
                        kl = k.lower()
                        if any(x in kl for x in ["phone", "number", "msisdn", "sim"]):
                            if v and str(v).strip() not in ("", "null", "None"):
                                info["sim_number"] = str(v)
                        if any(x in kl for x in ["name", "model", "device"]):
                            if v and str(v).strip() not in ("", "null", "None"):
                                info["device_name"] = str(v)
        conn.close()
    except Exception:
        pass
    return info


# ── Display ───────────────────────────────────────────────────────────────────

SEP = "─" * 60

def print_banner():
    print("\n" + "═" * 60)
    print("  📱  SMS Reader — Windows Phone Link Edition")
    print("═" * 60)

def print_message(msg: dict, index: int, sim_number: str):
    print(f"\n  Message #{index}")
    print(SEP)
    print(f"  From        : {msg.get('sender', 'Unknown')}")
    print(f"  SIM Number  : {sim_number}")
    print(f"  Received    : {msg.get('timestamp', 'Unknown')}")
    print(f"  Direction   : {msg.get('direction', '?')}")
    print(f"  Message     :")
    body = msg.get("body", "").strip()
    for line in (body.splitlines() or ["(empty)"]):
        print(f"    {line}")
    print(SEP)


# ── Monitor mode ──────────────────────────────────────────────────────────────

def monitor(db_path: str, sim_number: str, poll: float = 5.0):
    print(f"\n📡  Monitoring for new SMS (Ctrl+C to stop, polling every {poll}s)...\n")
    seen = set()

    def msg_id(m):
        return (m.get("sender"), m.get("timestamp"), m.get("body", "")[:30])

    for m in read_messages(db_path, limit=50):
        seen.add(msg_id(m))

    try:
        while True:
            msgs = read_messages(db_path, limit=20)
            for msg in msgs:
                mid = msg_id(msg)
                if mid not in seen:
                    seen.add(mid)
                    print(f"\n🔔  New message at {datetime.now().strftime('%H:%M:%S')}")
                    print_message(msg, 0, sim_number)
            time.sleep(poll)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Read SMS from Windows Phone Link (Link to Windows) database."
    )
    parser.add_argument("--last",    type=int,  default=3,   help="Number of recent messages (default: 3)")
    parser.add_argument("--monitor", action="store_true",    help="Monitor for new messages in real-time")
    parser.add_argument("--db",      type=str,  default=None,help="Path to Phone Link .db file (auto-detected if omitted)")
    parser.add_argument("--scan",    action="store_true",    help="Scan and list all Phone Link databases found")
    args = parser.parse_args()

    print_banner()

    # Scan mode
    if args.scan:
        print("\n🔍  Scanning for Phone Link databases...\n")
        dbs = find_phonelink_db()
        if not dbs:
            print("  No databases found.")
        for db in dbs:
            tables = get_tables(db)
            size = os.path.getsize(db) // 1024
            print(f"  📁 {db}")
            print(f"     Size: {size} KB  |  Tables: {', '.join(tables)}\n")
        sys.exit(0)

    # Find DB
    db_path = args.db
    if not db_path:
        print("\n🔍  Auto-detecting Phone Link database...")
        db_path = find_sms_db()
        if not db_path:
            print("  [!] Could not find Phone Link SMS database.\n")
            print("  Try these steps:")
            print("  1. Open 'Phone Link' app on Windows and make sure Messages are synced")
            print("  2. Send yourself a test SMS so data exists in the DB")
            print("  3. Run with --scan to see all detected databases")
            print("  4. Run with --db <path> to manually specify the database file")
            print("\n  Common locations to check manually:")
            print(r"  %LOCALAPPDATA%\Packages\Microsoft.YourPhone_8wekyb3d8bbwe\LocalCache")
            print(r"  %LOCALAPPDATA%\Packages\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\LocalCache")
            sys.exit(1)
        print(f"  ✓  Found: {db_path}")

    # Device info
    print("\n📋  Device / SIM Info")
    print(SEP)
    dev_info = get_device_info(db_path)
    print(f"  Device      : {dev_info.get('device_name', 'Unknown')}")
    print(f"  SIM Number  : {dev_info.get('sim_number', 'Unknown (Phone Link may not expose this)')}")
    print(SEP)
    sim_number = dev_info.get("sim_number", "Via Phone Link")

    if args.monitor:
        monitor(db_path, sim_number)
    else:
        print(f"\n📨  Last {args.last} SMS message(s):\n")
        msgs = read_messages(db_path, limit=args.last)
        if not msgs:
            print("  [!] No messages found.")
            print("      → Make sure Messages are synced in the Phone Link app")
            print("      → Try --scan to inspect available databases")
        else:
            for i, msg in enumerate(msgs, 1):
                print_message(msg, i, sim_number)

    print("\nDone.\n")


if __name__ == "__main__":
    main()
