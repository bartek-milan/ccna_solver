#!/usr/bin/env python3
"""
auto_itexamanswers_duckduckgo_selenium_headless_fuzzy.py

OCR → Headless DuckDuckGo search → first itexamanswers.net → correct_answer list
Fuzzy matching (90%) accounts for OCR errors/typos.
"""

import time
import re
import cv2
import numpy as np
from PIL import Image
import pytesseract
import mss
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from urllib.parse import urlparse, parse_qs, unquote
from difflib import SequenceMatcher
import cv2
import numpy as np
import ctypes
from ctypes import wintypes
import time
import tkinter as tk

# -----------------------------
# CONFIG
# -----------------------------
LEFT_PANE_PERCENT = 0.30
TOP_SEARCH_PERCENT = 0.20
MIN_CONF = 25
SLEEP_SECONDS = 5  # 5 seconds between searches
FUZZY_THRESHOLD = 0.9  # 90% similarity
WS_EX_LAYERED = 0x80000
WS_EX_TRANSPARENT = 0x20
WS_EX_TOPMOST = 0x00000008
WS_POPUP = 0x80000000
SW_SHOW = 5
LWA_ALPHA = 0x2

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
hinst = kernel32.GetModuleHandleW(None)

# -----------------------------
# SCREEN CAPTURE & OCR
# -----------------------------
def grab_screen():
    with mss.mss() as sct:
        mon = sct.monitors[1]
        sct_img = sct.grab(mon)
        img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

def to_gray(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.bilateralFilter(gray, 9, 75, 75)

def ocr_data(img):
    return pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

def show_overlay_dot(x, y, size=12, color="gray"):
    """
    Show a visible dot overlay at (x, y) using Tkinter.
    Returns the Tk window object so you can destroy it later.
    """
    root = tk.Tk()
    root.overrideredirect(True)  # no borders
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.9)  # semi-transparent
    root.geometry(f"{size}x{size}+{x - size//2}+{y - size//2}")

    canvas = tk.Canvas(root, width=size, height=size, highlightthickness=0, bg="white")
    canvas.pack()
    canvas.create_oval(0, 0, size, size, fill=color, outline="")

    root.update()
    return root

# -----------------------------
# FIND TEXT COORDINATES ON SCREEN
# -----------------------------
def find_text_position(full_image, text, min_ratio=0.6):
    """
    Returns (x, y) coordinates at the center of the matched text.
    """
    data = pytesseract.image_to_data(full_image, output_type=pytesseract.Output.DICT)
    words = [w.strip() for w in data["text"] if w.strip()]
    lefts = [int(data["left"][i]) for i, w in enumerate(data["text"]) if w.strip()]
    tops = [int(data["top"][i]) for i, w in enumerate(data["text"]) if w.strip()]
    widths = [int(data["width"][i]) for i, w in enumerate(data["text"]) if w.strip()]
    heights = [int(data["height"][i]) for i, w in enumerate(data["text"]) if w.strip()]

    best_ratio = 0
    best_coords = None

    n = len(words)
    text_tokens = text.lower().split()

    for window_size in range(1, min(len(text_tokens)+2, n+1)):
        for i in range(n - window_size + 1):
            chunk = " ".join(words[i:i+window_size]).lower()
            ratio = SequenceMatcher(None, text.lower(), chunk).ratio()
            if ratio > best_ratio and ratio >= min_ratio:
                best_ratio = ratio
                x = lefts[i]
                y = tops[i]
                w = sum(widths[i:i+window_size])
                h = max(heights[i:i+window_size])
                # center of the text bounding box
                best_coords = (x + w // 2, y + h // 2)

    if best_coords:
        print(f"[DEBUG] Approx match found at {best_coords} (ratio={best_ratio:.2f})")
        return best_coords
    else:
        print(f"[DEBUG] No text match found for: {text}")
        return None

def find_question_header_bbox_in_content(content_img):
    h, w = content_img.shape[:2]
    top_limit = int(h * TOP_SEARCH_PERCENT)
    data = ocr_data(content_img)

    best_token = None
    best_height = 0

    for i, txt in enumerate(data.get("text", [])):
        if not txt.strip():
            continue
        try:
            conf = int(float(data["conf"][i]))
        except:
            conf = -1
        if conf < MIN_CONF:
            continue

        tx = txt.strip()
        left, top, width, height = (
            int(data["left"][i]),
            int(data["top"][i]),
            int(data["width"][i]),
            int(data["height"][i])
        )

        if tx.lower().startswith("question"):
            return (left, top, width, height)

        if top < top_limit and height > best_height:
            best_height = height
            best_token = (left, top, width, height)

    return best_token

def reconstruct_sentences(text):
    text = text.replace("\n", " ")
    text = re.sub(r'\s{2,}', ' ', text)
    return re.findall(r'[^?]*\?', text)

def crop_region_below_header(content_img, header_bbox):
    h, w = content_img.shape[:2]
    start_y = int(h*0.12) if not header_bbox else max(0, header_bbox[1] + header_bbox[3] - 4)
    return content_img[start_y:h-30, 0:w]

def extract_questions_from_crop(crop_img):
    text = pytesseract.image_to_string(crop_img, config="--oem 3 --psm 6")
    return reconstruct_sentences(text), text

# -----------------------------
# HELPER: CLEAN OCR TEXT
# -----------------------------
def clean_ocr_text(text):
    text = re.sub(r'[^A-Za-z0-9?.,\-\s]+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()

# -----------------------------
# SELENIUM + DUCKDUCKGO
# -----------------------------
def duckduckgo_top_result(question, driver):
    query = f"site:itexamanswers.net {question}"
    search_url = f"https://duckduckgo.com/?q={query}&t=canonical"
    print("DEBUG: DuckDuckGo search for:", question)
    driver.get(search_url)

    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'a[data-testid="result-title-a"]'))
        )
    except:
        print("DEBUG: No results loaded in time")
        return None

    links = driver.find_elements(By.CSS_SELECTOR, 'a[data-testid="result-title-a"]')
    for a in links:
        href = a.get_attribute("href")
        if not href:
            continue
        if "duckduckgo.com/l/?" in href:
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            real_url = unquote(qs.get("uddg", [href])[0])
        else:
            real_url = href

        if "itexamanswers.net" in real_url:
            print("DEBUG: Top itexamanswers.net link:", real_url)
            return real_url

    return None

