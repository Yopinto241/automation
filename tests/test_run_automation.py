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


if __name__ == "__main__":
    unittest.main()
  
