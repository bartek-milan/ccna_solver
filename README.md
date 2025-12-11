# Online/Offline CCNA Solver

A lightweight tool that **reads CCNA questions from your screen, searches for answers online, and clicks the correct choices automatically.**  
Powered by OCR, a headless browser, and fast on-screen text matching.

---

## Features

- **OCR question detection** – Reads fuzzy or partial on-screen text.
- **Headless search** – Queries DuckDuckGo and fetches matching itexamanswers pages silently.
- **Accurate answer extraction** – Works even if the site layout changes.
- **Safe auto-clicking** – Clicks only inside the actual answer area, never the sidebar.
- **Hotkey automation** – One key triggers the entire pipeline.
- **Cooldown system** – Prevents accidental double-activation.

> ⚠️ Image-based questions are not supported.

---

## Usage

1. Open your CCNA question on screen.  
2. Run `ccna_solver.exe` (administrator recommended).  
3. Press **NUMPAD 1** to:
   - Capture the screen  
   - OCR the question  
   - Search online  
   - Extract correct answers  
   - Locate answers on screen  
   - Click them automatically  

The whole process runs quietly in the background.  
A 2-second cooldown prevents retriggering.

---

## Hotkeys

| Key          | Action                                                   |
|--------------|-----------------------------------------------------------|
| **NUMPAD 1** | Full run: OCR → search → extract → auto-click            |

---

## Requirements / Notes

- Windows recommended; tested on 1920×1080.  
- Requires admin rights for global hotkeys and off-focus clicking.  
- Uses `pydirectinput` for reliable input injection.  
- Avoids sidebar content and unrelated UI elements.

---

## Disclaimer

This tool is for personal study assistance only. Use responsibly.
