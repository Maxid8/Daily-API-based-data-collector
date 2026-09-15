CREATE TABLE daily_prices (
    ticker      TEXT NOT NULL,
    trade_date  DATE NOT NULL,
    open        NUMERIC(12,4),
    high        NUMERIC(12,4),
    low         NUMERIC(12,4),
    close       NUMERIC(12,4),
    volume      BIGINT,
    loaded_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, trade_date)
);

CREATE TABLE api_calls (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    called_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ticker        TEXT,
    success       BOOLEAN NOT NULL,
    error_reason  TEXT
);