-- One row per Shopify order. Shopify is the source of truth for orders and money.
-- When an order arrived in more than one file shape, the richest one wins:
-- the API pull, then the v2 export, then the v1 export (which has no money).

CREATE OR REPLACE TABLE fct_order AS
WITH ranked AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY order_name
            ORDER BY CASE stream WHEN 'orders_api' THEN 1 WHEN 'orders_export_v2' THEN 2 ELSE 3 END
        ) AS precedence
    FROM stg_shopify_orders
),
klaviyo_orders AS (
    SELECT source_event_id, min(occurred_at_utc) AS placed_event_at_utc, sum(value) AS klaviyo_value
    FROM stg_klaviyo_events
    WHERE stream = 'placed_order'
    GROUP BY source_event_id
)
SELECT
    o.order_name,
    o.shopify_order_id,
    c.customer_key,
    o.created_at_utc                                                    AS ordered_at_utc,
    CAST(timezone('__TIMEZONE__', CAST(o.created_at_utc AS TIMESTAMPTZ)) AS DATE) AS ordered_date_local,
    o.currency,
    o.gross_amount,
    o.discount_amount,
    o.tax_amount,
    o.shipping_amount,
    o.refunded_amount,
    o.refund_records_amount,
    o.cancelled_at_utc IS NOT NULL                                      AS is_cancelled,
    -- Money given back or never kept: the whole order if cancelled, otherwise what was refunded.
    CASE WHEN o.cancelled_at_utc IS NOT NULL THEN o.gross_amount ELSE o.refunded_amount END AS reversed_amount,
    o.gross_amount
      - CASE WHEN o.cancelled_at_utc IS NOT NULL THEN o.gross_amount ELSE o.refunded_amount END AS net_amount,
    o.financial_status,
    o.sales_channel,
    o.is_test,
    o.has_money,
    o.stream                                                            AS source_of_truth,
    k.source_event_id IS NOT NULL                                       AS seen_in_klaviyo,
    k.placed_event_at_utc                                               AS klaviyo_placed_at_utc,
    k.klaviyo_value
FROM ranked o
LEFT JOIN email_to_customer c ON c.email_norm = o.email_norm
LEFT JOIN klaviyo_orders k ON k.source_event_id = o.shopify_order_id
WHERE o.precedence = 1;
