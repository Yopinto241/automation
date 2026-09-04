#!/usr/bin/env python3
"""
Interactive Web Automation Tool
-------------------------------
Built on SeleniumBase + LibreWolf (Firefox-family engine).

Workflow:
  1. You give the target site (or load a saved plan).
  2. You build a SERIES of activities (type, click, wait, read, ...).
  3. You confirm and the system runs the whole series automatically.

Non-interactive usage (run a saved plan):
  python run_automation.py --file plans/my_plan.json [--headless]
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PLAN_DIR = os.path.join(SCRIPT_DIR, "plans")
OUT_DIR = os.path.join(SCRIPT_DIR, "output")
LAST_PLAN = os.path.join(SCRIPT_DIR, "last_plan.json")
RESULTS_FILE = os.path.join(OUT_DIR, "results.txt")

BROWSER = "firefox"  # -> LibreWolf (patched local SeleniumBase build)


# --------------------------------------------------------------------------
# Activity catalogue
# --------------------------------------------------------------------------
# Each activity: prompt text, and a list of parameter prompts.
# A param prompt ending in " (optional): " may be left empty.
ACTIVITY_MENU = [
    ("open", "Open a page (sub-page / different URL)"),
    ("type", "Type text into an input field"),
    ("click", "Click an element (button, link, icon...)"),
    ("select", "Choose an option from a dropdown"),
    ("press", "Press a keyboard key (Enter, Tab, Escape...)"),
    ("wait", "Pause for a number of seconds"),
    ("wait_for", "Wait until an element becomes visible"),
    ("read", "Read text from the page / an element"),
    ("check", "Verify that text appears on the page"),
    ("scroll", "Scroll the page"),
    ("hover", "Hover the mouse over an element"),
    ("shot", "Take a screenshot"),
    ("html", "Save the full page HTML to a file"),
]

ACTIVITY_PARAMS = {
    "open":      ["URL to open: "],
    "type":      ["Field: (CSS selector like '#user' or placeholder/label text): ",
                  "Text to type: "],
    "click":     ["Element: (CSS selector, or visible link/button text): "],
    "select":    ["Dropdown: (CSS selector, or its label/option text): ",
                  "Option to select: "],
    "press":     ["Key to press (Enter / Escape / Tab / Space / ArrowDown...): ",
                  "Element to send it to (optional): "],
    "wait":      ["Seconds to wait: "],
    "wait_for":  ["Element to wait for (selector or text): ",
                  "Timeout in seconds (optional, default 15): "],
    "read":      ["Element to read ('page' = whole page text): "],
    "check":     ["Text that should be on the page: "],
    "scroll":    ["Where? ('top' / 'bottom' / element CSS or text / e.g. '500' for px): "],
    "hover":     ["Element to hover (selector or text): "],
    "shot":      ["File name (optional, auto-generated if empty): "],
    "html":      ["File name (optional, auto-generated if empty): "],
}

META_COMMANDS = {
    "?": "show this help",
    "menu": "show the activity menu",
    "show": "show the current plan",
    "remove": "remove a step (e.g. 'remove 3')",
    "clear": "clear all steps",
    "save": "save the plan to a file",
    "load": "load a plan from a file",
    "run": "execute the series now",
    "exit": "quit without running",
}

KEY_ALIASES = {
    "enter": "RETURN", "return": "RETURN",
    "escape": "ESCAPE", "esc": "ESCAPE",
    "tab": "TAB",
    "space": "SPACE", "spacebar": "SPACE",
    "backspace": "BACKSPACE",
    "delete": "DELETE", "del": "DELETE",
    "arrowdown": "ARROW_DOWN", "down": "ARROW_DOWN",
    "arrowup": "ARROW_UP", "up": "ARROW_UP",
    "arrowleft": "ARROW_LEFT", "left": "ARROW_LEFT",
    "arrowright": "ARROW_RIGHT", "right": "ARROW_RIGHT",
    "home": "HOME", "end": "END",
    "pageup": "PAGE_UP", "pagedown": "PAGE_DOWN",
}


# --------------------------------------------------------------------------
# Selector helpers
# --------------------------------------------------------------------------
_KNOWN_TAGS = set("""
    a abbr address area article aside audio b base bdi bdo blockquote body br
    button canvas caption cite code col colgroup data datalist dd del details
    dfn dialog div dl dt em embed fieldset figcaption figure footer form h1 h2
    h3 h4 h5 h6 head header hgroup hr html i iframe img input ins kbd label
    legend li link main map mark menu meta meter nav noscript object ol optgroup
    option output p picture pre progress q rp rt ruby s samp script search
    section select slot small source span strong style sub summary sup table
    tbody td template textarea tfoot th thead time title tr track u ul var video
    wbr
