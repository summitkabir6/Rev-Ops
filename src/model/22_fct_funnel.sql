-- One row per person per quiz screen they reached, in screen order.
-- step_order is the person's own sequence (1, 2, 3...), so a gap between step_index
-- and step_order shows a screen the person skipped or that failed to fire.

CREATE OR REPLACE TABLE fct_funnel AS
SELECT
    p.person_id,
    c.customer_key,
    s.step_index,
    s.step_id,
    row_number() OVER (PARTITION BY p.person_id ORDER BY s.step_index) AS step_order,
    count(*) OVER (PARTITION BY p.person_id)                           AS steps_reached,
    p.first_event_at_utc,
    CAST(timezone('__TIMEZONE__', CAST(p.first_event_at_utc AS TIMESTAMPTZ)) AS DATE) AS first_seen_date_local,
    p.email_submitted_at_utc IS NOT NULL                               AS submitted_email,
    p.store_clicked_at_utc IS NOT NULL                                 AS clicked_store,
    p.utm_source,
    p.utm_campaign,
    p.utm_content,
    p.is_production,
    p.is_test OR coalesce(p.email_norm IN (SELECT email_norm FROM internal_emails), FALSE) AS is_internal
FROM stg_posthog_quiz_people p
JOIN stg_posthog_quiz_steps s USING (person_id)
LEFT JOIN email_to_customer c ON c.email_norm = p.email_norm;
