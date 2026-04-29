CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE pipelines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    current_stage VARCHAR(128),
    global_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_pipelines_status CHECK (
        status IN ('PENDING', 'RUNNING', 'SUSPENDED', 'COMPLETED', 'FAILED')
    )
);

CREATE TABLE stages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_id UUID NOT NULL,
    name VARCHAR(128) NOT NULL,
    agent_role VARCHAR(128) NOT NULL,
    requires_human_approval BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    output_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_stages_pipeline FOREIGN KEY (pipeline_id)
        REFERENCES pipelines (id)
        ON DELETE CASCADE,
    CONSTRAINT ck_stages_status CHECK (
        status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'REJECTED')
    ),
    CONSTRAINT uq_stages_pipeline_name UNIQUE (pipeline_id, name)
);

CREATE TABLE checkpoint_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stage_id UUID NOT NULL,
    decision VARCHAR(16) NOT NULL,
    feedback_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_checkpoint_feedback_stage FOREIGN KEY (stage_id)
        REFERENCES stages (id)
        ON DELETE CASCADE,
    CONSTRAINT ck_checkpoint_feedback_decision CHECK (
        decision IN ('APPROVE', 'REJECT')
    ),
    CONSTRAINT ck_checkpoint_feedback_reject_reason CHECK (
        decision <> 'REJECT'
        OR feedback_reason IS NOT NULL
    )
);

CREATE INDEX idx_pipelines_status ON pipelines (status);
CREATE INDEX idx_pipelines_updated_at ON pipelines (updated_at);
CREATE INDEX idx_stages_pipeline_id ON stages (pipeline_id);
CREATE INDEX idx_stages_status ON stages (status);
CREATE INDEX idx_checkpoint_feedback_stage_id ON checkpoint_feedback (stage_id);
