import os
import psycopg2
from psycopg2.extras import RealDictCursor
from mcp.server.fastmcp import FastMCP

DATABASE_URL = os.getenv("DATABASE_URL")

mcp = FastMCP("eurostar-checker")


@mcp.tool()
def get_eurostar_availability() -> str:
    """
    Get the latest Eurostar Snap availability for the next 8 days.
    Returns prices and schedules for Paris→Amsterdam and Amsterdam→Paris routes.
    Data is updated regularly from the Eurostar Snap website.
    """
    if not DATABASE_URL:
        return "DATABASE_URL not configured."

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT id, run_at
            FROM search_runs
            WHERE status = 'success'
            ORDER BY run_at DESC
            LIMIT 1
        """)
        run = cur.fetchone()

        if not run:
            return "No availability data yet."

        cur.execute("""
            SELECT route, travel_date::text, period, price_text, time_start, time_end, url
            FROM search_results
            WHERE run_id = %s AND travel_date >= CURRENT_DATE
            ORDER BY route, travel_date, period
        """, (run["id"],))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return f"No availability found (last checked: {run['run_at'].strftime('%Y-%m-%d %H:%M UTC')})."

        lines = [f"Eurostar Snap availability (last updated: {run['run_at'].strftime('%Y-%m-%d %H:%M UTC')})", ""]

        current_route = None
        current_date = None

        for row in rows:
            if row["route"] != current_route:
                current_route = row["route"]
                lines.append(f"## {current_route}")
                current_date = None

            if row["travel_date"] != current_date:
                current_date = row["travel_date"]
                lines.append(f"  {current_date}")

            if row["price_text"]:
                time_info = f" ({row['time_start']}–{row['time_end']})" if row["time_start"] else ""
                lines.append(f"    {row['period'].capitalize()}: {row['price_text']}{time_info}")
            else:
                lines.append(f"    {row['period'].capitalize()}: not available")

        return "\n".join(lines)

    except Exception as e:
        return f"Error fetching data: {e}"


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    mcp.run(transport="sse", host="0.0.0.0", port=port)
