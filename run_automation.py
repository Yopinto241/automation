#!/usr/bin/env python3
"""
Interactive Web Automation Tool
-------------------------------
Built on SeleniumBase (Firefox/LibreWolf, Chrome, or Edge).

Start menu:
  1. Track my actions  -> you browse, the tool records every action and
     turns it into a replayable plan, then offers to run it automatically.
  2. Build a plan step-by-step (type, click, copy, paste, generate, ...).
  3. Run a saved plan.
  4. Exit.

Special step activities:
  open_tab   - open another site in a NEW tab (keeps the current page).
  copy       - highlight + copy text from an element (or the whole page).
  copy_link  - copy a link's URL (or the current page URL).
  generate   - create a random number with a chosen digit count (no
               leading zero) e.g. a 9-digit ID number.
  clip       - keep only the last N characters of the copied value
               (e.g. the last 4 digits of a generated ID).
  paste      - paste the copied value into an input box.

The copied value acts like a clipboard: copy -> paste, or
copy -> clip -> paste, or generate -> paste -> clip -> paste.

Non-interactive usage:
  python run_automation.py --file plans/my_plan.json [--headless]
  python run_automation.py --record --site https://example.com [--browser chrome]
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PLAN_DIR = os.path.join(SCRIPT_DIR, "plans")
OUT_DIR = os.path.join(SCRIPT_DIR, "output")
LAST_PLAN = os.path.join(SCRIPT_DIR, "last_plan.json")
RESULTS_FILE = os.path.join(OUT_DIR, "results.txt")
PREFS_FILE = os.path.join(SCRIPT_DIR, "prefs.json")

DEFAULT_BROWSER = "firefox"
SUPPORTED_BROWSERS = ("chrome", "firefox", "edge", "librewolf")
MAX_RETRIES = 2


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
    ("open_tab", "Open a NEW TAB with another site (keeps this page open)"),
    ("copy", "Copy text from an element (highlight + copy)"),
    ("copy_link", "Copy a link URL (Enter = current page URL)"),
    ("generate", "Generate a random number (choose digit count, no leading zero)"),
    ("clip", "Keep only the last N characters of the copied value"),
    ("paste", "Paste the copied value into an input box"),
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
    "open_tab":  ["URL to open in the new tab: "],
    "copy":      ["Element to copy ('page' = whole page): "],
    "copy_link": ["Element with the link (Enter = current page URL): "],
    "generate":  ["How many digits? (e.g. 9): "],
    "clip":      ["How many characters to keep from the END? (default 4): "],
    "paste":     ["Box to paste into (selector or label text): "],
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


def xpath_literal(value):
    """Return an XPath string literal that supports both quote characters."""
    if "'" not in value:
        return "'%s'" % value
    if '"' not in value:
        return '"%s"' % value
    parts = value.split("'")
    arguments = []
    for index, part in enumerate(parts):
        if part:
            arguments.append('"%s"' % part)
        if index < len(parts) - 1:
            arguments.append('"\'"')
    return "concat(%s)" % ", ".join(arguments)


def safe_output_name(filename, default_name, extension):
    """Keep generated artifact names inside the current run directory."""
    name = (filename or "").strip() or default_name
    name = os.path.basename(name.replace("\\", "/"))
    if not name.lower().endswith(extension):
        name += extension
    if name in (".", "..") or name.startswith("."):
        raise ValueError("artifact filename must be a normal file name")
    return name


BROWSER_CANDIDATES = {
    "chrome": [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ],
    "firefox": [
        os.path.expandvars(r"%ProgramFiles%\Mozilla Firefox\firefox.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Mozilla Firefox\firefox.exe"),
    ],
    "edge": [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ],
    "librewolf": [
        os.path.expandvars(r"%ProgramFiles%\LibreWolf\librewolf.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\LibreWolf\librewolf.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\LibreWolf\librewolf.exe"),
    ],
}


# --------------------------------------------------------------------------
# LibreWolf support (seleniumbase has no native "librewolf" engine, so
# LibreWolf is launched through the Firefox engine pointed at its binary)
# --------------------------------------------------------------------------
def librewolf_executable():
    """Return the path to an installed LibreWolf binary, or None."""
    return next((candidate for candidate in BROWSER_CANDIDATES["librewolf"]
                 if os.path.isfile(candidate)), None)


def prepare_browser_launch(browser):
    """Make sure seleniumbase launches the right binary.

    seleniumbase 4.52.4 ignores ``binary_location`` for Firefox-family
    browsers (it only honours it for Chromium). LibreWolf shares the
    Firefox engine, so we patch the internal Firefox-options builder at
    runtime to point it at the LibreWolf executable.
    """
    if browser != "librewolf":
        return
    exe = librewolf_executable()
    if not exe:
        raise RuntimeError(
            "LibreWolf was selected but no librewolf.exe was found.\n"
            "Install LibreWolf or choose another browser.")
    import seleniumbase.core.browser_launcher as browser_launcher

    original = browser_launcher._set_firefox_options

    def patched(*args, **kwargs):
        options = original(*args, **kwargs)
        try:
            options.binary_location = exe
        except Exception:
            from selenium.webdriver.firefox.firefox_binary import FirefoxBinary
            options.binary = FirefoxBinary(exe)
        return options

    browser_launcher._set_firefox_options = patched


def sb_browser_name(browser):
    """Map our browser names to SeleniumBase's internal browser names.

    LibreWolf shares the Firefox engine: SeleniumBase has no "librewolf"
    option, so we ask for "firefox" and let prepare_browser_launch() point
    the Firefox options at the LibreWolf executable.
    """
    return "firefox" if browser == "librewolf" else browser


def discover_browsers():
    """Return installed supported browsers and their executable paths."""
    found = []
    for browser in SUPPORTED_BROWSERS:
        path = next((candidate for candidate in BROWSER_CANDIDATES[browser]
                     if os.path.isfile(candidate)), None)
        if path:
            found.append({"name": browser, "path": path})
    return found


def choose_browser(default_name=None):
    """Show installed browsers and return the user's selection."""
    browsers = discover_browsers()
    if not browsers:
        print("No supported browser executable was found.")
        print("Install Firefox, LibreWolf, Chrome, or Edge and try again.")
        return DEFAULT_BROWSER
    default_index = 1
    for index, browser in enumerate(browsers, 1):
        if browser["name"] == default_name:
            default_index = index
            break
    print("\nAVAILABLE BROWSERS:")
    for index, browser in enumerate(browsers, 1):
        print("  %d. %-7s %s" % (index, browser["name"], browser["path"]))
    while True:
        choice = input("Choose a browser [%d]: " % default_index).strip() or str(default_index)
        if choice.isdigit() and 1 <= int(choice) <= len(browsers):
            selected = browsers[int(choice) - 1]
            print("[selected %s]" % selected["name"])
            return selected["name"]
        print("!! choose one of the listed numbers")


