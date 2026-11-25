"""v1.8.8 easier master entry: the next code is filled in by itself; State -> City -> PIN from India Post's open data."""
from __future__ import annotations

import re

from sqlalchemy import select

from app.models import Customer, Location


def test_new_customer_gets_the_next_code_and_city_keeps_only_the_place(web, db):
    w = web("admin")
    loc = db.scalar(select(Location).where(Location.code == "RPR"))
    w.post("/masters/customers/save", {"code": "ZQT0099", "name": "Code Test Pharma", "location_id": str(loc.id),
                                       "state": "Chhattisgarh", "city": "Guma|493196", "pincode": "493196",
                                       "is_active": "1"})
    c = db.scalar(select(Customer).where(Customer.code == "ZQT0099"))
    assert (c.state, c.city, c.pincode) == ("Chhattisgarh", "Guma", "493196")

    page = w.get("/masters/customers/new").text
    assert re.search(r'name="code" value="ZQT0100"', page) and "the last one entered" in page
    assert 'data-geo-state' in page and '<option value="Chhattisgarh"' in page and "data-geo-city" in page
    assert page.index('name="state"') < page.index('name="city"') < page.index('name="pincode"')

    edit = w.get(f"/masters/customers/{c.id}").text  # an existing city stays chosen
    assert 'data-current="Guma"' in edit and '<option value="Chhattisgarh" selected' in edit


def test_phone_scan_box_is_never_locked_after_a_scan(web):
    # app.js disables a form's buttons for 8 s after a submit; a disabled Enter button stops a Bluetooth scanner's Enter
    page = web("admin").get("/gate/phone").text
    assert re.search(r'<form id="manual"[^>]*data-allow-resubmit', page)


def test_state_files_hold_places_districts_and_pins(web):
    data = web("admin").get("/static/geo/chhattisgarh.json").json()
    assert data["state"] == "Chhattisgarh"
    places = {(p, d, pin) for p, d, pin in data["places"]}
    assert ("Guma", "Baloda Bazar", "493196") in places and ("Raipur", "Raipur", "492001") in places
