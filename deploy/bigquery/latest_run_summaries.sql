CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.latest_run_summaries`
OPTIONS (
  description = "One latest reporting row per immutable research run ID"
)
AS
SELECT * EXCEPT (_dedupe_rank)
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY run_id
      ORDER BY ingested_at DESC
    ) AS _dedupe_rank
  FROM `__PROJECT_ID__.__DATASET_ID__.__TABLE_ID__`
)
WHERE _dedupe_rank = 1;
