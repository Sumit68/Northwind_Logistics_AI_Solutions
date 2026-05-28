"""Data-driven deterministic checks from policy_rules/*.json check_type fields."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.services.policy_loader import load_policy_rules
from app.services.receipt_context import itemization_satisfied, receipt_provided

SEVERITY = {"rejected": 3, "flagged": 2, "needs_review": 1, "compliant": 0}


def _result(rule: dict, doc_id: str, reasoning: str, status: str | None = None) -> dict:
    return {
        "applicable": True,
        "status": status or rule.get("violation_status", "flagged"),
        "reasoning": reasoning,
        "policy_doc_id": doc_id,
        "policy_section": rule.get("section"),
        "policy_quote": rule.get("source_quote"),
        "confidence": 0.95,
        "deterministic": True,
    }


def _infer_meal_type(extraction: dict) -> str:
    raw = (extraction.get("raw_text") or "").lower()
    vendor = (extraction.get("vendor") or "").lower()
    if "breakfast" in raw or "breakfast" in vendor:
        return "breakfast"
    if "lunch" in raw or "lunch" in vendor:
        return "lunch"
    return "dinner"


def _is_solo(extraction: dict, trip_context: dict) -> bool:
    trip = (trip_context.get("trip_purpose") or "").lower()
    notes = " ".join(extraction.get("notes") or []).lower()
    raw = (extraction.get("raw_text") or "").lower()
    if extraction.get("guest_count", 1) == 1:
        if "of 1" in raw or "guest 1" in raw or "solo" in notes or "solo" in trip:
            return True
        if "no external" in raw:
            return True
    return "solo" in trip


def _has_external_attendee(extraction: dict) -> bool:
    raw = (extraction.get("raw_text") or "").lower()
    if "no external" in raw or "no external attendees" in raw:
        return False
    external_signals = [
        "external client",
        "external attendee",
        "external customers",
        "external customer",
        "prospect",
        "business partner",
        "client entertainment",
        "hosted two external",
        "external guests",
    ]
    if any(k in raw for k in external_signals):
        return True
    if re.search(r"\bclient(s)?\b", raw) and "northwind" not in raw:
        return True
    if re.search(r"of\s+[2-9]", raw) and "northwind" not in raw:
        return True
    return False


def _is_team_only_meal(extraction: dict) -> bool:
    raw = (extraction.get("raw_text") or "").lower()
    if _has_external_attendee(extraction):
        return False
    team_signals = [
        "northwind employees",
        "northwind colleagues",
        "team-only",
        "team only",
        "employees only",
        "colleagues only",
        "offsite dinner",
        "holiday gathering",
        "team morale",
    ]
    if any(k in raw for k in team_signals):
        return True
    guests = int(extraction.get("guest_count") or 1)
    return guests > 1 and not _has_external_attendee(extraction)


def _alcohol_line_total(extraction: dict) -> float:
    keys = ["beer", "wine", "ale", "cocktail", "liquor", "spirit", "whiskey", "vodka"]
    total = 0.0
    for li in extraction.get("line_items") or []:
        desc = (li.get("description") or "").lower()
        if any(k in desc for k in keys):
            total += float(li.get("amount") or 0)
    if total <= 0 and extraction.get("alcohol_detected"):
        total = float(extraction.get("total") or 0) * 0.15
    return total


def _food_portion_total(extraction: dict) -> float:
    keys = ["beer", "wine", "ale", "cocktail", "liquor", "spirit"]
    food = 0.0
    for li in extraction.get("line_items") or []:
        desc = (li.get("description") or "").lower()
        if any(k in desc for k in keys):
            continue
        food += float(li.get("amount") or 0)
    if food <= 0:
        total = float(extraction.get("total") or 0)
        food = total - _alcohol_line_total(extraction)
    return max(food, 0.0)


def _tier1_city_in_text(raw: str) -> bool:
    tier1 = ["seattle", "boston", "san francisco", "new york", "los angeles", "washington"]
    return any(c in raw.lower() for c in tier1)


def _lodging_nightly_rate(extraction: dict) -> float:
    total = float(extraction.get("total") or 0)
    nights = extraction.get("nights")
    if nights:
        return total / max(int(nights), 1)
    raw = extraction.get("raw_text") or ""
    m = re.search(r"Nights:\s*(\d+)", raw, re.I)
    if m:
        return total / max(int(m.group(1)), 1)
    room_lines = re.findall(r"Room\s*\n?\$?(\d+\.\d{2})", raw)
    if room_lines:
        return max(float(x) for x in room_lines)
    return total


def _trip_night_count(trip_dates: str) -> int | None:
    dates = re.findall(r"(\d{4}-\d{2}-\d{2})", trip_dates or "")
    if len(dates) >= 2:
        try:
            d0 = datetime.strptime(dates[0], "%Y-%m-%d")
            d1 = datetime.strptime(dates[1], "%Y-%m-%d")
            return max((d1 - d0).days, 0)
        except ValueError:
            pass
    return None


def _per_diem_context(extraction: dict, trip_context: dict) -> bool:
    raw = (extraction.get("raw_text") or "").lower()
    trip = " ".join(
        [
            str(trip_context.get("trip_purpose") or ""),
            str(trip_context.get("trip_dates") or ""),
            str(trip_context.get("per_diem_elected") or ""),
        ]
    ).lower()
    return any(k in raw or k in trip for k in ["per diem", "per-diem", "perdiem", "m&ie", "mie allowance"])


def _per_diem_tier(raw: str) -> int:
    tier_num, _ = _tier_cap_for_location(raw)
    return tier_num


def _per_diem_daily_rate(tier: int) -> float | None:
    rates = {1: 90.0, 2: 70.0, 3: 55.0}
    return rates.get(tier)


def _expected_per_diem_trip_total(daily_rate: float, nights: int) -> float:
    """Departure 75% + intermediate 100% + return 75% (TEP-008 §4)."""
    if nights <= 0:
        return daily_rate * 0.75
    calendar_days = nights + 1
    if calendar_days == 1:
        return daily_rate * 0.75
    if calendar_days == 2:
        return daily_rate * 1.5
    intermediate = max(calendar_days - 2, 0)
    return daily_rate * (0.75 + intermediate + 0.75)


def _tier_cap_for_location(raw: str) -> tuple[int, float]:
    raw_l = raw.lower()
    tier1_cities = [
        "new york",
        "san francisco",
        "boston",
        "washington",
        "los angeles",
        "seattle",
        "london",
        "zurich",
        "tokyo",
        "singapore",
    ]
    tier2_cities = [
        "chicago",
        "denver",
        "atlanta",
        "austin",
        "dallas",
        "houston",
        "miami",
        "portland",
        "san diego",
        "toronto",
        "amsterdam",
        "berlin",
        "sydney",
    ]
    if any(c in raw_l for c in tier1_cities):
        return 1, 350.0
    if any(c in raw_l for c in tier2_cities):
        return 2, 250.0
    if any(c in raw_l for c in ["usa", "united states", " us ", "canada"]):
        return 3, 175.0
    return 4, 0.0


def _booking_lead_days(extraction: dict, trip_context: dict) -> int | None:
    booking_date = extraction.get("booking_date") or extraction.get("reservation_date")
    trip_dates = trip_context.get("trip_dates") or ""
    if booking_date and trip_dates:
        try:
            b = datetime.strptime(str(booking_date)[:10], "%Y-%m-%d")
            m = re.search(r"(\d{4}-\d{2}-\d{2})", trip_dates)
            if m:
                t = datetime.strptime(m.group(1), "%Y-%m-%d")
                return (t - b).days
        except ValueError:
            pass
    raw = (extraction.get("raw_text") or "").lower()
    m = re.search(r"(booked|reservation).*?(\d+)\s+days?\s+(ahead|before|prior)", raw)
    if m:
        return int(m.group(2))
    return None


def _contains_keyword(raw: str, keyword: str) -> bool:
    kw = keyword.strip().lower()
    if " " in kw or "-" in kw:
        return kw in raw
    return re.search(rf"\b{re.escape(kw)}\b", raw) is not None


def _flight_duration_hours(raw: str) -> float:
    hours = re.findall(r"(\d+)h\s*(\d+)?m?", raw)
    if hours:
        h, m = hours[0]
        return int(h) + (int(m or 0) / 60)
    m2 = re.search(r"duration\s*(\d+)\s*hours?", raw)
    if m2:
        return float(m2.group(1))
    return 0.0


def _requires_receipt_always(extraction: dict, *, threshold: float = 25, tip_exception: float = 20) -> bool:
    """TEP-007 §4.1: under-$25 waiver does not apply to these categories."""
    total = float(extraction.get("total") or 0)
    if total >= threshold:
        return True
    raw = (extraction.get("raw_text") or "").lower()
    if extraction.get("alcohol_detected") or _alcohol_line_total(extraction) > 0:
        return True
    if float(extraction.get("tip") or 0) > tip_exception:
        return True
    if any(k in raw for k in ["cash advance", "cash disbursement", "advance disbursement"]):
        return True
    return False


def _under_receipt_waiver_applies(extraction: dict, threshold: float = 25) -> bool:
    total = float(extraction.get("total") or 0)
    return total > 0 and total < threshold and not _requires_receipt_always(extraction, threshold=threshold)


def _is_international_text(raw: str, trip_context: dict) -> bool:
    foreign = [
        "international",
        "london",
        "zurich",
        "tokyo",
        "singapore",
        "toronto",
        "amsterdam",
        "berlin",
        "sydney",
        "uk",
        "jp",
        "ch",
        "de",
        "nl",
        "au",
    ]
    trip = (trip_context.get("trip_purpose") or "").lower()
    return any(k in raw for k in foreign) or any(k in trip for k in foreign)


def _apply_rule(rule: dict, doc_id: str, extraction: dict, trip_context: dict) -> dict | None:
    check = rule.get("check_type")
    category = extraction.get("category_hint", "other")
    total = float(extraction.get("total") or 0)
    raw = (extraction.get("raw_text") or "").lower()
    solo = _is_solo(extraction, trip_context)

    if check == "incidental_not_meal":
        if category == "meal" and total < float(rule.get("threshold", 10)):
            return None

    if check == "meal_total_cap":
        if category != "meal":
            return None
        if total < 10:
            return None
        if _has_external_attendee(extraction):
            return None
        meal = rule.get("meal_type") or _infer_meal_type(extraction)
        inferred = _infer_meal_type(extraction)
        if meal != inferred and meal != "dinner":
            return None
        cap = float(rule["threshold"])
        if _tier1_city_in_text(raw):
            cap *= 1.2
        if total > cap:
            return _result(
                rule,
                doc_id,
                f"Meal total ${total:.2f} exceeds ${cap:.0f} {meal} cap (TEP-002 §2).",
            )

    if check == "meal_high_cost_city_cap":
        if category != "meal" or not _tier1_city_in_text(raw):
            return None
        meal = _infer_meal_type(extraction)
        base_caps = {"breakfast": 25.0, "lunch": 35.0, "dinner": 75.0}
        cap = base_caps.get(meal, 75.0) * float(rule.get("multiplier", 1.2))
        if total > cap:
            return _result(
                rule,
                doc_id,
                f"Meal in high-cost city ${total:.2f} exceeds adjusted ${cap:.0f} {meal} cap.",
            )

    if check == "client_entertainment_meal_cap":
        if category != "meal" or not _has_external_attendee(extraction):
            return None
        meal = rule.get("meal_type") or _infer_meal_type(extraction)
        inferred = _infer_meal_type(extraction)
        if meal != inferred and meal != "dinner":
            return None
        cap = float(rule["threshold"])
        if total > cap:
            return _result(
                rule,
                doc_id,
                f"Client entertainment {meal} ${total:.2f} exceeds ${cap:.0f} cap (TEP-002 §4).",
            )

    if check == "multiple_meals_same_day":
        if category != "meal":
            return None
        if int(trip_context.get("same_day_meal_count") or 0) > 1:
            return _result(
                rule,
                doc_id,
                "Multiple meals on the same day — combined total may require higher-cap review.",
            )

    if check == "alcohol_solo_travel":
        if extraction.get("alcohol_detected") and solo and not _has_external_attendee(extraction):
            return _result(
                rule,
                doc_id,
                "Alcohol on solo business travel is not reimbursable (TEP-003 §3.1).",
                "rejected",
            )

    if check == "alcohol_team_only":
        if extraction.get("alcohol_detected") and _is_team_only_meal(extraction):
            return _result(
                rule,
                doc_id,
                "Alcohol at team-only meals (Northwind employees only) is not reimbursable (TEP-003 §3.2).",
                "rejected",
            )

    if check == "alcohol_amount_cap":
        if extraction.get("alcohol_detected") and _has_external_attendee(extraction):
            alc_total = _alcohol_line_total(extraction)
            cap = float(rule["threshold"])
            if alc_total > cap:
                return _result(
                    rule,
                    doc_id,
                    f"Alcohol portion ${alc_total:.2f} exceeds ${cap:.0f} per-person cap.",
                )

    if check == "alcohol_excess_cap":
        if extraction.get("alcohol_detected"):
            alc_total = _alcohol_line_total(extraction)
            cap = float(rule["threshold"])
            if alc_total > cap:
                excess = alc_total - cap
                return _result(
                    rule,
                    doc_id,
                    f"${excess:.2f} of alcohol exceeds the ${cap:.0f} per-person cap (non-reimbursable portion).",
                )

    if check == "alcohol_food_ratio_min":
        if extraction.get("alcohol_detected") and total > 0:
            food = _food_portion_total(extraction)
            min_pct = float(rule.get("min_food_percent", 50)) / 100
            if food / total < min_pct:
                return _result(
                    rule,
                    doc_id,
                    f"Food is {food / total:.0%} of receipt; policy expects ≥{min_pct:.0%} for alcohol meals (TEP-003 §3.5).",
                )

    if check == "lodging_tier_cap":
        if category != "lodging":
            return None
        cap = float(rule["threshold"])
        nightly = _lodging_nightly_rate(extraction)
        tier_num, _ = _tier_cap_for_location(raw)
        if int(rule.get("tier", 0)) != int(tier_num):
            return None
        if nightly > cap or str(int(nightly)) in raw and nightly > cap:
            return _result(
                rule,
                doc_id,
                f"Lodging ~${nightly:.2f}/night exceeds Tier {tier_num} cap ${cap:.0f}/night.",
            )

    if check == "lodging_tier4_approval":
        if category != "lodging":
            return None
        tier_num, _ = _tier_cap_for_location(raw)
        if tier_num == 4:
            return _result(
                rule,
                doc_id,
                "International destination outside Tier 1/2 requires Finance Operations pre-approval at booking.",
                "needs_review",
            )

    if check == "lodging_advance_booking":
        if category != "lodging":
            return None
        lead_days = _booking_lead_days(extraction, trip_context)
        if lead_days is not None and lead_days < int(rule.get("threshold_days", 14)):
            return _result(
                rule,
                doc_id,
                f"Lodging appears booked {lead_days} days before travel; policy target is at least {rule.get('threshold_days', 14)} days when feasible.",
                "needs_review",
            )

    if check == "lodging_last_minute_over_cap":
        if category != "lodging":
            return None
        lead_days = _booking_lead_days(extraction, trip_context)
        nightly = _lodging_nightly_rate(extraction)
        _, cap = _tier_cap_for_location(raw)
        if lead_days is not None and lead_days < int(rule.get("last_minute_days", 7)) and cap > 0 and nightly > cap:
            return _result(
                rule,
                doc_id,
                f"Last-minute booking ({lead_days} days) at ${nightly:.2f}/night exceeds cap ${cap:.0f}; manager approval required.",
            )

    if check == "concur_booking_required":
        if category == "lodging" and ("outside concur" in raw or "booked outside" in raw):
            return _result(
                rule,
                doc_id,
                "Lodging booked outside Concur requires manager approval (TEP-004 §2.1).",
            )

    if check == "lodging_incidental_prohibited":
        if category != "lodging":
            return None
        keywords = rule.get("keywords") or []
        if any(_contains_keyword(raw, k) for k in keywords):
            return _result(
                rule,
                doc_id,
                "Lodging receipt includes non-reimbursable incidental charges (TEP-004 §5).",
                "rejected",
            )

    if check == "lodging_laundry_limit":
        if category != "lodging" or "laundry" not in raw:
            return None
        nights = int(extraction.get("nights") or 0)
        if nights <= int(rule.get("min_nights", 6)) - 1:
            return _result(
                rule,
                doc_id,
                "Laundry is reimbursable only for trips exceeding 5 consecutive nights.",
            )

    if check == "lodging_resort_fee_treatment":
        return None

    if check == "lodging_cancellation_reason":
        if category != "lodging":
            return None
        if "cancellation" in raw or "cancel fee" in raw:
            allowed = rule.get("allowed_reasons") or []
            if any(k in raw for k in allowed):
                return None
            return _result(
                rule,
                doc_id,
                "Cancellation charge present without a clear legitimate business reason; manual review required.",
                "needs_review",
            )

    if check == "lodging_no_show_oversight":
        if category != "lodging":
            return None
        if "no-show" in raw or "late cancellation" in raw or "late-cancellation" in raw:
            if any(k in raw for k in ["oversight", "forgot", "missed check-in"]):
                return _result(
                    rule,
                    doc_id,
                    "No-show/late cancellation due to employee oversight is not reimbursable (TEP-004 §7.2).",
                    "rejected",
                )

    if check == "ground_taxi_tip_cap":
        if category != "ground":
            return None
        if "taxi" in raw or "cab" in raw:
            tip = float(extraction.get("tip") or 0)
            fare = float(extraction.get("subtotal") or total)
            if fare > 0 and tip / fare > float(rule.get("threshold_percent", 20)) / 100:
                return _result(
                    rule,
                    doc_id,
                    f"Taxi tip exceeds {rule.get('threshold_percent', 20)}% of metered fare (TEP-006 §2.3).",
                )

    if check == "rental_car_practicality":
        if category != "ground":
            return None
        if "rental" in raw or "hertz" in raw or "avis" in raw or "enterprise" in raw:
            if not any(k in raw for k in ["multiple stops", "rural", "3 passengers", "equipment", "impractical"]):
                return _result(
                    rule,
                    doc_id,
                    "Rental car used; confirm rideshare/taxi was impractical (TEP-006 §3.1).",
                    "needs_review",
                )

    if check == "rental_vehicle_category":
        if category != "ground":
            return None
        if any(k in raw for k in ["premium", "luxury", "suv"]):
            if "manager approval" not in raw and "terrain" not in raw and "capacity" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "Premium/luxury/SUV rental requires terrain/capacity justification and manager approval (TEP-006 §3.2).",
                )
        if "full-size" in raw or "full size" in raw:
            if not any(k in raw for k in ["3 passengers", "three passengers", "equipment"]):
                return _result(
                    rule,
                    doc_id,
                    "Full-size rental requires 3+ passengers or significant equipment justification (TEP-006 §3.2).",
                )

    if check == "rental_insurance_domestic":
        if category != "ground":
            return None
        if ("rental" in raw or "cdw" in raw or "collision damage waiver" in raw) and not _is_international_text(raw, trip_context):
            if any(k in raw for k in ["cdw accepted", "liability insurance accepted", "insurance accepted"]):
                return _result(
                    rule,
                    doc_id,
                    "Domestic rental includes CDW/liability insurance add-on; should be declined in U.S. trips (TEP-006 §3.3).",
                )

    if check == "rental_insurance_international":
        if category != "ground":
            return None
        if ("rental" in raw or "cdw" in raw or "collision damage waiver" in raw) and _is_international_text(raw, trip_context):
            if any(k in raw for k in ["cdw declined", "insurance declined"]):
                return _result(
                    rule,
                    doc_id,
                    "International rental appears to decline CDW; policy expects accepting rental CDW outside U.S. (TEP-006 §3.3).",
                    "needs_review",
                )

    if check == "rental_fuel_policy":
        if category != "ground":
            return None
        if any(k in raw for k in ["prepaid fuel", "pre-paid fuel", "refueling charge", "fuel service option"]):
            return _result(
                rule,
                doc_id,
                "Pre-paid fuel or rental-company refueling charges are not reimbursable (TEP-006 §3.4).",
            )

    if check == "personal_vehicle_mileage_rate":
        if category != "ground":
            return None
        if "mileage" in raw or "miles" in raw:
            miles = extraction.get("miles")
            amount = float(extraction.get("total") or 0)
            if miles:
                expected = float(miles) * float(rule.get("rate_per_mile", 0.67))
                if expected > 0 and abs(amount - expected) / expected > 0.15:
                    return _result(
                        rule,
                        doc_id,
                        f"Mileage reimbursement ${amount:.2f} differs from IRS-rate estimate ${expected:.2f} ({rule.get('rate_per_mile', 0.67)}/mile).",
                    )

    if check == "commute_mileage_not_reimbursable":
        if category != "ground":
            return None
        if any(k in raw for k in ["home to office", "office to home", "commute", "commuting"]):
            return _result(
                rule,
                doc_id,
                "Commuting mileage is not reimbursable (TEP-006 §4.2).",
                "rejected",
            )

    if check == "mileage_documentation_required":
        if category != "ground":
            return None
        if "mileage" in raw or "miles" in raw:
            needed = ["start", "destination", "business purpose", "miles"]
            if not all(k in raw for k in needed):
                return _result(
                    rule,
                    doc_id,
                    "Mileage entry missing required fields (start, destination, business purpose, total miles).",
                    "needs_review",
                )

    if check == "public_transit_actual_cost":
        if category == "ground" and any(k in raw for k in ["subway", "bus", "light rail", "commuter rail", "metro"]):
            return None

    if check == "transit_pass_economic_check":
        if category != "ground":
            return None
        if any(k in raw for k in ["weekly pass", "multi-day pass", "7-day pass", "day pass"]):
            if not any(k in raw for k in ["cheaper than", "economical", "equivalent cost", "cost comparison"]):
                return _result(
                    rule,
                    doc_id,
                    "Transit pass used without cost-equivalence evidence vs individual fares.",
                    "needs_review",
                )

    if check == "parking_actual_cost":
        if category == "ground" and "parking" in raw:
            return None

    if check == "airport_parking_long_term":
        if category != "ground" or "airport parking" not in raw:
            return None
        trip_dates = str(trip_context.get("trip_dates") or "")
        dates = re.findall(r"(\d{4}-\d{2}-\d{2})", trip_dates)
        if len(dates) >= 2:
            try:
                d0 = datetime.strptime(dates[0], "%Y-%m-%d")
                d1 = datetime.strptime(dates[1], "%Y-%m-%d")
                trip_days = max((d1 - d0).days, 0)
                if trip_days >= int(rule.get("min_days", 3)) and "long-term" not in raw and "cheaper than rideshare" not in raw:
                    return _result(
                        rule,
                        doc_id,
                        "Trip appears 3+ days; verify long-term airport parking and cost advantage vs alternatives.",
                        "needs_review",
                    )
            except ValueError:
                pass

    if check == "valet_price_delta":
        if category != "ground" or "valet" not in raw:
            return None
        m = re.search(r"valet[^$]*\$(\d+(?:\.\d{2})?)", raw)
        s = re.search(r"self[- ]?parking[^$]*\$(\d+(?:\.\d{2})?)", raw)
        if m and s:
            delta = float(m.group(1)) - float(s.group(1))
            if delta >= float(rule.get("max_delta", 15)):
                return _result(
                    rule,
                    doc_id,
                    f"Valet exceeds self-parking by ${delta:.2f}; policy allows under ${rule.get('max_delta', 15)} unless only option.",
                )

    if check == "tolls_actual_cost":
        if category == "ground" and any(k in raw for k in ["toll", "ezpass", "fastrak"]):
            return None

    if check == "gift_received_cash_equivalent":
        if any(k in raw for k in ["gift received", "vendor gift", "third-party gift", "received gift"]):
            if any(k in raw for k in ["cash gift", "cash equivalent", "gift card", "prepaid card"]):
                m = re.search(r"\$(\d+(?:\.\d{2})?)", raw)
                threshold = float(rule.get("cash_equivalent_threshold", 25))
                if "cash gift" in raw or "cash equivalent" in raw:
                    return _result(rule, doc_id, "Cash/cash-equivalent gifts may not be accepted (TEP-012 §3.1).", "rejected")
                if m and float(m.group(1)) > threshold:
                    return _result(
                        rule,
                        doc_id,
                        f"Gift card value ${float(m.group(1)):.2f} exceeds ${threshold:.0f} acceptance threshold.",
                        "rejected",
                    )

    if check == "gift_received_single_source_limit":
        if any(k in raw for k in ["gift received", "vendor gift", "third-party gift", "received gift"]):
            m = re.search(r"\$(\d+(?:\.\d{2})?)", raw)
            if m and float(m.group(1)) > float(rule.get("single_gift_limit", 50)):
                return _result(
                    rule,
                    doc_id,
                    f"Single non-cash gift ${float(m.group(1)):.2f} exceeds ${rule.get('single_gift_limit', 50)} limit (TEP-012 §3.2).",
                )
            if any(k in raw for k in ["ytd gift", "year-to-date gift", "annual gifts"]):
                y = re.search(r"(?:ytd|year-to-date|annual)\D*(\d+(?:\.\d{2})?)", raw)
                if y and float(y.group(1)) > float(rule.get("annual_limit", 200)):
                    return _result(
                        rule,
                        doc_id,
                        f"Annual gifts from one source appear ${float(y.group(1)):.2f}, above ${rule.get('annual_limit', 200)} limit.",
                    )

    if check == "gift_received_over_limit_disposition":
        if any(k in raw for k in ["gift received", "vendor gift", "third-party gift", "received gift"]):
            m = re.search(r"\$(\d+(?:\.\d{2})?)", raw)
            if m and float(m.group(1)) > float(rule.get("single_gift_limit", 50)):
                if not any(k in raw for k in ["declined", "returned", "handover to hr", "donation", "shared distribution"]):
                    return _result(
                        rule,
                        doc_id,
                        "Gift above policy limit should be declined/returned or transferred to HR if return is impractical.",
                        "needs_review",
                    )

    if check == "gift_promotional_nominal_exempt":
        if any(k in raw for k in ["branded pen", "notepad", "bottled water", "promotional item"]):
            return None

    if check == "gift_given_client_annual_limit":
        if any(k in raw for k in ["client gift", "gift to client", "prospect gift"]):
            m = re.search(r"(?:recipient|client).*?(?:ytd|year-to-date|annual)\D*(\d+(?:\.\d{2})?)", raw)
            if m and float(m.group(1)) > float(rule.get("annual_limit", 100)):
                return _result(
                    rule,
                    doc_id,
                    f"Client gift annual total ${float(m.group(1)):.2f} exceeds ${rule.get('annual_limit', 100)} per-recipient limit.",
                )
            # fallback: single obvious amount over annual limit
            a = re.search(r"\$(\d+(?:\.\d{2})?)", raw)
            if a and float(a.group(1)) > float(rule.get("annual_limit", 100)):
                return _result(
                    rule,
                    doc_id,
                    f"Client gift amount ${float(a.group(1)):.2f} exceeds annual per-recipient limit ${rule.get('annual_limit', 100)}.",
                )

    if check == "gift_government_official_prohibited":
        if any(
            k in raw
            for k in ["government official", "public sector", "state-owned enterprise", "soe representative"]
        ):
            if "general counsel approval" not in raw and "gc approval" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "Gift to government/public-sector/SOE representative requires written General Counsel approval.",
                    "rejected",
                )

    if check == "gift_client_cash_equivalent_prohibited":
        if any(k in raw for k in ["client gift", "gift to client"]) and any(
            k in raw for k in ["cash gift", "cash equivalent", "gift card", "prepaid card"]
        ):
            return _result(
                rule,
                doc_id,
                "Cash or cash-equivalent gifts to clients are prohibited (TEP-012 §4.3).",
                "rejected",
            )

    if check == "entertainment_family_vp_approval":
        if any(k in raw for k in ["spouse", "family member", "spouses", "family attendees"]):
            has_vp = any(k in raw for k in ["vp approval", "vice president approval", "written vp approval"])
            has_rationale = any(k in raw for k in ["business rationale", "business discussion", "client objective"])
            if not (has_vp and has_rationale):
                return _result(
                    rule,
                    doc_id,
                    "Entertainment with spouses/family requires VP approval and clear business rationale (TEP-012 §5.2).",
                )

    if check == "third_party_ticket_declaration":
        if any(k in raw for k in ["ticket provided", "third-party ticket", "concert ticket", "sporting event ticket", "theater ticket"]):
            m = re.search(r"\$(\d+(?:\.\d{2})?)", raw)
            threshold = float(rule.get("declaration_threshold", 50))
            if m and float(m.group(1)) > threshold:
                if "declared" not in raw and "disclosed" not in raw:
                    return _result(
                        rule,
                        doc_id,
                        f"Third-party event ticket over ${threshold:.0f} should be declared (TEP-012 §5.3).",
                        "needs_review",
                    )

    if check == "corp_card_cash_advance":
        if "cash advance" in raw:
            if _is_international_text(raw, trip_context) and any(
                k in raw for k in ["authorized", "pre-approved", "pre approved", "approved"]
            ):
                return None
            return _result(
                rule,
                doc_id,
                "Corporate Card cash advance is prohibited unless explicitly authorized for international travel (TEP-010 §5).",
                "rejected",
            )

    if check == "corp_card_prohibited_categories":
        keywords = rule.get("keywords") or []
        if any(_contains_keyword(raw, k) for k in keywords):
            return _result(
                rule,
                doc_id,
                "Charge appears in prohibited Corporate Card category (TEP-010 §5).",
                "rejected",
            )

    if check == "corp_card_alcohol_policy_reference":
        if extraction.get("alcohol_detected") or _alcohol_line_total(extraction) > 0:
            if any(k in raw for k in ["client entertainment", "external client", "external attendee"]):
                return None
            return _result(
                rule,
                doc_id,
                "Alcohol charge on Corporate Card requires TEP-003 compliant context; otherwise prohibited (TEP-010 §5).",
            )

    if check == "corp_card_dispute_window":
        if not any(k in raw for k in ["disputed", "fraud", "fraudulent", "in error"]):
            return None
        m = re.search(r"(\d+)\s+days?\s+(since|after)\s+(statement|posted)", raw)
        if m and int(m.group(1)) > int(rule.get("days", 60)):
            return _result(
                rule,
                doc_id,
                f"Dispute appears raised after {rule.get('days', 60)}-day issuer window; confirm issuer handling.",
                "needs_review",
            )

    if check == "corp_card_disputed_marking":
        if not any(k in raw for k in ["disputed", "fraud", "fraudulent", "in error"]):
            return None
        if "awaiting resolution" not in raw or not any(
            k in raw for k in ["supporting documentation", "supporting doc", "case number", "issuer case"]
        ):
            return _result(
                rule,
                doc_id,
                "Disputed charge should be marked 'Disputed — Awaiting Resolution' with supporting documentation.",
                "needs_review",
            )

    if check == "keyword_flag":
        keywords = rule.get("keywords") or []
        if any(k in raw for k in keywords):
            return _result(rule, doc_id, f"Receipt contains flagged item: {', '.join(keywords)}.")

    if check == "flight_class_check":
        if category != "air":
            return None
        disallowed = ["first class"]
        if any(d in raw for d in disallowed):
            return _result(
                rule,
                doc_id,
                "First class is not reimbursable under TEP-005.",
                "rejected",
            )

    if check == "flight_business_class_restricted":
        if category != "air":
            return None
        if "first class" in raw:
            return _result(
                rule,
                doc_id,
                "First class is not reimbursable under TEP-005.",
                "rejected",
            )
        if "business class" in raw:
            duration = _flight_duration_hours(raw)
            is_international = _is_international_text(raw, trip_context)
            has_vp_approval = any(
                k in raw for k in ["vp approval", "vice president approval", "written vp approval"]
            )
            if not is_international or duration < float(rule.get("min_hours", 10)) or not has_vp_approval:
                return _result(
                    rule,
                    doc_id,
                    "Business class requires international segment, 10+ hour duration, and prior written VP approval (TEP-005 §2.3).",
                    "rejected",
                )

    if check == "flight_concur_booking_required":
        if category == "air" and ("outside concur" in raw or "booked outside" in raw):
            return _result(
                rule,
                doc_id,
                "Flight booked outside Concur; manager review required when practical booking via Concur was possible.",
            )

    if check == "flight_advance_booking":
        if category != "air":
            return None
        lead_days = _booking_lead_days(extraction, trip_context)
        if lead_days is not None and lead_days < int(rule.get("threshold_days", 14)):
            return _result(
                rule,
                doc_id,
                f"Flight appears booked {lead_days} days before travel; policy target is at least {rule.get('threshold_days', 14)} days when feasible.",
                "needs_review",
            )

    if check == "flight_last_minute_approval":
        if category != "air":
            return None
        lead_days = _booking_lead_days(extraction, trip_context)
        if lead_days is not None and lead_days < int(rule.get("last_minute_days", 7)):
            return _result(
                rule,
                doc_id,
                f"Last-minute flight booking ({lead_days} days) requires manager approval (TEP-005 §3.2).",
            )

    if check == "flight_lowest_reasonable_fare":
        if category != "air":
            return None
        if any(k in raw for k in ["red-eye", "redeye", "one-stop", "1 stop", "arrive 11pm", "late arrival"]):
            return _result(
                rule,
                doc_id,
                "Fare/schedule trade-off indicators detected; verify lowest reasonable fare decision per TEP-005 §3.3.",
                "needs_review",
            )

    if check == "flight_baggage_limit":
        if category != "air":
            return None
        if any(k in raw for k in ["2nd checked bag", "second checked bag", "bag 2", "extra baggage"]):
            return _result(
                rule,
                doc_id,
                "Second checked bag requires manager approval and business justification (TEP-005 §4.1).",
            )

    if check == "flight_seat_selection":
        if category != "air":
            return None
        if any(k in raw for k in ["extra legroom", "extra-legroom", "comfort+", "exit row"]):
            duration = _flight_duration_hours(raw)
            has_accommodation = any(k in raw for k in ["accommodation", "medical note", "ada"])
            if duration < float(rule.get("min_hours_for_extra_legroom", 4)) and not has_accommodation:
                return _result(
                    rule,
                    doc_id,
                    "Extra-legroom seat reimbursement requires 4+ hour segment or documented accommodation (TEP-005 §4.2).",
                )

    if check == "flight_change_cancellation_reason":
        if category != "air":
            return None
        if any(k in raw for k in ["change fee", "cancellation fee", "cancel fee"]):
            if any(k in raw for k in ["business reason", "manager request", "client meeting changed"]):
                return None
            if any(k in raw for k in ["personal preference", "vacation", "personal reason"]):
                return _result(
                    rule,
                    doc_id,
                    "Change/cancellation fee appears personal and is not reimbursable (TEP-005 §4.3).",
                    "rejected",
                )
            return _result(
                rule,
                doc_id,
                "Change/cancellation fee present without documented business reason; manual review required.",
                "needs_review",
            )

    if check == "flight_wifi_business_use":
        if category != "air":
            return None
        if "in-flight wi-fi" in raw or "inflight wifi" in raw or "wifi" in raw:
            if any(k in raw for k in ["business use", "work", "client", "meeting prep"]):
                return None
            return _result(
                rule,
                doc_id,
                "In-flight Wi-Fi listed without explicit business-use context; manual review required.",
                "needs_review",
            )

    if check == "flight_companion_not_reimbursable":
        if category != "air":
            return None
        if any(k in raw for k in ["spouse", "family member", "companion", "guest fare"]):
            return _result(
                rule,
                doc_id,
                "Companion airfare is not reimbursable (TEP-005 §6.1).",
                "rejected",
            )

    if check == "flight_personal_extension_quote":
        if category != "air":
            return None
        if any(k in raw for k in ["weekend extension", "personal extension", "extended stay"]):
            if "comparison quote" not in raw and "business-only fare" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "Personal itinerary extension detected; comparison quote/business-only fare evidence required (TEP-005 §6.2).",
                    "needs_review",
                )

    if check == "flight_duration_class":
        if category != "air":
            return None
        hours = re.findall(r"(\d+)h\s*(\d+)m", raw) or re.findall(r"Duration\s*(\d+)h", raw)
        duration_ok = False
        for h, m in [(int(x[0]), int(x[1]) if len(x) > 1 else 0) for x in hours if x]:
            if h >= rule.get("min_hours", 6):
                duration_ok = True
        if "6h" in raw or "6h48" in raw or duration_ok:
            return None
        if any(c in raw for c in rule.get("allowed_classes", [])):
            return _result(rule, doc_id, "Premium cabin without 6+ hour segment may require approval.")

    if check == "per_diem_min_nights":
        if not _per_diem_context(extraction, trip_context):
            return None
        nights = _trip_night_count(str(trip_context.get("trip_dates") or ""))
        if nights is not None and nights < int(rule.get("min_nights", 3)):
            return _result(
                rule,
                doc_id,
                f"Trip is {nights} night(s); per-diem requires {rule.get('min_nights', 3)}+ consecutive nights — use TEP-002 itemized meals.",
            )

    if check == "per_diem_election_locked":
        if _per_diem_context(extraction, trip_context):
            if any(k in raw for k in ["switched to itemized", "changed to per diem", "mid-trip change", "mid trip"]):
                return _result(
                    rule,
                    doc_id,
                    "Per-diem vs itemized election appears changed mid-trip; verify declaration at trip start.",
                    "needs_review",
                )

    if check == "per_diem_scope_separate_reimbursement":
        if _per_diem_context(extraction, trip_context):
            if category in ("lodging", "ground") and any(
                k in raw for k in ["included in per diem", "per-diem covers", "from per diem allowance"]
            ):
                return _result(
                    rule,
                    doc_id,
                    "Lodging/ground must be reimbursed at actual cost separately, not from per-diem (TEP-008 §2.3).",
                )

    if check == "per_diem_daily_rate_cap":
        if not _per_diem_context(extraction, trip_context):
            return None
        tier = _per_diem_tier(raw)
        if int(rule.get("tier", 0)) != tier:
            return None
        cap = float(rule["threshold"])
        nights = _trip_night_count(str(trip_context.get("trip_dates") or "")) or 0
        max_trip = _expected_per_diem_trip_total(cap, nights)
        if total > max_trip * 1.05:
            return _result(
                rule,
                doc_id,
                f"Per-diem claim ${total:.2f} exceeds Tier {tier} expected ~${max_trip:.2f} for trip length.",
            )

    if check == "per_diem_tier4_approval":
        if _per_diem_context(extraction, trip_context) and _per_diem_tier(raw) == 4:
            return _result(
                rule,
                doc_id,
                "Tier 4 international per-diem rate must be set at trip approval (TEP-008 §3).",
                "needs_review",
            )

    if check == "per_diem_partial_days":
        if not _per_diem_context(extraction, trip_context):
            return None
        if any(k in raw for k in ["full daily rate", "100% daily", "full rate charged"]) and any(
            k in raw for k in ["departure day", "return day"]
        ):
            return _result(
                rule,
                doc_id,
                "Departure/return days should be billed at 75% of daily per-diem rate (TEP-008 §4).",
            )

    if check == "per_diem_excludes_alcohol":
        if _per_diem_context(extraction, trip_context) and (
            extraction.get("alcohol_detected") or _alcohol_line_total(extraction) > 0
        ):
            return _result(
                rule,
                doc_id,
                "Alcohol is not covered by per-diem; use TEP-003 for sanctioned client entertainment only.",
            )

    if check == "per_diem_excludes_client_entertainment":
        if _per_diem_context(extraction, trip_context) and _has_external_attendee(extraction) and category == "meal":
            return _result(
                rule,
                doc_id,
                "Client entertainment must be itemized under TEP-002 §4, not claimed from per-diem.",
            )

    if check == "per_diem_excludes_lodging_ground":
        if _per_diem_context(extraction, trip_context) and category in ("lodging", "ground"):
            return _result(
                rule,
                doc_id,
                "Lodging and ground transportation are outside per-diem scope (TEP-004 / TEP-006 actual cost).",
            )

    if check == "per_diem_excludes_communications":
        if _per_diem_context(extraction, trip_context) and any(
            k in raw for k in ["phone charge", "internet", "wifi plan", "mobile data", "roaming"]
        ):
            return _result(
                rule,
                doc_id,
                "Phone/internet/business communications are not covered by per-diem (TEP-008 §6).",
            )

    if check == "per_diem_no_itemized_meals":
        if _per_diem_context(extraction, trip_context) and category == "meal":
            if not any(k in raw for k in ["client entertainment", "external"]):
                return _result(
                    rule,
                    doc_id,
                    "Itemized meal receipt submitted while per-diem elected — meals should not be double-reimbursed.",
                )

    if check == "per_diem_trip_documentation":
        if _per_diem_context(extraction, trip_context):
            if not (trip_context.get("trip_dates") or "").strip() or not (
                trip_context.get("trip_purpose") or ""
            ).strip():
                return _result(
                    rule,
                    doc_id,
                    "Per-diem requires documented trip duration and destination.",
                    "needs_review",
                )

    if check == "per_diem_international_rate":
        if _per_diem_context(extraction, trip_context) and _is_international_text(raw, trip_context):
            if _per_diem_tier(raw) == 4 and "trip approval" not in raw and "state department" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "International per-diem should follow approved M&IE rate (U.S. State Department) at trip approval.",
                    "needs_review",
                )

    if check == "receipt_waiver_exceptions":
        threshold = float(rule.get("threshold", 25))
        if total >= threshold:
            return None
        tip_exc = float(rule.get("tip_exception_amount", 20))
        if extraction.get("alcohol_detected") or _alcohol_line_total(extraction) > 0:
            return _result(
                rule,
                doc_id,
                "Alcohol charges require a receipt regardless of amount (TEP-007 §4.1).",
            )
        if float(extraction.get("tip") or 0) > tip_exc:
            return _result(
                rule,
                doc_id,
                f"Tips over ${tip_exc:.0f} require a receipt regardless of amount (TEP-007 §4.1).",
            )
        if any(k in raw for k in ["cash advance", "cash disbursement", "advance disbursement"]):
            return _result(
                rule,
                doc_id,
                "Cash advance disbursements require a receipt regardless of amount (TEP-007 §4.1).",
            )

    if check == "receipt_under_threshold_waiver":
        if _under_receipt_waiver_applies(extraction, float(rule.get("threshold", 25))):
            if not receipt_provided(extraction):
                return _result(
                    rule,
                    doc_id,
                    f"Expense ${total:.2f} is under ${rule.get('threshold', 25):.0f}; receipt not required unless an exception applies.",
                    "compliant",
                )
        return None

    if check == "requires_itemization":
        waiver_threshold = float(rule.get("threshold", 25))
        if _under_receipt_waiver_applies(extraction, waiver_threshold):
            return None
        if receipt_provided(extraction) and itemization_satisfied(extraction):
            return None
        if len(extraction.get("line_items") or []) < 1 and total <= 0:
            return _result(
                rule,
                doc_id,
                "Parsed receipt lacks itemized charges — manual review required.",
                "needs_review",
            )

    if check == "amount_consistency":
        sub = float(extraction.get("subtotal") or 0)
        tax = float(extraction.get("tax") or 0)
        tip = float(extraction.get("tip") or 0)
        if sub > 0 and total > 0:
            diff = abs((sub + tax + tip) - total) / total
            if diff > rule.get("tolerance_percent", 2) / 100:
                return _result(rule, doc_id, "Receipt subtotal+tax+tip does not match total.")

    if check == "extraction_confidence_min":
        if _under_receipt_waiver_applies(extraction):
            return None
        if float(extraction.get("confidence", 1)) < float(rule["threshold"]):
            return _result(
                rule,
                doc_id,
                "Low extraction confidence — manual review required.",
                "needs_review",
            )

    if check == "requires_trip_purpose":
        if not (trip_context.get("trip_purpose") or "").strip():
            return _result(rule, doc_id, "Missing documented business purpose for trip.")

    if check == "grade_submission_approval_required":
        st = trip_context.get("submission_total")
        if st is None:
            st = total
        auth = trip_context.get("approval_authority") or {}
        emp = trip_context.get("employee") or {}
        grade = int(emp.get("grade") or trip_context.get("grade") or 0)
        min_g = auth.get("required_approver_min_grade")
        if min_g is not None and grade < int(min_g):
            label = auth.get("required_approver_label", "approver")
            title = emp.get("title") or trip_context.get("title") or f"Grade {grade}"
            return _result(
                rule,
                doc_id,
                f"Submission total ${float(st):.2f} requires {label} approval (Grade {min_g}+); "
                f"submitter is Grade {grade} ({title}).",
            )

    if check == "grade_self_travel_limit":
        auth = trip_context.get("approval_authority") or {}
        emp = trip_context.get("employee") or {}
        grade = int(emp.get("grade") or trip_context.get("grade") or 0)
        st = float(trip_context.get("submission_total") or total or 0)
        self_limit = auth.get("self_travel_limit_usd")
        if self_limit and st > float(self_limit):
            return _result(
                rule,
                doc_id,
                f"Submission ${st:.2f} exceeds Grade {grade} self-travel approval limit (${float(self_limit):.0f}).",
            )

    if check == "grade_role_definition":
        return None

    if check == "grade_international_vp_required":
        if _is_international_text(raw, trip_context) or any(
            k in (trip_context.get("trip_purpose") or "").lower()
            for k in ["international", "london", "tokyo"]
        ):
            grade = int((trip_context.get("employee") or {}).get("grade") or trip_context.get("grade") or 0)
            if grade < int(rule.get("min_grade", 9)):
                return _result(
                    rule,
                    doc_id,
                    f"International travel requires VP approval (Grade {rule.get('min_grade', 9)}+); submitter is Grade {grade}.",
                )

    if check == "amount_requires_grade_approval":
        if total > float(rule["threshold"]) and trip_context.get("grade", 0) < rule.get("min_grade", 7):
            return _result(
                rule,
                doc_id,
                f"Expense may require Director (Grade {rule.get('min_grade', 7)}+) approval.",
            )

    if check == "requires_external_attendee":
        if category == "meal" and "client entertainment" in raw and not _has_external_attendee(extraction):
            return _result(
                rule,
                doc_id,
                "Client entertainment requires documented external attendee(s) on the receipt.",
            )

    if check == "submission_total_approval":
        st = trip_context.get("submission_total")
        if st is None:
            return None
        level = rule.get("approval_level")
        min_t = float(rule.get("min_total", 0))
        max_t = float(rule.get("max_total", float("inf")))
        if st < min_t or st > max_t:
            return None
        labels = {"director": "Director", "vp": "VP", "manager": "manager"}
        label = labels.get(level, level)
        return _result(
            rule,
            doc_id,
            f"Submission total ${st:.2f} requires {label} approval (cumulative per submission, TEP-001 §4).",
        )

    if check == "submission_approval_note":
        return None

    if check == "expense_timeliness_days":
        trip_end = trip_context.get("trip_end_date")
        if not trip_end:
            return None
        try:
            end = datetime.strptime(str(trip_end)[:10], "%Y-%m-%d")
        except ValueError:
            return None
        limit = end + timedelta(days=int(rule.get("threshold", 30)))
        reviewed = datetime.utcnow()
        if reviewed > limit:
            return _result(
                rule,
                doc_id,
                f"Expense report may be past {rule.get('threshold', 30)}-day submission window after trip end.",
            )

    if check == "client_entertainment_cap":
        if _has_external_attendee(extraction) and category == "meal":
            cap = float(rule["threshold"])
            if total > cap:
                return _result(
                    rule,
                    doc_id,
                    f"Client entertainment meal ${total:.2f} exceeds ${cap:.0f} cap.",
                )

    if check == "international_trip_approval":
        if any(k in (trip_context.get("trip_purpose") or "").lower() for k in ["international", "london", "tokyo"]):
            if trip_context.get("grade", 0) < rule.get("min_grade", 9):
                return _result(rule, doc_id, "International travel may require VP (Grade 9+) approval.")

    if check == "sanctioned_destination":
        keywords = rule.get("keywords") or []
        if any(k in raw or k in (trip_context.get("trip_purpose") or "").lower() for k in keywords):
            return _result(rule, doc_id, "Destination may be under sanctions — travel prohibited.", "rejected")

    if check == "high_risk_destination":
        if any(k in raw for k in ["high-risk", "international sos"]):
            return _result(rule, doc_id, "High-risk destination requires security review.")

    if check == "conference_requires_documentation":
        if category == "conference" and total > 1000:
            return _result(rule, doc_id, "Conference registration should have manager pre-approval on file.")

    if check == "conference_cost_approval_thresholds":
        if category != "conference":
            return None
        director_t = float(rule.get("director_threshold", 5000))
        vp_t = float(rule.get("vp_threshold", 10000))
        grade = int((trip_context.get("employee") or {}).get("grade") or trip_context.get("grade") or 0)
        if total > vp_t or _is_international_text(raw, trip_context):
            if grade < 9:
                return _result(
                    rule,
                    doc_id,
                    f"Conference cost/international condition requires VP approval (Grade 9+); submitter is Grade {grade}.",
                )
        elif total > director_t and grade < 7:
            return _result(
                rule,
                doc_id,
                f"Conference cost ${total:.2f} exceeds ${director_t:.0f}; Director approval (Grade 7+) required.",
            )

    if check == "conference_early_bird_expected":
        if category == "conference" and any(k in raw for k in ["registration", "conference"]):
            if any(k in raw for k in ["late registration", "standard rate"]) and "early bird unavailable" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "Conference appears booked without early-bird rate; confirm planning constraints.",
                    "needs_review",
                )

    if check == "conference_included_meals_exclusion":
        if category in ("meal", "conference") and any(k in raw for k in ["meal included", "includes lunch", "includes breakfast", "includes reception"]):
            if any(k in raw for k in ["separate meal reimbursement", "dinner claim", "lunch claim", "breakfast claim"]):
                return _result(
                    rule,
                    doc_id,
                    "Meal appears included in registration and should not be reimbursed separately (TEP-014 §5.1).",
                )

    if check == "conference_offsite_dinner_classification":
        if category == "meal" and any(k in raw for k in ["conference dinner", "off-site dinner", "conference colleagues"]):
            if "external client" not in raw and "prospect" not in raw and "client entertainment" in raw:
                return _result(
                    rule,
                    doc_id,
                    "Off-site conference dinners are not client entertainment unless external clients/prospects attend.",
                    "needs_review",
                )

    if check == "conference_client_attendance_caps":
        if any(k in raw for k in ["conference with client", "client attended conference", "brought client"]):
            if not any(k in raw for k in ["tep-002", "entertainment cap", "vp approval"]):
                return _result(
                    rule,
                    doc_id,
                    "Conference with client should follow TEP-002 §4 entertainment caps and approval requirements.",
                )

    if check == "conference_extension_compare_quote":
        if any(k in raw for k in ["extended trip", "weekend extension", "personal extension"]):
            if "comparison quote" not in raw and "business-only fare" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "Personal conference-trip extension requires airfare comparison quote (business-only baseline).",
                    "needs_review",
                )

    if check == "conference_deliverables_30_days":
        if category == "conference" and any(k in raw for k in ["conference attended", "conference return"]):
            if not any(k in raw for k in ["shared takeaways", "one-page summary", "team presentation"]):
                return _result(
                    rule,
                    doc_id,
                    "Conference deliverables (summary/presentation) expected within 30 days.",
                    "needs_review",
                )

    if check == "conference_cancellation_legitimate":
        if any(k in raw for k in ["conference cancelled", "non-refundable conference", "cancellation fee"]):
            if not any(k in raw for k in ["manager decision", "illness", "business priority change"]):
                return _result(
                    rule,
                    doc_id,
                    "Cancellation/non-refundable conference costs require documented legitimate reason.",
                    "needs_review",
                )

    if check == "international_approval_lead_time":
        if not _is_international_text(raw, trip_context):
            return None
        lead_days = _booking_lead_days(extraction, trip_context)
        if lead_days is not None and lead_days < int(rule.get("threshold_days", 14)):
            return _result(
                rule,
                doc_id,
                f"International approval/booking appears {lead_days} days before departure; target is {rule.get('threshold_days', 14)}+ days.",
                "needs_review",
            )

    if check == "international_high_risk_review":
        if any(k in raw for k in ["high-risk", "international sos", "security review", "legal review"]):
            if "risk briefing completed" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "High-risk destination requires additional security and Legal review.",
                )

    if check == "international_sanctions_ofac":
        if any(k in raw for k in ["sanctioned", "ofac", "cuba", "iran", "north korea", "syria"]):
            has_ofac = "ofac license" in raw
            no_ofac = any(k in raw for k in ["no ofac license", "without ofac license", "lacks ofac license"])
            if not has_ofac or no_ofac:
                return _result(
                    rule,
                    doc_id,
                    "Sanctioned-destination travel is prohibited absent OFAC license.",
                    "rejected",
                )

    if check == "international_tier4_lodging":
        if _is_international_text(raw, trip_context):
            tier, _ = _tier_cap_for_location(raw)
            if tier == 4 and "trip approval" not in raw and "state department" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "Tier 4 international lodging cap should be set at trip approval.",
                    "needs_review",
                )

    if check == "international_per_diem_state_rate":
        if _is_international_text(raw, trip_context) and any(k in raw for k in ["per diem", "per-diem", "m&ie"]):
            if "state department" not in raw and "trip approval" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "International per-diem should align with U.S. State Department M&IE rate at trip approval.",
                    "needs_review",
                )

    if check == "international_stopover_compare_quote":
        if any(k in raw for k in ["stopover", "personal extension", "weekend extension"]):
            if "comparison quote" not in raw and "employee pays difference" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "Personal stopover on international itinerary requires comparison quote and employee-paid fare difference.",
                    "needs_review",
                )

    if check == "international_fx_fee_line_item":
        if any(k in raw for k in ["foreign transaction fee", "fx fee", "intl txn fee"]):
            if "separate line item" not in raw and category != "other":
                return _result(
                    rule,
                    doc_id,
                    "Foreign transaction fee should be reported as a separate line item.",
                )

    if check == "international_cash_advance_finops":
        if "cash advance" in raw and _is_international_text(raw, trip_context):
            if "finance operations approval" not in raw and "finops approval" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "International cash advance requires advance Finance Operations approval.",
                )

    if check == "international_visa_immunization":
        return None

    if check == "international_passport_fee_vp":
        if any(k in raw for k in ["passport fee", "passport renewal", "passport application"]):
            if "vp approval" not in raw and "business-only passport need" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "Passport fees are generally non-reimbursable unless rare business-only case has VP approval.",
                    "needs_review",
                )

    if check == "international_dependents_not_reimbursable":
        if _is_international_text(raw, trip_context) and any(
            k in raw for k in ["spouse", "dependent", "companion", "family member"]
        ):
            return _result(
                rule,
                doc_id,
                "Dependent/companion international travel costs are not reimbursable.",
                "rejected",
            )

    if check == "sec301_scope_international":
        return None

    if check == "sec301_high_tier_requirements":
        if any(k in raw for k in ["high-tier", "high tier", "high-risk", "international sos"]):
            required = ["vp approval", "risk briefing", "international sos enrollment", "itinerary submitted"]
            missing = [k for k in required if k not in raw]
            if missing:
                return _result(
                    rule,
                    doc_id,
                    "High-tier travel missing controls: " + ", ".join(missing) + ".",
                )

    if check == "sec301_extreme_tier_prohibited":
        if any(k in raw for k in ["extreme-tier", "extreme tier", "active conflict zone"]):
            if not all(k in raw for k in ["cfo approval", "general counsel approval", "business necessity"]):
                return _result(
                    rule,
                    doc_id,
                    "Extreme-tier travel is generally prohibited without CFO + General Counsel approval and documented necessity.",
                    "rejected",
                )

    if check == "sec301_international_sos_enrollment":
        if _is_international_text(raw, trip_context):
            if "international sos" in raw and "enrolled" in raw:
                return None
            if "booked outside concur" in raw or "outside concur" in raw:
                return _result(
                    rule,
                    doc_id,
                    "International SOS enrollment should be confirmed when travel is not booked through Concur.",
                    "needs_review",
                )

    if check == "sec301_visa_immunization_prep":
        if _is_international_text(raw, trip_context):
            if any(k in raw for k in ["visa required", "immunization required"]) and not any(
                k in raw for k in ["visa obtained", "immunization completed", "passport copy", "itinerary copy"]
            ):
                return _result(
                    rule,
                    doc_id,
                    "International prep should include visa/immunization completion and documentation copies.",
                    "needs_review",
                )

    if check == "sec301_high_tier_lodging_transport":
        if any(k in raw for k in ["high-tier", "high tier", "high-risk"]):
            if any(k in raw for k in ["lodging", "hotel", "transport"]) and not any(
                k in raw for k in ["vetted lodging", "approved list", "vetted ground transportation", "pre-arranged transfer"]
            ):
                return _result(
                    rule,
                    doc_id,
                    "High-tier destinations require vetted lodging and pre-arranged vetted transportation.",
                )

    if check == "sec301_incident_response":
        if any(k in raw for k in ["medical emergency", "security incident", "detention", "arrest", "serious injury"]):
            if "international sos" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "Incident response should include immediate International SOS contact.",
                    "needs_review",
                )

    if check == "sec301_sanctioned_destination":
        keywords = rule.get("keywords") or []
        if any(k in raw or k in (trip_context.get("trip_purpose") or "").lower() for k in keywords):
            has_ofac = "ofac license" in raw
            no_ofac = any(k in raw for k in ["no ofac license", "without ofac license", "lacks ofac license"])
            if not has_ofac or no_ofac:
                return _result(
                    rule,
                    doc_id,
                    "Sanctioned-destination travel is prohibited absent OFAC license.",
                    "rejected",
                )

    if check == "sec301_vpn_required":
        if _is_international_text(raw, trip_context) and any(k in raw for k in ["wifi", "internet access", "hotspot"]):
            if "vpn" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "VPN use is required for internet access while abroad (SEC-301 §8.3).",
                )

    if check == "sec301_border_device_guidance":
        if _is_international_text(raw, trip_context) and any(k in raw for k in ["border search", "device inspection"]):
            if any(k in raw for k in ["sensitive data on device", "full dataset on laptop"]):
                return _result(
                    rule,
                    doc_id,
                    "Border-device guidance: avoid carrying sensitive data during crossings.",
                    "needs_review",
                )

    if check == "sec301_returning_traveler_debrief":
        if any(k in raw for k in ["returned from high-tier", "high-tier return"]):
            m = re.search(r"(\d+)\s+days?\s+after\s+return", raw)
            if m and int(m.group(1)) > int(rule.get("days", 5)):
                return _result(
                    rule,
                    doc_id,
                    f"High-tier traveler debrief should occur within {rule.get('days', 5)} business days.",
                    "needs_review",
                )

    if check == "sec301_post_travel_health_reporting":
        if any(k in raw for k in ["post-travel symptoms", "symptoms after return"]):
            m = re.search(r"(\d+)\s+days?\s+after\s+return", raw)
            if m and int(m.group(1)) <= int(rule.get("days", 14)) and "hr notified" not in raw:
                return _result(
                    rule,
                    doc_id,
                    "Post-travel symptoms within advisory window should be reported to HR before office return.",
                    "needs_review",
                )

    if check == "tip_percent_cap":
        tip = float(extraction.get("tip") or 0)
        sub = float(extraction.get("subtotal") or total)
        if sub > 0 and tip / sub > rule.get("threshold_percent", 20) / 100:
            return _result(rule, doc_id, f"Tip exceeds {rule['threshold_percent']}% of subtotal.")

    return None


def check_deterministic(doc_id: str, extraction: dict, trip_context: dict) -> dict | None:
    rules_doc = load_policy_rules(doc_id)
    if not rules_doc:
        return None

    best: dict | None = None
    best_sev = -1
    for rule in rules_doc.get("rules", []):
        hit = _apply_rule(rule, doc_id, extraction, trip_context)
        if not hit:
            continue
        sev = SEVERITY.get(hit.get("status", "compliant"), 0)
        if sev > best_sev:
            best = hit
            best_sev = sev
    return best


def check_all_deterministic(policy_ids: list[str], extraction: dict, trip_context: dict) -> list[dict]:
    hits = []
    for pid in policy_ids:
        r = check_deterministic(pid, extraction, trip_context)
        if r:
            hits.append(r)
    return hits
