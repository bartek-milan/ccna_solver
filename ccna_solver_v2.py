#!/usr/bin/env python3

"""
Rewritten: hotkey-triggered OCR → DuckDuckGo → itexamanswers.net → click answers
Press NUMPAD1 to trigger. 20-second cooldown.
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

# -----------------------------
# CONFIG
# -----------------------------
LEFT_PANE_PERCENT = 0.30
TOP_SEARCH_PERCENT = 0.20
MIN_CONF = 25
FUZZY_THRESHOLD = 0.9
SLEEP_BETWEEN_ANSWERS = 0.2
HOTKEY = "1"
COOLDOWN = 2     # seconds


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
    text = re.sub(r"[^A-Za-z0-9?.,\- ]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def reconstruct_sentences(text):
    text = text.replace("\n", " ")
    text = re.sub(r"\s{2,}", " ", text)
    return re.findall(r"[^?]*\?", text)


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
        if not txt.strip():
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
        txt_low = txt.strip().lower()

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
    return img[start:h - 30, 0:w]


def extract_questions(img):
    text = pytesseract.image_to_string(img, config="--oem 3 --psm 6")
    return reconstruct_sentences(text)


# -----------------------------
# SEARCH / SELENIUM
# -----------------------------
def ddg_top_result(question, driver):
    query = f"site:itexamanswers.net {question}"
    url = f"https://duckduckgo.com/?q={query}&t=canonical"
    driver.get(url)

    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'a[data-testid="result-title-a"]'))
        )
    except:
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

    # fall back to red
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
def find_text_position(full_img, text, min_ratio=0.6):
    data = ocr_text_data(full_img)

    words = []
    boxes = []
    for i, w in enumerate(data["text"]):
        if w.strip():
            words.append(w.strip())
            boxes.append((
                int(data["left"][i]),
                int(data["top"][i]),
                int(data["width"][i]),
                int(data["height"][i])
            ))

    text_low = text.lower()
    best = None
    best_ratio = 0

    for i in range(len(words)):
        for j in range(i+1, len(words)+1):
            chunk = " ".join(words[i:j]).lower()
            ratio = SequenceMatcher(None, text_low, chunk).ratio()
            if ratio >= min_ratio and ratio > best_ratio:
                best_ratio = ratio
                left = boxes[i][0]
                top = boxes[i][1]
                width = sum(b[2] for b in boxes[i:j])
                height = max(b[3] for b in boxes[i:j])
                best = (left + width//2, top + height//2)

    return best


# -----------------------------
# MAIN
# -----------------------------
# -----------------------------
# MAIN
# -----------------------------
def main():
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

    driver = webdriver.Chrome(options=chrome_opts)


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

                            link = ddg_top_result(q, driver)
                            if not link:
                                print("❌ No result")
                                continue

                            answers = extract_answers(driver, q, link)
                            if not answers:
                                print("❌ No answers found")
                                continue

                            print("✅ Answers:", answers)

                            # Click each answer
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


                finally:
                    # Mark work as finished
                    busy = False
                    last_finish = time.time()
                    print(f"✔ Completed. Cooling down for {COOLDOWN} seconds...\n")

            time.sleep(0.05)

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
