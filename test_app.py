"""Tests for app.py - run:  python -m unittest -v test_app
No Streamlit needed: the engine is pure Python and the UI is exercised with a stub module."""
import random
import string
import sys
import unittest
from unittest.mock import MagicMock

import app
from app import (DemoSession, MockPlacidWayAPI, OnboardingBot, classify, fmt, chat_html, EDGE_MESSAGES, SCENARIOS)


def make(scenario="Active - nothing started"):
    api = MockPlacidWayAPI(scenario)
    return api, OnboardingBot(api)


class Journey(unittest.TestCase):
    def test_full_happy_path_with_verification(self):
        api, bot = make()
        t = bot.handle("START")
        self.assertIn("Step 1 of 5 — Center Details", t.reply)
        self.assertIn("/provider-portal/agreement/center-details", t.reply)
        self.assertIn("Welcome to PlacidWay", t.reply)
        # DONE without the portal changing -> must NOT advance
        before = api.snapshot()
        t = bot.handle("DONE")
        self.assertIn("not fully completed", t.reply)
        self.assertNotIn("Step 2", t.reply)
        self.assertEqual(before, api.snapshot(), "chat must never change backend state")
        expected = [("Step 2 of 5 — Licenses & Certificates", "center-certificates", "1 of 5"),
                    ("Step 3 of 5 — Center Credentials", "center-credentials", "2 of 5"),
                    ("Step 4 of 5 — Marketing Package", "center-marketing", "3 of 5"),
                    ("Step 5 of 5 — Provider Agreement", "center-agreement", "4 of 5")]
        for header, link, count in expected:
            api.complete_current_step()
            t = bot.handle("DONE")
            self.assertIn(header, t.reply)
            self.assertIn(link, t.reply)
            self.assertIn(count + " completed", t.reply)
            self.assertIn("✓", t.reply)
        api.complete_current_step()
        t = bot.handle("SIGNED")
        self.assertIn("Application complete ✓", t.reply)
        self.assertNotIn("http", t.reply, "no link once everything is complete")

    def test_never_links_completed_sections(self):
        api, bot = make("Almost there (Steps 1-4 done)")
        r = bot.handle("CONTINUE").reply
        self.assertIn("center-agreement", r)
        for done in ("center-details", "center-certificates", "center-credentials", "center-marketing"):
            self.assertNotIn(done, r)
        self.assertIn("4 of 5", r)

    def test_resume_never_restarts(self):
        api, bot = make("Partially completed (Step 1 done)")
        for msg in ("START", "hi", "CONTINUE"):
            r = bot.handle(msg).reply
            self.assertIn("Step 2 of 5", r)
            self.assertNotIn("Step 1 of 5", r)

    def test_activation_first(self):
        api, bot = make("New provider (account not activated)")
        r = bot.handle("START").reply
        self.assertIn("activate your account", r)
        self.assertNotIn("center-details", r)
        self.assertIn("not fully completed", bot.handle("DONE").reply)
        api.complete_current_step()                      # provider activates
        r = bot.handle("DONE").reply
        self.assertIn("Account Activation complete ✓", r)
        self.assertIn("Step 1 of 5 — Center Details", r)

    def test_fully_complete(self):
        api, bot = make("Fully completed")
        self.assertIn("Application complete ✓", bot.handle("CONTINUE").reply)
        self.assertIn("5 of 5", bot.handle("STATUS").reply)
        self.assertIn("complete", bot.handle("HELP").reply.lower())

    def test_status_and_help(self):
        api, bot = make("Partially completed (Step 1 done)")
        s = bot.handle("STATUS").reply
        self.assertIn("1 of 5", s)
        self.assertIn("✅ Center Details", s)
        self.assertIn("⬜", s)
        h = bot.handle("HELP").reply
        self.assertIn("Help for **Licenses & Certificates**", h)
        self.assertIn("Commands:", h)

    def test_in_progress_shows_missing_fields(self):
        api, bot = make()
        api.start_current_step()
        r = bot.handle("CONTINUE").reply
        self.assertIn("Still missing: address, website", r)


