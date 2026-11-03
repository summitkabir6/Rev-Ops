"""The same metric, computed independently from each source.

Each query reads only its own source's staged table, so a disagreement between
two rows is a real disagreement between the systems, not between two joins.
Every query takes $start and $end as local dates (inclusive) in the shop's time zone.

Definitions:
  order_count        orders placed in the window; Shopify test orders excluded.
  gross_revenue      order totals (tax and shipping included), as each system records them.
  refunds            money reversed. Shopify: the whole order if cancelled, otherwise the
                     refunded amount, dated by order date. Klaviyo: Refunded Order plus
                     Cancelled Order event values, dated by event date.
  net_revenue        gross_revenue minus refunds.
  active_customers   distinct customers with at least one order in the window.
  quiz_email_captures   distinct people who gave an email on the quiz; test and internal excluded.
  quiz_store_taps       distinct people who tapped an app store badge on the quiz.
  capture_to_store_tap_rate   quiz_store_taps / quiz_email_captures.
"""

LOCAL = "CAST(timezone('{tz}', CAST({col} AS TIMESTAMPTZ)) AS DATE)"


def local_date(col: str, tz: str) -> str:
    return LOCAL.format(tz=tz, col=col)


def queries(tz: str) -> dict[tuple[str, str], str]:
    k_date = local_date("occurred_at_utc", tz)
    p_capture = local_date("email_submitted_at_utc", tz)
    p_tap = local_date("store_clicked_at_utc", tz)

    shopify_orders = """
        FROM fct_order
        WHERE source_of_truth = 'orders_api' AND NOT is_test
          AND ordered_date_local BETWEEN $start AND $end
    """
    klaviyo_placed = f"""
        FROM stg_klaviyo_events
        WHERE stream = 'placed_order' AND {k_date} BETWEEN $start AND $end
    """
    klaviyo_reversed = f"""
        FROM stg_klaviyo_events
        WHERE stream IN ('refunded_order', 'cancelled_order') AND {k_date} BETWEEN $start AND $end
    """
    klaviyo_leads = f"""
        FROM stg_klaviyo_events
        WHERE stream = 'quiz_completed' AND NOT is_test
          AND email_norm NOT IN (SELECT email_norm FROM internal_emails)
          AND {k_date} BETWEEN $start AND $end
    """
    klaviyo_taps = f"""
        FROM stg_klaviyo_events
        WHERE stream = 'quiz_store_clicked' AND NOT is_test
          AND email_norm NOT IN (SELECT email_norm FROM internal_emails)
          AND {k_date} BETWEEN $start AND $end
    """
    posthog_people = """
        FROM stg_posthog_quiz_people
        WHERE is_production AND NOT is_test
          AND coalesce(email_norm NOT IN (SELECT email_norm FROM internal_emails), TRUE)
    """

    return {
        ("order_count", "shopify"): f"SELECT count(*) {shopify_orders}",
        ("order_count", "klaviyo"): f"SELECT count(*) {klaviyo_placed}",

        ("gross_revenue", "shopify"): f"SELECT coalesce(sum(gross_amount), 0) {shopify_orders}",
        ("gross_revenue", "klaviyo"): f"SELECT coalesce(sum(value), 0) {klaviyo_placed}",

        ("refunds", "shopify"): f"SELECT coalesce(sum(reversed_amount), 0) {shopify_orders}",
        ("refunds", "klaviyo"): f"SELECT coalesce(sum(value), 0) {klaviyo_reversed}",

        ("net_revenue", "shopify"): f"SELECT coalesce(sum(net_amount), 0) {shopify_orders}",
        ("net_revenue", "klaviyo"): f"""
            SELECT (SELECT coalesce(sum(value), 0) {klaviyo_placed})
                 - (SELECT coalesce(sum(value), 0) {klaviyo_reversed})
        """,

        ("active_customers", "shopify"): f"SELECT count(DISTINCT customer_key) {shopify_orders}",
        ("active_customers", "klaviyo"): f"""
            SELECT count(DISTINCT c.customer_key)
            FROM stg_klaviyo_events k JOIN email_to_customer c USING (email_norm)
            WHERE k.stream = 'placed_order'
              AND {local_date('k.occurred_at_utc', tz)} BETWEEN $start AND $end
        """,

        ("quiz_email_captures", "klaviyo"): f"SELECT count(DISTINCT email_norm) {klaviyo_leads}",
        ("quiz_email_captures", "posthog"): f"""
            SELECT count(*) {posthog_people} AND {p_capture} BETWEEN $start AND $end
        """,

        ("quiz_store_taps", "posthog"): f"""
            SELECT count(*) {posthog_people} AND {p_tap} BETWEEN $start AND $end
        """,
        ("quiz_store_taps", "klaviyo"): f"SELECT count(DISTINCT email_norm) {klaviyo_taps}",

        ("capture_to_store_tap_rate", "posthog"): f"""
            SELECT round(100.0 * count(*) FILTER (WHERE {p_tap} BETWEEN $start AND $end)
                   / nullif(count(*) FILTER (WHERE {p_capture} BETWEEN $start AND $end), 0), 1)
            {posthog_people}
        """,
        ("capture_to_store_tap_rate", "klaviyo"): f"""
            SELECT round(100.0 * (SELECT count(DISTINCT email_norm) {klaviyo_taps})
                   / nullif((SELECT count(DISTINCT email_norm) {klaviyo_leads}), 0), 1)
        """,
    }


METRIC_ORDER = [
    "order_count", "gross_revenue", "refunds", "net_revenue", "active_customers",
    "quiz_email_captures", "quiz_store_taps", "capture_to_store_tap_rate",
]
