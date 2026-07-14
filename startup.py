import os
import json
import re
import subprocess
import sqlite3
import threading
import time
from datetime import datetime

import numpy as np
import pyautogui
import pyttsx3
import requests
import sounddevice as sd
import speech_recognition as sr

# ---------------- Engine setup ----------------
engine = pyttsx3.init()
engine.setProperty('voice', engine.getProperty('voices')[1].id)
engine_lock = threading.Lock()  # pyttsx3 is not thread-safe, so we guard it

# ---------------- Shared state ----------------
app_index = {}
index_ready = threading.Event()  # signals when indexing is done

DB_PATH = "jarvis_logs.db"

# ---------------- Speech recognition setup ----------------
recognizer = sr.Recognizer()
recognizer.pause_threshold = 0.8  # thoda kam pause on silence = faster response


# ---------------- Database setup ----------------
def init_db():
    """Creates the logs table if it doesn't already exist."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS command_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            command TEXT NOT NULL,
            matched_app TEXT,
            status TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def log_command(command, matched_app, status):
    """Every command gets logged: what was asked, what matched, success/fail."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO command_logs (timestamp, command, matched_app, status) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), command, matched_app, status),
    )
    conn.commit()
    conn.close()


# ---------------- Voice ----------------
def speak(text):
    print(f"Jarvis: {text}")
    with engine_lock:
        engine.say(text)
        engine.runAndWait()


def index_startapps():
    """
    Uses PowerShell's Get-StartApps to find ALL installed apps, including
    Microsoft Store / UWP apps (like WhatsApp) that don't show up as
    plain .exe/.lnk files in Program Files.
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json"],
            capture_output=True, text=True, timeout=15,
        )
        apps = json.loads(result.stdout)
        if isinstance(apps, dict):  # if only one app, PowerShell doesn't return a list
            apps = [apps]

        for app in apps:
            name = app.get("Name", "").lower().strip()
            app_id = app.get("AppID", "")
            if name and app_id:
                # This special "shell:appsFolder\..." path works directly with os.startfile
                app_index.setdefault(name, f"shell:appsFolder\\{app_id}")
    except Exception as e:
        print(f"[Get-StartApps indexing failed: {e}]")


# ---------------- Indexing ----------------
def index_apps():
    """Indexes all .exe / .lnk files once at startup, in the background."""
    print("Indexing apps... Please wait (this runs once)...")
    search_paths = [
        r"C:\Program Files",
        r"C:\Program Files (x86)",
        os.path.join(os.environ.get('APPDATA', ''), r'Microsoft\Windows\Start Menu\Programs'),
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft\\WindowsApps'),
    ]

    for path in search_paths:
        if not os.path.isdir(path):
            continue  # skip paths that don't exist on this machine
        for root, dirs, files in os.walk(path):
            for file in files:
                if file.lower().endswith((".exe", ".lnk")):
                    name = file.lower().replace(".exe", "").replace(".lnk", "")
                    # don't overwrite an existing shorter/cleaner match with a duplicate
                    app_index.setdefault(name, os.path.join(root, file))

    print(f"Indexing complete! {len(app_index)} apps found (files). Now indexing Store apps...")
    index_startapps()
    print(f"Total apps indexed: {len(app_index)}. System Ready.")
    index_ready.set()


# ---------------- Matching ----------------
def find_best_match(app_name):
    """
    Returns the best matching (name, path) or (None, None).
    Priority: exact match > name starts with query > query is substring of name.
    Avoids random substring false-positives by ranking matches instead of
    taking the first dict hit.
    """
    exact, starts_with, contains = None, None, None

    for name, path in app_index.items():
        if app_name == name:
            exact = (name, path)
            break
        if starts_with is None and name.startswith(app_name):
            starts_with = (name, path)
        if contains is None and app_name in name:
            contains = (name, path)

    return exact or starts_with or contains or (None, None)


# ---------------- Listening ----------------
SAMPLE_RATE = 16000
CHUNK_DURATION = 0.2           # seconds per audio chunk checked
MAX_RECORD_SECONDS = 6         # hard ceiling, safety net
SILENCE_LIMIT = 0.5            # stop after this much silence following speech
SILENCE_THRESHOLD = 300        # amplitude below this counts as "silence"


def listen_command():
    """
    Records adaptively: starts capturing, and stops as soon as it detects
    the person has gone quiet again (instead of always waiting a fixed
    duration). Much snappier than a fixed-length recording.
    """
    print("Listening...")
    chunk_samples = int(SAMPLE_RATE * CHUNK_DURATION)
    max_chunks = int(MAX_RECORD_SECONDS / CHUNK_DURATION)
    silence_chunks_needed = int(SILENCE_LIMIT / CHUNK_DURATION)

    frames = []
    silent_streak = 0
    speech_started = False

    try:
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16')
        stream.start()
        for _ in range(max_chunks):
            data, _ = stream.read(chunk_samples)
            frames.append(data.copy())
            amplitude = np.abs(data).max()

            if amplitude > SILENCE_THRESHOLD:
                speech_started = True
                silent_streak = 0
            elif speech_started:
                silent_streak += 1
                if silent_streak >= silence_chunks_needed:
                    break
        stream.stop()
        stream.close()
    except Exception as e:
        print(f"Mic error: {e}")
        return None

    if not speech_started:
        return None  # nothing but silence, don't bother sending to Google

    recording = np.concatenate(frames)
    audio_bytes = recording.tobytes()
    audio_data = sr.AudioData(audio_bytes, SAMPLE_RATE, 2)

    try:
        text = recognizer.recognize_google(audio_data, language="en-IN")
        print(f"You said: {text}")
        return text.lower().strip()
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        print(f"Speech service error: {e}")
        return None


def close_app(query):
    """Closes a running app by matching its process name (partial match)."""
    keyword = query.replace("close", "", 1).strip().lower()
    keyword = " ".join(w for w in keyword.split() if len(w) > 1)

    if not keyword:
        speak("Kaunsi app band karni hai, naam bolo.")
        return

    try:
        ps_cmd = (
            f"Get-Process | Where-Object {{$_.ProcessName -like '*{keyword}*'}} "
            f"| Stop-Process -Force"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
        )
        speak(f"{keyword} band kar diya.")
        log_command(query, keyword, "closed")
    except Exception as e:
        speak("App band karne mein error aa gaya.")
        log_command(query, keyword, f"close_error:{e}")


from urllib.parse import quote

BRAVE_PATHS = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    os.path.join(os.environ.get('LOCALAPPDATA', ''), r"BraveSoftware\Brave-Browser\Application\brave.exe"),
]


def find_brave():
    for path in BRAVE_PATHS:
        if os.path.isfile(path):
            return path
    # fallback: check indexed apps
    _, path = find_best_match("brave")
    return path


def open_url_in_brave(url):
    brave = find_brave()
    if brave:
        subprocess.Popen([brave, url])
        return True
    return False


def get_first_youtube_video(query):
    """Scrapes YouTube search results to find the first video's ID."""
    try:
        url = f"https://www.youtube.com/results?search_query={quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=8)
        match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"[YouTube fetch error: {e}]")
    return None


