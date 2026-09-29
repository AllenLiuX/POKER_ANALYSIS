-- WPK recorder mirror. Applied by wpk_recorder.cloud_sync (direct Postgres).
-- Lives in schema wpk so it does not mix with public.attempts / opponents.
-- frames and raw_events stay on the local SQLite database.

CREATE SCHEMA IF NOT EXISTS wpk;

CREATE TABLE IF NOT EXISTS wpk.hands (
    hand_id text PRIMARY KEY,
    table_id text,
    started_at text,
    ended_at text,
    status text NOT NULL,
    game_mode text NOT NULL DEFAULT 'holdem',
    hand_number bigint,
    played_at text,
    button_seat bigint,
    small_blind double precision,
    big_blind double precision,
    ante double precision,
    pot double precision,
    board_json text NOT NULL DEFAULT '[]',
    quality_status text NOT NULL DEFAULT 'unknown',
    quality_reasons_json text NOT NULL DEFAULT '[]',
    excluded_from_stats integer NOT NULL DEFAULT 1,
    hand_json text NOT NULL
);

CREATE TABLE IF NOT EXISTS wpk.players (
    user_id text PRIMARY KEY,
    latest_alias text,
    first_seen text NOT NULL,
    last_seen text NOT NULL
);

CREATE TABLE IF NOT EXISTS wpk.hand_players (
    hand_id text NOT NULL,
    seat bigint NOT NULL,
    user_id text,
    alias text,
    position text,
    stack_start double precision,
    stack_end double precision,
    hole_cards_json text NOT NULL,
    is_hero integer NOT NULL,
    net double precision,
    insurance_result double precision,
    fund double precision,
    PRIMARY KEY (hand_id, seat)
);

CREATE TABLE IF NOT EXISTS wpk.actions (
    hand_id text NOT NULL,
    sequence bigint NOT NULL,
    event_sequence bigint,
    action_id text,
    street text NOT NULL,
    seat bigint,
    user_id text,
    alias text,
    action text NOT NULL,
    amount double precision,
    amount_to double precision,
    stack_after double precision,
    source_message text,
    PRIMARY KEY (hand_id, sequence)
);

CREATE TABLE IF NOT EXISTS wpk.decision_snapshots (
    hand_id text NOT NULL,
    action_sequence bigint NOT NULL,
    event_sequence bigint,
    user_id text,
    alias text,
    seat bigint,
    street text NOT NULL,
    position text,
    game_mode text NOT NULL,
    chosen_action text NOT NULL,
    pot_before double precision NOT NULL,
    stack_before double precision,
    effective_stack double precision,
    effective_stack_bb double precision,
    spr double precision,
    to_call double precision NOT NULL,
    facing_action text NOT NULL,
    facing_amount double precision NOT NULL,
    bet_fraction double precision,
    raise_multiple double precision,
    is_ip integer,
    is_preflop_aggressor integer NOT NULL,
    players_in_hand bigint NOT NULL,
    board_texture_json text NOT NULL,
    PRIMARY KEY (hand_id, action_sequence)
);

CREATE TABLE IF NOT EXISTS wpk.opportunities (
    hand_id text NOT NULL,
    action_sequence bigint NOT NULL,
    user_id text,
    metric text NOT NULL,
    success integer NOT NULL,
    position text,
    game_mode text NOT NULL,
    effective_stack_bb double precision,
    PRIMARY KEY (hand_id, action_sequence, metric)
);

CREATE TABLE IF NOT EXISTS wpk.board_cards (
    hand_id text NOT NULL,
    card_index bigint NOT NULL,
    street text NOT NULL,
    card text NOT NULL,
    PRIMARY KEY (hand_id, card_index)
);

CREATE TABLE IF NOT EXISTS wpk.results (
    hand_id text NOT NULL,
    seat bigint NOT NULL,
    user_id text,
    alias text,
    net double precision,
    stack_end double precision,
    insurance_result double precision,
    fund double precision,
    shown_cards_json text NOT NULL,
    PRIMARY KEY (hand_id, seat)
);

CREATE TABLE IF NOT EXISTS wpk.hand_squid_players (
    hand_id text NOT NULL,
    user_id text NOT NULL,
    alias text,
    squid_start bigint NOT NULL,
    squid_end bigint NOT NULL,
    PRIMARY KEY (hand_id, user_id)
);

