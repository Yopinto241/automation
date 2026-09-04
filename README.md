# Web Automation Console

An interactive, plan-driven browser automation tool built with SeleniumBase and Firefox/LibreWolf.

## What it does

- Opens a target site and runs an ordered series of browser activities.
- Supports opening URLs, typing, clicking, selecting options, pressing keys, waiting, scrolling, hovering, reading, checking text, screenshots, and HTML capture.
- Resolves CSS selectors, XPath, IDs, names, and simple visible text or form labels.
- Saves screenshots, HTML, and read results under `output/run_YYYYMMDD_HHMMSS/`.
- Saves the most recently run interactive plan as `last_plan.json`.

## Setup

Create and activate a virtual environment, then install the dependency:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Make sure Firefox or LibreWolf is installed. SeleniumBase may download or configure the required driver when it runs.

## Usage

Start the interactive plan builder:

```powershell
python run_automation.py
```

Run a saved plan without prompts:

```powershell
python run_automation.py --file plans\example_plan.json --headless --browser firefox
```

Interactive mode lists detected Chrome, Firefox/LibreWolf, and Edge installations and lets you choose one. On Windows, `automate.bat` runs the same command using the active `python` on `PATH`.

Run the unit tests with:

```powershell
python -m unittest discover -s tests -v
```

Each run also produces a structured `results.json` summary. Artifact names are restricted to the run output directory, and invalid plans are rejected before browser startup.

Only automate sites and accounts where you have permission. Avoid storing credentials or other secrets in plans.