def load_prefs():
    """Return saved browser/mode preferences (safe defaults when missing)."""
    try:
        with open(PREFS_FILE, "r", encoding="utf-8") as f:
            prefs = json.load(f)
        return {
            "browser": prefs.get("browser")
                       if prefs.get("browser") in SUPPORTED_BROWSERS else None,
            "mode": "headless" if prefs.get("mode") == "headless" else "gui",
        }
    except (OSError, ValueError):
        return {"browser": None, "mode": "gui"}


def save_prefs(browser, mode):
    """Remember the last browser and run mode for the next automation run."""
    prefs = {"browser": browser, "mode": "headless" if mode == "headless" else "gui"}
    try:
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except OSError:
        pass


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
        esc = xpath_literal(ident.split("=", 1)[1].strip())
        return ("//*[(self::a or self::button or self::span or self::input)"
                "[contains(normalize-space(), %s)]][1]" % esc), "xpath"
    if looks_like_xpath(ident):
        return ident, "xpath"
    if looks_like_css(ident):
        return ident, "css"

    # Plain free text -> guess where it belongs on the page
    esc = xpath_literal(ident)
    if kind == "type":
        xp = (
            "//*[(self::input or self::textarea or self::select)"
            "[contains(@placeholder, %s) or contains(@aria-label, %s)"
            " or contains(@name, %s) or contains(@id, %s)]]"
            " | //label[contains(normalize-space(), %s)]"
            "/following::*[1][self::input or self::textarea or self::select]"
            % (esc, esc, esc, esc, esc)
        )
        return xp, "xpath"
    if kind == "click":
        xp = ("//*[(self::button or self::a or self::span or self::div or"
              " self::input[contains(@type,'submit') or contains(@type,'button')])"
              "[contains(normalize-space(), %s)]][1]" % esc)
        return xp, "xpath"
    # generic (read/hover/wait): any element containing that text
    xp = ("//*[not(self::script) and not(self::style)]"
          "[contains(normalize-space(), %s)][1]" % esc)
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
    validate_plan(plan)
    return plan