def play_song(query):
    song = query
    for keyword in ("play", "search song", "search"):
        song = song.replace(keyword, "", 1)
    song = song.strip()

    if not song:
        speak("Kaunsa gaana chalana hai, bolo.")
        song = listen_command()
        if not song:
            speak("Kuch sunayi nahi diya, phir se try karo.")
            return

    speak(f"{song} dhoond raha hoon.")
    video_id = get_first_youtube_video(song)

    if video_id:
        url = f"https://www.youtube.com/watch?v={video_id}&autoplay=1"
        if open_url_in_brave(url):
            speak(f"{song} play kar raha hoon.")
            log_command(query, "youtube_play", "success")
        else:
            speak("Brave browser nahi mila.")
            log_command(query, "youtube_play", "brave_not_found")
    else:
        # fallback: just open search results if we couldn't find a direct video
        url = f"https://www.youtube.com/results?search_query={quote(song)}"
        open_url_in_brave(url)
        speak("Direct video nahi mila, search results khol diye.")
        log_command(query, "youtube_search", "fallback")


def open_website(name, url):
    if open_url_in_brave(url):
        speak(f"{name} khol raha hoon.")
    else:
        speak("Brave browser nahi mila.")


def toggle_playback(action):
    """
    Sends spacebar to the currently focused window, which pauses/resumes
    on YouTube. Requires the browser tab to be the active/focused window.
    """
    try:
        pyautogui.press('space')
        speak(f"Song {action} kar diya.")
    except Exception as e:
        speak("Control karne mein error aa gaya.")
        print(f"[toggle_playback error: {e}]")


