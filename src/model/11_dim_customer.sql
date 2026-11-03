-- Identity resolution across Shopify, Klaviyo and PostHog.
--
-- A customer is a set of email addresses we are certain belong to one person.
-- Two addresses join the same customer only when:
--   exact_email          they are identical after lowercasing and trimming;
--   shopify_customer_id  Shopify filed both under one customer id;
--   klaviyo_profile_id   Klaviyo filed both under one profile id;
--   order_id_link        the same Shopify order id is placed under one address in
--                        Shopify and another in Klaviyo.
-- Anything weaker (plus-addresses, Gmail dots, matching names) is never merged.
-- Those pairs go to identity_review and both stay separate customers, so a
-- doubtful match can inflate active customers but can never hide one.
-- PostHog visitors who never gave an email are not customers; they stay in fct_funnel.

CREATE OR REPLACE TABLE identity_sources AS
SELECT DISTINCT 'shopify' AS source, coalesce(shopify_customer_id, order_name) AS source_id, email_norm, created_at_utc AS seen_at
FROM stg_shopify_orders WHERE email_norm IS NOT NULL
UNION ALL
SELECT DISTINCT 'klaviyo', profile_id, email_norm, occurred_at_utc
FROM stg_klaviyo_events WHERE email_norm IS NOT NULL
UNION ALL
SELECT DISTINCT 'posthog', person_id, email_norm, first_event_at_utc
FROM stg_posthog_quiz_people WHERE email_norm IS NOT NULL;

CREATE OR REPLACE TABLE identity_edges AS
SELECT DISTINCT a.email_norm AS email_a, b.email_norm AS email_b, 'shopify_customer_id' AS rule, a.source_id AS evidence
FROM identity_sources a JOIN identity_sources b
  ON a.source = 'shopify' AND b.source = 'shopify' AND a.source_id = b.source_id AND a.email_norm < b.email_norm
UNION
SELECT DISTINCT a.email_norm, b.email_norm, 'klaviyo_profile_id', a.source_id
FROM identity_sources a JOIN identity_sources b
  ON a.source = 'klaviyo' AND b.source = 'klaviyo' AND a.source_id = b.source_id AND a.email_norm < b.email_norm
UNION
SELECT DISTINCT s.email_norm, k.email_norm, 'order_id_link', s.order_name
FROM stg_shopify_orders s
JOIN stg_klaviyo_events k ON k.stream = 'placed_order' AND k.source_event_id = s.shopify_order_id
WHERE s.email_norm IS NOT NULL AND k.email_norm IS NOT NULL AND s.email_norm <> k.email_norm;

-- Each address takes the alphabetically first address it is connected to as its root.
CREATE OR REPLACE TABLE identity_roots AS
WITH RECURSIVE
    addresses AS (SELECT DISTINCT email_norm FROM identity_sources),
    undirected AS (
        SELECT email_a AS a, email_b AS b FROM identity_edges
        UNION SELECT email_b, email_a FROM identity_edges
    ),
    reach(email_norm, reachable) AS (
        SELECT email_norm, email_norm FROM addresses
        UNION
        SELECT r.email_norm, u.b FROM reach r JOIN undirected u ON u.a = r.reachable
    )
SELECT email_norm, min(reachable) AS root_email
FROM reach
GROUP BY email_norm;

CREATE OR REPLACE TABLE customer_identity AS
SELECT
    'c_' || left(md5(r.root_email), 12) AS customer_key,
    s.source,
    s.source_id,
    s.email_norm,
    CASE
        WHEN s.email_norm = r.root_email THEN 'exact_email'
        ELSE (SELECT min(rule) FROM identity_edges e WHERE s.email_norm IN (e.email_a, e.email_b))
    END AS match_rule
FROM (SELECT DISTINCT source, source_id, email_norm FROM identity_sources) s
JOIN identity_roots r USING (email_norm);

-- Pairs that look like one person but fail every rule above.
CREATE OR REPLACE TABLE identity_review AS
WITH keyed AS (
    SELECT DISTINCT
        r.root_email,
        r.email_norm,
        regexp_replace(split_part(r.email_norm, '@', 1), '\+.*$', '') AS local_no_plus,
        split_part(r.email_norm, '@', 2) AS domain
    FROM identity_roots r
),
loose AS (
    SELECT
        root_email,
        email_norm,
        CASE WHEN domain IN ('gmail.com', 'googlemail.com')
             THEN replace(local_no_plus, '.', '') || '@gmail.com'
             ELSE local_no_plus || '@' || domain END AS loose_key
    FROM keyed
)
SELECT DISTINCT
    a.email_norm AS email_a,
    b.email_norm AS email_b,
    'plus-address or gmail-dot variant' AS reason
FROM loose a JOIN loose b
  ON a.loose_key = b.loose_key AND a.root_email < b.root_email;

CREATE OR REPLACE TABLE dim_customer AS
SELECT
    ci.customer_key,
    min(r.root_email)                                     AS primary_email_norm,
    count(DISTINCT ci.email_norm)                         AS email_count,
    min(s.seen_at)                                        AS first_seen_at_utc,
    arg_min(s.source, s.seen_at)                          AS first_seen_source,
    bool_or(ci.source = 'shopify')                        AS in_shopify,
    bool_or(ci.source = 'klaviyo')                        AS in_klaviyo,
    bool_or(ci.source = 'posthog')                        AS in_posthog,
    CASE WHEN count(DISTINCT ci.email_norm) > 1 THEN 'merged' ELSE 'single_email' END AS resolution,
    bool_or(ci.email_norm IN (SELECT email_a FROM identity_review UNION SELECT email_b FROM identity_review)) AS needs_review
FROM customer_identity ci
JOIN identity_roots r ON r.email_norm = ci.email_norm
JOIN identity_sources s ON s.email_norm = ci.email_norm AND s.source = ci.source AND s.source_id = ci.source_id
GROUP BY ci.customer_key;

-- Address-level lookup used by the fact tables.
CREATE OR REPLACE TABLE email_to_customer AS
SELECT DISTINCT email_norm, customer_key FROM customer_identity;
