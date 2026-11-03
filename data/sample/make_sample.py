"""Build the synthetic sample in data/sample/. Every row is invented; nothing is copied from a real export.

    python data/sample/make_sample.py

The files have exactly the shapes of the real pulls, so --dry-run and the tests exercise
the same loaders and SQL. The sample deliberately contains each failure pattern the real
data showed, so the reconciliation has something to catch:

  - an order that is both cancelled and refunded (Klaviyo sends two events)
  - a cancellation inside the window for an order placed before it
  - partial refunds (Klaviyo sends no event)
  - a Shopify test order (Klaviyo still records it)
  - a Klaviyo profile whose email differs from the Shopify order email (identity merge)
  - a plus-address pair that must go to review, not merge
  - quiz leads before PostHog started tracking, a lead PostHog never identified,
    an unresolved {{ad.name}} macro, an internal test address, and preview-host traffic
  - both Shopify CSV export versions (v1 Windows-1252, v2 UTF-8 with line items)
"""
import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
rng = random.Random(7)
UTC = timezone.utc

WINDOW_START = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
DAYS = 35
INTERNAL = "owner@example.com"


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def klaviyo_iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def money(x: float) -> str:
    return f"{x:.2f}"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


# ---------------------------------------------------------------- Shopify orders (API shape)

orders = []
for n in range(60):
    created = WINDOW_START + timedelta(days=rng.uniform(0, DAYS))
    subtotal = rng.choice([35.0, 129.99, 234.99, 559.99, 909.99])
    shipping = rng.choice([0.0, 19.99, 49.99])
    tax = round(subtotal * 0.13, 2)
    total = round(subtotal + shipping + tax, 2)
    orders.append({
        "number": 5000 + n,
        "email": f"customer{n:03d}@example.com",
        "created": created,
        "subtotal": subtotal, "shipping": shipping, "tax": tax, "total": total,
        "cancelled": None, "refunded": 0.0, "refunds": [], "status": "PAID", "test": False,
    })
orders.sort(key=lambda o: o["created"])
for i, o in enumerate(orders):
    o["number"] = 5000 + i

# Planted cases, by position in the sorted list.
partial = [orders[5], orders[22], orders[40]]
for o in partial:
    o["refunded"] = 10.0
    o["status"] = "PARTIALLY_REFUNDED"
    o["refunds"] = [(o["created"] + timedelta(hours=3), 10.0)]

cancelled = [orders[12], orders[31]]
for o in cancelled:
    o["cancelled"] = o["created"] + timedelta(days=1)
    o["status"] = "VOIDED"
    o["refunds"] = [(o["cancelled"], o["total"])]

both = orders[47]
both["cancelled"] = both["created"] + timedelta(days=2)
both["refunded"] = both["total"]
both["status"] = "REFUNDED"
both["refunds"] = [(both["cancelled"], both["total"])]

orders[18]["test"] = True
orders[33]["email"] = "jo.smith+gym@gmail.com"   # plus-address variant of a Klaviyo-only lead
orders[50]["email"] = "pat.lee@work.example.com"  # Klaviyo profile holds pat.lee@home.example.com

# An order placed before the window, cancelled inside it.
early = {
    "number": 4990, "email": "customer900@example.com",
    "created": WINDOW_START - timedelta(days=4), "subtotal": 234.99, "shipping": 19.99,
    "tax": 30.55, "total": 285.53, "cancelled": WINDOW_START + timedelta(days=1),
    "refunded": 0.0, "refunds": [], "status": "VOIDED", "test": False,
}
early["refunds"] = [(early["cancelled"], early["total"])]