def validate_plan(plan):
    """Validate plan structure before opening a browser."""
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        raise ValueError("plan must be an object with a steps list")
    if "site" not in plan or not isinstance(plan["site"], str):
        raise ValueError("plan site must be a string")
    for index, step in enumerate(plan["steps"], 1):
        if not isinstance(step, dict) or not isinstance(step.get("action"), str):
            raise ValueError("step %d must have an action" % index)
        action = step["action"]
        if action not in {name for name, _ in ACTIVITY_MENU}:
            raise ValueError("step %d has unknown action: %s" % (index, action))
        for field in {
            "open": ("url",), "type": ("field", "value"),
            "click": ("element",), "select": ("dropdown", "option"),
            "press": ("key",), "wait_for": ("element",),
            "check": ("text",), "hover": ("element",),
            "open_tab": ("url",), "copy": ("element",),
            "generate": ("digits",), "paste": ("element",),
        }.get(action, ()):
            if not isinstance(step.get(field), str) or not step[field].strip():
                raise ValueError("step %d requires non-empty %s" % (index, field))
        if action == "generate":
            try:
                digits = int(step["digits"])
                if not 1 <= digits <= 30:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("step %d has invalid generate digits" % index)
        if action == "clip":
            try:
                chars = int(step.get("chars", 4))
                if not 1 <= chars <= 30:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("step %d has invalid clip chars" % index)
        if action == "wait":
            try:
                if float(step.get("seconds", 1)) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("step %d has invalid wait seconds" % index)
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


def generate_number(digits):
    """Return a random number with exactly `digits` digits (no leading zero)."""
    digits = int(digits)
    low = 10 ** (digits - 1) if digits > 1 else 0
    return str(random.randint(low, (10 ** digits) - 1))


def clip_value(value, chars):
    """Return only the last `chars` characters of `value`."""
    return str(value)[-int(chars):]


# Highlight + copy a page element's value/text (returns the copied string).
COPY_TEXT_JS = r"""
var el = arguments[0];
if (!el) return '';
var value = '';
if (typeof el.value === 'string' &&
    (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) {
    value = el.value;
    try { el.focus(); el.select(); } catch (e) {}
} else {
    value = el.innerText || el.textContent || '';
    try {
        var range = document.createRange();
        range.selectNodeContents(el);
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
    } catch (e) {}
}
try { document.execCommand('copy'); } catch (e) {}
return value;
"""

# Copy a link's URL (href) from an element.
COPY_LINK_JS = r"""
var el = arguments[0];
if (!el) return '';
return el.href || el.getAttribute('href') || el.innerText || el.textContent || '';
"""


