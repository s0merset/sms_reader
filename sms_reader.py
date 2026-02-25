#!/usr/bin/env python3
"""
SMS Reader CLI (ADB version) - Reads SMS messages from an Android phone via ADB.
No special drivers needed — just USB Debugging enabled.

Requirements:
    - ADB installed and in PATH  (https://developer.android.com/tools/releases/platform-tools)
    - USB Debugging enabled on your Android phone
    - Phone connected via USB and authorized (accept the "Allow USB Debugging" prompt)

Usage:
    python sms_reader.py                  # Show last 3 SMS messages
    python sms_reader.py --last 10        # Show last 10 messages
    python sms_reader.py --monitor        # Monitor for new messages in real-time
    python sms_reader.py --device <id>    # Target a specific device (if multiple connected)
"""

import subprocess
import json
import time
import argparse
import sys
import re
from datetime import datetime


# ── ADB helpers ───────────────────────────────────────────────────────────────

def run_adb(args: list[str], device_id: str = None) -> tuple[str, str, int]:
    """Run an adb command. Returns (stdout, stderr, returncode)."""
    cmd = ["adb"]
    if device_id:
        cmd += ["-s", device_id]
    cmd += args
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def check_adb_installed() -> bool:
    _, _, code = run_adb(["version"])
    return code == 0


def get_connected_devices() -> list[dict]:
    stdout, _, _ = run_adb(["devices", "-l"])
    devices = []
    for line in stdout.splitlines()[1:]:
        if not line.strip() or "offline" in line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            dev = {"id": parts[0]}
            for part in parts[2:]:
                if ":" in part:
                    k, v = part.split(":", 1)
                    dev[k] = v
            devices.append(dev)
    return devices


def check_device_authorized(device_id: str) -> bool:
    stdout, _, _ = run_adb(["devices"], device_id)
    return "unauthorized" not in stdout


# ── SIM info ──────────────────────────────────────────────────────────────────

def get_sim_info(device_id: str) -> dict:
    """Get SIM card number, operator, IMEI via ADB shell commands."""
    info = {}

    # Phone number (requires READ_PHONE_STATE — may return 'null' on some ROMs)
    out, _, _ = run_adb(["shell", "service call iphonesubinfo 15 | grep -o '\"[^\"]*\"' | tr -d '\"'"], device_id)
    number = out.strip().replace(".", "").strip()
    info["sim_number"] = number if number and number not in ("null", "") else "Unknown (hidden by ROM)"

    # Try getprop as fallback
    if info["sim_number"].startswith("Unknown"):
        out2, _, _ = run_adb(["shell", "getprop", "net.rmnet0.local-ip"], device_id)
        out3, _, _ = run_adb(["shell", "getprop", "gsm.sim.operator.alpha"], device_id)
        info["operator"] = out3.strip() or "Unknown"
    else:
        out3, _, _ = run_adb(["shell", "getprop", "gsm.sim.operator.alpha"], device_id)
        info["operator"] = out3.strip() or "Unknown"

    # IMEI
    out4, _, _ = run_adb(["shell", "service call iphonesubinfo 1 | grep -o '\"[^\"]*\"' | tr -d '\"\\n. '"], device_id)
    info["imei"] = out4.strip() or "Unknown"

    # IMSI via telephony db (needs root) — try anyway
    out5, _, _ = run_adb(["shell", "settings get secure android_id"], device_id)
    info["android_id"] = out5.strip() or "Unknown"

    return info


# ── SMS reading ───────────────────────────────────────────────────────────────