class Commands(unittest.TestCase):
    def test_classification(self):
        cases = {"start": "START", " START ": "START", "Start!": "START", "done!!": "DONE", "START START": "START",
                 "DONE DONE DONE": "DONE", "I am done": "FREE_TEXT", "hi": "GREETING", "Hello!": "GREETING",
                 "": "EMPTY", "   ": "EMPTY", "x" * 1001: "TOO_LONG", "asdfghjkl": "GARBAGE", "?!?!": "GARBAGE",
                 "😂😂": "GARBAGE", "12345": "GARBAGE", "hahahahaha": "GARBAGE", "ulta seedha kuch bhi": "FREE_TEXT",
                 "SELECT * FROM users;": "FREE_TEXT", "What should I upload?": "FREE_TEXT", "strengths": "FREE_TEXT",
                 "signed": "SIGNED", "StOp": "STOP"}
        for text, want in cases.items():
            self.assertEqual(classify(text), want, repr(text[:30]))

    def test_support_sales_handoff_payload(self):
        api, bot = make("Partially completed (Step 1 done)")
        bot.handle("START")
        t = bot.handle("SUPPORT")
        self.assertEqual(t.handoff["team"], "SUPPORT")
        self.assertEqual(t.handoff["provider_id"], "P-1003")
        self.assertEqual(t.handoff["current_step"], "Licenses & Certificates")
        self.assertTrue(t.handoff["chat_history"])
        self.assertEqual(bot.handle("SALES").handoff["team"], "SALES")
        self.assertEqual(len(bot.handoffs), 2)

    def test_pause_then_continue(self):
        api, bot = make()
        bot.handle("PAUSE")
        self.assertTrue(bot.paused)
        r = bot.handle("CONTINUE").reply
        self.assertFalse(bot.paused)
        self.assertIn("Center Details", r)

    def test_stop_is_silent_until_start(self):
        api, bot = make()
        bot.handle("START")
        t = bot.handle("STOP")
        self.assertIn("unsubscribed", t.reply)
        self.assertEqual(t.trace["Fetch Provider Status (Mock API)"]["status"], "skipped", "STOP must bypass the API")
        calls = len(api.calls)
        for msg in ("STATUS", "hello", "asdf", "SUPPORT"):
            q = bot.handle(msg)
            self.assertEqual(q.reply, "")
            self.assertTrue(q.note)
        self.assertEqual(len(api.calls), calls, "no API calls while opted out")
        r = bot.handle("START")
        self.assertIn("Center Details", r.reply)
        self.assertFalse(bot.opted_out)