def run_plan(plan, headless=False, browser=DEFAULT_BROWSER):
    from seleniumbase import SB

    validate_plan(plan)
    steps = plan["steps"]
    site = plan.get("site", "")
    print("\nLaunching %s (%s mode) ..." % (browser.capitalize(), "headless" if headless else "GUI"))
    print("Site: %s" % site)
    print("Steps: %d" % len(steps))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(OUT_DIR, "run_" + ts)
    os.makedirs(run_dir, exist_ok=True)
    results_log = os.path.join(run_dir, "results.txt")
    results_json = os.path.join(run_dir, "results.json")

    ok_count, fail_count = 0, 0
    failures = []

    prepare_browser_launch(browser)
    with SB(browser=sb_browser_name(browser), headless=headless,
             locale_code="en") as sb:
        try:
            if site:
                print("\n[1/??] Opening %s" % site)
                sb.open(site)
                sb.wait_for_ready_state_complete()
        except Exception as e:
            print("    !! Could not open %s: %s" % (site, e))
            return False, [], "Failed to open the site: %s" % e

        ctx = {"clipboard": None}
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
                    sel, by = sel_for(step["dropdown"], "type")
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
                    fname = safe_output_name(step.get("filename"),
                                             "shot_%02d" % idx, ".png")
                    path = os.path.join(run_dir, fname)
                    sb.save_screenshot(path)
                    print("%s -> saved %s" % (label, path))

                elif action == "html":
                    fname = safe_output_name(step.get("filename"),
                                             "page_%02d" % idx, ".html")
                    path = os.path.join(run_dir, fname)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(sb.driver.page_source)
                    print("%s -> saved %s" % (label, path))

                elif action == "open_tab":
                    url = normalize_site(step.get("url", ""))
                    sb.open_new_tab()
                    sb.open(url)
                    sb.wait_for_ready_state_complete()
                    print("%s -> %s  OK" % (label, url))

                elif action == "copy":
                    target = (step.get("element") or "page").strip()
                    if target.lower() in ("page", "body", "whole", "all"):
                        ctx["clipboard"] = sb.get_text("body").strip()
                    else:
                        sel, by = sel_for(target, "generic")
                        el = sb.find_element(sel, by=by)
                        ctx["clipboard"] = sb.execute_script(COPY_TEXT_JS, el) or ""
                    print("%s -> %d chars copied  OK" % (label, len(ctx["clipboard"])))

                elif action == "copy_link":
                    target = (step.get("element") or "").strip()
                    if not target:
                        ctx["clipboard"] = sb.get_current_url()
                    else:
                        sel, by = sel_for(target, "click")
                        el = sb.find_element(sel, by=by)
                        ctx["clipboard"] = sb.execute_script(COPY_LINK_JS, el) or ""
                    print("%s -> '%s'  OK" % (label, ctx["clipboard"]))

                elif action == "generate":
                    ctx["clipboard"] = generate_number(step.get("digits", 9))
                    print("%s -> %s  OK" % (label, ctx["clipboard"]))

                elif action == "clip":
                    if ctx.get("clipboard") is None:
                        raise RuntimeError("nothing copied yet - "
                                           "add copy / copy_link / generate first")
                    ctx["clipboard"] = clip_value(ctx["clipboard"], step.get("chars", 4))
                    print("%s -> '%s'  OK" % (label, ctx["clipboard"]))

                elif action == "paste":
                    if ctx.get("clipboard") is None:
                        raise RuntimeError("nothing to paste - "
                                           "add copy / copy_link / generate first")
                    sel, by = sel_for(step["element"], "type")
                    sb.type(sel, ctx["clipboard"], by=by)
                    print("%s -> pasted %d chars  OK" % (label, len(ctx["clipboard"])))

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
    with open(results_json, "w", encoding="utf-8") as f:
        json.dump({"site": site, "browser": browser, "ok": ok_count,
                   "failed": fail_count,
                   "failures": [{"step": i, "action": a, "error": e}
                                for i, a, e in failures]}, f, indent=2)
    print("Output folder: %s" % run_dir)
    if failures:
        print("-" * 62)
        for idx, action, err in failures:
            print("  step %d [%s] -> %s" % (idx, action, err))
    print("=" * 62)
    return fail_count == 0, run_dir, failures


