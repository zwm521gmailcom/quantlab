PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS datasets (
    entity_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'validated', 'published', 'deprecated')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dataset_versions (
    entity_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    path TEXT NOT NULL,
    row_count INTEGER,
    fields_json TEXT NOT NULL DEFAULT '[]',
    date_min TEXT,
    date_max TEXT,
    manifest_hash TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'validated', 'published', 'deprecated')),
    quality_status TEXT NOT NULL DEFAULT 'passed' CHECK (quality_status IN ('passed', 'warning', 'failed', 'needs_review')),
    PRIMARY KEY (entity_id, version_id),
    FOREIGN KEY (entity_id) REFERENCES datasets(entity_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS dataset_scan_audits (
    audit_id TEXT PRIMARY KEY,
    audit_type TEXT NOT NULL CHECK (audit_type IN ('manual_rescan')),
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    changed_count INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS factors (
    entity_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '未分类',
    status TEXT NOT NULL CHECK (status IN ('draft', 'validated', 'published', 'deprecated')),
    asset_class TEXT NOT NULL DEFAULT 'cn_a'
);
CREATE TABLE IF NOT EXISTS factor_versions (
    entity_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    formula TEXT NOT NULL,
    input_fields_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT '',
    frequency TEXT NOT NULL DEFAULT '',
    missing_policy TEXT NOT NULL DEFAULT '',
    pit_policy TEXT NOT NULL DEFAULT '',
    pit_lineage_json TEXT NOT NULL DEFAULT '{}',
    upstream_factor_versions_json TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual', 'automatic', 'import')),
    author TEXT NOT NULL DEFAULT '未登记',
    code_hash TEXT NOT NULL DEFAULT '',
    generation_run_id TEXT NOT NULL DEFAULT '',
    artifact_id TEXT NOT NULL DEFAULT '',
    last_verified_at TEXT,
    quality_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'validated', 'published', 'deprecated')),
    quality_status TEXT NOT NULL DEFAULT 'passed' CHECK (quality_status IN ('passed', 'warning', 'failed', 'needs_review')),
    PRIMARY KEY (entity_id, version_id),
    FOREIGN KEY (entity_id) REFERENCES factors(entity_id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, dataset_version_id)
        REFERENCES dataset_versions(entity_id, version_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS factor_diagnostics (
    entity_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    diagnosed_at TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}',
    diagnostic_run_id TEXT NOT NULL DEFAULT '',
    artifact_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (entity_id, version_id),
    FOREIGN KEY (entity_id, version_id)
        REFERENCES factor_versions(entity_id, version_id) ON DELETE RESTRICT,
    FOREIGN KEY (diagnostic_run_id) REFERENCES research_runs(run_id) ON DELETE RESTRICT,
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS factor_calculation_runs (
    calculation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_entity_id TEXT NOT NULL,
    factor_version_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    label_definition TEXT NOT NULL DEFAULT 't+1 open -> t+2 close',
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_message TEXT,
    params_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    row_count INTEGER,
    coverage REAL,
    missing_rows INTEGER,
    ic_mean REAL,
    ic_positive_ratio REAL,
    ic_std REAL,
    turnover_mean REAL,
    effective_days INTEGER,
    icir REAL,
    top_bottom_spread REAL,
    monotonicity REAL,
    factor_mean REAL,
    factor_std REAL,
    factor_min REAL,
    factor_max REAL,
    factor_skew REAL,
    factor_kurtosis REAL,
    quantiles_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (factor_entity_id, factor_version_id)
        REFERENCES factor_versions(entity_id, version_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_factor_calculation_factor
    ON factor_calculation_runs(factor_entity_id, factor_version_id, calculation_id DESC);
CREATE TABLE IF NOT EXISTS models (
    entity_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'validated', 'published', 'deprecated'))
);
CREATE TABLE IF NOT EXISTS model_versions (
    entity_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    dataset_id TEXT,
    dataset_version_id TEXT,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'validated', 'published', 'deprecated')),
    quality_status TEXT NOT NULL DEFAULT 'passed' CHECK (quality_status IN ('passed', 'warning', 'failed', 'needs_review')),
    content_hash TEXT NOT NULL DEFAULT '',
    CHECK ((dataset_id IS NULL) = (dataset_version_id IS NULL)),
    PRIMARY KEY (entity_id, version_id),
    FOREIGN KEY (entity_id) REFERENCES models(entity_id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, dataset_version_id)
        REFERENCES dataset_versions(entity_id, version_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS model_factor_versions (
    model_entity_id TEXT NOT NULL,
    model_version_id TEXT NOT NULL,
    factor_entity_id TEXT NOT NULL,
    factor_version_id TEXT NOT NULL,
    PRIMARY KEY (model_entity_id, model_version_id, factor_entity_id, factor_version_id),
    FOREIGN KEY (model_entity_id, model_version_id)
        REFERENCES model_versions(entity_id, version_id) ON DELETE RESTRICT,
    FOREIGN KEY (factor_entity_id, factor_version_id)
        REFERENCES factor_versions(entity_id, version_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS strategies (
    entity_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'validated', 'published', 'deprecated'))
);
CREATE TABLE IF NOT EXISTS strategy_versions (
    entity_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    model_entity_id TEXT,
    model_version_id TEXT,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'validated', 'published', 'deprecated')),
    quality_status TEXT NOT NULL DEFAULT 'passed' CHECK (quality_status IN ('passed', 'warning', 'failed', 'needs_review')),
    content_hash TEXT NOT NULL DEFAULT '',
    CHECK ((model_entity_id IS NULL) = (model_version_id IS NULL)),
    PRIMARY KEY (entity_id, version_id),
    FOREIGN KEY (entity_id) REFERENCES strategies(entity_id) ON DELETE RESTRICT,
    FOREIGN KEY (model_entity_id, model_version_id)
        REFERENCES model_versions(entity_id, version_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS strategy_factor_versions (
    strategy_entity_id TEXT NOT NULL,
    strategy_version_id TEXT NOT NULL,
    factor_entity_id TEXT NOT NULL,
    factor_version_id TEXT NOT NULL,
    PRIMARY KEY (strategy_entity_id, strategy_version_id, factor_entity_id, factor_version_id),
    FOREIGN KEY (strategy_entity_id, strategy_version_id)
        REFERENCES strategy_versions(entity_id, version_id) ON DELETE RESTRICT,
    FOREIGN KEY (factor_entity_id, factor_version_id)
        REFERENCES factor_versions(entity_id, version_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS run_registry (
    run_id TEXT PRIMARY KEY CHECK (
        length(run_id) = 20 AND
        run_id GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'
    ),
    run_type TEXT NOT NULL CHECK (run_type IN ('research', 'model_training', 'backtest')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS model_training_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    model_entity_id TEXT,
    model_version_id TEXT,
    dataset_id TEXT,
    dataset_version_id TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    CHECK ((model_entity_id IS NULL) = (model_version_id IS NULL)),
    CHECK ((dataset_id IS NULL) = (dataset_version_id IS NULL)),
    FOREIGN KEY (run_id) REFERENCES run_registry(run_id) ON DELETE RESTRICT,
    FOREIGN KEY (model_entity_id, model_version_id)
        REFERENCES model_versions(entity_id, version_id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, dataset_version_id)
        REFERENCES dataset_versions(entity_id, version_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '未命名研究',
    research_type TEXT NOT NULL DEFAULT 'manual' CHECK (research_type IN ('manual', 'automatic')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    config_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    copied_from_run_id TEXT,
    parent_run_id TEXT,
    error_message TEXT,
    FOREIGN KEY (run_id) REFERENCES run_registry(run_id) ON DELETE RESTRICT,
    FOREIGN KEY (copied_from_run_id) REFERENCES research_runs(run_id) ON DELETE RESTRICT,
    FOREIGN KEY (parent_run_id) REFERENCES research_runs(run_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    strategy_entity_id TEXT,
    strategy_version_id TEXT,
    dataset_id TEXT,
    dataset_version_id TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    submission_token TEXT UNIQUE,
    CHECK ((strategy_entity_id IS NULL) = (strategy_version_id IS NULL)),
    CHECK ((dataset_id IS NULL) = (dataset_version_id IS NULL)),
    FOREIGN KEY (run_id) REFERENCES run_registry(run_id) ON DELETE RESTRICT,
    FOREIGN KEY (strategy_entity_id, strategy_version_id)
        REFERENCES strategy_versions(entity_id, version_id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, dataset_version_id)
        REFERENCES dataset_versions(entity_id, version_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS backtest_drafts (
    draft_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    factor_version_ids_json TEXT NOT NULL DEFAULT '[]',
    strategy_entity_id TEXT,
    strategy_version_id TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backtest_plans (
    plan_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'running', 'completed', 'stopped')),
    closed INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backtest_plan_items (
    item_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    selected INTEGER NOT NULL DEFAULT 1 CHECK (selected IN (0, 1)),
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'queued', 'running', 'completed', 'failed', 'skipped')),
    run_id TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES backtest_plans(plan_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS backtest_plan_items_plan_idx ON backtest_plan_items(plan_id, sort_order);
CREATE TABLE IF NOT EXISTS backtest_model_versions (
    backtest_run_id TEXT NOT NULL,
    fold_index INTEGER NOT NULL,
    model_entity_id TEXT NOT NULL,
    model_version_id TEXT NOT NULL,
    train_start TEXT,
    train_end TEXT,
    test_start TEXT,
    test_end TEXT,
    PRIMARY KEY (backtest_run_id, fold_index),
    FOREIGN KEY (backtest_run_id) REFERENCES backtest_runs(run_id) ON DELETE RESTRICT,
    FOREIGN KEY (model_entity_id, model_version_id)
        REFERENCES model_versions(entity_id, version_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS backtest_steps (
    run_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    started_at TEXT,
    finished_at TEXT,
    error_message TEXT,
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (run_id, ordinal),
    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    artifact_role TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES run_registry(run_id) ON DELETE RESTRICT,
    UNIQUE (run_id, artifact_role, content_hash)
);
CREATE TABLE IF NOT EXISTS run_sequences (
    stamp TEXT PRIMARY KEY,
    next_serial INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS system_audit_logs (
    audit_id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS datasets_lifecycle_state_machine
BEFORE UPDATE OF status ON datasets
WHEN NOT (
    NEW.status = OLD.status OR
    (OLD.status = 'draft' AND NEW.status = 'validated') OR
    (OLD.status = 'validated' AND NEW.status = 'published') OR
    (OLD.status = 'published' AND NEW.status = 'deprecated')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid lifecycle status transition');
END;

CREATE TRIGGER IF NOT EXISTS factors_lifecycle_state_machine
BEFORE UPDATE OF status ON factors
WHEN NOT (
    NEW.status = OLD.status OR
    (OLD.status = 'draft' AND NEW.status = 'validated') OR
    (OLD.status = 'validated' AND NEW.status = 'published') OR
    (OLD.status = 'published' AND NEW.status = 'deprecated')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid lifecycle status transition');
END;

CREATE TRIGGER IF NOT EXISTS models_lifecycle_state_machine
BEFORE UPDATE OF status ON models
WHEN NOT (
    NEW.status = OLD.status OR
    (OLD.status = 'draft' AND NEW.status = 'validated') OR
    (OLD.status = 'validated' AND NEW.status = 'published') OR
    (OLD.status = 'published' AND NEW.status = 'deprecated')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid lifecycle status transition');
END;

CREATE TRIGGER IF NOT EXISTS strategies_lifecycle_state_machine
BEFORE UPDATE OF status ON strategies
WHEN NOT (
    NEW.status = OLD.status OR
    (OLD.status = 'draft' AND NEW.status = 'validated') OR
    (OLD.status = 'validated' AND NEW.status = 'published') OR
    (OLD.status = 'published' AND NEW.status = 'deprecated')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid lifecycle status transition');
END;

CREATE TRIGGER IF NOT EXISTS dataset_versions_lifecycle_state_machine
BEFORE UPDATE OF status ON dataset_versions
WHEN NOT (
    NEW.status = OLD.status OR
    (OLD.status = 'draft' AND NEW.status = 'validated') OR
    (OLD.status = 'validated' AND NEW.status = 'published') OR
    (OLD.status = 'published' AND NEW.status = 'deprecated')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid lifecycle status transition');
END;

CREATE TRIGGER IF NOT EXISTS factor_versions_lifecycle_state_machine
BEFORE UPDATE OF status ON factor_versions
WHEN NOT (
    NEW.status = OLD.status OR
    (OLD.status = 'draft' AND NEW.status = 'validated') OR
    (OLD.status = 'validated' AND NEW.status = 'published') OR
    (OLD.status = 'published' AND NEW.status = 'deprecated')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid lifecycle status transition');
END;

CREATE TRIGGER IF NOT EXISTS model_versions_lifecycle_state_machine
BEFORE UPDATE OF status ON model_versions
WHEN NOT (
    NEW.status = OLD.status OR
    (OLD.status = 'draft' AND NEW.status = 'validated') OR
    (OLD.status = 'validated' AND NEW.status = 'published') OR
    (OLD.status = 'published' AND NEW.status = 'deprecated')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid lifecycle status transition');
END;

CREATE TRIGGER IF NOT EXISTS strategy_versions_lifecycle_state_machine
BEFORE UPDATE OF status ON strategy_versions
WHEN NOT (
    NEW.status = OLD.status OR
    (OLD.status = 'draft' AND NEW.status = 'validated') OR
    (OLD.status = 'validated' AND NEW.status = 'published') OR
    (OLD.status = 'published' AND NEW.status = 'deprecated')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid lifecycle status transition');
END;

CREATE TRIGGER IF NOT EXISTS dataset_versions_immutable_published
BEFORE UPDATE ON dataset_versions
WHEN OLD.status IN ('published', 'deprecated') AND (
    NEW.path IS NOT OLD.path OR
    NEW.row_count IS NOT OLD.row_count OR
    NEW.fields_json IS NOT OLD.fields_json OR
    NEW.date_min IS NOT OLD.date_min OR
    NEW.date_max IS NOT OLD.date_max OR
    NEW.manifest_hash IS NOT OLD.manifest_hash OR
    NEW.metadata_json IS NOT OLD.metadata_json
)
BEGIN
    SELECT RAISE(ABORT, 'published dataset version metadata is immutable');
END;

CREATE TRIGGER IF NOT EXISTS factor_versions_immutable_published
BEFORE UPDATE ON factor_versions
WHEN OLD.status IN ('published', 'deprecated') AND (
    NEW.dataset_id IS NOT OLD.dataset_id OR
    NEW.dataset_version_id IS NOT OLD.dataset_version_id OR
    NEW.formula IS NOT OLD.formula OR
    NEW.input_fields_json IS NOT OLD.input_fields_json OR
    NEW.source IS NOT OLD.source OR
    NEW.direction IS NOT OLD.direction OR
    NEW.frequency IS NOT OLD.frequency OR
    NEW.missing_policy IS NOT OLD.missing_policy OR
    NEW.pit_policy IS NOT OLD.pit_policy OR
    NEW.pit_lineage_json IS NOT OLD.pit_lineage_json OR
    NEW.upstream_factor_versions_json IS NOT OLD.upstream_factor_versions_json OR
    NEW.origin IS NOT OLD.origin OR
    NEW.author IS NOT OLD.author OR
    NEW.code_hash IS NOT OLD.code_hash OR
    NEW.generation_run_id IS NOT OLD.generation_run_id OR
    NEW.artifact_id IS NOT OLD.artifact_id OR
    NEW.last_verified_at IS NOT OLD.last_verified_at OR
    NEW.quality_json IS NOT OLD.quality_json
)
BEGIN
    SELECT RAISE(ABORT, 'published factor version is immutable');
END;

CREATE TRIGGER IF NOT EXISTS model_versions_immutable_published
BEFORE UPDATE ON model_versions
WHEN OLD.status IN ('published', 'deprecated') AND (
    NEW.dataset_id IS NOT OLD.dataset_id OR
    NEW.dataset_version_id IS NOT OLD.dataset_version_id OR
    NEW.parameters_json IS NOT OLD.parameters_json
)
BEGIN
    SELECT RAISE(ABORT, 'published model version is immutable');
END;

CREATE TRIGGER IF NOT EXISTS strategy_versions_immutable_published
BEFORE UPDATE ON strategy_versions
WHEN OLD.status IN ('published', 'deprecated') AND (
    NEW.model_entity_id IS NOT OLD.model_entity_id OR
    NEW.model_version_id IS NOT OLD.model_version_id OR
    NEW.parameters_json IS NOT OLD.parameters_json
)
BEGIN
    SELECT RAISE(ABORT, 'published strategy version is immutable');
END;

CREATE TRIGGER IF NOT EXISTS dataset_versions_no_published_draft
BEFORE UPDATE OF status ON dataset_versions
WHEN OLD.status IN ('published', 'deprecated') AND NEW.status = 'draft'
BEGIN
    SELECT RAISE(ABORT, 'published version cannot return to draft');
END;

CREATE TRIGGER IF NOT EXISTS factor_versions_no_published_draft
BEFORE UPDATE OF status ON factor_versions
WHEN OLD.status IN ('published', 'deprecated') AND NEW.status = 'draft'
BEGIN
    SELECT RAISE(ABORT, 'published version cannot return to draft');
END;

CREATE TRIGGER IF NOT EXISTS model_versions_no_published_draft
BEFORE UPDATE OF status ON model_versions
WHEN OLD.status IN ('published', 'deprecated') AND NEW.status = 'draft'
BEGIN
    SELECT RAISE(ABORT, 'published version cannot return to draft');
END;

CREATE TRIGGER IF NOT EXISTS strategy_versions_no_published_draft
BEFORE UPDATE OF status ON strategy_versions
WHEN OLD.status IN ('published', 'deprecated') AND NEW.status = 'draft'
BEGIN
    SELECT RAISE(ABORT, 'published version cannot return to draft');
END;

CREATE TRIGGER IF NOT EXISTS model_training_runs_initial_status
BEFORE INSERT ON model_training_runs
WHEN NEW.status != 'queued'
BEGIN
    SELECT RAISE(ABORT, 'run must start queued');
END;

CREATE TRIGGER IF NOT EXISTS research_runs_initial_status
BEFORE INSERT ON research_runs
WHEN NEW.status != 'queued'
BEGIN
    SELECT RAISE(ABORT, 'run must start queued');
END;

CREATE TRIGGER IF NOT EXISTS backtest_runs_initial_status
BEFORE INSERT ON backtest_runs
WHEN NEW.status != 'queued'
BEGIN
    SELECT RAISE(ABORT, 'run must start queued');
END;

CREATE TRIGGER IF NOT EXISTS model_training_runs_state_machine
BEFORE UPDATE OF status ON model_training_runs
WHEN NOT (
    NEW.status = OLD.status OR
    (OLD.status = 'queued' AND NEW.status = 'running') OR
    (OLD.status = 'running' AND NEW.status IN ('completed', 'failed'))
)
BEGIN
    SELECT RAISE(ABORT, 'invalid run status transition');
END;

CREATE TRIGGER IF NOT EXISTS research_runs_state_machine
BEFORE UPDATE OF status ON research_runs
WHEN NOT (
    NEW.status = OLD.status OR
    (OLD.status = 'queued' AND NEW.status = 'running') OR
    (OLD.status = 'running' AND NEW.status IN ('completed', 'failed'))
)
BEGIN
    SELECT RAISE(ABORT, 'invalid run status transition');
END;

CREATE TRIGGER IF NOT EXISTS backtest_runs_state_machine
BEFORE UPDATE OF status ON backtest_runs
WHEN NOT (
    NEW.status = OLD.status OR
    (OLD.status = 'queued' AND NEW.status = 'running') OR
    (OLD.status = 'failed' AND NEW.status = 'running') OR
    (OLD.status = 'running' AND NEW.status IN ('completed', 'failed'))
)
BEGIN
    SELECT RAISE(ABORT, 'invalid run status transition');
END;