def extract_correct_answers_for_question(driver, question_text, url):
    driver.get(url)
    time.sleep(1)
    soup = BeautifulSoup(driver.page_source, "html.parser")

    q_clean = question_text.strip().lower()

    # ---- NEW: robust similarity scoring ----
    def score(a, b):
        # tokenize
        ta = set(a.split())
        tb = set(b.split())
        
        token_overlap = len(ta & tb) / max(1, len(ta | tb))
        seq = SequenceMatcher(None, a, b).ratio()
        
        # weighted score
        return (token_overlap * 0.6) + (seq * 0.4)

    # Accept anything above 0.55 (OCR-safe)
    MIN_SCORE = 0.55

    # All red patterns
    red_patterns = [
        "color:red", "color: red",
        "#ff0000", "#f00", "#d00",
        "rgb(255,0,0)", "rgb(255, 0, 0)"
    ]

    def is_red(el):
        style = el.get("style", "").replace(" ", "").lower()
        return any(r in style for r in red_patterns)

    best_ul = None
    best_score = 0

    # ---- Find the closest question on-page ----
    for p in soup.find_all("p"):
        strong = p.find("strong")
        if not strong:
            continue

        strong_text = strong.get_text(strip=True).lower()
        s = score(q_clean, strong_text)

        if s > best_score and s >= MIN_SCORE:
            best_score = s
            best_ul = p.find_next_sibling("ul")

    if not best_ul:
        print("DEBUG: No matching question found (best score too low)")
        return []

    # ---- Extract answers ----

    # A) First try class="correct_answer"
    answers = [li.get_text(strip=True) for li in best_ul.find_all("li", class_="correct_answer")]
    if answers:
        print(f"DEBUG: Found class correct_answer (score={best_score:.2f}): {answers}")
        return answers

    # B) Fall back to red-color detection
    red_hits = []

    for li in best_ul.find_all("li"):
        if is_red(li):
            red_hits.append(li.get_text(strip=True))
            continue
        for child in li.find_all():
            if is_red(child):
                red_hits.append(li.get_text(strip=True))
                break

    if red_hits:
        print(f"DEBUG: Found red-text answers (score={best_score:.2f}): {red_hits}")
        return red_hits

    print(f"DEBUG: No correct answers found for UL (score={best_score:.2f})")
    return []

# -----------------------------
# MAIN LOOP
# -----------------------------
def main():
    print("🤖 OCR → Headless DuckDuckGo → Top Result → correct_answer (fuzzy match)\n")
    seen = set()

    # Setup Chrome (headless)
    chrome_options = Options()
    #chrome_options.add_argument("--headless")
    #chrome_options.add_argument("--disable-gpu")
    #chrome_options.add_argument("--no-sandbox")
    #chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("window-size=1920,1080")
    driver = webdriver.Chrome(options=chrome_options)

    try:
        while True:
            screen = grab_screen()
            H, W = screen.shape[:2]

            left_cut = int(W * LEFT_PANE_PERCENT)
            content = screen[:, left_cut:W]
            content_gray = to_gray(content)

            header = find_question_header_bbox_in_content(content_gray)
            crop = crop_region_below_header(content_gray, header)

            if crop.size == 0:
                time.sleep(SLEEP_SECONDS)
                continue

            questions, _ = extract_questions_from_crop(crop)

            for q in questions:
                q = clean_ocr_text(q)
                if q in seen or len(q) > 400:
                    continue

                print("\n❓ QUESTION:\n", q)

                link = duckduckgo_top_result(q, driver)
                if not link:
                    print("❌ No DuckDuckGo result")
                    continue

                answer = extract_correct_answers_for_question(driver, q, link)
                if answer:
                    print("✅ ANSWERS:", answer)
                else:
                    print("❌ ANSWER NOT FOUND")
                #dot marking
                for ans in answer:
                    full_img = grab_screen()
                    overlay_windows = []

                    pos = find_text_position(full_img, ans)
                    if pos:
                        win = show_overlay_dot(pos[0], pos[1], size=3, color="gray")
                        overlay_windows.append(win)

# Optional: remove all after some time
                time.sleep(1)
                for win in overlay_windows:
                    win.destroy()
                    overlay_windows.clear()




                # 5-second delay between searches
                time.sleep(SLEEP_SECONDS)

    except KeyboardInterrupt:
        print("\n🛑 Stopped.")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