# --------------------------------------------------------------------------
# Recording engine (learn-by-watching)
# --------------------------------------------------------------------------
RECORDER_JS = r"""
(function () {
  if (window.__haRecOn) { return; }
  window.__haRecOn = true;
  var ENDPOINT = "__ENDPOINT__";
  var Q = (window.__haEvents = []);
  function nextSeq() { window.__haSeq = (window.__haSeq || 0) + 1; return window.__haSeq; }
  function push(evt) {
    evt.seq = nextSeq();
    evt.ts = Date.now();
    evt.url = window.location.href;
    Q.push(evt);
    try { window.localStorage.setItem('__ha_events', JSON.stringify(Q)); } catch (e) {}
    try {
      fetch(ENDPOINT, { method: 'POST', mode: 'no-cors',
                        headers: { 'Content-Type': 'text/plain' },
                        body: JSON.stringify(evt) });
    } catch (e) {}
  }
  function clean(s) {
    if (!s) { return ''; }
    return String(s).replace(/\s+/g, ' ').trim();
  }
  function describe(el) {
    if (!el || !el.tagName) { return null; }
    var info = { tag: String(el.tagName).toLowerCase() };
    ['id', 'name', 'type', 'placeholder', 'aria-label', 'title', 'href'].forEach(function (a) {
      var v = el.getAttribute ? el.getAttribute(a) : null;
      if (v !== null && v !== '') { info[a] = v; }
    });
    var cn = el.className;
    if (cn && typeof cn === 'string' && clean(cn)) { info['class'] = clean(cn); }
    var txt = clean(el.innerText || el.textContent || '');
    if (txt && txt.length <= 150) { info.text = txt; }
    if (el.closest) {
      var lab = el.closest('label');
      if (lab) {
        var lt = clean(lab.innerText || lab.textContent || '');
        if (lt) { info.label = lt; }
      }
    }
    if (el.tagName === 'SELECT' && el.selectedIndex >= 0 && el.options[el.selectedIndex]) {
      info.selected = clean(el.options[el.selectedIndex].text);
    }
    return info;
  }
  document.addEventListener('click', function (e) {
    var el = e.target;
    if (el && el.closest && el.closest('[data-ha-ignore]')) { return; }
    var info = describe(el);
    if (info) { push({ t: 'click', info: info }); }
  }, true);
  document.addEventListener('change', function (e) {
    var el = e.target;
    if (!el || !/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) { return; }
    var val = el.value || '';
    if (el.tagName === 'SELECT' && el.selectedIndex >= 0 && el.options[el.selectedIndex]) {
      val = clean(el.options[el.selectedIndex].text);
    }
    push({ t: 'change', info: describe(el), value: val });
  }, true);
  document.addEventListener('submit', function (e) {
    var form = e.target;
    var btn = null;
    if (form && form.querySelector) {
      btn = form.querySelector('button[type="submit"], input[type="submit"]');
    }
    if (btn) { push({ t: 'click', info: describe(btn), via: 'submit' }); }
    else { push({ t: 'press', key: 'Enter', via: 'submit' }); }
  }, true);
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') { return; }
    var el = e.target;
    if (!el || !/^(INPUT|TEXTAREA)$/.test(el.tagName)) { return; }
    if (el.closest) {
      var f = el.closest('form');
      if (f && f.querySelector('button[type="submit"], input[type="submit"]')) { return; }
    }
    push({ t: 'press', key: 'Enter', info: describe(el) });
  }, true);
  document.addEventListener('pagehide', function () {
    try { window.localStorage.setItem('__ha_events', JSON.stringify(Q)); } catch (e) {}
  });
  push({ t: 'load', info: {} });
})();
"""


