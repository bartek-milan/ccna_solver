# CCNA Solver

A fully automated tool that **reads CCNA questions from your screen, searches for answers online, and clicks the correct choices for you.**  
Powered by OCR, a headless browser, and precise on-screen text matching.  
Still hated by Markovich since 2025.

---

## Features

- **Automatic OCR question detection**  
  Reads the question directly from your screen (even partial or fuzzy text).

- **Headless browser search**  
  Searches DuckDuckGo invisibly in the background and opens the matching `itexamanswers.net` page.

- **Robust answer extraction**  
  Identifies correct answers even if the site layout shifts or formatting changes.

- **Safe, accurate clicking**  
  Clicks only inside the **actual question area**, avoiding the sidebar and other UI elements.

- **Hotkey-driven automation**  
  Press a key → the tool runs OCR → searches → extracts → finds → and clicks the correct answers.

- **Built-in cooldown**  
  Prevents repeated activation until the solver has completed a run.

> ⚠️ Image-based questions are **not supported**.

---

## Usage

1. Open your CCNA test window on screen.  
2. Run `ccna_solver.exe` as administrator.  
3. Use the hotkey to trigger the solver:

   **Primary workflow:**  
   - **NUMPAD 1** → Run the full pipeline  
     - Capture screen  
     - OCR the question  
     - Search online  
     - Extract correct answers  
     - Locate them on your screen  
     - Click them automatically  

4. The process runs headlessly and quietly — no browser window appears.  
5. A 2-second cooldown prevents accidental double triggers.

---

## Hotkeys

| Key          | Action                                                   |
|--------------|-----------------------------------------------------------|
| **NUMPAD 1** | Run OCR → search → extract answers → auto-click them     |
| **NUMPAD 9** | Quit the program                                         |

---

## Notes

- Must be run with **Administrator privileges** to enable global input control and off-focus clicking.  
- Designed for Windows; tested on 1920×1080.  
- Uses `pydirectinput` (or pyautogui if configured) to allow clicking even when the console is out of view.  
- Does **not** click sidebar results or unrelated UI elements — only the true answer region.

---

## Legacy

Created to eliminate endless CCNA busywork and provide fast, reliable practice automation.

---

## Disclaimer

This tool is intended for personal learning and study assistance. Use responsibly.
