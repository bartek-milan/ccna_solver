#!/usr/bin/env python3
"""
Offline + Online hybrid answer finder.

Behavior:
 - On startup it loads offline Q/A pairs from questions.json1 (newline-delimited JSON).
 - It prefers the online flow (DuckDuckGo -> itexamanswers) when reachable.
 - If itexamanswers is unreachable or a query fails online, the script falls back to offline.
 - The script periodically re-checks the site and will switch back to online when available.
 - Hotkey triggers OCR/search. Default is NUMPAD1 (HOTKEY="1").
"""

import time
import re
import cv2
import numpy as np
import pytesseract
import pydirectinput as pyautogui
import keyboard
import mss
from PIL import Image
from difflib import SequenceMatcher
from urllib.parse import urlparse, parse_qs, unquote
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import json
import os
import socket

# Optional: use requests if available for faster HEAD checks
try:
    import requests
    _HAS_REQUESTS = True
except Exception:
    _HAS_REQUESTS = False

# -----------------------------
# CONFIG
# -----------------------------
LEFT_PANE_PERCENT = 0.30
TOP_SEARCH_PERCENT = 0.20
MIN_CONF = 25
FUZZY_THRESHOLD = 0.9
SLEEP_BETWEEN_ANSWERS = 0.2
HOTKEY = "1"                 # NUMPAD1 in original environment
COOLDOWN = 2                 # seconds between allowed triggers
ONLINE_CHECK_INTERVAL = 30   # seconds between online reachability checks
OFFLINE_MATCH_THRESHOLD = 0.50  # minimal similarity to consider a match
OFFLINE_FILENAME = "questions.jsonl"  # <-- your filename

# Online mode state
ONLINE_MODE = True
LAST_ONLINE_CHECK = 0

# -----------------------------
# LOAD OFFLINE JSONL (newline-delimited JSON lines)
# -----------------------------
OFFLINE_QA = []