""".split())


def looks_like_css(ident):
    """Return True when the identifier is clearly a CSS selector."""
    ident = ident.strip()
    if ident.lower().startswith(("css=",)):
        return True
    if ident.startswith(("#", ".", "[", "*")):
        return True
    if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_\-]*", ident):
        # A single bare word is only a CSS selector when it is a known tag.
        # (Otherwise words like "Username" mean placeholder/label text.)
        return ident.lower() in _KNOWN_TAGS
    if re.search(r"[.#\[\]:>]", ident):
        return True
    return False


def looks_like_xpath(ident):
    ident = ident.strip()
    return ident.lower().startswith("xpath=") or ident.startswith(("/", "("))


def resolve_selector(ident, kind=None):
    """
    Convert a user identifier into a (selector, kind) pair where kind is one
    of: css | xpath | id | name.
    kind hint: 'click' | 'type' | 'generic' (used only when the identifier is
    free text rather than a selector).
    """
    ident = ident.strip()
    if not ident:
        raise ValueError("empty element identifier")

    low = ident.lower()
    if low.startswith("css="):
        return ident[4:].strip(), "css"
    if low.startswith("xpath="):
        return ident[6:].strip(), "xpath"
    if low.startswith("id="):
        return ident[3:].strip(), "id"
    if low.startswith("name="):
        return ident[5:].strip(), "name"
    if low.startswith(("link=", "text=")):
        esc = ident.split("=", 1)[1].strip().replace("'", "\\'")
        return ("//*[(self::a or self::button or self::span or self::input)"
                "[contains(normalize-space(), '%s')]][1]" % esc), "xpath"
    if looks_like_xpath(ident):
        return ident, "xpath"
    if looks_like_css(ident):
        return ident, "css"

    # Plain free text -> guess where it belongs on the page
    esc = ident.replace("'", "\\'")
    if kind == "type":
        xp = (
            "//*[(self::input or self::textarea or self::select)"
            "[contains(@placeholder, '%s') or contains(@aria-label, '%s')"
            " or contains(@name, '%s') or contains(@id, '%s')]]"
            " | //label[contains(normalize-space(), '%s')]"
            "/following::*[1][self::input or self::textarea or self::select]"
            % (esc, esc, esc, esc, esc)
        )
        return xp, "xpath"
    if kind == "click":
        xp = ("//*[(self::button or self::a or self::span or self::div or"
              " self::input[contains(@type,'submit') or contains(@type,'button')])"
              "[contains(normalize-space(), '%s')]][1]" % esc)
        return xp, "xpath"
    # generic (read/hover/wait): any element containing that text
    xp = ("//*[not(self::script) and not(self::style)]"
          "[contains(normalize-space(), '%s')][1]" % esc)
    return xp, "xpath"


def sel_for(ident, kind="generic"):
    """Return (selector, selenium By) ready for sb calls that accept by=."""
    from selenium.webdriver.common.by import By
    sel, k = resolve_selector(ident, kind)
    return sel, {"css": By.CSS_SELECTOR, "id": By.ID,
                 "name": By.NAME}.get(k, By.XPATH)


# --------------------------------------------------------------------------
# Plan helpers
# --------------------------------------------------------------------------
def show_plan(plan):
    print("\n" + "=" * 62)
    print("PLAN  |  site: %s  |  steps: %d" % (plan.get("site", "-"), len(plan["steps"])))
    print("-" * 62)
    if not plan["steps"]:
        print("(no steps yet - add activities below)")
    for i, step in enumerate(plan["steps"], 1):
        parts = ["%s" % step["action"]]
        for key in step:
            if key == "action":
                continue
            parts.append("%s=%s" % (key, step[key]))
        print("  %2d. %s" % (i, "  ".join(parts)))
    print("=" * 62 + "\n")


def save_plan(plan, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    print("[saved plan -> %s]" % path)


def load_plan(path):
    with open(path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    assert "site" in plan and "steps" in plan
    return plan


def normalize_site(url):
    url = url.strip()
    if not url:
        return url
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", url):
        url = "https://" + url
    return url


# --------------------------------------------------------------------------
# Execution engine
# --------------------------------------------------------------------------
def key_for(name):
    from selenium.webdriver.common.keys import Keys
    alias = KEY_ALIASES.get(name.strip().lower())
    if alias is None:
        alias = name.strip().upper()
    key = getattr(Keys, alias, None)
    if key is None:
        # allow raw values like "\ue007"
        return name
    return key


def run_plan(plan, headless=False):
    from seleniumbase import SB

    steps = plan["steps"]
    site = plan.get("site", "")
    print("\nLaunching LibreWolf (%s mode) ..." % ("headless" if headless else "GUI"))
    print("Site: %s" % site)
    print("Steps: %d" % len(steps))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(OUT_DIR, "run_" + ts)
    os.makedirs(run_dir, exist_ok=True)
    results_log = os.path.join(run_dir, "results.txt")

    ok_count, fail_count = 0, 0
    failures = []

    with SB(browser=BROWSER, headless=headless, locale_code="en") as sb:
        try:
            if site:
                print("\n[1/??] Opening %s" % site)
                sb.open(site)
                sb.wait_for_ready_state_complete()
        except Exception as e:
            print("    !! Could not open %s: %s" % (site, e))
            return False, [], "Failed to open the site: %s" % e

        for idx, step in enumerate(steps, 1):
            action = step["action"]
            label = "  [%d/%d] %s" % (idx, len(steps), action)
            try:
                t0 = time.time()
                if action == "open":
                    url = normalize_site(step.get("url", step.get("site", "")))
                    sb.open(url)
                    sb.wait_for_ready_state_complete()
                    print("%s -> %s  OK" % (label, url))

                elif action == "type":
                    sel, by = sel_for(step["field"], "type")
                    sb.type(sel, step["value"], by=by)
                    print("%s -> '%s' OK" % (label, step["value"]))

                elif action == "click":
                    sel, by = sel_for(step["element"], "click")
                    sb.click(sel, by=by)
                    print("%s -> %s  OK" % (label, step["element"]))

                elif action == "select":
                    sel, by = sel_for(step["dropdown"], "generic")
                    sb.select_option_by_text(sel, step["option"], by=by)
                    print("%s -> '%s' OK" % (label, step["option"]))

                elif action == "press":
                    k = key_for(step["key"])
                    if step.get("element", "").strip():
                        sel, by = sel_for(step["element"], "generic")
                        sb.press_keys(sel, k, by=by)
                    else:
                        sb.driver.switch_to.active_element.send_keys(k)
                    print("%s -> %s OK" % (label, step["key"]))

                elif action == "wait":
                    secs = float(step.get("seconds", 1))
                    time.sleep(secs)
                    print("%s -> %.1fs OK" % (label, secs))

                elif action == "wait_for":
                    timeout = int(step.get("timeout", 15))
                    sel, by = sel_for(step["element"], "generic")
                    sb.wait_for_element_visible(sel, by=by, timeout=timeout)
                    print("%s -> visible OK" % label)

                elif action == "read":
                    target = step.get("element", "page").strip() or "page"
                    if target.lower() in ("page", "body", "whole", "all"):
                        text = sb.get_text("body")
                    else:
                        sel, by = sel_for(target, "generic")
                        text = sb.get_text(sel, by=by)
                    line = "STEP %d (%s): %s" % (idx, target, text.strip()[:2000])
                    with open(results_log, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                    print("%s -> %d chars (also saved to %s)" % (label, len(text.strip()),
                                                                 os.path.basename(results_log)))
                    print("    content: %s" % text.strip()[:200])

                elif action == "check":
                    text = step["text"]
                    try:
                        sb.assert_text_visible(text)
                        print("%s -> '%s' FOUND  OK" % (label, text))
                    except Exception:
                        raise AssertionError("text not found on page: %s" % text)

                elif action == "scroll":
                    target = step.get("where", "bottom").strip().lower()
                    if target in ("top", "up", "0"):
                        sb.execute_script("window.scrollTo(0, 0);")
                    elif target in ("bottom", "down"):
                        sb.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    elif re.fullmatch(r"-?\d+", target):
                        sb.execute_script("window.scrollBy(0, %s);" % target)
                    else:
                        sel, by = sel_for(step["where"], "generic")
                        sb.scroll_to(sel, by=by)
                    print("%s -> %s OK" % (label, step.get("where", "bottom")))

                elif action == "hover":
                    sel, by = sel_for(step["element"], "generic")
                    sb.hover(sel, by=by)
                    print("%s -> %s OK" % (label, step["element"]))

                elif action == "shot":
                    fname = step.get("filename", "").strip()
                    if not fname:
                        fname = "shot_%02d.png" % idx
                    if not fname.lower().endswith(".png"):
                        fname += ".png"
                    path = os.path.join(run_dir, fname)
                    sb.save_screenshot(path)
                    print("%s -> saved %s" % (label, path))

                elif action == "html":
                    fname = step.get("filename", "").strip()
                    if not fname:
                        fname = "page_%02d.html" % idx
                    if not fname.lower().endswith(".html"):
                        fname += ".html"
                    path = os.path.join(run_dir, fname)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(sb.driver.page_source)
                    print("%s -> saved %s" % (label, path))

                else:
                    raise ValueError("unknown action: %s" % action)

                ok_count += 1
                dt = time.time() - t0
                print("        (%.1fs)" % dt)
            except Exception as e:
                fail_count += 1
                failures.append((idx, action, str(e)))
                print("    !! STEP FAILED: %s" % e)

    print("\n" + "=" * 62)
    print("RUN COMPLETE  |  ok: %d   failed: %d" % (ok_count, fail_count))
    print("Output folder: %s" % run_dir)
    if failures:
        print("-" * 62)
        for idx, action, err in failures:
            print("  step %d [%s] -> %s" % (idx, action, err))
    print("=" * 62)
    return fail_count == 0, run_dir, failures


# --------------------------------------------------------------------------
# Interactive builder
# --------------------------------------------------------------------------
def ask(prompt, default=None, required=True):
    while True:
        raw = input(prompt).strip()
        if raw == "" and default is not None:
            return default
        if raw == "" and required:
            print("(empty input - try again, or type 'exit')")
            continue
        return raw


def pick_activity():
    print("\nACTIVITIES (choose one):")
    for num, (name, desc) in enumerate(ACTIVITY_MENU, 1):
        print("  %2d. %-10s %s" % (num, name, desc))
    print("      command -> '?' help | 'show' | 'remove N' | 'clear' |"
          " 'save' | 'load' | 'run' | 'exit'")
    choice = input("\nWhat would you like to do next? (number or command) ").strip().lower()
    if choice == "":
        return None
    if choice in ("?", "help"):
        return "?"
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(ACTIVITY_MENU):
            return ACTIVITY_MENU[idx][0]
        print("!! invalid number")
        return pick_activity()
    if choice in ("menu",):
        return pick_activity()
    if choice in ("show", "remove", "clear", "save", "load", "run", "exit"):
        return choice
    # maybe "remove 3"
    if choice.startswith("remove "):
        return choice
    # allow typing the activity name directly
    names = [n for n, _ in ACTIVITY_MENU]
    if choice in names:
        return choice
    print("!! unknown choice: %s" % choice)
    return pick_activity()


def collect_step(action, plan):
    prompts = ACTIVITY_PARAMS[action]
    answers = []
    for p in prompts:
        optional = "(optional" in p or p.endswith("optional): ")
        if optional:
            raw = input(p + "\n  (press Enter to skip) > ").strip()
        else:
            raw = ask(p)
        answers.append(raw)
    step = {"action": action}
    if action == "open":
        step["url"] = normalize_site(answers[0])
    elif action == "type":
        step["field"], step["value"] = answers[0], answers[1]
    elif action == "click":
        step["element"] = answers[0]
    elif action == "select":
        step["dropdown"], step["option"] = answers[0], answers[1]
    elif action == "press":
        step["key"] = answers[0] or "Enter"
        step["element"] = answers[1]
    elif action == "wait":
        step["seconds"] = answers[0] or "1"
    elif action == "wait_for":
        step["element"] = answers[0]
        step["timeout"] = answers[1] or "15"
    elif action == "read":
        step["element"] = answers[0] or "page"
    elif action == "check":
        step["text"] = answers[0]
    elif action == "scroll":
        step["where"] = answers[0] or "bottom"
    elif action == "hover":
        step["element"] = answers[0]
    elif action in ("shot", "html"):
        step["filename"] = answers[0]
    return step


def interactive():
    print("=" * 62)
    print("  WEB AUTOMATION CONSOLE  (SeleniumBase + LibreWolf)")
    print("=" * 62)
    os.makedirs(PLAN_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    plan = None
    if os.path.exists(LAST_PLAN):
        ans = input("Load your last plan? (y/N): ").strip().lower()
        if ans in ("y", "yes"):
            try:
                plan = load_plan(LAST_PLAN)
                print("[loaded last plan]")
            except Exception:
                plan = None

    if plan is None:
        site = input("1) Site to automate (e.g. https://example.com): ").strip()
        site = normalize_site(site)
        if not site:
            print("No site given - using about:blank (you can add 'open' steps).")
            site = ""
        plan = {"site": site, "steps": []}
        print("\nGreat. Now add ACTIVITIES in the order you want them done.")
        print("Type the activity number (or its name), then answer the prompts.")
        print("Type 'run' when the series is ready and automation will start.\n")

    while True:
        choice = pick_activity()
        if choice is None or choice == "?":
            print("\nCommands: menu | show | remove N | clear | save | load | run | exit")
            print("Activities: " + ", ".join(n for n, _ in ACTIVITY_MENU))
            continue
        if choice == "exit":
            print("Bye.")
            return
        if choice == "show":
            show_plan(plan)
            continue
        if choice == "clear":
            plan["steps"] = []
            print("[plan cleared]")
            continue
        if choice == "run":
            break
        if choice.startswith("remove "):
            try:
                n = int(choice.split()[1])
                if 1 <= n <= len(plan["steps"]):
                    removed = plan["steps"].pop(n - 1)
                    print("[removed step %d: %s]" % (n, removed["action"]))
                else:
                    print("!! no step %d" % n)
            except (IndexError, ValueError):
                print("usage: remove N   (e.g. 'remove 3')")
            continue
        if choice == "save":
            default_name = "plan_%s.json" % datetime.now().strftime("%Y%m%d_%H%M")
            name = input("Plan file name [%s]: " % default_name).strip() or default_name
            if not name.endswith(".json"):
                name += ".json"
            path = os.path.join(PLAN_DIR, name)
            save_plan(plan, path)
            continue
        if choice == "load":
            files = sorted(f for f in os.listdir(PLAN_DIR) if f.endswith(".json")) \
                if os.path.isdir(PLAN_DIR) else []
            if not files:
                print("(no saved plans yet - use 'save' first)")
                continue
            print("Saved plans:")
            for i, f in enumerate(files, 1):
                print("  %d. %s" % (i, f))
            ans = input("Load which number? ").strip()
            try:
                fname = files[int(ans) - 1]
                plan = load_plan(os.path.join(PLAN_DIR, fname))
                print("[loaded %s]" % fname)
            except Exception:
                print("!! could not load that plan")
            continue

        # regular activity
        step = collect_step(choice, plan)
        plan["steps"].append(step)
        print("[added step %d: %s]" % (len(plan["steps"]), choice))

    # Run
    show_plan(plan)
    if not plan["steps"] and not plan.get("site"):
        print("Nothing to do - the plan is empty.")
        return
    mode = input("Run mode?  (g)ui / (h)eadless  [g]: ").strip().lower()
    headless = mode.startswith("h")
    print("\nStarting automation in 3 seconds - switch to the browser window...")
    time.sleep(3)
    ok, run_dir, failures = run_plan(plan, headless=headless)
    try:
        save_plan(plan, LAST_PLAN)
    except Exception:
        pass
    again = input("\nRun again / edit more / exit? (r/e/x) [x]: ").strip().lower()
    if again == "r":
        run_plan(plan, headless=headless)
    elif again == "e":
        interactive()
    else:
        print("Done. Output saved under: %s" % OUT_DIR)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Web automation console (SeleniumBase + LibreWolf)")
    ap.add_argument("--file", help="run a saved plan JSON directly (non-interactive)")
    ap.add_argument("--headless", action="store_true", help="run without a visible browser window")
    args = ap.parse_args()

    if args.file:
        path = args.file
        if not os.path.isabs(path):
            path = os.path.join(SCRIPT_DIR, path)
        try:
            plan = load_plan(path)
        except Exception as e:
            print("!! Could not load plan '%s': %s" % (args.file, e))
            sys.exit(1)
        show_plan(plan)
        ok, run_dir, failures = run_plan(plan, headless=args.headless)
        sys.exit(0 if ok else 1)
    interactive()


if __name__ == "__main__":
    main()
