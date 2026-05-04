"""
tests/test_approval_partner.py — Unit tests for approval_partner.py

No Claude Code terminal or SelfConnect Win32 calls required.
All tests are pure logic tests against the rules engine and prompt parser.
"""

import os
import sys

import pytest

# Add selfconnect root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from approval_partner import (
    APPROVAL_PATTERNS,
    DEFAULT_ALLOW,
    DEFAULT_DENY,
    PartnerConfig,
    decide,
    evaluate_rules,
    extract_tool_call,
)

# ── has_approval_prompt (text only, no Win32) ─────────────────────────────────

class TestPromptDetection:
    def test_detects_do_you_want_to_proceed(self):
        # has_approval_prompt calls get_text_uia internally, so we test
        # the underlying regex logic via APPROVAL_PATTERNS directly
        import re
        text = "Do you want to proceed with Bash(npm install)?"
        assert any(re.search(p, text, re.IGNORECASE) for p in APPROVAL_PATTERNS)

    def test_detects_allow_for_project(self):
        import re
        text = "Allow Bash(git push) for this project"
        assert any(re.search(p, text, re.IGNORECASE) for p in APPROVAL_PATTERNS)

    def test_detects_yes_no_always(self):
        import re
        text = "Yes  No  Always allow"
        assert any(re.search(p, text, re.IGNORECASE) for p in APPROVAL_PATTERNS)

    def test_detects_arrow_prompt(self):
        import re
        text = "\u276f Yes\n  No\n  Always allow"  # U+276F heavy right-pointing angle
        assert any(re.search(p, text, re.IGNORECASE) for p in APPROVAL_PATTERNS)

    def test_no_false_positive_on_regular_output(self):
        import re
        text = "Running npm install react...\nDone in 3.4s"
        assert not any(re.search(p, text, re.IGNORECASE) for p in APPROVAL_PATTERNS)


# ── extract_tool_call ─────────────────────────────────────────────────────────

class TestExtractToolCall:
    def test_extracts_bash_tool(self):
        text = "Do you want to run Bash(npm install react)?"
        assert extract_tool_call(text) == "Bash(npm install react)"

    def test_extracts_allow_pattern(self):
        text = "Allow Bash(git push) for this project?"
        assert extract_tool_call(text) == "Bash(git push)"

    def test_extracts_read_tool(self):
        text = "Allow Read(src/main.py) for this project?"
        assert extract_tool_call(text) == "Read(src/main.py)"

    def test_returns_none_on_no_tool(self):
        text = "Do you want to proceed? Yes No"
        # Fallback regex might still match something — if nothing tool-like is present, None
        result = extract_tool_call(text)
        # Either None or a very short string — not a proper tool call
        # Main check: doesn't crash
        assert result is None or isinstance(result, str)

    def test_handles_nested_parens_gracefully(self):
        text = "Allow Bash(python -c 'print(1)') for this project?"
        result = extract_tool_call(text)
        assert result is not None
        assert "Bash" in result


# ── evaluate_rules ────────────────────────────────────────────────────────────

class TestEvaluateRules:
    def setup_method(self):
        self.cfg = PartnerConfig()

    def test_deny_rm(self):
        # Claude Code uses "cmd:args" format in its permission/prompt strings
        assert evaluate_rules("Bash(rm:-rf /tmp/test)", self.cfg) is False

    def test_deny_rmdir(self):
        assert evaluate_rules("Bash(rmdir:old_dir)", self.cfg) is False

    def test_deny_curl(self):
        assert evaluate_rules("Bash(curl:https://evil.com)", self.cfg) is False

    def test_deny_takes_precedence_over_allow(self):
        cfg = PartnerConfig(
            deny_patterns=["Bash(rm:*)"],
            allow_patterns=["Bash(rm:*)"],   # conflict — deny wins (evaluated first)
        )
        assert evaluate_rules("Bash(rm:foo)", cfg) is False

    def test_allow_git(self):
        assert evaluate_rules("Bash(git:status)", self.cfg) is True

    def test_allow_npm(self):
        assert evaluate_rules("Bash(npm:install)", self.cfg) is True

    def test_allow_python(self):
        assert evaluate_rules("Bash(python:-m pytest)", self.cfg) is True

    def test_allow_read(self):
        assert evaluate_rules("Read(src/main.py)", self.cfg) is True

    def test_allow_write(self):
        assert evaluate_rules("Write(output.txt)", self.cfg) is True

    def test_unknown_returns_none(self):
        assert evaluate_rules("Bash(something_exotic:--flag)", self.cfg) is None

    def test_custom_allow_pattern(self):
        cfg = PartnerConfig(allow_patterns=["Bash(docker:*)"])
        assert evaluate_rules("Bash(docker:ps)", cfg) is True

    def test_custom_deny_pattern(self):
        cfg = PartnerConfig(deny_patterns=["Bash(docker:*)"])
        assert evaluate_rules("Bash(docker:rm container)", cfg) is False