CREATE TABLE IF NOT EXISTS wpk.squid_awards (
    hand_id text NOT NULL,
    user_id text NOT NULL,
    alias text,
    award_count bigint NOT NULL,
    round_id text,
    event_sequence bigint,
    PRIMARY KEY (hand_id, user_id)
);

CREATE TABLE IF NOT EXISTS wpk.showdown_observations (
    hand_id text NOT NULL,
    user_id text NOT NULL,
    alias text,
    street text NOT NULL,
    hole_cards_json text NOT NULL,
    board_json text NOT NULL,
    preflop_class text NOT NULL,
    strength_category text NOT NULL,
    draws_json text NOT NULL,
    equity_vs_random double precision NOT NULL,
    position text,
    effective_stack_bb double precision,
    squid_count bigint NOT NULL DEFAULT 0,
    cumulative_net_before double precision NOT NULL DEFAULT 0,
    profit_state text NOT NULL,
    action_line text NOT NULL,
    feature_tokens_json text NOT NULL,
    PRIMARY KEY (hand_id, user_id, street)
);

CREATE TABLE IF NOT EXISTS wpk.decision_states (
    decision_id text NOT NULL,
    state_hash text NOT NULL,
    sequence bigint NOT NULL,
    hand_id text NOT NULL,
    captured_at text,
    source text NOT NULL,
    quality_status text NOT NULL,
    payload_json text NOT NULL,
    PRIMARY KEY (decision_id, state_hash)
);

CREATE TABLE IF NOT EXISTS wpk.strategy_evaluations (
    decision_id text NOT NULL,
    state_hash text NOT NULL,
    sequence bigint NOT NULL,
    hand_id text NOT NULL,
    created_at text NOT NULL,
    engine_version text NOT NULL,
    status text NOT NULL,
    recommended_action text,
    raise_to double precision,
    confidence text,
    payload_json text NOT NULL,
    PRIMARY KEY (decision_id, state_hash, engine_version)
);

CREATE TABLE IF NOT EXISTS wpk.inference_contexts (
    context_hash text PRIMARY KEY,
    decision_id text NOT NULL,
    state_hash text NOT NULL,
    sequence bigint NOT NULL,
    hand_id text NOT NULL,
    subject text NOT NULL,
    context_version text NOT NULL,
    temporal_quality text NOT NULL,
    created_at text NOT NULL,
    payload_json text NOT NULL
);

CREATE TABLE IF NOT EXISTS wpk.inference_runs (
    run_id text PRIMARY KEY,
    decision_id text NOT NULL,
    state_hash text NOT NULL,
    sequence bigint NOT NULL,
    hand_id text NOT NULL,
    context_hash text NOT NULL,
    template_id text NOT NULL,
    template_hash text NOT NULL,
    prompt_hash text,
    model text,
    reasoning_depth text NOT NULL,
    analysis_mode text NOT NULL,
    status text NOT NULL,
    validation_status text NOT NULL,
    latency_ms bigint,
    error_reason text,
    created_at text NOT NULL,
    payload_json text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wpk_hand_players_user ON wpk.hand_players (user_id, hand_id);
CREATE INDEX IF NOT EXISTS idx_wpk_actions_user ON wpk.actions (user_id, hand_id, street);
CREATE INDEX IF NOT EXISTS idx_wpk_decisions_user ON wpk.decision_snapshots (user_id, street, position, game_mode);
CREATE INDEX IF NOT EXISTS idx_wpk_opportunities_user ON wpk.opportunities (user_id, metric, game_mode, position);
CREATE INDEX IF NOT EXISTS idx_wpk_showdown_player ON wpk.showdown_observations (user_id, street, strength_category);
CREATE INDEX IF NOT EXISTS idx_wpk_decision_states_hand ON wpk.decision_states (hand_id, sequence);
CREATE INDEX IF NOT EXISTS idx_wpk_strategy_hand ON wpk.strategy_evaluations (hand_id, sequence);
CREATE INDEX IF NOT EXISTS idx_wpk_inference_contexts_hand ON wpk.inference_contexts (hand_id, sequence);
CREATE INDEX IF NOT EXISTS idx_wpk_inference_runs_hand ON wpk.inference_runs (hand_id, created_at);
