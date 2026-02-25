#!/usr/bin/env python3
import subprocess
import re
import time
import sys
import click
from datetime import datetime

# --- ADB & System Helpers ---

def run_adb(args, device_id=None):
    cmd = ["adb"]
    if device_id:
        cmd += ["-s", device_id]
    cmd += args
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return res.stdout, res.stderr, res.returncode

def get_connected_devices():
    stdout, _, _ = run_adb(["devices", "-l"])
    devices =[]
    for line in stdout.splitlines()[1:]:
        if not line.strip() or "offline" in line or "unauthorized" in line:
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

def get_sim_info(device_id):
    info = {}
    m, _, _ = run_adb(["shell", "getprop", "ro.product.model"], device_id)
    v, _, _ = run_adb(["shell", "getprop", "ro.build.version.release"], device_id)
    info["model"], info["android"] = m.strip() or "Unknown", v.strip() or "Unknown"

    sim_number = "Unknown"

    # Method 1: Query the Telephony SIM Info database
    out, _, _ = run_adb(["shell", "content", "query", "--uri", "content://telephony/siminfo", "--projection", "number"], device_id)
    match = re.search(r"number=([\+\d]{7,15})", out)
    if match:
        sim_number = match.group(1)

    # Method 2: Dumpsys Subscription fallback
    if sim_number == "Unknown":
        out, _, _ = run_adb(["shell", "dumpsys", "subscription"], device_id)
        match = re.search(r"number=([\+\d]{7,15})", out)
        if match:
            sim_number = match.group(1)

    # Method 3: telephony.registry fallback
    if sim_number == "Unknown":
        out, _, _ = run_adb(["shell", "dumpsys", "telephony.registry"], device_id)
        match = re.search(r"mPhoneNumber=([\+\d]{7,15})", out)
        if match:
            sim_number = match.group(1)

    # Method 4: Bash Script integration (iphonesubinfo 19 hex decoding)
    if sim_number == "Unknown":
        out, _, _ = run_adb(["shell", "service", "call", "iphonesubinfo", "19"], device_id)
        
        hex_chunks = re.findall(r"\b([0-9a-f]{8})\b", out.lower())
        decoded_bytes = bytearray()
        
        for chunk in hex_chunks:
            swapped = chunk[4:6] + chunk[6:8] + chunk[0:2] + chunk[2:4]
            try:
                decoded_bytes.extend(bytes.fromhex(swapped))
            except ValueError:
                pass
                
        raw_str = decoded_bytes.decode("utf-8", errors="ignore")
        clean_num = re.sub(r"[^\+0-9]", "", raw_str)
        if len(clean_num) >= 7:
            sim_number = clean_num

    info["sim_number"] = sim_number
    return info

# --- Contact & SMS Logic ---

def get_contact_name(device_id, phone_number):
    """Resolves a phone number to a saved contact name using Android's Contacts Provider."""
    if not phone_number or phone_number == "Unknown" or not any(c.isdigit() for c in phone_number):
        return None
        
    safe_num = phone_number.replace("+", "%2B").replace(" ", "")
    uri = f"content://com.android.contacts/phone_lookup/{safe_num}"
    
    out, _, _ = run_adb(["shell", "content", "query", "--uri", uri, "--projection", "display_name"], device_id)
    
    match = re.search(r"display_name=(.*?)(?:,|$)", out)
    if match and match.group(1) and match.group(1) != "NULL":
        return match.group(1).strip()
    return None

def get_unique_senders(device_id):
    """Retrieves a unique list of all numbers that have sent an SMS to the device."""
    out, _, _ = run_adb(["shell", "content", "query", "--uri", "content://sms/inbox", "--projection", "address"], device_id)
    
    senders = set()
    for line in out.splitlines():
        line = line.replace("\r", "")
        match = re.search(r"address=(.*)", line)
        if match:
            addr = match.group(1).strip()
            if addr:
                senders.add(addr)
                
    return sorted(list(senders))

def parse_content_rows(raw):
    rows =[]
    blocks = re.split(r"\bRow:\s*\d+\s+", raw)
    for block in blocks:
        if not block.strip(): continue
        row = {}
        parts = re.split(r",\s*(?=[a-zA-Z_]+=)", block.strip())
        for part in parts:
            if "=" in part:
                k, _, v = part.partition("=")
                row[k.strip()] = v.strip()
        if row: rows.append(row)
    return rows