def read_sms(device_id: str, limit: int = 3) -> list[dict]:
    """
    Read SMS messages from the Android content provider.
    Queries content://sms — no root needed.
    """
    # Columns: _id, address (sender), date, body, type (1=inbox,2=sent), read
    query = (
        f"content query --uri content://sms/inbox "
        f"--projection _id,address,date,body,read "
        f"--sort 'date DESC' "
        f"--limit {limit} --limit {limit}"
    )

    # ADB content query command
    out, err, code = run_adb([
        "shell", "content", "query",
        "--uri", "content://sms/inbox",
        "--projection", "_id:address:date:body:read",
        "--sort", "date DESC",
    ], device_id)

    if code != 0 or not out:
        return []

    messages = []
    # Each row starts with "Row: N"
    rows = re.split(r'Row:\s*\d+\s*', out)
    for row in rows:
        if not row.strip():
            continue
        msg = {}
        for field in ["_id", "address", "date", "body", "read"]:
            m = re.search(rf'{field}=([^\,]+?)(?:,\s*\w+=|$)', row, re.DOTALL)
            if m:
                msg[field] = m.group(1).strip()
        if msg:
            # Convert epoch ms to readable timestamp
            if "date" in msg:
                try:
                    ts = int(msg["date"]) / 1000
                    msg["date_human"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    msg["date_human"] = msg.get("date", "")
            messages.append(msg)

    return messages[:limit]


# ── Display ───────────────────────────────────────────────────────────────────

SEP = "─" * 60

def print_banner():
    print("\n" + "═" * 60)
    print("  📱  SMS Reader CLI  [ADB Mode]")
    print("═" * 60)


def print_sim_info(info: dict):
    print(f"\nSIM / Device Info")
    print(SEP)
    print(f"  SIM Number  : {info.get('sim_number', 'Unknown')}")
    print(f"  Operator    : {info.get('operator', 'Unknown')}")
    print(f"  IMEI        : {info.get('imei', 'Unknown')}")
    print(f"  Android ID  : {info.get('android_id', 'Unknown')}")
    print(SEP)


def print_message(msg: dict, sim_number: str, label: str = "SMS"):
    read_status = "Read" if msg.get("read") == "1" else "Unread"
    print(f"\n[{label}]")
    print(SEP)
    print(f"  ID          : {msg.get('_id', '?')}")
    print(f"  Status      : {read_status}")
    print(f"  From        : {msg.get('address', 'Unknown')}")
    print(f"  SIM Number  : {sim_number}")
    print(f"  Received    : {msg.get('date_human', msg.get('date', '?'))}")
    print(f"  Message     :")
    body = msg.get("body", "").strip()
    for line in body.splitlines():
        print(f"    {line}")
    print(SEP)


# ── Monitor mode ──────────────────────────────────────────────────────────────

def monitor_sms(device_id: str, sim_number: str, poll_interval: float = 4.0):
    print(f"\n📡  Monitoring for new SMS messages (Ctrl+C to stop)...\n")
    seen_ids = set()

    # Seed with existing messages
    for msg in read_sms(device_id, limit=50):
        seen_ids.add(msg.get("_id"))

    try:
        while True:
            msgs = read_sms(device_id, limit=20)
            for msg in msgs:
                mid = msg.get("_id")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    print(f"\n🔔  New message at {datetime.now().strftime('%H:%M:%S')}")
                    print_message(msg, sim_number, label="NEW SMS")
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped.")


# ── Setup instructions ────────────────────────────────────────────────────────

def print_setup_guide():
    print("""
┌─────────────────────────────────────────────────────────┐
│              SETUP: Enable USB Debugging                │
├─────────────────────────────────────────────────────────┤
│  1. On your Android phone:                              │
│     Settings → About Phone → tap Build Number 7 times  │
│     (unlocks Developer Options)                         │
│                                                         │
│  2. Settings → Developer Options → Enable USB Debugging │
│                                                         │
│  3. Plug in your phone via USB                          │
│     Accept the "Allow USB Debugging?" prompt on phone   │
│                                                         │
│  4. Install ADB if not already:                         │
│     https://developer.android.com/tools/releases/       │
│     platform-tools                                      │
│     (add to PATH so 'adb' works in terminal)            │
│                                                         │
│  5. Verify with: adb devices                            │
│     Your device should show as "device" (not            │
│     "unauthorized")                                     │
└─────────────────────────────────────────────────────────┘
""")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Read SMS messages from an Android phone via ADB."
    )
    parser.add_argument("--last",    type=int, default=3,  help="Number of recent messages to show (default: 3)")
    parser.add_argument("--monitor", action="store_true",  help="Monitor for new incoming SMS in real-time")
    parser.add_argument("--device",  help="ADB device ID (use if multiple devices connected)")
    parser.add_argument("--setup",   action="store_true",  help="Show setup instructions")
    args = parser.parse_args()

    if args.setup:
        print_setup_guide()
        sys.exit(0)

    print_banner()

    # Check ADB is installed
    print("\n🔍  Checking ADB...")
    if not check_adb_installed():
        print("  [!] 'adb' not found. Please install Android Platform Tools.")
        print("      https://developer.android.com/tools/releases/platform-tools")
        print("\n  Run with --setup for full instructions.")
        sys.exit(1)
    print("  ✓  ADB found.")

    # Check connected devices
    print("\n🔌  Looking for connected Android devices...")
    devices = get_connected_devices()

    if not devices:
        print("  [!] No authorized Android device found.")
        print("\n  Make sure:")
        print("    • USB Debugging is enabled on your phone")
        print("    • You accepted the 'Allow USB Debugging' prompt on your phone")
        print("    • Your phone is connected via USB")
        print("\n  Run with --setup for full instructions.")
        sys.exit(1)

    # Pick device
    device_id = args.device
    if not device_id:
        if len(devices) > 1:
            print(f"\n  Multiple devices found:")
            for i, d in enumerate(devices):
                print(f"    [{i}] {d['id']}  model={d.get('model', '?')}")
            print(f"\n  Using first device. Use --device <id> to select another.")
        device_id = devices[0]["id"]

    model = devices[0].get("model", "Android Device")
    print(f"  ✓  Connected: {model} ({device_id})")

    # Get SIM info
    print("\n📋  Fetching SIM info...")
    sim_info = get_sim_info(device_id)
    print_sim_info(sim_info)
    sim_number = sim_info.get("sim_number", "Unknown")

    if args.monitor:
        monitor_sms(device_id, sim_number)
    else:
        print(f"\n📨  Last {args.last} SMS message(s) (Inbox):\n")
        msgs = read_sms(device_id, limit=args.last)
        if not msgs:
            print("  (No messages found — inbox may be empty or permission denied)")
        for msg in msgs:
            print_message(msg, sim_number)

    print("\nDone.\n")


if __name__ == "__main__":
    main()
