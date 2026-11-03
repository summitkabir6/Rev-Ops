-- One row per email delivered by the quiz results flow, with the consent state the
-- recipient was in at that moment. Two consent signals exist and they mean different things:
--   consent_state_at_send   account-level email marketing consent: the latest Klaviyo
--                           subscribe or unsubscribe event at or before the send.
--   quiz_consent_at_send    the CASL box on the quiz: marketing_consent on the latest
--                           quiz submission at or before the send.
-- The results email (E1) goes to everyone who submits, by design; follow-ups need quiz consent.

CREATE OR REPLACE TABLE fct_send AS
WITH sends AS (
    SELECT * FROM stg_klaviyo_events WHERE stream = 'received_email_quiz_flow'
),
consent_events AS (
    SELECT profile_id, occurred_at_utc,
           CASE stream WHEN 'subscribed_email' THEN 'subscribed' ELSE 'unsubscribed' END AS state
    FROM stg_klaviyo_events
    WHERE stream IN ('subscribed_email', 'unsubscribed_email')
),
quiz_submissions AS (
    SELECT profile_id, occurred_at_utc, marketing_consent
    FROM stg_klaviyo_events
    WHERE stream = 'quiz_completed'
)
SELECT
    s.event_id                                                       AS send_id,
    c.customer_key,
    s.message_id,
    s.flow_id,
    s.occurred_at_utc                                                AS sent_at_utc,
    CAST(timezone('__TIMEZONE__', CAST(s.occurred_at_utc AS TIMESTAMPTZ)) AS DATE) AS sent_date_local,
    coalesce((
        SELECT arg_max(ce.state, ce.occurred_at_utc)
        FROM consent_events ce
        WHERE ce.profile_id = s.profile_id AND ce.occurred_at_utc <= s.occurred_at_utc
    ), 'no_consent_event_in_window')                                 AS consent_state_at_send,
    (
        SELECT arg_max(q.marketing_consent, q.occurred_at_utc)
        FROM quiz_submissions q
        WHERE q.profile_id = s.profile_id AND q.occurred_at_utc <= s.occurred_at_utc
    )                                                                AS quiz_consent_at_send,
    s.email_norm IN (SELECT email_norm FROM internal_emails)         AS is_internal
FROM sends s
LEFT JOIN email_to_customer c ON c.email_norm = s.email_norm;