def read_sms(device_id, limit=3, all_msgs=False, debug=False):
    raw, err, code = run_adb(["shell", "content", "query", "--uri", "content://sms/inbox"], device_id)
    
    if debug:
        click.secho(f"\n[DEBUG RAW]\n{raw[:500]}...", fg='yellow', dim=True)

    if not raw.strip() or "No result found" in raw:
        return[]

    rows = parse_content_rows(raw)
    rows.sort(key=lambda r: int(r.get("date", 0)) if str(r.get("date", "")).isdigit() else 0, reverse=True)

    if all_msgs:
        return rows[:max(limit, 100)]

    seen, results = set(),[]
    for r in rows:
        sender = re.sub(r"[\s\-\(\)\+]", "", r.get("address", "")).upper()
        if sender and sender not in seen:
            seen.add(sender)
            results.append(r)
        if len(results) >= limit: break
    return results

# --- UI Helpers ---

def display_message(row, index, device_id, label="Conversation"):
    date_val = row.get("date", "0")
    try:
        ts = int(date_val) // 1000 if int(date_val) > 10**12 else int(date_val)
        time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except:
        time_str = date_val

    # Grab the Sender's number and Contact Name
    sender_number = row.get('address', 'Unknown')
    contact_name = get_contact_name(device_id, sender_number)
    
    # Display the Name in 'From:' if they exist in contacts, otherwise fallback to the number
    from_display = contact_name if contact_name else sender_number

    click.echo(f"\n{click.style(f' {label} #{index} ', bg='blue', fg='white', bold=True)}")
    click.echo(f"{click.style('From   :', fg='cyan')} {from_display}")
    click.echo(f"{click.style('Time   :', fg='cyan')} {time_str}")
    
    # Replaced Device SIM with the Sender's Contact Number, as requested
    click.echo(f"{click.style('Number :', fg='cyan')} {sender_number} (Slot: {row.get('sim_id', '1')})")
    
    click.echo(f"{click.style('Message:', fg='cyan')}\n    {row.get('body', '').strip()}")
    click.echo(click.style("─" * 50, dim=True))

# --- CLI Command ---

@click.command()
@click.option('--last', default=3, help='Number of messages/conversations to show.')
@click.option('--monitor', is_flag=True, help='Watch for new messages in real-time.')
@click.option('--all', 'show_all', is_flag=True, help='Show all messages (un-grouped).')
@click.option('--senders', is_flag=True, help='List all unique SMS sender contact numbers.')
@click.option('--device', help='Specific ADB device ID.')
@click.option('--debug', is_flag=True, help='Show raw ADB output.')
def main(last, monitor, show_all, senders, device, debug):
    """SMS Reader for Android via ADB Wireless Debugging."""
    
    # 1. Check ADB
    _, _, code = run_adb(["version"])
    if code != 0:
        click.secho("Error: ADB not found. Make sure it's in your PATH.", fg='red', bold=True)
        sys.exit(1)

    # 2. Get Device
    devices = get_connected_devices()
    if not devices:
        click.secho("Error: No devices connected via ADB.", fg='red')
        sys.exit(1)
    
    target_id = device or devices[0]["id"]
    sim = get_sim_info(target_id)

    # 3. Print Header
    click.clear()
    click.secho(f"📱 SMS READER [ADB]", fg='green', bold=True)
    click.echo(f"Device : {sim['model']} (Android {sim['android']})")
    click.echo(f"SIM #  : {click.style(sim['sim_number'], fg='yellow', bold=True)}")
    click.echo(click.style("=" * 50, dim=True))

    # 4. Handle Senders Mode
    if senders:
        click.secho(f"\n👥 UNIQUE SMS SENDERS", fg='magenta', bold=True)
        unique_list = get_unique_senders(target_id)
        
        if not unique_list:
            click.echo("No senders found.")
        else:
            for num in unique_list:
                cname = get_contact_name(target_id, num)
                display_str = f"{cname} ({num})" if cname else f"{num}"
                click.echo(f" • {display_str}")
        return

    # 5. Handle Monitor Mode
    if monitor:
        click.secho(f"\n📡 Monitoring {target_id}... (Ctrl+C to stop)", fg='magenta', italic=True)
        seen_ids = {r.get("_id") for r in read_sms(target_id, limit=100, all_msgs=True)}
        try:
            while True:
                time.sleep(4)
                current = read_sms(target_id, limit=10, all_msgs=True)
                for m in reversed(current):
                    if m.get("_id") not in seen_ids:
                        display_message(m, "NEW", target_id, label="ALERT")
                        seen_ids.add(m.get("_id"))
        except KeyboardInterrupt:
            click.echo("\nMonitoring stopped.")
            return

    # 6. Standard Fetch
    msgs = read_sms(target_id, limit=last, all_msgs=show_all, debug=debug)
    if not msgs:
        click.echo("No messages found.")
    else:
        label = "Message" if show_all else "Conversation"
        for i, m in enumerate(msgs, 1):
            display_message(m, i, target_id, label=label)

if __name__ == "__main__":
    main()
