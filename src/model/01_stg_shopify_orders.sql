-- Staging: one row per Shopify order per file shape.
-- Raw is append-only, so a re-pulled order can appear more than once; the latest load wins.

CREATE OR REPLACE TABLE stg_shopify_orders AS
WITH api AS (
    SELECT
        stream,
        payload->>'name'                                                  AS order_name,
        regexp_extract(payload->>'id', '(\d+)$', 1)                       AS shopify_order_id,
        regexp_extract(payload->'customer'->>'id', '(\d+)$', 1)           AS shopify_customer_id,
        payload->>'email'                                                 AS email_raw,
        CAST(CAST(payload->>'createdAt' AS TIMESTAMPTZ) AS TIMESTAMP)     AS created_at_utc,
        CAST(CAST(payload->>'cancelledAt' AS TIMESTAMPTZ) AS TIMESTAMP)   AS cancelled_at_utc,
        payload->>'displayFinancialStatus'                                AS financial_status,
        payload->>'sourceName'                                            AS sales_channel,
        CAST(payload->>'test' AS BOOLEAN)                                 AS is_test,
        payload->>'currencyCode'                                          AS currency,
        CAST(payload->'totalPriceSet'->'shopMoney'->>'amount' AS DECIMAL(12, 2))         AS gross_amount,
        CAST(payload->'totalDiscountsSet'->'shopMoney'->>'amount' AS DECIMAL(12, 2))     AS discount_amount,
        CAST(payload->'totalTaxSet'->'shopMoney'->>'amount' AS DECIMAL(12, 2))           AS tax_amount,
        CAST(payload->'totalShippingPriceSet'->'shopMoney'->>'amount' AS DECIMAL(12, 2)) AS shipping_amount,
        CAST(payload->'totalRefundedSet'->'shopMoney'->>'amount' AS DECIMAL(12, 2))      AS refunded_amount,
        -- Refund records include voided authorisations, which totalRefunded does not.
        coalesce(list_sum([
            CAST(r->'totalRefundedSet'->'shopMoney'->>'amount' AS DECIMAL(12, 2))
            FOR r IN CAST(payload->'refunds' AS JSON[])
        ]), 0)                                                            AS refund_records_amount,
        CAST(list_min([
            CAST(CAST(r->>'createdAt' AS TIMESTAMPTZ) AS TIMESTAMP)
            FOR r IN CAST(payload->'refunds' AS JSON[])
        ]) AS TIMESTAMP)                                                  AS first_refund_at_utc,
        TRUE                                                              AS has_money,
        loaded_at
    FROM raw_shopify
    WHERE stream = 'orders_api'
),

-- v2 export: one row per line item; only the first row of an order carries the totals.
export_v2 AS (
    SELECT
        stream,
        payload->>'Name'                                                  AS order_name,
        nullif(payload->>'Id', '')                                        AS shopify_order_id,
        NULL                                                              AS shopify_customer_id,
        payload->>'Email'                                                 AS email_raw,
        CAST(strptime(payload->>'Created at', '%Y-%m-%d %H:%M:%S %z') AS TIMESTAMP) AS created_at_utc,
        CAST(strptime(nullif(payload->>'Cancelled at', ''), '%Y-%m-%d %H:%M:%S %z') AS TIMESTAMP) AS cancelled_at_utc,
        upper(payload->>'Financial Status')                               AS financial_status,
        payload->>'Source'                                                AS sales_channel,
        FALSE                                                             AS is_test,
        payload->>'Currency'                                              AS currency,
        CAST(nullif(payload->>'Total', '') AS DECIMAL(12, 2))             AS gross_amount,
        CAST(nullif(payload->>'Discount Amount', '') AS DECIMAL(12, 2))   AS discount_amount,
        CAST(nullif(payload->>'Taxes', '') AS DECIMAL(12, 2))             AS tax_amount,
        CAST(nullif(payload->>'Shipping', '') AS DECIMAL(12, 2))          AS shipping_amount,
        CAST(nullif(payload->>'Refunded Amount', '') AS DECIMAL(12, 2))   AS refunded_amount,
        CAST(nullif(payload->>'Refunded Amount', '') AS DECIMAL(12, 2))   AS refund_records_amount,
        NULL::TIMESTAMP                                                   AS first_refund_at_utc,
        TRUE                                                              AS has_money,
        loaded_at
    FROM raw_shopify
    WHERE stream = 'orders_export_v2'
      AND payload->>'Financial Status' <> ''
),

-- v1 export: a shipping-label layout with no dates or money. Kept for identity only.
export_v1 AS (
    SELECT
        stream,
        payload->>'Name'          AS order_name,
        NULL                      AS shopify_order_id,
        NULL                      AS shopify_customer_id,
        payload->>'Email'         AS email_raw,
        NULL::TIMESTAMP           AS created_at_utc,
        NULL::TIMESTAMP           AS cancelled_at_utc,
        NULL                      AS financial_status,
        NULL                      AS sales_channel,
        FALSE                     AS is_test,
        NULL                      AS currency,
        NULL::DECIMAL(12, 2)      AS gross_amount,
        NULL::DECIMAL(12, 2)      AS discount_amount,
        NULL::DECIMAL(12, 2)      AS tax_amount,
        NULL::DECIMAL(12, 2)      AS shipping_amount,
        NULL::DECIMAL(12, 2)      AS refunded_amount,
        NULL::DECIMAL(12, 2)      AS refund_records_amount,
        NULL::TIMESTAMP           AS first_refund_at_utc,
        FALSE                     AS has_money,
        loaded_at
    FROM raw_shopify
    WHERE stream = 'orders_export_v1'
),

unioned AS (
    SELECT * FROM api
    UNION ALL SELECT * FROM export_v2
    UNION ALL SELECT * FROM export_v1
)

SELECT
    * EXCLUDE (email_raw),
    email_raw,
    nullif(lower(trim(email_raw)), '') AS email_norm
FROM unioned
QUALIFY row_number() OVER (PARTITION BY stream, order_name ORDER BY loaded_at DESC) = 1;
