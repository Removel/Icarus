CREATE TABLE schema_version (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE wake_requests (
    id uuid PRIMARY KEY,
    boot_id uuid NOT NULL,
    chat_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('waiting','sent','accepted','resolved','no_action','failed')),
    reason text NOT NULL,
    rule_id text,
    payload jsonb NOT NULL,
    decision_detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    outcome jsonb,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at timestamptz,
    accepted_at timestamptz,
    deadline_at timestamptz,
    error_type text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX uq_wake_active ON wake_requests(boot_id,chat_id)
    WHERE status IN ('waiting','sent','accepted');
CREATE INDEX ix_wake_due ON wake_requests(boot_id,status,next_attempt_at);

CREATE TABLE observed_items (
    id uuid PRIMARY KEY,
    boot_id uuid NOT NULL,
    source text NOT NULL,
    chat_id text NOT NULL,
    message_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    fingerprint text NOT NULL,
    deleted boolean NOT NULL DEFAULT false,
    is_self boolean NOT NULL DEFAULT false,
    strong_signal boolean NOT NULL DEFAULT false,
    sender_id text NOT NULL,
    sender_name text,
    sent_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    content jsonb NOT NULL,
    locator jsonb NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'new'
        CHECK (status IN ('new','delivered','processed','verified','ignored','failed')),
    status_reason text,
    decision_detail jsonb,
    wake_id uuid REFERENCES wake_requests(id),
    UNIQUE(source,chat_id,message_id,revision),
    UNIQUE(source,chat_id,message_id,fingerprint)
);
CREATE INDEX ix_observed_pending ON observed_items(boot_id,chat_id,observed_at,id)
    WHERE status='new' AND wake_id IS NULL;
CREATE INDEX ix_observed_latest ON observed_items(source,chat_id,message_id,revision DESC);
CREATE INDEX ix_observed_wake ON observed_items(wake_id);
