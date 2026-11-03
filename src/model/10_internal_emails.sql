-- Internal addresses are excluded from lead and tap counts in every source. Two ways in:
--   1. anyone who ever sent an event flagged is_test;
--   2. any address whose sha256 is listed in config/internal_emails.yml, for test runs
--      that were never flagged.

CREATE OR REPLACE TABLE internal_emails AS
WITH known AS (
    SELECT email_norm FROM stg_klaviyo_events WHERE email_norm IS NOT NULL
    UNION SELECT email_norm FROM stg_posthog_quiz_people WHERE email_norm IS NOT NULL
    UNION SELECT email_norm FROM stg_shopify_orders WHERE email_norm IS NOT NULL
),
flagged AS (
    SELECT email_norm, 'is_test event' AS evidence FROM stg_klaviyo_events WHERE is_test AND email_norm IS NOT NULL
    UNION SELECT email_norm, 'is_test event' FROM stg_posthog_quiz_people WHERE is_test AND email_norm IS NOT NULL
    UNION SELECT email_norm, 'listed in config' FROM known WHERE sha256(email_norm) IN (__INTERNAL_HASHES__)
)
SELECT email_norm, min(evidence) AS evidence
FROM flagged
GROUP BY email_norm;