def api_node(o: dict) -> dict:
    def m(x):
        return {"shopMoney": {"amount": money(x)}}
    return {
        "id": f"gid://shopify/Order/{900000 + o['number']}",
        "name": f"#{o['number']}",
        "email": o["email"],
        "createdAt": iso(o["created"]),
        "processedAt": iso(o["created"]),
        "cancelledAt": iso(o["cancelled"]) if o["cancelled"] else None,
        "test": o["test"],
        "sourceName": "web",
        "displayFinancialStatus": o["status"],
        "currencyCode": "CAD",
        "customer": {"id": f"gid://shopify/Customer/{700000 + o['number']}"},
        "subtotalPriceSet": m(o["subtotal"]),
        "totalPriceSet": m(o["total"]),
        "totalDiscountsSet": m(0.0),
        "totalTaxSet": m(o["tax"]),
        "totalShippingPriceSet": m(o["shipping"]),
        "totalRefundedSet": m(o["refunded"]),
        "refunds": [{"createdAt": iso(ts), "totalRefundedSet": m(amt)} for ts, amt in o["refunds"]],
    }


write_jsonl(HERE / "shopify" / "orders_api_sample.jsonl", [api_node(o) for o in orders])

# ---------------------------------------------------------------- Shopify CSV exports, both versions

V1 = ["Name", "Email", "Lineitem name", "Shipping Name", "Shipping City", "Shipping Province",
      "Shipping Country", "Notes", "Shipping Province Name"]
