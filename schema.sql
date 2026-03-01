-- Eurostar Checker PostgreSQL Schema
-- Execute this on your Railway PostgreSQL database

CREATE TABLE IF NOT EXISTS check_runs (
    id SERIAL PRIMARY KEY,
    run_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS check_results (
    id SERIAL PRIMARY KEY,
    check_run_id INTEGER NOT NULL REFERENCES check_runs(id) ON DELETE CASCADE,
    route VARCHAR(100) NOT NULL,
    departure_date DATE NOT NULL,
    morning_price VARCHAR(50),
    morning_time_range VARCHAR(20),
    afternoon_price VARCHAR(50),
    afternoon_time_range VARCHAR(20),
    url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(check_run_id, route, departure_date)
);

-- Create indices for faster queries
CREATE INDEX IF NOT EXISTS idx_check_results_route
ON check_results(route);

CREATE INDEX IF NOT EXISTS idx_check_results_departure_date
ON check_results(departure_date);

CREATE INDEX IF NOT EXISTS idx_check_runs_run_date
ON check_runs(run_date);
