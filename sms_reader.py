#!/usr/bin/env python3
"""
SMS Reader CLI - Reads incoming and last 3 SMS messages from a connected phone/GSM modem.
Communicates via AT commands over serial (USB) connection.

Requirements:
    pip install pyserial

Usage:
    python sms_reader.py                  # Auto-detect port
    python sms_reader.py --port COM3      # Windows
    python sms_reader.py --port /dev/ttyUSB0  # Linux/macOS
    python sms_reader.py --monitor        # Monitor for new messages in real-time
"""

import serial
import serial.tools.list_ports
import time
import re
import argparse
import sys
from datetime import datetime


# ── AT command helpers ────────────────────────────────────────────────────────

def send_at(ser: serial.Serial, command: str, timeout: float = 2.0) -> str:
    """Send an AT command and return the response."""
    ser.write((command + "\r").encode())
    time.sleep(0.3)
    deadline = time.time() + timeout
    response = ""
    while time.time() < deadline:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting).decode(errors="replace")
            response += chunk
            # Stop when we get a final result code
            if re.search(r"\r\n(OK|ERROR|\+CMS ERROR|\+CME ERROR)[^\n]*\r\n", response):
                break
        time.sleep(0.05)
    return response.strip()


def init_modem(ser: serial.Serial) -> bool:
    """Initialize modem – returns True on success."""
    checks = [
        ("AT", "OK"),           # Basic check
        ("AT+CMGF=1", "OK"),    # Text mode for SMS
        ("AT+CSCS=\"GSM\"", "OK"),  # GSM character set
    ]
    for cmd, expected in checks:
        resp = send_at(ser, cmd)
        if expected not in resp:
            print(f"  [!] Command '{cmd}' failed. Response: {resp!r}")
            return False
    return True


# ── SIM / device info ─────────────────────────────────────────────────────────

def get_sim_number(ser: serial.Serial) -> str:
    """Try to retrieve the SIM card's own phone number."""
    # CNUM is the standard command; not all modems support it
    resp = send_at(ser, "AT+CNUM", timeout=3)
    match = re.search(r'\+CNUM:\s*"[^"]*","(\+?[\d]+)"', resp)
    if match:
        return match.group(1)

    # Fallback: try reading from EF_MSISDN via +CPBS
    resp2 = send_at(ser, 'AT+CPBS="ON"')
    if "OK" in resp2:
        resp3 = send_at(ser, "AT+CPBR=1")
        m = re.search(r'\+CPBR:\d+,"(\+?[\d]+)"', resp3)
        if m:
            return m.group(1)

    return "Unknown (not reported by modem)"


def get_imsi(ser: serial.Serial) -> str:
    """Get IMSI – identifies the SIM card uniquely."""
    resp = send_at(ser, "AT+CIMI", timeout=3)
    match = re.search(r"(\d{10,20})", resp)
    return match.group(1) if match else "Unknown"


def get_operator(ser: serial.Serial) -> str:
    resp = send_at(ser, "AT+COPS?")
    match = re.search(r'\+COPS:\d,\d,"([^"]+)"', resp)
    return match.group(1) if match else "Unknown"


# ── SMS parsing ───────────────────────────────────────────────────────────────

def parse_sms_list(raw: str) -> list[dict]:
    """
    Parse the raw output of AT+CMGL or AT+CMGR into a list of message dicts.
    Handles both +CMGL and +CMGR response formats.
    """
    messages = []
    # Pattern: +CMGL: index,"status","sender",,"timestamp"\r\nbody
    pattern = re.compile(
        r'\+CMG[LR]:\s*(\d+)?,?"([^"]*)",\s*"([^"]*)"(?:,\s*"([^"]*)")?\r?\n(.*?)(?=\r?\n\+CMG|\r?\n\r?\nOK|$)',
        re.DOTALL
    )
    for m in pattern.finditer(raw):
        index   = m.group(1) or "?"
        status  = m.group(2)
        sender  = m.group(3)
        ts      = m.group(4) or ""
        body    = m.group(5).strip().replace("\r", "")
        messages.append({
            "index":     index,
            "status":    status,
            "sender":    sender,
            "timestamp": ts,
            "body":      body,
        })
    return messages


def read_all_sms(ser: serial.Serial) -> list[dict]:
    """Read ALL stored SMS messages from the modem."""
    resp = send_at(ser, 'AT+CMGL="ALL"', timeout=10)
    return parse_sms_list(resp)


def read_last_n_sms(ser: serial.Serial, n: int = 3) -> list[dict]:
    """Return the last N SMS messages (by storage index)."""
    all_msgs = read_all_sms(ser)
    return all_msgs[-n:] if len(all_msgs) >= n else all_msgs


# ── Display helpers ───────────────────────────────────────────────────────────

SEPARATOR = "─" * 60

