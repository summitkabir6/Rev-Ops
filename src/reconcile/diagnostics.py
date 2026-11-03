"""Checks that explain a variance.

Every check returns two numbers:
    records   how many records show this cause
    effect    how much those records move (other source - source of truth), in the
              metric's own units, or NULL when the effect cannot be added up (rates).

The checks for one metric are written to split the variance into parts that do not
overlap, so for counts and money their effects add up to the whole variance. The
report prints how much the checks explain; whatever is left is unexplained and says so.
The most likely cause is the check with the largest effect.
"""
from .metrics import local_date


def checks(tz: str) -> dict[str, list[tuple[str, str]]]:
    k = local_date("k.occurred_at_utc", tz)

    # Orders: Shopify's window set vs Klaviyo's window set, matched on Shopify order id.
    shopify_window = """
        SELECT * FROM fct_order
        WHERE source_of_truth = 'orders_api' AND NOT is_test AND ordered_date_local BETWEEN $start AND $end
    """
    klaviyo_placed_window = f"""
        SELECT * FROM stg_klaviyo_events k
        WHERE k.stream = 'placed_order' AND {k} BETWEEN $start AND $end
    """
    klaviyo_only = f"""
        FROM ({klaviyo_placed_window}) kp
        WHERE kp.source_event_id NOT IN (SELECT shopify_order_id FROM ({shopify_window}))
    """
    shopify_only = f"""
        FROM ({shopify_window}) so
        WHERE so.shopify_order_id NOT IN (SELECT source_event_id FROM ({klaviyo_placed_window}))
    """
    matched = f"""
        FROM ({shopify_window}) so
        JOIN ({klaviyo_placed_window}) kp ON kp.source_event_id = so.shopify_order_id
    """
    order_count = [
        ("Klaviyo Placed Order events with no Shopify order in the window (test orders, or the order falls outside the window)",
         f"SELECT count(*), count(*) {klaviyo_only}"),
        ("Shopify orders with no Klaviyo Placed Order event in the window",
         f"SELECT count(*), -count(*) {shopify_only}"),
    ]
    gross_revenue = [
        (order_count[0][0], f"SELECT count(*), coalesce(sum(kp.value), 0) {klaviyo_only}"),
        (order_count[1][0], f"SELECT count(*), -coalesce(sum(so.gross_amount), 0) {shopify_only}"),
        ("Matched orders where Klaviyo's value differs from the Shopify total",
         f"SELECT count(*) FILTER (WHERE kp.value <> so.gross_amount), coalesce(sum(kp.value - so.gross_amount), 0) {matched}"),
    ]

    # Refunds: Klaviyo's Refunded Order + Cancelled Order events (by event date)
    # vs Shopify's reversed amount on orders placed in the window.
    klaviyo_reversal_window = f"""
        SELECT * FROM stg_klaviyo_events k
        WHERE k.stream IN ('refunded_order', 'cancelled_order') AND {k} BETWEEN $start AND $end
    """
    refunds = [
        ("Orders both cancelled and refunded, which Klaviyo records twice (a Refunded Order and a Cancelled Order event)",
         f"""SELECT count(*), coalesce(sum(r.value), 0)
             FROM ({klaviyo_reversal_window}) r
             JOIN ({shopify_window}) so ON so.shopify_order_id = r.source_event_id
             WHERE r.stream = 'refunded_order' AND so.is_cancelled"""),
        ("Klaviyo refund or cancel events in the window for orders not placed in it",
         f"""SELECT count(*), coalesce(sum(r.value), 0)
             FROM ({klaviyo_reversal_window}) r
             WHERE r.source_event_id NOT IN (SELECT shopify_order_id FROM ({shopify_window}))"""),
        ("Refunds Klaviyo never saw: partial refunds send no Refunded Order event",
         f"""SELECT count(*), -coalesce(sum(so.refunded_amount), 0)
             FROM ({shopify_window}) so
             WHERE NOT so.is_cancelled AND so.refunded_amount > 0
               AND so.shopify_order_id NOT IN (SELECT source_event_id FROM ({klaviyo_reversal_window}) WHERE stream = 'refunded_order')"""),
        ("Cancelled Shopify orders whose Cancelled Order event falls outside the window",
         f"""SELECT count(*), -coalesce(sum(so.gross_amount), 0)
             FROM ({shopify_window}) so
             WHERE so.is_cancelled
               AND so.shopify_order_id NOT IN (SELECT source_event_id FROM ({klaviyo_reversal_window}) WHERE stream = 'cancelled_order')"""),
        ("Matched cancellations where Klaviyo's value differs from the Shopify total",
         f"""SELECT count(*) FILTER (WHERE r.value <> so.gross_amount), coalesce(sum(r.value - so.gross_amount), 0)
             FROM ({klaviyo_reversal_window}) r
             JOIN ({shopify_window}) so ON so.shopify_order_id = r.source_event_id
             WHERE r.stream = 'cancelled_order' AND so.is_cancelled"""),
        ("Matched full refunds where Klaviyo's value differs from Shopify's refunded amount",
         f"""SELECT count(*) FILTER (WHERE r.value <> so.refunded_amount), coalesce(sum(r.value - so.refunded_amount), 0)
             FROM ({klaviyo_reversal_window}) r
             JOIN ({shopify_window}) so ON so.shopify_order_id = r.source_event_id
             WHERE r.stream = 'refunded_order' AND NOT so.is_cancelled"""),
    ]
    # net = gross - refunds, so a refund cause moves net the other way.
    net_revenue = gross_revenue + [
        (label, f"SELECT records, -effect FROM ({sql}) AS t(records, effect)") for label, sql in refunds
    ]
    active_customers = [
        (label, f"SELECT records, NULL FROM ({sql}) AS t(records, effect)") for label, sql in order_count
    ]

    # Quiz captures: PostHog (other) vs Klaviyo (truth), matched on email.
    p_capture = local_date("email_submitted_at_utc", tz)
    p_tap = local_date("store_clicked_at_utc", tz)
    klaviyo_leads = f"""
        SELECT DISTINCT email_norm, min(occurred_at_utc) AS first_at FROM stg_klaviyo_events k
        WHERE k.stream = 'quiz_completed' AND NOT k.is_test AND {k} BETWEEN $start AND $end
          AND k.email_norm NOT IN (SELECT email_norm FROM internal_emails)
        GROUP BY email_norm
    """
    posthog_people = """
        SELECT * FROM stg_posthog_quiz_people
        WHERE is_production AND NOT is_test
          AND coalesce(email_norm NOT IN (SELECT email_norm FROM internal_emails), TRUE)
    """
    posthog_captures = f"SELECT * FROM ({posthog_people}) WHERE {p_capture} BETWEEN $start AND $end"
    first_posthog = "(SELECT min(first_event_at_utc) FROM stg_posthog_quiz_people WHERE is_production)"
    quiz_email_captures = [
        ("Klaviyo leads recorded before PostHog's first quiz event",
         f"""SELECT count(*), -count(*) FROM ({klaviyo_leads}) kl
             WHERE kl.first_at < {first_posthog}
               AND kl.email_norm NOT IN (SELECT email_norm FROM ({posthog_captures}) WHERE email_norm IS NOT NULL)"""),
        ("Klaviyo leads with no PostHog capture on the same email, after PostHog tracking began",
         f"""SELECT count(*), -count(*) FROM ({klaviyo_leads}) kl
             WHERE kl.first_at >= {first_posthog}
               AND kl.email_norm NOT IN (SELECT email_norm FROM ({posthog_captures}) WHERE email_norm IS NOT NULL)"""),
        ("PostHog captures where the person has no email, so they cannot be matched",
         f"SELECT count(*), count(*) FROM ({posthog_captures}) WHERE email_norm IS NULL"),
        ("PostHog captures whose email has no Klaviyo lead in the window",
         f"""SELECT count(*), count(*) FROM ({posthog_captures})
             WHERE email_norm IS NOT NULL AND email_norm NOT IN (SELECT email_norm FROM ({klaviyo_leads}))"""),
    ]

    # Store taps: Klaviyo (other) vs PostHog (truth), matched on email.
    klaviyo_taps = f"""
        SELECT DISTINCT email_norm FROM stg_klaviyo_events k
        WHERE k.stream = 'quiz_store_clicked' AND NOT k.is_test AND {k} BETWEEN $start AND $end
          AND k.email_norm NOT IN (SELECT email_norm FROM internal_emails)
    """
    posthog_taps = f"SELECT * FROM ({posthog_people}) WHERE {p_tap} BETWEEN $start AND $end"
    quiz_store_taps = [
        ("Klaviyo store-click profiles with no PostHog store tap on the same email",
         f"""SELECT count(*), count(*) FROM ({klaviyo_taps})
             WHERE email_norm NOT IN (SELECT email_norm FROM ({posthog_taps}) WHERE email_norm IS NOT NULL)"""),
        ("PostHog store taps by people with no email (Klaviyo only sees identified profiles)",
         f"SELECT count(*), -count(*) FROM ({posthog_taps}) WHERE email_norm IS NULL"),
        ("PostHog store taps whose email has no Klaviyo store click",
         f"""SELECT count(*), -count(*) FROM ({posthog_taps})
             WHERE email_norm IS NOT NULL AND email_norm NOT IN (SELECT email_norm FROM ({klaviyo_taps}))"""),
    ]
    capture_to_store_tap_rate = [
        (label, f"SELECT records, NULL FROM ({sql}) AS t(records, effect)")
        for label, sql in quiz_store_taps + quiz_email_captures
    ]

    return {
        "order_count": order_count,
        "gross_revenue": gross_revenue,
        "refunds": refunds,
        "net_revenue": net_revenue,
        "active_customers": active_customers,
        "quiz_email_captures": quiz_email_captures,
        "quiz_store_taps": quiz_store_taps,
        "capture_to_store_tap_rate": capture_to_store_tap_rate,
    }