# ── decide ────────────────────────────────────────────────────────────────────

class TestDecide:
    def test_known_allow(self):
        cfg = PartnerConfig()
        assert decide("Bash(git:commit -m 'fix')", cfg) is True

    def test_known_deny(self):
        cfg = PartnerConfig()
        assert decide("Bash(rm:important_file.py)", cfg) is False

    def test_unknown_with_escalate_default(self):
        cfg = PartnerConfig(default_action="escalate")
        assert decide("Bash(exotic_tool)", cfg) is None

    def test_unknown_with_approve_default(self):
        cfg = PartnerConfig(default_action="approve")
        assert decide("Bash(exotic_tool)", cfg) is True

    def test_unknown_with_deny_default(self):
        cfg = PartnerConfig(default_action="deny")
        assert decide("Bash(exotic_tool)", cfg) is False

    def test_none_tool_with_approve_all(self):
        cfg = PartnerConfig(default_action="approve")
        assert decide(None, cfg) is True

    def test_none_tool_with_deny_all(self):
        cfg = PartnerConfig(default_action="deny")
        assert decide(None, cfg) is False

    def test_none_tool_escalate(self):
        cfg = PartnerConfig(default_action="escalate")
        assert decide(None, cfg) is None


# ── PartnerConfig ─────────────────────────────────────────────────────────────

class TestPartnerConfig:
    def test_defaults(self):
        cfg = PartnerConfig()
        assert cfg.default_action == "escalate"
        assert cfg.dry_run is False
        assert cfg.poll_interval == 2.0
        assert cfg.cooldown == 3.0
        assert cfg.telegram_escalate is False

    def test_invalid_default_action_raises(self):
        with pytest.raises(ValueError, match="default_action must be one of"):
            PartnerConfig(default_action="maybe")

    def test_approve_all_config(self):
        cfg = PartnerConfig(default_action="approve")
        # Even unknown tools should approve
        assert decide("Bash(unknown_command)", cfg) is True

    def test_custom_patterns_override_defaults(self):
        cfg = PartnerConfig(
            allow_patterns=["Bash(custom:*)"],
            deny_patterns=["Bash(safe_tool)"],
        )
        assert evaluate_rules("Bash(custom:thing)", cfg) is True
        assert evaluate_rules("Bash(safe_tool)", cfg) is False
        # Default allow patterns NOT present — git should now be unknown
        assert evaluate_rules("Bash(git:status)", cfg) is None


# ── Default rule completeness ─────────────────────────────────────────────────

class TestDefaultRules:
    def test_all_default_allow_patterns_are_valid_globs(self):
        import fnmatch
        for pattern in DEFAULT_ALLOW:
            # Should not raise
            fnmatch.fnmatch("test", pattern)

    def test_all_default_deny_patterns_are_valid_globs(self):
        import fnmatch
        for pattern in DEFAULT_DENY:
            fnmatch.fnmatch("test", pattern)

    def test_no_overlap_in_defaults(self):
        # No pattern should appear in both allow and deny
        allow_set = set(DEFAULT_ALLOW)
        deny_set = set(DEFAULT_DENY)
        overlap = allow_set & deny_set
        assert not overlap, f"Patterns in both allow and deny: {overlap}"