def control_volume(direction, steps=3):
    """Presses system volume up/down media key multiple times."""
    key = 'volumeup' if direction == 'up' else 'volumedown'
    try:
        for _ in range(steps):
            pyautogui.press(key)
        speak(f"Volume {'badha' if direction == 'up' else 'kam'} diya.")
    except Exception as e:
        speak("Volume control mein error aa gaya.")
        print(f"[volume error: {e}]")


def get_brightness():
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"],
            capture_output=True, text=True, timeout=8,
        )
        return int(result.stdout.strip())
    except Exception as e:
        print(f"[get_brightness error: {e}]")
        return None


def set_brightness(level):
    level = max(0, min(100, level))
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})"],
            capture_output=True, text=True, timeout=8,
        )
        return True
    except Exception as e:
        print(f"[set_brightness error: {e}]")
        return False


def control_brightness(direction, step=15):
    current = get_brightness()
    if current is None:
        speak("Brightness control is laptop pe support nahi ho raha.")
        return

    new_level = current + step if direction == 'up' else current - step
    if set_brightness(new_level):
        speak(f"Brightness {'badha' if direction == 'up' else 'kam'} diya.")
    else:
        speak("Brightness badalne mein error aa gaya.")


# ---------------- Command execution ----------------
def execute_command(query):
    # 'open' ke baad ka text lo, aur 1-letter filler words (jaise misheard 'D') hata do
    raw = query.replace("open", "", 1).strip().lower()
    app_name = " ".join(word for word in raw.split() if len(word) > 1)

    if not index_ready.is_set():
        speak("Indexing abhi complete nahi hui hai, thoda ruko.")
        log_command(query, None, "indexing_not_ready")
        return

    name, path = find_best_match(app_name)

    if path:
        try:
            os.startfile(path)
            speak(f"Opening {name}")
            log_command(query, name, "success")
        except Exception as e:
            speak("App open karne mein error aa gaya.")
            log_command(query, name, f"error:{e}")
    else:
        speak("App nahi mili, sorry.")
        log_command(query, None, "not_found")


# ---------------- Main ----------------
def main():
    init_db()

    # Start indexing in background
    threading.Thread(target=index_apps, daemon=True).start()

    print("--- Universal AI Assistant Ready ---")
    print("(Indexing chal rahi hai background mein, bolna shuru kar sakte ho)")
    speak("Jarvis ready hai.")

    while True:
        cmd = listen_command()

        if cmd is None:
            continue  # kuch samajh nahi aaya, chup-chaap phir se suno

        if 'exit' in cmd or 'stop jarvis' in cmd or 'jarvis band karo' in cmd:
            speak("Goodbye!")
            break

        cleaned = cmd.strip().rstrip('.!?').strip()

        if 'brightness' in cmd:
            if any(w in cmd for w in ('up', 'increase', 'badhao', 'badha')):
                control_brightness('up')
            elif any(w in cmd for w in ('down', 'decrease', 'kam', 'ghatao')):
                control_brightness('down')
            else:
                speak("Brightness badhani hai ya kam karni hai, bolo.")
        elif 'volume' in cmd:
            if any(w in cmd for w in ('up', 'increase', 'badhao', 'badha')):
                control_volume('up')
            elif any(w in cmd for w in ('down', 'decrease', 'kam', 'ghatao')):
                control_volume('down')
            else:
                speak("Volume badhana hai ya kam karna hai, bolo.")
        elif cleaned in ('play', 'resume', 'continue'):
            toggle_playback("resume")
        elif cleaned in ('stop', 'pause'):
            toggle_playback("pause")
        elif 'play' in cmd or 'search' in cmd:
            if cmd.startswith('debug'):
                # Debug helper: "debug search whatsapp" shows matching indexed app names in console
                keyword = cmd.replace("debug search", "").replace("debug", "").strip()
                matches = [n for n in app_index if keyword in n]
                print(f"Matches for '{keyword}': {matches[:15]}")
                speak(f"{len(matches)} matches mile, console mein dekho.")
            else:
                play_song(cmd)
        elif cmd.startswith('close'):
            close_app(cmd)
        elif 'open ' in cmd:
            if 'youtube' in cmd and not any(w in cmd for w in ['play', 'song', 'gaana']):
                open_website("YouTube", "https://www.youtube.com")
            else:
                execute_command(cmd)
        else:
            speak("Samjha nahi. 'open' ke saath app ka naam bolo.")


if __name__ == "__main__":
    main()
