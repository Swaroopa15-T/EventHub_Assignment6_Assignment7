import json
import os
import re
from playwright.sync_api import Page, expect

EMAIL = os.environ.get("EVENTHUB_EMAIL", "swarooparajbhan@gmail.com")
PASSWORD = os.environ.get("EVENTHUB_PASSWORD", "your-password-here")


def test_discover_storage(page: Page):
    calls = []
    page.on("request", lambda r: calls.append(f"{r.method} {r.url}")
            if "api.eventhub" in r.url and r.method != "OPTIONS" else None)

    page.goto("/login")
    page.get_by_label("Email").fill(EMAIL)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name=re.compile("sign in", re.I)).click()
    expect(page.get_by_role("button", name="Logout")).to_be_visible()

    storage = page.evaluate("() => Object.fromEntries(Object.entries(localStorage))")
    # hide most of any long token value before you share the file
    safe = {k: (v[:25] + "...") if len(v) > 40 else v for k, v in storage.items()}
    with open("storage_keys.json", "w", encoding="utf-8") as f:
        json.dump({"localStorage": safe, "api_calls": calls}, f, indent=2)