def print_banner():
    print("\n" + "═" * 60)
    print("  📱  SMS Reader CLI")
    print("═" * 60)


def print_sim_info(sim_number: str, imsi: str, operator: str):
    print(f"\n{'SIM Info':}")
    print(SEPARATOR)
    print(f"  SIM Number  : {sim_number}")
    print(f"  IMSI        : {imsi}")
    print(f"  Operator    : {operator}")
    print(SEPARATOR)


def print_message(msg: dict, sim_number: str, label: str = "SMS"):
    print(f"\n[{label}]")
    print(SEPARATOR)
    print(f"  Index       : {msg['index']}")
    print(f"  Status      : {msg['status']}")
    print(f"  From        : {msg['sender']}")
    print(f"  SIM Number  : {sim_number}")
    if msg['timestamp']:
        print(f"  Timestamp   : {msg['timestamp']}")
    print(f"  Message     :")
    for line in msg['body'].splitlines():
        print(f"              {line}")
    print(SEPARATOR)


# ── Port detection ────────────────────────────────────────────────────────────

KNOWN_MODEM_VIDS = {0x12D1, 0x19D2, 0x1E0E, 0x1A86, 0x067B, 0x0403, 0x04E2}

def detect_modem_port() -> str | None:
    """Try to auto-detect a GSM modem / phone serial port."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None

    # Prefer ports that look like modems
    for p in ports:
        vid = p.vid or 0
        desc = (p.description or "").lower()
        if vid in KNOWN_MODEM_VIDS or any(k in desc for k in ("modem", "gsm", "huawei", "zte", "sierra", "option", "wwan")):
            return p.device

    # Last resort – return first available port
    return ports[0].device


def list_ports():
    print("\nAvailable serial ports:")
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("  (none found)")
    for p in ports:
        print(f"  {p.device:20s}  {p.description}")


# ── Monitor mode ──────────────────────────────────────────────────────────────

def monitor_new_sms(ser: serial.Serial, sim_number: str, poll_interval: float = 3.0):
    """Poll for new (UNREAD) messages continuously."""
    print(f"\n📡  Monitoring for new SMS messages (Ctrl+C to stop)...\n")
    seen_indices = set()

    # Seed with already-read messages so we don't re-display them
    for msg in read_all_sms(ser):
        seen_indices.add(msg["index"])

    try:
        while True:
            msgs = read_all_sms(ser)
            for msg in msgs:
                if msg["index"] not in seen_indices:
                    seen_indices.add(msg["index"])
                    print(f"\n🔔  New message at {datetime.now().strftime('%H:%M:%S')}")
                    print_message(msg, sim_number, label="NEW SMS")
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Read SMS messages from a phone/GSM modem connected via USB."
    )
    parser.add_argument("--port",    help="Serial port (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("--baud",    type=int, default=115200, help="Baud rate (default 115200)")
    parser.add_argument("--last",    type=int, default=3,      help="Number of last messages to show (default 3)")
    parser.add_argument("--monitor", action="store_true",      help="Monitor for new incoming SMS in real-time")
    parser.add_argument("--list-ports", action="store_true",   help="List available serial ports and exit")
    args = parser.parse_args()

    if args.list_ports:
        list_ports()
        sys.exit(0)

    print_banner()

    # Resolve port
    port = args.port
    if not port:
        print("\n🔍  Auto-detecting modem port...")
        port = detect_modem_port()
        if not port:
            print("  [!] No serial port found. Connect your phone/modem and try again.")
            print("      Or specify a port with --port <PORT>")
            list_ports()
            sys.exit(1)
        print(f"  Found: {port}")

    # Open serial connection
    print(f"\n🔌  Connecting to {port} at {args.baud} baud...")
    try:
        ser = serial.Serial(port, baudrate=args.baud, timeout=2)
    except serial.SerialException as e:
        print(f"  [!] Could not open port: {e}")
        sys.exit(1)

    time.sleep(1)  # Let port settle

    # Init modem
    print("⚙️   Initializing modem...")
    if not init_modem(ser):
        print("  [!] Modem initialization failed. Check connection and try again.")
        ser.close()
        sys.exit(1)
    print("  ✓  Modem ready.")

    # Gather SIM info
    print("\n📋  Fetching SIM info...")
    sim_number = get_sim_number(ser)
    imsi       = get_imsi(ser)
    operator   = get_operator(ser)
    print_sim_info(sim_number, imsi, operator)

    if args.monitor:
        monitor_new_sms(ser, sim_number)
    else:
        # Show last N messages
        print(f"\n📨  Last {args.last} SMS message(s):\n")
        msgs = read_last_n_sms(ser, args.last)
        if not msgs:
            print("  (No messages found in storage)")
        for msg in msgs:
            print_message(msg, sim_number)

    ser.close()
    print("\nDone.\n")


if __name__ == "__main__":
    main()
