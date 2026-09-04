import unittest

import run_automation


class PlanHelperTests(unittest.TestCase):
    def test_valid_plan(self):
        plan = {"site": "https://example.com", "steps": [{"action": "check", "text": "Example Domain"}]}
        self.assertIs(run_automation.validate_plan(plan), plan)

    def test_invalid_action(self):
        with self.assertRaises(ValueError):
            run_automation.validate_plan({"site": "", "steps": [{"action": "explode"}]})

    def test_xpath_literal_supports_quotes(self):
        literal = run_automation.xpath_literal('Bob\'s "button"')
        self.assertTrue(literal.startswith("concat("))
        self.assertIn("Bob", literal)

    def test_artifact_name_is_safe(self):
        self.assertEqual(run_automation.safe_output_name("../shot", "default", ".png"), "shot.png")

    def test_resolve_selector_generic_text(self):
        sel, kind = run_automation.resolve_selector("some visible text", "generic")
        self.assertEqual(kind, "xpath")
        self.assertIn("not(self::script)", sel)

    def test_events_to_plan_type_and_click(self):
        events = [
            {"t": "load", "url": "https://school.example/app", "ts": 1, "seq": 0},
            {"t": "change",
             "info": {"tag": "input", "type": "text", "name": "email"},
             "value": "a@b.c", "ts": 2, "seq": 1},
            {"t": "click", "info": {"tag": "button", "text": "Apply now"},
             "ts": 3, "seq": 2},
        ]
        plan = run_automation.events_to_plan(events, "https://school.example/app")
        self.assertEqual([s["action"] for s in plan["steps"]], ["type", "click"])
        self.assertEqual(plan["steps"][0]["field"], "email")
        self.assertEqual(plan["steps"][0]["value"], "a@b.c")
        self.assertEqual(plan["steps"][1]["element"], "Apply now")

    def test_events_to_plan_skips_link_clicks(self):
        events = [
            {"t": "load", "url": "https://school.example/courses", "ts": 1, "seq": 0},
            {"t": "click",
             "info": {"tag": "a", "href": "https://school.example/apply",
                       "text": "Apply for courses"},
             "ts": 2, "seq": 1},
            {"t": "load", "url": "https://school.example/apply", "ts": 3, "seq": 2},
        ]
        plan = run_automation.events_to_plan(events, "https://school.example/start")
        self.assertEqual([s["action"] for s in plan["steps"]], ["open", "open"])
        self.assertEqual(plan["steps"][0]["url"], "https://school.example/courses")
        self.assertEqual(plan["steps"][1]["url"], "https://school.example/apply")


if __name__ == "__main__":
    unittest.main()
  
class NewFeatureTests(unittest.TestCase):
    def test_librewolf_is_supported(self):
        self.assertIn("librewolf", run_automation.SUPPORTED_BROWSERS)

    def test_sb_browser_name_maps_librewolf_to_firefox(self):
        self.assertEqual(run_automation.sb_browser_name("librewolf"), "firefox")
        self.assertEqual(run_automation.sb_browser_name("chrome"), "chrome")

    def test_librewolf_candidates_include_program_files(self):
        candidates = run_automation.BROWSER_CANDIDATES["librewolf"]
        self.assertTrue(any("LibreWolf" in c and c.endswith("librewolf.exe")
                            for c in candidates))

    def test_new_activities_in_catalogue(self):
        names = [name for name, _ in run_automation.ACTIVITY_MENU]
        for expected in ("open_tab", "copy", "copy_link", "generate", "clip", "paste"):
            self.assertIn(expected, names)

    def test_generate_number_no_leading_zero(self):
        for digits in (2, 9):
            for _ in range(50):
                value = run_automation.generate_number(digits)
                self.assertEqual(len(value), digits)
                self.assertNotEqual(value[0], "0")

    def test_generate_number_single_digit(self):
        for _ in range(50):
            self.assertEqual(len(run_automation.generate_number(1)), 1)

    def test_clip_value_last_chars(self):
        self.assertEqual(run_automation.clip_value("123456789", 4), "6789")
        self.assertEqual(run_automation.clip_value("abc", 4), "abc")

    def test_validate_plan_new_steps(self):
        plan = {"site": "https://school.example", "steps": [
            {"action": "generate", "digits": "9"},
            {"action": "paste", "element": "#id-box"},
            {"action": "clip", "chars": "4"},
            {"action": "open_tab", "url": "https://school.example/apply"},
            {"action": "copy", "element": "page"},
            {"action": "copy_link", "element": "Apply"},
        ]}
        self.assertIs(run_automation.validate_plan(plan), plan)

    def test_validate_plan_bad_generate_digits(self):
        with self.assertRaises(ValueError):
            run_automation.validate_plan(
                {"site": "", "steps": [{"action": "generate", "digits": "0"}]})