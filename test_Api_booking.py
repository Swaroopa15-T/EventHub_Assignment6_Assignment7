import json
import os
import re

import pytest
from playwright.sync_api import Page, Playwright, expect

EMAIL = os.environ.get("EVENTHUB_EMAIL", "swarooparajbhan@gmail.com")
PASSWORD = os.environ.get("EVENTHUB_PASSWORD", "your-password-here")
API_BASE = "https://api.eventhub.rahulshettyacademy.com/api/"

CUSTOMER = {"customerName": "Api Customer", "customerEmail": "api.customer@example.com",
            "customerPhone": "9876543210"}
QTY = 2


# ---------- small JSON helpers (work with wrapped or plain responses) ----------
def norm(k: str) -> str:
    return k.lower().replace("_", "")


def walk(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield obj, k
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def find_list(body):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for v in body.values():
            found = find_list(v)
            if found is not None:
                return found
    return None


def find_key(body, wanted: list):
    """First scalar value whose key matches one of the wanted names."""
    names = [norm(w) for w in wanted]
    for container, key in walk(body):
        if norm(key) in names and not isinstance(container[key], (dict, list)):
            return container[key]
    return None


def find_booking(body):
    for container, key in walk(body):
        if key == "bookingRef":
            return container
    return None


def seed_storage(token: str) -> dict:
    """localStorage entries to set before the app loads."""
    return {"eventhub_token": token}


# ---------- API helpers ----------
def api_login(api) -> str:
    resp = api.post("auth/login", data={"email": EMAIL, "password": PASSWORD})
    assert resp.ok, f"Login failed: {resp.status} {resp.text()[:300]}"
    token = find_key(resp.json(), ["token", "accessToken", "jwt"])
    assert token, f"No token in login response: {resp.text()[:300]}"
    return token


def lookup_by_ref(api, ref: str):
    """Look a booking up by reference by scanning the booking list (returns None if gone)."""
    for page_no in range(1, 31):
        resp = api.get(f"bookings?page={page_no}&limit=10")
        assert resp.ok, f"List failed: {resp.status} {resp.text()[:300]}"
        items = find_list(resp.json()) or []
        for b in items:
            if b.get("bookingRef") == ref:
                return b
        if len(items) < 10:
            return None
    return None


def find_card(page: Page, ref: str):
    badge = page.get_by_text(ref, exact=True)
    expect(badge).to_have_count(1, timeout=30_000)
    return badge.locator("xpath=ancestor::*[.//*[normalize-space()='View Details']][1]")


def money(n) -> str:
    return f"${float(n):,.0f}" if float(n) == int(float(n)) else f"${float(n):,.2f}"


def test_api_booking_lifecycle(page: Page, playwright: Playwright):
    # Step 1: authenticated API session
    api = playwright.request.new_context(base_url=API_BASE)
    try:
        token = api_login(api)
        api.dispose()
        api = playwright.request.new_context(
            base_url=API_BASE, extra_http_headers={"Authorization": f"Bearer {token}"})

        # Step 2: runtime-selected event with >= 2 available seats
        resp = api.get("events?page=1&limit=50")
        assert resp.ok, f"Events failed: {resp.status} {resp.text()[:300]}"
        events = find_list(resp.json()) or []
        assert events, "No events returned"
        chosen = None
        for ev in events:
            seats = find_key(ev, ["availableSeats", "seatsAvailable", "seatsLeft",
                                  "availableTickets", "seats"])
            if isinstance(seats, (int, float)) and seats >= 2:
                chosen = ev
                break
        assert chosen, f"No event with >= 2 seats. First event keys: {list(events[0].keys())}"
        event = {
            "id": find_key(chosen, ["id"]),
            "title": find_key(chosen, ["title", "name"]),
            "category": find_key(chosen, ["category"]),
            "city": find_key(chosen, ["city"]),
            "price": find_key(chosen, ["price", "ticketPrice"]),
        }
        assert all(v is not None for v in event.values()), f"Missing event fields: {event}"

        # Step 3: create booking (quantity 2)
        create = api.post("bookings", data={"eventId": event["id"], "quantity": QTY, **CUSTOMER})
        assert create.ok, f"Create failed: {create.status} {create.text()[:400]}"

        # Step 4: id + reference present, store expected values
        created = find_booking(create.json())
        assert created, f"No booking object in create response: {create.text()[:400]}"
        booking = {
            "id": created["id"],
            "ref": created["bookingRef"],
            "quantity": created["quantity"],
            "total": created["totalPrice"],
        }
        assert booking["id"] and str(booking["ref"]).strip()
        assert booking["quantity"] == QTY
        expected_total = float(event["price"]) * QTY
        assert float(booking["total"]) == expected_total
        total_text = money(booking["total"])

        try:
            # Step 5: look up by reference and compare with create response
            found = lookup_by_ref(api, booking["ref"])
            assert found, "Booking not found by reference after creation"
            assert found["id"] == booking["id"]
            assert found["bookingRef"] == booking["ref"]
            assert found["quantity"] == booking["quantity"]
            assert float(found["totalPrice"]) == float(booking["total"])

            # Step 6: seed the browser session BEFORE the first navigation
            page.context.add_init_script(
                "const items = " + json.dumps(seed_storage(token)) + ";"
                "for (const [k, v] of Object.entries(items)) { localStorage.setItem(k, v); }"
            )
            page.goto("/bookings")
            expect(page.get_by_role("heading", name="My Bookings")).to_be_visible(timeout=30_000)
            card = find_card(page, booking["ref"])

            # Step 7
            expect(card).to_contain_text(event["title"])
            expect(card).to_contain_text(re.compile(rf"\b{QTY}\s*tickets?\b", re.I))
            expect(card).to_contain_text(total_text)

            # Step 8
            card.get_by_text("View Details").click()
            expect(page).to_have_url(re.compile(rf"/bookings/{booking['id']}$"))
            main = page.locator("main")
            expect(main.get_by_role("navigation")).to_contain_text(booking["ref"])
            expect(page.get_by_role("heading", level=1)).to_contain_text(event["title"])
            expect(main).to_contain_text(str(event["category"]))
            expect(main).to_contain_text(str(event["city"]))
            expect(main).to_contain_text(CUSTOMER["customerEmail"])
            summary = main.locator("xpath=//h2[normalize-space()='Payment Summary']/parent::*")
            expect(summary).to_contain_text(re.compile(rf"Tickets\s*{QTY}(?!\d)"))
            expect(summary).to_contain_text(total_text)
        finally:
            # Step 9: cancel through the API (runs even if a UI check failed)
            delete = api.delete(f"bookings/{booking['id']}")
            assert delete.ok, f"Cancel failed: {delete.status} {delete.text()[:300]}"

        # Step 10: no longer retrievable by reference (or a documented cancelled state)
        gone = lookup_by_ref(api, booking["ref"])
        assert gone is None or str(gone.get("status", "")).lower() == "cancelled", \
            f"Booking still retrievable: {gone}"

        # Step 11: UI shows no card for that reference
        page.goto("/bookings")
        expect(page.get_by_role("heading", name="My Bookings")).to_be_visible(timeout=30_000)
        expect(page.get_by_text(booking["ref"], exact=True)).to_have_count(0, timeout=30_000)
    finally:
        # Step 12: dispose the API session
        api.dispose()