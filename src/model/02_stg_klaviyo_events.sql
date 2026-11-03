-- Staging: one row per Klaviyo event, typed. Event ids are stable, so the latest load wins.

CREATE OR REPLACE TABLE stg_klaviyo_events AS
SELECT
    payload->>'id'                                                     AS event_id,
    stream,
    CAST(CAST(payload->>'datetime' AS TIMESTAMPTZ) AS TIMESTAMP)       AS occurred_at_utc,
    payload->>'profile_id'                                             AS profile_id,
    payload->>'profile_email'                                          AS email_raw,
    nullif(lower(trim(payload->>'profile_email')), '')                 AS email_norm,

    -- Order streams: Shopify's numeric order id, the order name, and the order email at purchase.
    payload->'event_properties'->>'$."$event_id"'                          AS source_event_id,
    payload->'event_properties'->'$."$extra"'->>'name'                     AS order_name,
    nullif(lower(trim(payload->'event_properties'->'$."$extra"'->>'email')), '') AS order_email_norm,
    CAST(payload->'event_properties'->>'$."$value"' AS DECIMAL(12, 2))     AS value,

    -- Quiz streams.
    coalesce(CAST(payload->'event_properties'->>'is_test' AS BOOLEAN), FALSE) AS is_test,
    CAST(payload->'event_properties'->>'marketing_consent' AS BOOLEAN) AS marketing_consent,
    payload->'event_properties'->>'utm_source'                         AS utm_source,
    payload->'event_properties'->>'utm_campaign'                       AS utm_campaign,
    payload->'event_properties'->>'utm_content'                        AS utm_content,

    -- Send streams.
    payload->'event_properties'->>'$."$message"'                           AS message_id,
    payload->'event_properties'->>'$."$flow"'                              AS flow_id,

    -- Consent streams.
    payload->'event_properties'->>'method'                             AS consent_method,
    payload->'event_properties'->>'list_name'                          AS list_name,
    loaded_at
FROM raw_klaviyo
QUALIFY row_number() OVER (PARTITION BY payload->>'id' ORDER BY loaded_at DESC) = 1;