def load_offline_file(path):
    global OFFLINE_QA
    OFFLINE_QA = []
    if not os.path.exists(path):
        print(f"⚠ Offline file not found: {path} (continuing without offline database)")
        return

    with open(path, "r", encoding="utf8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                # Each line is a JSON object
                item = json.loads(line)
                # Ensure expected shape
                if isinstance(item, dict) and "question" in item and "answers" in item:
                    OFFLINE_QA.append(item)
                else:
                    print(f"⚠ Skipping malformed line {i} in offline file.")
            except Exception as e:
                print(f"⚠ Error parsing line {i} in offline file: {e}")

load_offline_file(OFFLINE_FILENAME)
print(f"Loaded {len(OFFLINE_QA)} offline Q/A entries from {OFFLINE_FILENAME}")

# -----------------------------
# UTIL: normalize and offline matching
# -----------------------------
def normalize_question(q):
    if not q:
        return ""
    q = q.lower().strip()
    # Remove leading "question" word, leading numbers, "1.", "1)", "1 -", "1:"
    q = re.sub(r"^(question\s*)?\d+[\)\.\-:]*\s*", "", q)
    # Remove repeated whitespace
    q = re.sub(r"\s{2,}", " ", q)
    # Remove stray punctuation at ends
    q = q.strip(" .:-")
    return q

def find_offline_answers(question):
    if not OFFLINE_QA:
        return None

    target = normalize_question(question)
    best_score = 0.0
    best_answers = None

    for item in OFFLINE_QA:
        q_raw = item.get("question", "")
        q_norm = normalize_question(q_raw)
        # use SequenceMatcher ratio
        score = SequenceMatcher(None, target, q_norm).ratio()
        if score > best_score:
            best_score = score
            best_answers = item.get("answers")

    if best_score < OFFLINE_MATCH_THRESHOLD:
        # no good match
        # print(f"DEBUG: best offline score {best_score:.3f} for '{question[:50]}'")
        return None

    # Optionally return a copy
    return list(best_answers) if best_answers else None

# -----------------------------
# SCREEN & OCR
# -----------------------------
def grab_screen():
    with mss.mss() as sct:
        img = sct.grab(sct.monitors[1])
        img = Image.frombytes("RGB", img.size, img.rgb)
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def to_gray(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.bilateralFilter(gray, 9, 75, 75)


def clean_ocr_text(text):
    text = re.sub(r"[^A-Za-z0-9?.,\-()/: ]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def reconstruct_sentences(text):
    text = text.replace("\n", " ")
    text = re.sub(r"\s{2,}", " ", text)
    # Return sequences ending with question mark; if none, split into lines of reasonable length
    qs = re.findall(r"[^?]*\?", text)
    if qs:
        return qs
    # fallback: try splitting on question words/lines
    parts = re.split(r"(?:\n|\\n)+", text)
    return [p.strip() for p in parts if p.strip()]


def ocr_text_data(img):
    return pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)


# -----------------------------
# QUESTION REGION DETECTION
# -----------------------------
def find_question_header_bbox(img):
    data = ocr_text_data(img)
    h, w = img.shape[:2]
    top_limit = int(h * TOP_SEARCH_PERCENT)

    best = None
    best_height = 0

    for i, txt in enumerate(data["text"]):
        if not txt or not str(txt).strip():
            continue
        try:
            conf = int(float(data["conf"][i]))
        except:
            conf = -1

        if conf < MIN_CONF:
            continue

        left = int(data["left"][i])
        top = int(data["top"][i])
        width = int(data["width"][i])
        height = int(data["height"][i])
        txt_low = str(txt).strip().lower()

        if txt_low.startswith("question"):
            return (left, top, width, height)

        if top < top_limit and height > best_height:
            best = (left, top, width, height)
            best_height = height

    return best


def crop_below_header(img, header_bbox):
    h, w = img.shape[:2]
    if not header_bbox:
        start = int(h * 0.12)
    else:
        start = header_bbox[1] + header_bbox[3] - 4
    # keep a little bottom margin
    return img[start:h - 30, 0:w] if h - 30 > start else img[start:h, 0:w]


def extract_questions(img):
    text = pytesseract.image_to_string(img, config="--oem 3 --psm 6")
    return reconstruct_sentences(text)


# -----------------------------
# SEARCH / SELENIUM ONLINE FUNCTIONS
# -----------------------------
def ddg_top_result(question, driver, timeout=8):
    query = f"site:itexamanswers.net {question}"
    url = f"https://duckduckgo.com/?q={query}&t=canonical"
    driver.get(url)

    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'a[data-testid="result-title-a"]'))
        )
    except Exception:
        return None

    links = driver.find_elements(By.CSS_SELECTOR, 'a[data-testid="result-title-a"]')
    for a in links:
        href = a.get_attribute("href")
        if not href:
            continue

        if "duckduckgo.com/l/" in href:
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            real = unquote(qs.get("uddg", [href])[0])
        else:
            real = href

        if "itexamanswers.net" in real:
            return real

    return None


def extract_answers(driver, question_text, url):
    driver.get(url)
    time.sleep(1)
    soup = BeautifulSoup(driver.page_source, "html.parser")

    def score(a, b):
        ta = set(a.split())
        tb = set(b.split())
        overlap = len(ta & tb) / max(1, len(ta | tb))
        seq = SequenceMatcher(None, a, b).ratio()
        return overlap * 0.6 + seq * 0.4

    MIN_SCORE = 0.55
    q_low = question_text.lower().strip()

    best_ul = None
    best_score = 0

    for p in soup.find_all("p"):
        strong = p.find("strong")
        if not strong:
            continue

        text = strong.get_text(strip=True).lower()
        s = score(q_low, text)
        if s > best_score and s >= MIN_SCORE:
            best_score = s
            best_ul = p.find_next_sibling("ul")

    if not best_ul:
        return []

    answers = [li.get_text(strip=True) for li in best_ul.find_all("li", class_="correct_answer")]
    if answers:
        return answers

    # fall back to styled red text detection
    red_patterns = [
        "color:red", "color: red",
        "#ff0000", "#f00", "#d00",
        "rgb(255,0,0)"
    ]

    def is_red(el):
        st = el.get("style", "").replace(" ", "").lower()
        return any(x in st for x in red_patterns)

    red_hits = []
    for li in best_ul.find_all("li"):
        if is_red(li):
            red_hits.append(li.get_text(strip=True))
            continue
        for c in li.find_all():
            if is_red(c):
                red_hits.append(li.get_text(strip=True))
                break

    return red_hits