def ident_for(info, kind):
    """Best plan identifier for a recorded element."""
    if not info:
        return None
    tag = info.get("tag", "")
    text = (info.get("text") or "").strip()
    if kind in ("type", "select"):
        for key in ("label", "placeholder", "aria-label", "name", "id"):
            if info.get(key):
                return info[key]
        return None
    if text:
        return text
    for key in ("aria-label", "title", "id", "name"):
        if info.get(key):
            return info[key]
    cls = info.get("class")
    if cls:
        return "css=%s.%s" % (tag, ".".join(cls.split()))
    return None


def events_to_plan(events, start_site):
    """Convert recorded browser events into a replayable plan."""
    steps = []
    last_url = (start_site or "").strip()
    for evt in events:
        t = evt.get("t")
        url = evt.get("url") or ""
        if t == "load":
            if url and url != last_url:
                steps.append({"action": "open", "url": url})
                last_url = url
            elif url and not last_url:
                last_url = url
            continue
        if t == "change":
            info = evt.get("info") or {}
            tag = info.get("tag", "")
            if tag == "input" and info.get("type") in ("checkbox", "radio"):
                ident = ident_for(info, "click")
                if ident:
                    steps.append({"action": "click", "element": ident})
                continue
            value = (evt.get("value") or "").strip()
            ident = ident_for(info, "select" if tag == "select" else "type")
            if not ident or not value:
                continue
            if tag == "select":
                steps.append({"action": "select", "dropdown": ident, "option": value})
            else:
                steps.append({"action": "type", "field": ident, "value": value})
            continue
        if t == "click":
            info = evt.get("info") or {}
            href = info.get("href") or ""
            if info.get("tag") == "a" and href and not str(href).lower().startswith("javascript:"):
                continue  # navigation is handled by the open step
            ident = ident_for(info, "click")
            if not ident:
                continue
            steps.append({"action": "click", "element": ident})
            continue
        if t == "press":
            if evt.get("via") == "submit":
                continue
            info = evt.get("info") or {}
            ident = ident_for(info, "generic")
            step = {"action": "press", "key": "Enter"}
            if ident:
                step["element"] = ident
            steps.append(step)
            continue
    plan = {"site": last_url or (start_site or ""), "steps": steps}
    validate_plan(plan)
    return plan


