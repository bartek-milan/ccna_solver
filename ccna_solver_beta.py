#!/usr/bin/env python3
"""
auto_itexamanswers_duckduckgo_selenium_visible.py

OCR → Visible DuckDuckGo search → first itexamanswers.net → <strong> answer

REQUIRES:
pip install selenium mss pillow numpy opencv-python pytesseract beautifulsoup4
Also download ChromeDriver matching your Chrome version and ensure it's in PATH.
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

# -----------------------------
# CONFIG
# -----------------------------
LEFT_PANE_PERCENT = 0.30
TOP_SEARCH_PERCENT = 0.20
MIN_CONF = 25
SLEEP_SECONDS = 1.0

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
# HELPER: CLEAN OCR TEXT (optional)
# -----------------------------
def clean_ocr_text(text):
    # Remove garbage characters, multiple spaces
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
    from selenium.webdriver.common.by import By

    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'a[data-testid="result-title-a"]')
            )
        )
    except:
        print("DEBUG: No results loaded in time")
        return None

    links = driver.find_elements(
        By.CSS_SELECTOR,
        'a[data-testid="result-title-a"]'
    )

    for a in links:
        href = a.get_attribute("href")
        if not href:
            continue

        # Handle DDG redirect URLs
        if "duckduckgo.com/l/?" in href:
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            real_url = unquote(qs.get("uddg", [href])[0])
        else:
            real_url = href

        # Skip ads with DDG redirect but no real URL
        if "itexamanswers.net" in real_url:
            print("DEBUG: Top itexamanswers.net link:", real_url)
            return real_url

    return None

def extract_strong_answer(url, driver):
    driver.get(url)
    time.sleep(1)
    soup = BeautifulSoup(driver.page_source, "html.parser")

    answers = []

    # Iterate through all <ul> in page order
    for ul in soup.find_all("ul"):
        # Find all li elements with class="correct_answer" inside this UL
        correct_tags = ul.find_all("li", class_="correct_answer")
        if correct_tags:
            for tag in correct_tags:
                txt = tag.get_text(strip=True)
                if txt:
                    answers.append(txt)
            print("DEBUG: Found correct_answer items in first matching UL:", answers)
            return answers  # Stop after first UL with correct_answer

    # Fallback if nothing found
    print("DEBUG: No class='correct_answer' found in any UL")
    return []

# -----------------------------
# MAIN LOOP
# -----------------------------
def main():
    print("🤖 OCR → Visible DuckDuckGo → Top Result → <strong> Answer\n")
    seen = set()

    # Setup Chrome (VISIBLE)
    chrome_options = Options()
    # Remove the headless option so browser is visible
    # chrome_options.add_argument("--headless")  # <-- removed
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=chrome_options)

    try:
        while True:
            screen = grab_screen()
            H, W = screen.shape[:2]

            left_cut = int(W*LEFT_PANE_PERCENT)
            content = screen[:, left_cut:W]
            content_gray = to_gray(content)

            header = find_question_header_bbox_in_content(content_gray)
            crop = crop_region_below_header(content_gray, header)

            if crop.size == 0:
                time.sleep(5)
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

                answer = extract_strong_answer(link, driver)
                if answer:
                    print("✅ ANSWER:", answer)
                else:
                    print("❌ ANSWER NOT FOUND")

                print("-"*72)
                seen.add(q)

            time.sleep(SLEEP_SECONDS)

    except KeyboardInterrupt:
        print("\n🛑 Stopped.")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
