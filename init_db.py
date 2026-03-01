#!/usr/bin/env python3
"""Initialize PostgreSQL database schema for Eurostar checker."""

import os
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

# Load .env file
load_dotenv()

def init_db():
    conn = psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", 5432),
        database=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )
    cur = conn.cursor()

    # Create check_runs table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS check_runs (
            id SERIAL PRIMARY KEY,
            run_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Create check_results table
    cur.execute("""
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
    """)

    # Create indices for faster queries
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_check_results_route
        ON check_results(route);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_check_results_departure_date
        ON check_results(departure_date);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_check_runs_run_date
        ON check_runs(run_date);
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("✅ Database schema initialized successfully!")

if __name__ == "__main__":
    init_db()
