"""
bestbuy_to_pbp.py — Convert Best Buy process book to PBP v0.1 YAML

Generates trigger_yaml, remediation_yaml, verify_yaml, and provenance fields
for POST /api/pathbooks on aihangout.ai.

Usage:
    python bestbuy_to_pbp.py [--output <dir>]
    python bestbuy_to_pbp.py --post  # POST draft to aihangout.ai API
"""

import argparse
import hashlib
import json
import os

import yaml

TRACE_PATH = os.path.join(
    r"C:\Users\techai\PKA testing", "Owner's Inbox",
    "bestbuy-runbook", "bestbuy-ps5-trace.zip"
)
PROCESS_BOOK_PATH = os.path.join(
    r"C:\Users\techai\PKA testing", "Owner's Inbox",
    "bestbuy-runbook", "bestbuy-order-process.md"
)
PATHBOOK_API = "https://aihangout.ai/api/pathbooks"

# ---------------------------------------------------------------------------
# PBP field builders
# ---------------------------------------------------------------------------

def build_trigger_yaml() -> dict:
    """Generic e-commerce checkout trigger — not PS5-specific."""
    return {
        "schema_version": "pbp/v0.1",
        "id": "PBP-BESTBUY-ORDER-0001",
        "source_type": "navigation",
        "domain": "bestbuy.com",
        "intent": "e-commerce-order",
        "parameters": [
            {
                "name": "search_term",
                "type": "string",
                "required": True,
                "description": "URL-encoded search query (spaces replaced with +)",
                "example": "playstation+5+console"
            },
            {
                "name": "item_index",
                "type": "integer",
                "required": False,
                "default": 0,
                "description": "Which search result to add to cart (0 = first)"
            },
            {
                "name": "first_name",
                "type": "string",
                "required": True
            },
            {
                "name": "last_name",
                "type": "string",
                "required": True
            },
            {
                "name": "address",
                "type": "string",
                "required": True
            },
            {
                "name": "city",
                "type": "string",
                "required": True
            },
            {
                "name": "state",
                "type": "string",
                "required": True,
                "description": "2-letter US state code, e.g. MN"
            },
            {
                "name": "zip",
                "type": "string",
                "required": True,
                "description": "5-digit ZIP code"
            },
            {
                "name": "email",
                "type": "string",
                "required": True
            },
            {
                "name": "phone",
                "type": "string",
                "required": True,
                "description": "10-digit number, no punctuation, e.g. 6125551234"
            }
        ],
        "stop_condition": "Shipping form filled and verified — DO NOT click Continue to Payment Information",
        "recorded": "2026-05-17",
        "site_notes": "Guest checkout only. No account required."
    }


