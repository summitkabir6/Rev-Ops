-- Staging: one row per PostHog person who touched the quiz, plus the screens they saw.

CREATE OR REPLACE TABLE stg_posthog_quiz_people AS
SELECT
    payload->>'person_id'                                                 AS person_id,
    nullif(lower(trim(payload->>'person_email')), '')                     AS email_norm,
    CAST(CAST(payload->>'first_event_at' AS TIMESTAMPTZ) AS TIMESTAMP)    AS first_event_at_utc,
    payload->>'host'                                                      AS host,
    payload->>'app'                                                       AS app,
    payload->>'utm_source'                                                AS utm_source,
    payload->>'utm_campaign'                                              AS utm_campaign,
    payload->>'utm_content'                                               AS utm_content,
    payload->>'any_test' = '1'                                            AS is_test,
    CAST(CAST(payload->>'started_at' AS TIMESTAMPTZ) AS TIMESTAMP)        AS started_at_utc,
    CAST(CAST(payload->>'email_submitted_at' AS TIMESTAMPTZ) AS TIMESTAMP) AS email_submitted_at_utc,
    CAST(CAST(payload->>'completed_at' AS TIMESTAMPTZ) AS TIMESTAMP)      AS completed_at_utc,
    CAST(CAST(payload->>'store_clicked_at' AS TIMESTAMPTZ) AS TIMESTAMP)  AS store_clicked_at_utc,
    payload->>'steps_seen'                                                AS steps_seen,
    payload->>'host' = '__QUIZ_HOST__' AND payload->>'app' = '__QUIZ_APP__' AS is_production,
    loaded_at
FROM raw_posthog
QUALIFY row_number() OVER (PARTITION BY payload->>'person_id' ORDER BY loaded_at DESC) = 1;

CREATE OR REPLACE TABLE stg_posthog_quiz_steps AS
SELECT
    p.person_id,
    CAST(split_part(step, ':', 1) AS INTEGER) AS step_index,
    split_part(step, ':', 2)                  AS step_id
FROM stg_posthog_quiz_people AS p,
     unnest(string_split(p.steps_seen, ';')) AS t(step)
WHERE p.steps_seen IS NOT NULL;