with open(HERE / "shopify" / "orders_export_v1_sample.csv", "w", encoding="cp1252", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(V1)
    for k in range(4):
        w.writerow([f"#{4800 + k}", f"legacy{k}@example.com", "Adjustable Dumbbell Set", f"Customer {k}",
                    "Toronto", "ON", "CA", "Leave at side door ’", "Ontario"])

V2 = ["Name", "Email", "Financial Status", "Paid at", "Fulfillment Status", "Fulfilled at", "Accepts Marketing",
      "Currency", "Subtotal", "Shipping", "Taxes", "Total", "Discount Code", "Discount Amount", "Shipping Method",
      "Created at", "Lineitem quantity", "Lineitem name", "Lineitem price", "Lineitem compare at price",
      "Lineitem sku", "Lineitem requires shipping", "Lineitem taxable", "Lineitem fulfillment status",
      "Billing Name", "Billing Street", "Billing Address1", "Billing Address2", "Billing Company", "Billing City",
      "Billing Zip", "Billing Province", "Billing Country", "Billing Phone", "Shipping Name", "Shipping Street",
      "Shipping Address1", "Shipping Address2", "Shipping Company", "Shipping City", "Shipping Zip",
      "Shipping Province", "Shipping Country", "Shipping Phone", "Notes", "Note Attributes", "Cancelled at",
      "Payment Method", "Payment Reference", "Refunded Amount", "Vendor", "Id", "Tags", "Risk Level", "Source",
      "Lineitem discount", "Tax 1 Name", "Tax 1 Value", "Tax 2 Name", "Tax 2 Value", "Tax 3 Name", "Tax 3 Value",
      "Tax 4 Name", "Tax 4 Value", "Tax 5 Name", "Tax 5 Value", "Phone", "Receipt Number", "Duties",
      "Billing Province Name", "Shipping Province Name", "Payment ID", "Payment Terms Name",
      "Next Payment Due At", "Payment References"]
with open(HERE / "shopify" / "orders_export_v2_sample.csv", "w", encoding="utf-8-sig", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=V2)
    w.writeheader()
    for k in range(3):
        base = {col: "" for col in V2}
        base.update({"Name": f"#{4900 + k}", "Email": f"spring{k}@example.com", "Lineitem quantity": "1",
                     "Lineitem name": "Hex Dumbbell Pair", "Lineitem price": "129.99"})
        first = dict(base)
        first.update({"Financial Status": "paid", "Currency": "CAD", "Subtotal": "259.98", "Shipping": "19.99",
                      "Taxes": "33.80", "Total": "313.77", "Discount Amount": "0.00",
                      "Created at": f"2026-06-0{k + 1} 10:15:00 -0400", "Refunded Amount": "0.00",
                      "Id": str(800000 + k), "Source": "web"})
        w.writerow(first)
        second = dict(base)
        second["Lineitem name"] = "Resistance Band"
        w.writerow(second)

# ---------------------------------------------------------------- Klaviyo order events

def order_event(event_id: str, metric: str, ts: datetime, o: dict, email: str, value: float) -> dict:
    return {
        "id": event_id, "metric_id": metric, "datetime": klaviyo_iso(ts),
        "profile_id": f"P{o['number']}", "profile_email": email,
        "event_properties": {
            "$event_id": str(900000 + o["number"]), "$value": round(value, 2), "$currency_code": "CAD",
            "$extra": {"name": f"#{o['number']}", "id": 900000 + o["number"], "email": o["email"],
                       "created_at": iso(o["created"]), "test": o["test"], "source_name": "web"},
        },
    }


placed, refunded_events, cancelled_events = [], [], []
for o in orders:
    email = "pat.lee@home.example.com" if o is orders[50] else o["email"]
    placed.append(order_event(f"KP{o['number']}", "M_PLACED", o["created"] + timedelta(seconds=40), o, email, o["total"]))
    if o["cancelled"]:
        cancelled_events.append(order_event(f"KC{o['number']}", "M_CANCELLED", o["cancelled"], o, email, o["total"]))
    if o["status"] == "REFUNDED":
        refunded_events.append(order_event(f"KR{o['number']}", "M_REFUNDED", o["cancelled"], o, email, o["refunded"]))
cancelled_events.append(order_event("KC4990", "M_CANCELLED", early["cancelled"], early, early["email"], early["total"]))

write_jsonl(HERE / "klaviyo" / "placed_order_sample.jsonl", placed)
write_jsonl(HERE / "klaviyo" / "refunded_order_sample.jsonl", refunded_events)
write_jsonl(HERE / "klaviyo" / "cancelled_order_sample.jsonl", cancelled_events)

# ---------------------------------------------------------------- Quiz: Klaviyo leads and PostHog people

POSTHOG_START = WINDOW_START + timedelta(hours=20)   # PostHog began tracking the next morning
VARIANTS = ["Free Focus Test", "Sign Post"]

leads = []
for n in range(40):
    ts = WINDOW_START + timedelta(hours=2) + timedelta(days=rng.uniform(0, DAYS - 1))
    leads.append({"email": f"lead{n:03d}@example.com", "ts": ts, "consent": n % 3 != 0,
                  "utm": VARIANTS[n % 2], "tapped": n % 6 == 0})
leads.sort(key=lambda x: x["ts"])
leads[0]["ts"] = WINDOW_START + timedelta(hours=3)        # before PostHog started
leads[1]["ts"] = WINDOW_START + timedelta(hours=5)        # before PostHog started
leads[7]["utm"] = "{{ad.name}}"
leads[9]["email"] = "josmith@gmail.com"                   # plus-address pair with order #5033
leads.append({"email": INTERNAL, "ts": WINDOW_START + timedelta(hours=4), "consent": True,
              "utm": None, "tapped": True, "test": True})

quiz_completed, store_clicked, received, subscribed = [], [], [], []
for i, lead in enumerate(leads):
    is_test = lead.get("test", False)
    props = {"$event_id": f"Q{i}", "is_test": is_test, "marketing_consent": lead["consent"],
             "utm_source": "meta", "utm_campaign": "Quiz", "utm_content": lead["utm"]}
    quiz_completed.append({"id": f"KQ{i}", "metric_id": "M_QUIZ", "datetime": klaviyo_iso(lead["ts"]),
                           "profile_id": f"L{i}", "profile_email": lead["email"], "event_properties": props})
    received.append({"id": f"KE{i}", "metric_id": "M_RECEIVED", "datetime": klaviyo_iso(lead["ts"] + timedelta(minutes=1)),
                     "profile_id": f"L{i}", "profile_email": lead["email"],
                     "event_properties": {"$message": "MSG_E1", "$flow": "FLOW_QUIZ", "Subject": "Your full results"}})
    if lead["consent"]:
        subscribed.append({"id": f"KS{i}", "metric_id": "M_SUBSCRIBED", "datetime": klaviyo_iso(lead["ts"]),
                           "profile_id": f"L{i}", "profile_email": lead["email"],
                           "event_properties": {"method": "API", "list_name": "Quiz leads (consented)"}})
    if lead["tapped"]:
        store_clicked.append({"id": f"KT{i}", "metric_id": "M_STORE", "datetime": klaviyo_iso(lead["ts"] + timedelta(minutes=2)),
                              "profile_id": f"L{i}", "profile_email": lead["email"],
                              "event_properties": {"$event_id": f"T{i}", "is_test": is_test, "platform": "ios"}})
unsubscribed = [{"id": "KU1", "metric_id": "M_UNSUBSCRIBED", "datetime": klaviyo_iso(leads[4]["ts"] + timedelta(days=3)),
                 "profile_id": "L4", "profile_email": leads[4]["email"],
                 "event_properties": {"method": "EMAIL_UNSUBSCRIBE"}}]

write_jsonl(HERE / "klaviyo" / "quiz_completed_sample.jsonl", quiz_completed)
write_jsonl(HERE / "klaviyo" / "quiz_store_clicked_sample.jsonl", store_clicked)
write_jsonl(HERE / "klaviyo" / "received_email_quiz_flow_sample.jsonl", received)
write_jsonl(HERE / "klaviyo" / "subscribed_email_sample.jsonl", subscribed)
write_jsonl(HERE / "klaviyo" / "unsubscribed_email_sample.jsonl", unsubscribed)

HEADER = ["person_id", "person_email", "first_event_at", "host", "app", "utm_source", "utm_campaign",
          "utm_content", "any_test", "started_at", "email_submitted_at", "completed_at",
          "store_clicked_at", "steps_seen"]
NEVER = "1970-01-01T00:00:00Z"


def steps(upto: int) -> str:
    return ";".join(f"{i:02d}:S{i:02d}" for i in range(upto + 1))


def ph_time(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.000000Z")


people = []
for i, lead in enumerate(leads):
    if lead["ts"] < POSTHOG_START or lead.get("test"):
        continue
    email = "(null)" if i in (12, 25) else lead["email"]   # identify() never fired for these two
    if i == 30:
        continue                                           # Klaviyo has this lead, PostHog has no person
    start = lead["ts"] - timedelta(minutes=4)
    tap = ph_time(lead["ts"] + timedelta(minutes=2)) if lead["tapped"] and i != 18 else NEVER
    people.append([f"p-{i:04d}", email, ph_time(start), "quiz.gymarmy.app", "lockedin_quiz", "meta", "Quiz",
                   lead["utm"] or "(null)", "0", ph_time(start), ph_time(lead["ts"]),
                   ph_time(lead["ts"] + timedelta(seconds=5)), tap, steps(35)])

for j in range(45):                                        # visitors who never gave an email
    ts = POSTHOG_START + timedelta(days=rng.uniform(0, DAYS - 2))
    reached = rng.randint(0, 30)
    started = ph_time(ts) if reached >= 1 else NEVER
    tap = ph_time(ts + timedelta(minutes=6)) if j % 15 == 0 else NEVER
    people.append([f"a-{j:04d}", "(null)", ph_time(ts), "quiz.gymarmy.app", "lockedin_quiz", "meta", "Quiz",
                   VARIANTS[j % 2], "0", started, NEVER, NEVER, tap, steps(reached)])

preview = POSTHOG_START + timedelta(days=3)
people.append(["v-0001", "(null)", ph_time(preview), "preview.lovable.example", "lockedin_quiz", "(null)", "(null)", "(null)",
               "0", ph_time(preview), ph_time(preview + timedelta(minutes=3)), NEVER, NEVER, steps(35)])
people.append(["t-0001", INTERNAL, ph_time(POSTHOG_START), "quiz.gymarmy.app", "lockedin_quiz", "(null)", "(null)", "(null)",
               "1", ph_time(POSTHOG_START), ph_time(POSTHOG_START + timedelta(minutes=2)), NEVER, NEVER, steps(35)])

(HERE / "posthog").mkdir(exist_ok=True)
with open(HERE / "posthog" / "quiz_people_sample.psv", "w", encoding="utf-8", newline="\n") as fh:
    fh.write("|".join(HEADER) + "\n")
    for row in sorted(people):
        fh.write("|".join(row) + "\n")

print("sample written to", HERE)
