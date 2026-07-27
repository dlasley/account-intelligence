-- Phase 3: Cross-model narrative audit harness tables.
-- ADR-016 D4, D11, D12.
-- Two append-only tables — no deleted_at, no updated_at (ADR-005 §Decision 8).
-- audit_source discriminates CI / nightly / manual rows for trend analysis and
-- retention policy: CI rows (audit_source = 'ci') older than 90 days are pruned
-- by a scheduled cron or Supabase scheduled function.  Nightly rows are retained
-- indefinitely.  See ADR-016 D5 / D11.  Actual pruning is not part of this migration.

-- ---------------------------------------------------------------------------
-- narrative_audits
-- One row per criterion per audit invocation (5 rows per narrative per run).
-- ---------------------------------------------------------------------------

CREATE TABLE narrative_audits (
    id                uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      uuid          NOT NULL REFERENCES workspaces(id),
    narrative_id      uuid          NOT NULL REFERENCES narratives(id),
    audit_run_id      text          NOT NULL
                      CHECK (
                          audit_run_id ~ '^(ci|nightly|manual)_[A-Za-z0-9_-]{1,200}$'
                      ),
    -- structured prefix convention (ADR-016 D11):
    --   ci_<sha8>_<unix-ts>      e.g. ci_a1b2c3d4_1746316800
    --   nightly_<YYYY-MM-DD>     e.g. nightly_2026-05-03
    --   manual_<hint>_<unix-ts>  e.g. manual_local_1746316800
    audit_source      text          NOT NULL DEFAULT 'manual'
                      CHECK (audit_source IN ('ci', 'nightly', 'manual')),
    criterion         text          NOT NULL
                      CHECK (criterion IN (
                          'faithfulness', 'coverage', 'calibration',
                          'hallucination', 'tone_fit'
                      )),
    passed            boolean       NOT NULL,
    score             smallint      NULL,             -- 1-5 for scored criteria; NULL for binary
    reasoning         text          NOT NULL,
    details           jsonb         NOT NULL DEFAULT '{}',
    -- faithfulness:    {"cited_signal_ids": [...]}
    -- coverage:        {"missing_dimensions": [...]}
    -- hallucination:   {"invented_items": [...]}
    auditor_model     text          NOT NULL,
    audited_at        timestamptz   NOT NULL DEFAULT now(),
    prompt_tokens     integer       NOT NULL,
    completion_tokens integer       NOT NULL,
    cost_usd          numeric(10,6) NOT NULL
);

CREATE INDEX idx_narrative_audits_narrative_id ON narrative_audits(narrative_id);
CREATE INDEX idx_narrative_audits_audit_run_id ON narrative_audits(audit_run_id);
CREATE INDEX idx_narrative_audits_audited_at   ON narrative_audits(audited_at DESC);
CREATE INDEX idx_narrative_audits_workspace_criterion
    ON narrative_audits(workspace_id, criterion, audited_at DESC);

ALTER TABLE narrative_audits ENABLE ROW LEVEL SECURITY;

CREATE POLICY narrative_audits_workspace_isolation
    ON narrative_audits
    FOR ALL
    USING  (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());

GRANT ALL ON narrative_audits TO authenticated, service_role;

COMMENT ON TABLE narrative_audits IS
    'Append-only audit criterion rows (5 per narrative per run). '
    'CI rows (audit_source = ''ci'') older than 90 days should be pruned '
    'by a periodic scheduled function. Nightly rows are retained indefinitely.';

-- ---------------------------------------------------------------------------
-- narrative_audit_runs
-- One aggregate row per (narrative_id, audit_run_id).
-- Computed deterministically in Python from the 5 criterion rows (no LLM call).
-- ---------------------------------------------------------------------------

CREATE TABLE narrative_audit_runs (
    id                  uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        uuid          NOT NULL REFERENCES workspaces(id),
    narrative_id        uuid          NOT NULL REFERENCES narratives(id),
    audit_run_id        text          NOT NULL
                        CHECK (
                            audit_run_id ~ '^(ci|nightly|manual)_[A-Za-z0-9_-]{1,200}$'
                        ),
    overall_passed      boolean       NOT NULL,
    hard_gate_failures  integer       NOT NULL DEFAULT 0,
    warning_failures    integer       NOT NULL DEFAULT 0,
    score_summary       jsonb         NOT NULL DEFAULT '{}',
    -- shape: {
    --   "faithfulness": {"passed": bool, "score": int|null},
    --   "coverage":     {"passed": bool},
    --   "calibration":  {"passed": bool, "score": int|null},
    --   "hallucination": {"passed": bool},
    --   "tone_fit":     {"passed": bool}
    -- }
    audit_source        text          NOT NULL
                        CHECK (audit_source IN ('ci', 'nightly', 'manual')),
    auditor_model       text          NOT NULL,
    audited_at          timestamptz   NOT NULL DEFAULT now(),
    prompt_tokens       integer       NOT NULL,
    completion_tokens   integer       NOT NULL,
    cost_usd            numeric(10,6) NOT NULL
);

ALTER TABLE narrative_audit_runs
    ADD CONSTRAINT uq_narrative_audit_runs UNIQUE (narrative_id, audit_run_id);

CREATE INDEX idx_narrative_audit_runs_narrative_id ON narrative_audit_runs(narrative_id);
CREATE INDEX idx_narrative_audit_runs_audit_run_id ON narrative_audit_runs(audit_run_id);
CREATE INDEX idx_narrative_audit_runs_audited_at   ON narrative_audit_runs(audited_at DESC);

ALTER TABLE narrative_audit_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY narrative_audit_runs_workspace_isolation
    ON narrative_audit_runs
    FOR ALL
    USING  (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());

GRANT ALL ON narrative_audit_runs TO authenticated, service_role;

COMMENT ON TABLE narrative_audit_runs IS
    'One aggregate row per (narrative_id, audit_run_id). '
    'Written atomically alongside the 5 narrative_audits criterion rows. '
    'overall_passed is False when any hard-gate criterion (faithfulness, coverage, '
    'calibration, hallucination) has passed=False. tone_fit failures increment '
    'warning_failures only.';