class Problems(unittest.TestCase):
    def test_api_down(self):
        api, bot = make("Partially completed (Step 1 done)")
        bot.handle("START")
        api.api_down = True
        snap = {k: dict(v) for k, v in api.steps.items()}
        for msg in ("STATUS", "DONE", "CONTINUE", "HELP", "asdfgh", "What should I upload?"):
            t = bot.handle(msg)
            self.assertEqual(t.error_type, "API_FAILURE", msg)
            self.assertIn("couldn't check", t.reply)
            self.assertEqual(t.trace["Fetch Provider Status (Mock API)"]["status"], "error")
        self.assertEqual(snap, {k: dict(v) for k, v in api.steps.items()})
        t = bot.handle("SUPPORT")                          # humans stay reachable
        self.assertEqual(t.handoff["team"], "SUPPORT")
        self.assertIsNone(t.handoff["provider_id"])
        api.api_down = False
        self.assertIn("Step 2 of 5", bot.handle("CONTINUE").reply)

    def test_corrupt_data_escalates(self):
        api, bot = make()
        api.corrupt = True
        t = bot.handle("STATUS")
        self.assertEqual(t.error_type, "UNEXPECTED_STATUS")
        self.assertEqual(t.handoff["reason"], "UNEXPECTED_STATUS_VALUE")
        self.assertEqual(t.trace["Validate API Response"]["status"], "error")
        self.assertIn("progress has not been changed", t.reply)

    def test_garbage_never_crashes_and_guides_back(self):
        api, bot = make()
        for msg in ("asdfghjkl", "?!?!?!", "😂😂👍", "ulta seedha kuch bhi", "I am done with this step", "<script>alert(1)</script>", "blah " * 300):
            t = bot.handle(msg)
            self.assertIn("Step 1 of 5 — Center Details", t.reply, msg[:20])
            self.assertIn("DONE", t.reply)
            self.assertNotEqual(t.error_type, "UNEXPECTED_ERROR")

    def test_empty_message(self):
        api, bot = make()
        t = bot.handle("   ")
        self.assertEqual(t.error_type, "INVALID_MESSAGE")
        self.assertIn("didn't receive a readable message", t.reply)
        self.assertEqual(t.trace["Fetch Provider Status (Mock API)"]["status"], "skipped")

    def test_unknown_streak_offers_support_then_resets(self):
        api, bot = make()
        r1 = bot.handle("asdfghjkl").reply
        r2 = bot.handle("qwertyuiop").reply
        r3 = bot.handle("zzzzzzzz").reply
        self.assertNotIn("not getting through", r1 + r2)
        self.assertIn("not getting through", r3)
        bot.handle("STATUS")
        self.assertEqual(bot.unknown_streak, 0)
        self.assertNotIn("not getting through", bot.handle("asdfghjkl").reply)


class FAQ(unittest.TestCase):
    def test_step_specific_answers(self):
        api, bot = make("Partially completed (Step 1 done)")            # step 2
        self.assertIn("Medical facility license", bot.handle("What should I upload?").reply)
        self.assertIn("Upload the documents you currently have", bot.handle("I don't have one of the documents.").reply)
        api, bot = make("Active - nothing started")
        self.assertIn("Center name", bot.handle("What information do I need?").reply)
        api, bot = make("Partially completed (Step 1 done)")
        api.steps["certificates"]["status"] = "COMPLETED"
        self.assertIn("optional", bot.handle("I don't have my social media links.").reply)
        api.steps["center_credentials"]["status"] = "COMPLETED"
        self.assertIn("reply **SALES**", bot.handle("Which package should I choose?").reply)
        api.steps["marketing_package"]["status"] = "COMPLETED"
        self.assertIn("authorized to represent", bot.handle("Who should sign it?").reply)

    def test_general_faq(self):
        api, bot = make()
        self.assertIn("progress will be saved", bot.handle("Can I do this later?").reply)
        self.assertIn("5 application steps", bot.handle("how many steps are there").reply)
        self.assertIn("can't confirm approval", bot.handle("is my application approved?").reply)

    def test_guardrails(self):
        api, bot = make("Almost there (Steps 1-4 done)")
        self.assertIn("can't give legal advice", bot.handle("Is this legally binding? do I need a lawyer").reply)
        self.assertIn("**SALES**", bot.handle("how much does it cost").reply)
        self.assertNotIn("is complete", bot.handle("is my agreement done?").reply.lower().replace("not fully", ""))

    def test_ai_hook_is_guarded(self):
        api = MockPlacidWayAPI()
        bot = OnboardingBot(api, faq_fallback=lambda q, s: "Your application has been completed, congratulations!")
        r = bot.handle("something unrelated xyz").reply
        self.assertNotIn("congratulations", r.lower())
        bot2 = OnboardingBot(api, faq_fallback=lambda q, s: "Please use the form on the page.")
        self.assertIn("Please use the form", bot2.handle("something unrelated xyz").reply)