def build_remediation_yaml() -> dict:
    """8-step navigation sequence for Best Buy guest checkout."""
    return {
        "schema_version": "pbp/v0.1",
        "steps": [
            {
                "step": 1,
                "name": "navigate_homepage",
                "action": "navigate",
                "url": "https://www.bestbuy.com",
                "wait_ms": 3000,
                "verify": "page.title contains 'Best Buy' OR search bar visible",
                "selectors": [],
                "on_failure": "retry once, then abort"
            },
            {
                "step": 2,
                "name": "search_item",
                "action": "navigate",
                "url": "https://www.bestbuy.com/site/searchpage.jsp?st={search_term}",
                "url_notes": "Replace spaces in search_term with +. URL param 'st=' is stable (years).",
                "wait_ms": 4000,
                "verify": "results grid visible, Add to cart buttons present",
                "selectors": [
                    {
                        "purpose": "search URL",
                        "pattern": "https://www.bestbuy.com/site/searchpage.jsp?st=",
                        "stability": "HIGH"
                    }
                ],
                "on_failure": "check search_term encoding, retry"
            },
            {
                "step": 3,
                "name": "add_to_cart",
                "action": "click",
                "selector": "role=button[name='Add to cart']",
                "selector_index": "{item_index}",
                "wait_ms": 3000,
                "verify": "flyout shows 'Added to cart' with green checkmark",
                "stability": "HIGH",
                "on_failure": "if 'Sold Out' or 'Coming Soon' → abort or increment item_index",
                "selectors": [
                    {
                        "purpose": "add to cart",
                        "pattern": "internal:role=button[name=\"Add to cart\"i]",
                        "stability": "HIGH"
                    }
                ]
            },
            {
                "step": 4,
                "name": "go_to_cart",
                "action": "click",
                "selector": "text='Go to cart'",
                "fallback_url": "https://www.bestbuy.com/cart",
                "wait_ms": 3000,
                "verify": "page shows 'Your Cart' with item listed, Checkout button visible",
                "stability": "HIGH",
                "on_failure": "navigate directly to https://www.bestbuy.com/cart"
            },
            {
                "step": 5,
                "name": "checkout",
                "action": "click",
                "selector": "role=button[name='Checkout']",
                "wait_ms": 4000,
                "verify": "redirected through identity/signin page",
                "redirect_path": "/cart → /identity/signin?token=... → may auto-advance",
                "stability": "HIGH",
                "on_failure": "wait additional 3000ms and check URL"
            },
            {
                "step": 6,
                "name": "continue_as_guest",
                "action": "click",
                "selector": "role=button[name='Continue as Guest']",
                "wait_ms": 3000,
                "verify": "redirected to /checkout/r/fulfillment, shipping form visible",
                "stability": "MEDIUM",
                "on_failure": "if button absent, site requires account — abort guest flow"
            },
            {
                "step": 7,
                "name": "fill_shipping_contact",
                "action": "fill_form",
                "wait_ms": 1000,
                "fields": [
                    {"label": "First Name", "value": "{first_name}", "type": "text", "selector_pattern": "internal:label=\"First Name\"i >> nth=0"},
                    {"label": "Last Name", "value": "{last_name}", "type": "text", "selector_pattern": "internal:label=\"Last Name\"i >> nth=0"},
                    {"label": "Address", "value": "{address}", "type": "text", "selector_pattern": "internal:label=\"Address\"i >> nth=0"},
                    {
                        "action": "dismiss_autocomplete",
                        "method": "click heading h2:has-text('Shipping')",
                        "wait_ms": 1500,
                        "reason": "Address autocomplete dropdown blocks City/ZIP fields"
                    },
                    {"label": "City", "value": "{city}", "type": "text", "selector_pattern": "internal:label=\"City\"i >> nth=0"},
                    {"label": "State", "value": "{state}", "type": "select", "selector_pattern": "internal:label=\"State\"i"},
                    {"label": "ZIP Code", "value": "{zip}", "type": "text", "selector_pattern": "internal:label=\"ZIP Code\"i"},
                    {"label": "Email Address", "value": "{email}", "type": "email", "selector_pattern": "internal:label=\"Email Address\"i"},
                    {"label": "Phone Number", "value": "{phone}", "type": "tel", "selector_pattern": "internal:label=\"Phone Number\"i", "format": "10 digits no punctuation"}
                ],
                "gotchas": [
                    "Address autocomplete MUST be dismissed before filling City/ZIP",
                    "Phone: 10 digits, no dashes (6125551234 not 612-555-1234)",
                    "Use as billing address: pre-checked, leave it",
                    "Opt-In text updates: unchecked by default, leave it"
                ],
                "stability": "HIGH"
            },
            {
                "step": 8,
                "name": "verify_and_stop",
                "action": "screenshot_and_stop",
                "wait_ms": 0,
                "verify": "all form fields filled, Continue to Payment Information button visible",
                "stop": True,
                "stop_reason": "STOP_POINT — do NOT click Continue to Payment Information unless explicitly authorized",
                "selector_to_not_click": "role=button[name='Continue to Payment Information']",
                "stability": "MEDIUM"
            }
        ],
        "error_recovery": [
            {"condition": "Add to cart button missing", "recovery": "Item sold out — try next result or different item"},
            {"condition": "Flyout doesn't appear after add", "recovery": "Navigate directly to https://www.bestbuy.com/cart"},
            {"condition": "Sign-in page without Guest option", "recovery": "Abort — site requires account for this flow"},
            {"condition": "Address autocomplete blocks City/ZIP", "recovery": "Click any heading text to dismiss, then fill City/ZIP"},
            {"condition": "Phone validation error", "recovery": "Ensure exactly 10 digits, no punctuation"},
            {"condition": "Page timeout or blank", "recovery": "Refresh once, wait 5s. If still blank, restart from step 1"},
            {"condition": "CAPTCHA or bot detection", "recovery": "Cannot proceed automatically — flag for human review"}
        ],
        "timing_budget_ms": {
            "homepage_load": 3000,
            "search_results": 4000,
            "after_add_to_cart": 3000,
            "cart_page": 3000,
            "after_checkout_click": 4000,
            "after_guest_click": 3000,
            "after_address_type": 1500,
            "after_all_fields": 1000,
            "total_estimate": "25000-30000"
        }
    }


def build_verify_yaml() -> dict:
    """Verification checkpoints per step."""
    return {
        "schema_version": "pbp/v0.1",
        "checkpoints": [
            {"after_step": 1, "check": "url_contains", "value": "bestbuy.com"},
            {"after_step": 2, "check": "element_visible", "selector": "role=button[name='Add to cart']"},
            {"after_step": 3, "check": "element_visible", "selector": "text='Go to cart'"},
            {"after_step": 4, "check": "element_visible", "selector": "role=button[name='Checkout']"},
            {"after_step": 5, "check": "url_contains", "value": "identity/signin"},
            {"after_step": 6, "check": "url_contains", "value": "checkout/r/fulfillment"},
            {"after_step": 7, "check": "element_filled", "labels": ["First Name", "Last Name", "Address", "City", "ZIP Code", "Email Address", "Phone Number"]},
            {"after_step": 8, "check": "element_visible", "selector": "role=button[name='Continue to Payment Information']", "note": "visible but NOT clicked"}
        ]
    }