# -----------------------------
# FIND POSITION OF TEXT ON SCREEN
# -----------------------------
def find_text_position(full_img, text, min_ratio=0.60):
    data = ocr_text_data(full_img)

    # Pre-extract words + boxes
    words = []
    boxes = []
    for i, w in enumerate(data["text"]):
        w = w.strip()
        if w:
            words.append(w.lower())  # pre-lowercase
            boxes.append((
                int(data["left"][i]),
                int(data["top"][i]),
                int(data["width"][i]),
                int(data["height"][i])
            ))

    if not words:
        return None

    target = text.lower()
    target_words = target.split()
    k = len(target_words)

    # Allow wider window for multi-line / bold OCR breaks
    window_sizes = {k - 2, k - 1, k, k + 1, k + 2}
    window_sizes = [ws for ws in window_sizes if ws > 0]

    best = None
    best_ratio = 0

    sm = SequenceMatcher(None, target, "")  # reuse matcher

    for size in window_sizes:
        if size > len(words):
            continue

        for start in range(len(words) - size + 1):

            chunk_words = words[start:start + size]
            chunk = " ".join(chunk_words)

            # --- SUPER FAST FILTERS ---
            # 1. First word must share first letter
            if chunk_words[0][0] != target_words[0][0]:
                continue

            # 2. Quick substring check (cheap)
            hit = 0
            for tw in target_words:
                if tw in chunk:
                    hit = 1
                    break
            if not hit:
                continue
            # ---------------------------

            # Expensive check (only ~5% of candidates now)
            sm.set_seq2(chunk)
            ratio = sm.ratio()

            if ratio >= min_ratio and ratio > best_ratio:
                best_ratio = ratio

                # Combine bounding boxes
                left = boxes[start][0]
                top = boxes[start][1]
                width = sum(b[2] for b in boxes[start:start+size])
                height = max(b[3] for b in boxes[start:start+size])

                best = (left + width // 2, top + height // 2)

    return best


# -----------------------------
# CONNECTIVITY CHECK (itexamanswers)
# -----------------------------
def check_online_quick():
    """
    Quick check whether itexamanswers.net appears reachable.
    Uses requests.head if available, else a raw socket connect to port 443.
    Returns True if reachable, False otherwise.
    """
    host = "itexamanswers.net"
    if _HAS_REQUESTS:
        try:
            r = requests.head(f"https://{host}", timeout=3, allow_redirects=True)
            # treat 4xx as reachable (site replied) but 5xx as server error
            return r.status_code < 500
        except Exception:
            return False
    else:
        # fallback: try TCP connect to port 443
        try:
            sock = socket.create_connection((host, 443), timeout=3)
            sock.close()
            return True
        except Exception:
            return False


# -----------------------------
# MAIN
# -----------------------------
def main():
    global ONLINE_MODE, LAST_ONLINE_CHECK

    print("🚀 Ready. Press NUMPAD1 to run. Cooldown =", COOLDOWN, "seconds.")

    chrome_opts = Options()

    # Headless but with full browser features
    chrome_opts.add_argument("--headless=new")
    chrome_opts.add_argument("--disable-gpu")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--disable-dev-shm-usage")

    # Prevent headless detection
    chrome_opts.add_argument("--disable-blink-features=AutomationControlled")
    chrome_opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_opts.add_experimental_option('useAutomationExtension', False)

    # A real user-agent (VERY IMPORTANT)
    chrome_opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )

    # Keep layout stable
    chrome_opts.add_argument("--window-size=1920,1080")

    # Create driver (if Chrome/Chromedriver present)
    try:
        driver = webdriver.Chrome(options=chrome_opts)
    except Exception as e:
        print(f"⚠ Could not start Chrome WebDriver: {e}")
        print("⚠ Online mode will be disabled.")
        driver = None
        ONLINE_MODE = False

    busy = False          # true while performing search
    last_finish = 0       # stores time when last search finished

    try:
        while True:

            # If busy, do not allow triggering again
            if busy:
                time.sleep(0.05)
                continue

            # If cooldown after finishing
            if time.time() - last_finish < COOLDOWN:
                time.sleep(0.05)
                continue

            # Periodic online check to re-enable ONLINE_MODE when site returns
            if time.time() - LAST_ONLINE_CHECK > ONLINE_CHECK_INTERVAL:
                is_up = check_online_quick()
                if is_up and not ONLINE_MODE:
                    # Only switch if we also have a working Selenium driver
                    if driver is not None:
                        print("🌐 itexamanswers.net appears reachable — switching to ONLINE mode.")
                        ONLINE_MODE = True
                    else:
                        print("🌐 itexamanswers.net reachable, but WebDriver unavailable; staying OFFLINE.")
                        ONLINE_MODE = False
                elif not is_up and ONLINE_MODE:
                    print("⚠ itexamanswers.net unreachable — switching to OFFLINE mode.")
                    ONLINE_MODE = False
                LAST_ONLINE_CHECK = time.time()

            # Check for hotkey
            if keyboard.is_pressed(HOTKEY):
                busy = True
                print("\n🔥 Hotkey pressed. Running OCR/Search...\n")

                try:
                    # 1) Capture screen
                    full = grab_screen()
                    H, W = full.shape[:2]
                    content = full[:, int(W * LEFT_PANE_PERCENT):]

                    gray = to_gray(content)
                    header = find_question_header_bbox(gray)
                    crop = crop_below_header(gray, header)

                    if crop.size == 0:
                        print("❌ crop failed")
                    else:
                        questions = extract_questions(crop)

                        for q in questions:
                            q = clean_ocr_text(q)
                            if not q:
                                continue

                            print("❓ QUESTION:", q)

                            answers = None

                            # ----------------------------
                            # Periodic quick check before using online
                            # ----------------------------
                            if ONLINE_MODE and time.time() - LAST_ONLINE_CHECK > ONLINE_CHECK_INTERVAL:
                                # run a quick check now to ensure we haven't lost connectivity
                                if not check_online_quick():
                                    print("⚠ itexamanswers.net quick-check failed — switching to OFFLINE mode.")
                                    ONLINE_MODE = False
                                LAST_ONLINE_CHECK = time.time()

                            # ----------------------------
                            # IF ONLINE MODE: try online first, but be ready to fallback
                            # ----------------------------
                            if ONLINE_MODE and driver is not None:
                                try:
                                    link = ddg_top_result(q, driver)
                                    if not link:
                                        raise RuntimeError("No ddg result")

                                    answers = extract_answers(driver, q, link)
                                    if not answers:
                                        raise RuntimeError("No answers extracted from page")

                                    print("🌐 Online answers:", answers)

                                except Exception as e:
                                    # Any exception during online lookup triggers a fallback for this query and changes global mode
                                    print(f"⚠ Online lookup failed for question: {e}. Falling back to offline and switching OFFLINE mode.")
                                    ONLINE_MODE = False
                                    answers = find_offline_answers(q)
                                    if answers:
                                        print("📁 Offline answers (fallback):", answers)

                            # ----------------------------
                            # IF OFFLINE MODE or online fallback
                            # ----------------------------
                            elif not ONLINE_MODE:
                                answers = find_offline_answers(q)
                                if answers:
                                    print("📁 Offline answers:", answers)

                            # Final check
                            if not answers:
                                print("❌ No answers found anywhere (online/offline).")
                                continue

                            # Click each answer on screen if found
                            for ans in answers:
                                screen = grab_screen()
                                pos = find_text_position(screen, ans)
                                if pos:
                                    # make sure it's not on the left sidebar
                                    sidebar_x_limit = int(W * LEFT_PANE_PERCENT)

                                    if pos[0] < sidebar_x_limit:
                                        print("⛔ Sidebar match ignored:", ans, pos)
                                        continue

                                    print("→ Clicking:", ans, pos)
                                    pyautogui.moveTo(pos[0], pos[1], duration=0.1)
                                    pyautogui.click()
                                    time.sleep(SLEEP_BETWEEN_ANSWERS)
                                else:
                                    print("⛔ Could not locate answer on screen to click:", ans)

                finally:
                    # Mark work as finished
                    busy = False
                    last_finish = time.time()
                    print(f"✔ Completed. Cooling down for {COOLDOWN} seconds...\n")

            time.sleep(0.05)

    finally:
        if 'driver' in locals() and driver is not None:
            driver.quit()


if __name__ == "__main__":
    main()
