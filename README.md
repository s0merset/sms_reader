## 📱 Android ADB SMS Reader & Monitor

A lightweight, Python-based Command Line Interface (CLI) tool that lets you read, manage, and monitor SMS messages on your Android device directly from your computer terminal using ADB (Android Debug Bridge).

## ✨ Features

* **Read SMS Messages**: Fetch the latest text messages or conversations directly from your device.
* **Real-time Monitoring**: Watch for incoming text messages in real-time right in your terminal.
* **Contact Resolution**: Automatically matches sender phone numbers with saved Contact Names on your Android device.
* **SIM & Device Info Extraction**: Attempts to extract device model, Android version, and the device's SIM phone number using multiple fallback methods.
* **Unique Senders List**: Instantly scrape and view a list of all unique numbers/contacts that have sent you an SMS.
* **Multi-Device Support**: Easily target a specific phone if multiple devices are connected via USB or Wireless Debugging.

## 📋 Prerequisites

Before using this tool, ensure you have the following set up:

1. **Python 3.6+** installed on your system.
2. **ADB (Android Debug Bridge)** installed and added to your system's PATH. 
   * *Windows/Mac/Linux*: You can get it from the [Android SDK Platform-Tools](https://developer.android.com/studio/releases/platform-tools).
3. **USB Debugging** or **Wireless Debugging** enabled on your Android device (found in Developer Options).
4. Your computer must be **authorized** on your Android device (accept the prompt on your phone screen when you connect it for the first time).

# 🚀 Installation

1. Clone or download the script to your local machine (e.g., `sms_reader.py`).
2. Install the required Python library (`click`):

   ```bash
   pip install click sms_reader
   ```

3. (Optional for Linux/macOS) Make the script executable:
    ```bash 
    chmod +x sms_reader.py
    ```

## 💻 Usage
Run the script from your terminal. If you run it without any arguments, it will default to showing your last 3 SMS conversations.
```bash
    python sms_reader.py
    ```

## 🛠️ Available Commands & Options
| Option          | Description                                                                                       | Example                                           |
| --------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `--last <int>`  | Specify the number of messages/conversations to display (Default is 3).                           | `python sms_reader.py --last 5`                   |
| `--monitor`     | Run continuously and watch for new incoming SMS messages in real-time.                            | `python sms_reader.py --monitor`                  |
| `--all`         | Show all recent messages sequentially, rather than grouping them by unique senders/conversations. | `python sms_reader.py --all --last 10`            |
| `--senders`     | Print a clean list of all unique contacts/numbers that have ever texted you.                      | `python sms_reader.py --senders`                  |
| `--device <id>` | Target a specific device if multiple phones/emulators are connected via ADB.                      | `python sms_reader.py --device 192.168.1.10:5555` |
| `--debug`       | Print raw ADB queries and output for troubleshooting.                                             | `python sms_reader.py --debug`                    |
| `--help`        | Show the help menu with all available commands.                                                   | `python sms_reader.py --help`                     |