def compute_trace_sha256() -> str:
    if not os.path.exists(TRACE_PATH):
        return "FILE_NOT_FOUND"
    with open(TRACE_PATH, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build_pathbook_payload() -> dict:
    trace_sha256 = compute_trace_sha256()
    trigger = build_trigger_yaml()
    remediation = build_remediation_yaml()
    verify = build_verify_yaml()

    return {
        # ── Required fields (API returns 400 without these) ──────────────────
        "title": "Best Buy — Guest Checkout (Generic)",
        "error_signature": "bestbuy.com: guest checkout navigation (e-commerce-order)",
        "trigger_yaml": yaml.dump(trigger, sort_keys=False, allow_unicode=True),
        "remediation_yaml": yaml.dump(remediation, sort_keys=False, allow_unicode=True),
        # ── Optional but important ───────────────────────────────────────────
        "pathbook_id": "PBP-BESTBUY-ORDER-0001",   # canonical ID; auto-generated if omitted
        "protocol_version": "pbp-0.1",
        "summary": (
            "Parameterized navigation pathbook for Best Buy e-commerce guest checkout. "
            "Covers: search → add to cart → go to cart → checkout → guest → shipping form fill → STOP. "
            "Does NOT place an order. Works for any searchable item."
        ),
        "source_type": "navigation",
        "ecosystem": "browser-commerce",
        "runtime": "playwright-browser",
        "package_name": "bestbuy.com",
        "status": "draft",
        "trust_tier": "draft",
        "confidence": 0.2,
        "token_savings_estimate": 0,
        "verify_yaml": yaml.dump(verify, sort_keys=False, allow_unicode=True),
        "provenance": json.dumps({
            "source_agent": "Claude Code (Playwright runner)",
            "source_hwnd": 3870126,
            "orchestrator_agent": "AXIOM",
            "orchestrator_hwnd": 9307910,
            "artifact": "bestbuy-ps5-trace.zip",
            "artifact_sha256": trace_sha256,
            "artifact_size_bytes": os.path.getsize(TRACE_PATH) if os.path.exists(TRACE_PATH) else 0,
            "recorded": "2026-05-17",
            "actions_captured": 67,
            "errors": 0,
            "process_book": "bestbuy-order-process.md",
            "mesh_session": "f685a137-81d1-4c62-b8c5-9f3ecd7be949",
            "transport": "SelfConnect PostMessage(WM_CHAR)",
            "sdk_version": "0.10.0"
        })
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=os.path.join(
        r"C:\Users\techai\PKA testing", "Owner's Inbox", "bestbuy-runbook"
    ))
    parser.add_argument("--post", action="store_true", help="POST draft to aihangout.ai API")
    args = parser.parse_args()

    payload = build_pathbook_payload()

    # Write human-readable YAML files
    out = args.output
    os.makedirs(out, exist_ok=True)

    trigger_path = os.path.join(out, "pbp_trigger.yaml")
    remediation_path = os.path.join(out, "pbp_remediation.yaml")
    verify_path = os.path.join(out, "pbp_verify.yaml")
    payload_path = os.path.join(out, "pbp_payload.json")

    import yaml as _yaml
    with open(trigger_path, "w", encoding="utf-8") as f:
        _yaml.dump(build_trigger_yaml(), f, sort_keys=False, allow_unicode=True)

    with open(remediation_path, "w", encoding="utf-8") as f:
        _yaml.dump(build_remediation_yaml(), f, sort_keys=False, allow_unicode=True)

    with open(verify_path, "w", encoding="utf-8") as f:
        _yaml.dump(build_verify_yaml(), f, sort_keys=False, allow_unicode=True)

    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"trigger_yaml  -> {trigger_path}")
    print(f"remediation   -> {remediation_path}")
    print(f"verify_yaml   -> {verify_path}")
    print(f"full payload  -> {payload_path}")

    trace_sha = compute_trace_sha256()
    print(f"\nProvenance SHA256: {trace_sha}")
    print("Pathbook ID: PBP-BESTBUY-ORDER-0001")

    if args.post:
        import urllib.request
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            PATHBOOK_API,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                print(f"\nPOST {PATHBOOK_API} → {resp.status}")
                print(body)
        except Exception as e:
            print(f"\nPOST failed: {e}")
            print("Payload saved locally. Submit manually or re-run with auth token.")


if __name__ == "__main__":
    main()
