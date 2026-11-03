from src.ingest.common import append_rows
from src.model.build import build


def key_for(con, email):
    rows = con.execute("SELECT customer_key FROM email_to_customer WHERE email_norm = ?", [email]).fetchall()
    return rows[0][0] if rows else None


def test_same_email_in_two_systems_is_one_customer(warehouse):
    # customer000 placed a Shopify order and has a Klaviyo Placed Order profile.
    row = warehouse.execute("""
        SELECT in_shopify, in_klaviyo, email_count, resolution FROM dim_customer
        WHERE primary_email_norm = 'customer000@example.com'
    """).fetchall()
    assert row == [(True, True, 1, "single_email")]


def test_case_and_whitespace_differences_merge(warehouse):
    append_rows(warehouse, "raw_klaviyo", "klaviyo", "quiz_completed", "test", [{
        "id": "KQ-case", "metric_id": "M_QUIZ", "datetime": "2026-10-10T12:00:00+00:00",
        "profile_id": "L-case", "profile_email": "  Customer001@Example.COM ",
        "event_properties": {"is_test": False, "marketing_consent": True},
    }])
    build(warehouse)
    assert key_for(warehouse, "customer001@example.com") is not None
    assert warehouse.execute(
        "SELECT count(DISTINCT customer_key) FROM customer_identity WHERE email_norm = 'customer001@example.com'"
    ).fetchall()[0][0] == 1


def test_different_emails_on_the_same_order_merge_by_order_id(warehouse):
    work = key_for(warehouse, "pat.lee@work.example.com")
    home = key_for(warehouse, "pat.lee@home.example.com")
    assert work is not None and work == home
    rule = warehouse.execute(
        "SELECT match_rule FROM customer_identity WHERE email_norm = 'pat.lee@work.example.com'"
    ).fetchall()[0][0]
    assert rule == "order_id_link"
    assert warehouse.execute(
        "SELECT email_count, resolution FROM dim_customer WHERE customer_key = ?", [work]
    ).fetchall() == [(2, "merged")]


def test_plus_address_lookalikes_are_flagged_not_merged(warehouse):
    plus = key_for(warehouse, "jo.smith+gym@example.com")
    plain = key_for(warehouse, "jo.smith@example.com")
    assert plus != plain
    pair = warehouse.execute("""
        SELECT count(*) FROM identity_review
        WHERE 'jo.smith+gym@example.com' IN (email_a, email_b) AND 'jo.smith@example.com' IN (email_a, email_b)
    """).fetchall()[0][0]
    assert pair == 1
    flagged = warehouse.execute(
        "SELECT bool_and(needs_review) FROM dim_customer WHERE customer_key IN (?, ?)", [plus, plain]
    ).fetchall()[0][0]
    assert flagged is True


def test_anonymous_quiz_visitors_are_not_customers(warehouse):
    anonymous_in_dim = warehouse.execute("""
        SELECT count(*) FROM customer_identity ci
        JOIN stg_posthog_quiz_people p ON ci.source = 'posthog' AND ci.source_id = p.person_id
        WHERE p.email_norm IS NULL
    """).fetchall()[0][0]
    assert anonymous_in_dim == 0
    unkeyed_funnel_people = warehouse.execute(
        "SELECT count(DISTINCT person_id) FROM fct_funnel WHERE customer_key IS NULL"
    ).fetchall()[0][0]
    assert unkeyed_funnel_people > 0


def test_internal_addresses_come_from_test_events(warehouse):
    internal = {e for (e,) in warehouse.execute("SELECT email_norm FROM internal_emails").fetchall()}
    assert internal == {"owner@example.com"}


def test_hashed_config_marks_an_unflagged_address_internal(warehouse, monkeypatch):
    import hashlib

    from src.model import build as build_module

    digest = hashlib.sha256(b"lead000@example.com").hexdigest()
    monkeypatch.setattr(build_module, "load_internal_hashes", lambda: [digest])
    build_module.build(warehouse)
    rows = warehouse.execute(
        "SELECT evidence FROM internal_emails WHERE email_norm = 'lead000@example.com'"
    ).fetchall()
    assert rows == [("listed in config",)]