def record_session(site, browser=DEFAULT_BROWSER):
    """Open a visible browser, capture the user's actions, save a replayable plan."""
    import http.server
    import socketserver
    import threading
    from seleniumbase import SB

    events = []
    seen = set()

    class RecorderHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8", "replace")
            try:
                evt = json.loads(body)
                if isinstance(evt, dict):
                    key = (evt.get("url"), evt.get("ts"), evt.get("seq"), evt.get("t"))
                    if key not in seen:
                        seen.add(key)
                        events.append(evt)
            except Exception:
                pass
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), RecorderHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = "http://127.0.0.1:%d/rec" % port

    print("=" * 62)
    print("  RECORD MODE - do your actions in the browser")
    print("=" * 62)
    print("Site: %s" % site)
    print("Clicks, typed text, dropdowns and submits are captured.")
    print("Close the browser window (or press Ctrl+C) to finish.")
    print("Note: typed values are stored in the plan file - don't")
    print("record passwords unless that is intended.\n")

    def inject(sb):
        try:
            sb.execute_script(RECORDER_JS.replace("__ENDPOINT__", endpoint))
        except Exception:
            pass

    def pull(sb):
        try:
            arr = sb.execute_script("return window.__haEvents || []") or []
        except Exception:
            arr = []
        try:
            raw = sb.execute_script(
                "try { return window.localStorage.getItem('__ha_events') || ''; }"
                " catch (e) { return ''; }") or ""
            stashed = json.loads(raw) if raw else []
        except Exception:
            stashed = []
        for evt in list(arr) + list(stashed):
            if not isinstance(evt, dict):
                continue
            key = (evt.get("url"), evt.get("ts"), evt.get("seq"), evt.get("t"))
            if key not in seen:
                seen.add(key)
                events.append(evt)

    try:
        prepare_browser_launch(browser)
        with SB(browser=sb_browser_name(browser), headless=False,
                 locale_code="en") as sb:
            sb.open(site)
            sb.wait_for_ready_state_complete()
            inject(sb)
            last_url = site
            while True:
                time.sleep(2)
                try:
                    pull(sb)
                    url = sb.get_current_url()
                except Exception:
                    break
                if url != last_url:
                    last_url = url
                    events.append({"t": "load", "url": url,
                                   "ts": int(time.time() * 1000), "seq": 0})
                    inject(sb)
    except KeyboardInterrupt:
        print("\n[recording stopped]")
    except Exception as e:
        print("\n[browser session ended: %s]" % e)
    finally:
        try:
            server.shutdown()
        except Exception:
            pass

    time.sleep(1)  # let any last events arrive
    events.sort(key=lambda e: (e.get("ts") or 0, e.get("seq") or 0))
    plan = events_to_plan(events, site)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(PLAN_DIR, "recorded_%s.json" % ts)
    save_plan(plan, path)
    show_plan(plan)
    print("[recorded plan -> %s]" % path)
    again = input("\nRun this recording now? (Y/n): ").strip().lower()
    if again in ("", "y", "yes"):
        prefs = load_prefs()
        mode_default = "h" if prefs.get("mode") == "headless" else "g"
        mode = input("Run mode?  (g)ui / (h)eadless  [%s]: " % mode_default).strip().lower()
        headless = prefs.get("mode") == "headless" if mode == "" else mode.startswith("h")
        save_prefs(browser, "headless" if headless else "gui")
        print("\nStarting automation in 3 seconds - switch to the browser window...")
        time.sleep(3)
        ok, run_dir, failures = run_plan(plan, headless=headless, browser=browser)
    return plan, path


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
    elif action == "open_tab":
        step["url"] = normalize_site(answers[0])
    elif action == "copy":
        step["element"] = answers[0] or "page"
    elif action == "copy_link":
        step["element"] = answers[0]
    elif action == "generate":
        step["digits"] = answers[0] or "9"
    elif action == "clip":
        step["chars"] = answers[0] or "4"
    elif action == "paste":
        step["element"] = answers[0]
    elif action in ("shot", "html"):
        step["filename"] = answers[0]
    return step


def interactive():
    print("=" * 62)
    print("  WEB AUTOMATION CONSOLE  (SeleniumBase)")
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
            print("Clipboard: copy / copy_link / generate -> (clip) -> paste")
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
    prefs = load_prefs()
    browser = choose_browser(default_name=prefs.get("browser"))
    mode_default = "h" if prefs.get("mode") == "headless" else "g"
    mode = input("Run mode?  (g)ui / (h)eadless  [%s]: " % mode_default).strip().lower()
    headless = prefs.get("mode") == "headless" if mode == "" else mode.startswith("h")
    save_prefs(browser, "headless" if headless else "gui")
    print("\nStarting automation in 3 seconds - switch to the browser window...")
    time.sleep(3)
    ok, run_dir, failures = run_plan(plan, headless=headless, browser=browser)
    try:
        save_plan(plan, LAST_PLAN)
    except Exception:
        pass
    again = input("\nRun again / edit more / exit? (r/e/x) [x]: ").strip().lower()
    if again == "r":
        run_plan(plan, headless=headless, browser=browser)
    elif again == "e":
        interactive()
    else:
        print("Done. Output saved under: %s" % OUT_DIR)


# --------------------------------------------------------------------------
# Start menu
# --------------------------------------------------------------------------
def track_flow(args):
    """Menu option 1: record the user's browsing, then offer to automate it."""
    site = normalize_site(args.site) if args.site else ""
    if not site:
        site = normalize_site(input("\nSite to track (e.g. https://school.edu): ").strip())
        if not site:
            print("No site given - back to menu.")
            return
    prefs = load_prefs()
    browser = args.browser or choose_browser(default_name=prefs.get("browser"))
    record_session(site, browser=browser)