class Invariants(unittest.TestCase):
    def test_edge_messages_all_safe(self):
        for scenario in SCENARIOS:
            d = DemoSession(scenario)
            for label, msg in EDGE_MESSAGES.items():
                d.send(msg)
                self.assertNotEqual(d.last_turn.error_type, "UNEXPECTED_ERROR", (scenario, label))

    def test_chat_never_changes_backend(self):
        rnd = random.Random(7)
        pool = list(app.COMMANDS) + ["hi", "", "asdf", "What should I upload?", "ulta seedha", "?!", "😂", "I am done", "START START"]
        for scenario in SCENARIOS:
            api, bot = make(scenario)
            snap = api.snapshot()
            for _ in range(200):
                bot.handle(rnd.choice(pool))
            self.assertEqual(snap, api.snapshot(), scenario)

    def test_fuzz_no_hidden_exceptions(self):
        rnd = random.Random(1234)
        alphabet = string.printable + "ğüşıöçاردوहिन्दी😀🔥\u200b\x00\x07"
        for scenario in SCENARIOS:
            api, bot = make(scenario)
            for i in range(1000):
                api.api_down = rnd.random() < 0.1
                api.corrupt = rnd.random() < 0.1
                text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 60)))
                t = bot.handle(text)
                self.assertNotEqual(t.error_type, "UNEXPECTED_ERROR", repr(text))
                self.assertEqual(set(t.trace), set(app.NODES))
                if i % 50 == 0:
                    bot.handle(rnd.choice(["START", "STOP", "CONTINUE", "PAUSE"]))
            for bad in (None, 123, [], {}):
                self.assertEqual(bot.handle(bad).kind in ("EMPTY", "STOP", "FREE_TEXT", "GARBAGE", "START"), True)

    def test_html_safety(self):
        evil = '<script>alert(1)</script> <img src=x onerror=alert(1)> "quoted" & more'
        self.assertNotIn("<script", fmt(evil))
        self.assertNotIn("<img", fmt(evil))
        page = chat_html([app.Msg("user", evil, "10:00"), app.Msg("bot", "**bold** http://www.placidway.com/x?a=1&b=2", "10:00"),
                          app.Msg("note", "n", "10:00")])
        self.assertNotIn("<script", page)
        self.assertIn("<b>bold</b>", page)
        self.assertIn('href="http://www.placidway.com/x?a=1&amp;b=2"', page)
        self.assertNotIn("\n", page, "must stay a single line so Markdown does not render it as a code block")


class UiSmoke(unittest.TestCase):
    """Executes main() against a stubbed `streamlit` to catch NameErrors/typos. NOT a real-Streamlit test."""

    class State(dict):
        __getattr__ = dict.get

        def __setattr__(self, k, v):
            self[k] = v

    def run_main(self, state, prompt=None, api_down=False, corrupt=False):
        st = MagicMock()
        st.session_state = state
        st.chat_input.return_value = prompt
        st.columns.side_effect = lambda spec: [MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
        st.tabs.side_effect = lambda labels: [MagicMock() for _ in labels]
        st.checkbox.side_effect = lambda label, value=False, key=None, **k: (api_down if key == "api_down" else corrupt if key == "api_corrupt" else value)

        def selectbox(label, options, index=0, key=None, **k):
            state.setdefault(key, options[index])
            return state[key]
        st.selectbox.side_effect = selectbox
        sys.modules["streamlit"] = st
        app.main()
        return st

    def test_renders_across_states(self):
        state = self.State()
        st = self.run_main(state)
        html_calls = [c.args[0] for c in st.markdown.call_args_list if c.args and 'class="wa-phone"' in c.args[0]]
        self.assertEqual(len(html_calls), 1)
        self.assertIn("Reply <b>START</b>", html_calls[0])
        for prompt, down, corrupt in (("START", False, False), ("DONE", False, False), ("asdfgh", False, False),
                                       ("STATUS", True, False), ("STATUS", False, True), ("SUPPORT", True, False), ("STOP", False, False)):
            self.run_main(state, prompt, down, corrupt)
        demo = state["demo"]
        self.assertTrue(any(m.role == "user" for m in demo.messages))
        # edge-case + portal helpers used by sidebar callbacks
        demo.portal_complete(); demo.portal_partial(); demo.restart("Fully completed"); demo.portal_complete(); demo.portal_partial()
        self.run_main(state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
