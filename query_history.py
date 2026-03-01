#!/usr/bin/env python3
"""Query Eurostar checker history from PostgreSQL."""

import os
import psycopg2
from datetime import datetime, timedelta
import sys

def get_connection():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", 5432),
        database=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def show_recent_checks(days=7):
    """Show recent check runs."""
    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT id, run_date,
               (SELECT COUNT(*) FROM check_results WHERE check_run_id = check_runs.id) as result_count
        FROM check_runs
        WHERE run_date > CURRENT_TIMESTAMP - INTERVAL '%s days'
        ORDER BY run_date DESC
        LIMIT 20;
    """
    cur.execute(query, (days,))

    print(f"\n📊 Recent check runs (last {days} days):")
    print("-" * 70)
    for row in cur.fetchall():
        run_id, run_date, count = row
        print(f"  Run #{run_id} | {run_date.strftime('%Y-%m-%d %H:%M:%S')} | {count} results")

    cur.close()
    conn.close()

def show_availability(days=30):
    """Show latest availability for each route/date."""
    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT DISTINCT ON (route, departure_date)
               route, departure_date, morning_price, morning_time_range,
               afternoon_price, afternoon_time_range, created_at
        FROM check_results
        WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '%s days'
        ORDER BY route, departure_date, created_at DESC;
    """
    cur.execute(query, (days,))

    print(f"\n✈️  Latest availability status (last {days} days):")
    print("-" * 100)

    for row in cur.fetchall():
        route, date, m_price, m_time, a_price, a_time, created = row

        m_str = f"€{m_price} ({m_time})" if m_price else "—"
        a_str = f"€{a_price} ({a_time})" if a_price else "—"

        print(f"  {route} | {date} | Morning: {m_str:<20} | Afternoon: {a_str:<20} | {created.strftime('%H:%M:%S')}")

    cur.close()
    conn.close()

def show_best_prices(days=30):
    """Show best prices found for each route."""
    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT route,
               MIN(CAST(SUBSTRING(morning_price FROM '^[0-9]+') AS FLOAT)) as min_morning,
               MIN(CAST(SUBSTRING(afternoon_price FROM '^[0-9]+') AS FLOAT)) as min_afternoon,
               COUNT(*) as checks
        FROM check_results
        WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '%s days'
          AND (morning_price IS NOT NULL OR afternoon_price IS NOT NULL)
        GROUP BY route;
    """
    cur.execute(query, (days,))

    print(f"\n💰 Best prices found (last {days} days):")
    print("-" * 70)

    for row in cur.fetchall():
        route, min_m, min_a, checks = row
        m_str = f"€{min_m:.0f}" if min_m else "—"
        a_str = f"€{min_a:.0f}" if min_a else "—"
        print(f"  {route:<25} | Morning: {m_str:<8} | Afternoon: {a_str:<8} | ({checks} checks)")

    cur.close()
    conn.close()

if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30

    show_recent_checks(days)
    print()
    show_best_prices(days)
    print()
    show_availability(days)