def run_saved_flow(args):
    """Menu option 3: pick a saved plan, confirm browser/mode, run it."""
    os.makedirs(PLAN_DIR, exist_ok=True)
    files = sorted(f for f in os.listdir(PLAN_DIR) if f.endswith(".json"))
    if not files:
        print("(no saved plans yet - use 'Track my actions' or build one first)")
        return
    print("\nSAVED PLANS:")
    for i, f in enumerate(files, 1):
        print("  %2d. %s" % (i, f))
    ans = input("\nRun which plan? (number, or Enter to go back): ").strip()
    if not ans:
        return
    try:
        path = os.path.join(PLAN_DIR, files[int(ans) - 1])
        plan = load_plan(path)
    except Exception:
        print("!! could not load that plan")
        return
    show_plan(plan)
    prefs = load_prefs()
    browser = args.browser or choose_browser(default_name=prefs.get("browser"))
    mode_default = "h" if prefs.get("mode") == "headless" else "g"
    mode = input("Run mode?  (g)ui / (h)eadless  [%s]: " % mode_default).strip().lower()
    headless = prefs.get("mode") == "headless" if mode == "" else mode.startswith("h")
    save_prefs(browser, "headless" if headless else "gui")
    print("\nStarting automation in 3 seconds - switch to the browser window...")
    time.sleep(3)
    run_plan(plan, headless=headless, browser=browser)


def main_menu(args):
    """Show the start menu and loop until the user exits."""
    while True:
        prefs = load_prefs()
        print("=" * 62)
        print("  WEB AUTOMATION CONSOLE  (SeleniumBase)")
        print("=" * 62)
        print("  Saved: browser=%s  mode=%s"
              % (prefs.get("browser") or "-", prefs.get("mode", "gui")))
        print("-" * 62)
        print("  1. Track my actions  (record what you do -> automate later)")
        print("  2. Build a plan step-by-step")
        print("  3. Run a saved plan")
        print("  4. Exit")
        print("-" * 62)
        choice = input("What would you like to do? (1-4): ").strip().lower()
        if choice == "1":
            track_flow(args)
        elif choice == "2":
            interactive()
        elif choice == "3":
            run_saved_flow(args)
        elif choice in ("4", "exit", "q"):
            print("Bye.")
            return
        else:
            print("!! choose 1, 2, 3 or 4")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Web automation console (SeleniumBase)")
    ap.add_argument("--file", help="run a saved plan JSON directly (non-interactive)")
    ap.add_argument("--record", action="store_true",
                    help="record your browser actions into a replayable plan")
    ap.add_argument("--site", help="site to open in record mode (e.g. https://example.com)")
    ap.add_argument("--headless", action="store_true", help="run without a visible browser window")
    ap.add_argument("--browser", choices=SUPPORTED_BROWSERS,
                    help="browser engine to use (default: choose interactively)")
    args = ap.parse_args()

    if args.record:
        site = normalize_site(args.site) if args.site else ""
        if not site:
            site = normalize_site(input("Site to automate (e.g. https://example.com): ").strip())
        if not site:
            print("No site given - nothing to record.")
            sys.exit(1)
        prefs = load_prefs()
        browser = args.browser or choose_browser(default_name=prefs.get("browser"))
        record_session(site, browser=browser)
        sys.exit(0)

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
        prefs = load_prefs()
        browser = args.browser or choose_browser(default_name=prefs.get("browser"))
        headless = args.headless or prefs.get("mode") == "headless"
        save_prefs(browser, "headless" if headless else "gui")
        ok, run_dir, failures = run_plan(plan, headless=headless, browser=browser)
        sys.exit(0 if ok else 1)
    main_menu(args)


if __name__ == "__main__":
    main()
