import os
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import smtplib
from email.mime.text import MIMEText
import re

# ---- Email robustness helpers (IPv4-first + SendGrid HTTP) ----
import ssl as _ssl
import socket as _socket
from email.utils import formataddr as _formataddr
import urllib.request as _urlreq
import urllib.error as _urlerr
import json as _json
import time as _time

def _resolve_all(host, port, ipv4_only=False):
    try:
        info = _socket.getaddrinfo(host, port, type=_socket.SOCK_STREAM)
    except _socket.gaierror as e:
        print(f"[mail] DNS resolution failed for {host}:{port} -> {e!r}")
        return []
    if ipv4_only:
        info = [a for a in info if a[0] == _socket.AF_INET]
    info_sorted = sorted(info, key=lambda a: 0 if a[0] == _socket.AF_INET else 1)
    return [(fam, sockaddr[0]) for fam,_,_,_,sockaddr in info_sorted]

def _smtp_send_all_addrs(host, port, sender, password, recipients, msg_str, ipv4_only=False):
    addrs = _resolve_all(host, port, ipv4_only=ipv4_only)
    if not addrs:
        print(f"[mail] No addresses to try for {host}:{port}")
        return False
    last_err = None
    import smtplib as _smtplib
    for i, (family, ip) in enumerate(addrs, start=1):
        fam_name = "IPv4" if family == _socket.AF_INET else "IPv6"
        print(f"[mail] Trying {host}:{port} -> {ip} ({fam_name}) [{i}/{len(addrs)}]")
        try:
            with _smtplib.SMTP(host=ip, port=port, timeout=20) as server:
                server.ehlo()
                server.starttls(context=_ssl.create_default_context())
                server.ehlo()
                server.login(sender, password)
                server.sendmail(sender, recipients, msg_str)
            print(f"[mail] Email sent via {ip} ✔")
            return True
        except Exception as e:
            print(f"[mail] Failed via {ip}: {e!r}")
            last_err = e
            _time.sleep(1)
    if last_err:
        print(f"[mail] All addresses failed; last error: {last_err!r}")
    return False

def _send_via_sendgrid(sender_email, sender_name, recipients, subject, html):
    api_key = SENDGRID_API_KEY
    if not api_key:
        return False, "SENDGRID_API_KEY not set"
    payload = {
        "personalizations": [{"to": [{"email": r} for r in recipients]}],
        "from": {"email": sender_email or "no-reply@example.com", "name": sender_name or "Eurostar Snap Bot"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}],
    }
    data = _json.dumps(payload).encode("utf-8")
    req = _urlreq.Request(
        url="https://api.sendgrid.com/v3/mail/send",
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _urlreq.urlopen(req, timeout=20) as resp:
            code = resp.getcode()
            if code >= 300:
                return False, f"SendGrid HTTP {code}"
            return True, None
    except _urlerr.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            err_body = str(e)
        return False, f"SendGrid HTTPError {e.code}: {err_body}"
    except Exception as e:
        return False, repr(e)

# Configuration from environment variables
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT", "")  # comma-separated emails
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_SERVER_ALT = os.getenv("SMTP_SERVER_ALT", "smtp.googlemail.com")
FORCE_IPV4_ONLY = os.getenv("FORCE_IPV4_ONLY", "0") == "1"
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Eurostar Snap Bot")
EMAIL_SUBJECT_PREFIX = os.getenv("EMAIL_SUBJECT_PREFIX", "")
ROUTE_LABEL = os.getenv("ROUTE_LABEL", "")


# SNAP URLs
SNAP_PARIS_TO_AMS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8727100&destination=8400058&outbound={date}"
SNAP_AMS_TO_PARIS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8400058&destination=8727100&outbound={date}"

# -------------------- Helpers for time/price parsing --------------------

def _normalize_time_component(value: int) -> str:
    return f"{value:02d}"


def _normalize_time_string(time_str: str) -> str:
    if not time_str:
        return ""
    cleaned = time_str.strip().lower().replace("h", ":")
    m = re.search(r"(\d{1,2}):(\d{2})", cleaned)
    if not m:
        return ""
    hour = int(m.group(1))
    minute = int(m.group(2))
    return f"{_normalize_time_component(hour)}:{_normalize_time_component(minute)}"


def _parse_time_range_from_text(text: str):
    if not text:
        return None
    lowered = text.strip().lower()
    
    # French format: "Départ entre 06:10 et 14:00"
    m = re.search(r'départ\s+entre\s+(\d{1,2}):(\d{2})\s+et\s+(\d{1,2}):(\d{2})', lowered)
    if m:
        start_h = int(m.group(1))
        start_m = int(m.group(2))
        end_h = int(m.group(3))
        end_m = int(m.group(4))
        return f"{_normalize_time_component(start_h)}:{_normalize_time_component(start_m)}", f"{_normalize_time_component(end_h)}:{_normalize_time_component(end_m)}"
    
    # French format with 'h': "Départ entre 6h10 et 14h00"
    m = re.search(r'départ\s+entre\s+(\d{1,2})h(\d{2})\s+et\s+(\d{1,2})h(\d{2})', lowered)
    if m:
        start_h = int(m.group(1))
        start_m = int(m.group(2))
        end_h = int(m.group(3))
        end_m = int(m.group(4))
        return f"{_normalize_time_component(start_h)}:{_normalize_time_component(start_m)}", f"{_normalize_time_component(end_h)}:{_normalize_time_component(end_m)}"
    
    # English format: "Departure between 06:10 and 14:00"
    m = re.search(r'departure\s+between\s+(\d{1,2}):(\d{2})\s+and\s+(\d{1,2}):(\d{2})', lowered)
    if m:
        start_h = int(m.group(1))
        start_m = int(m.group(2))
        end_h = int(m.group(3))
        end_m = int(m.group(4))
        return f"{_normalize_time_component(start_h)}:{_normalize_time_component(start_m)}", f"{_normalize_time_component(end_h)}:{_normalize_time_component(end_m)}"
    
    # Standard formats with separators
    m = re.search(r"(\d{1,2}:\d{2})\s*(?:-|–|—|to|à)\s*(\d{1,2}:\d{2})", lowered)
    if m:
        start = _normalize_time_string(m.group(1))
        end = _normalize_time_string(m.group(2))
        if start and end:
            return start, end
    
    # Try to capture without minutes on either side (e.g., 7-12 or 7h-12h)
    m2 = re.search(r"(\d{1,2})(?::?(\d{2}))?\s*(?:-|–|—|to|à)\s*(\d{1,2})(?::?(\d{2}))?", lowered)
    if m2:
        sh = int(m2.group(1))
        sm = int(m2.group(2)) if m2.group(2) else 0
        eh = int(m2.group(3))
        em = int(m2.group(4)) if m2.group(4) else 0
        return f"{_normalize_time_component(sh)}:{_normalize_time_component(sm)}", f"{_normalize_time_component(eh)}:{_normalize_time_component(em)}"
    
    return None


def _infer_band(label_text: str, time_range):
    text = (label_text or "").lower()
    if any(k in text for k in ["morning", "matin"]):
        return "morning"
    if any(k in text for k in ["afternoon", "apres", "après"]):
        return "afternoon"
    if time_range:
        try:
            start_hour = int(time_range[0].split(":")[0])
            # Morning before 14:00, otherwise afternoon
            return "morning" if start_hour < 14 else "afternoon"
        except Exception:
            return None
    return None


def _price_to_float(price_text: str) -> float:
    if not price_text:
        return float("inf")
    # Remove thin spaces and convert comma decimal to dot
    normalized = price_text.replace("\u202f", "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", normalized)
    if not m:
        return float("inf")
    try:
        return float(m.group(1))
    except Exception:
        return float("inf")


def _merge_time_ranges(time_ranges):
    # time_ranges: list of (start, end)
    if not time_ranges:
        return None
    def to_minutes(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    starts = sorted(time_ranges, key=lambda r: to_minutes(r[0]))
    ends = sorted(time_ranges, key=lambda r: to_minutes(r[1]), reverse=True)
    return starts[0][0], ends[0][1]


def _format_date_for_display(date_str: str) -> str:
    """Convert YYYY-MM-DD to 'Monday 18th August 2025' format"""
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        
        # Get day name
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        day_name = day_names[date_obj.weekday()]
        
        # Get day with ordinal suffix
        day = date_obj.day
        if 4 <= day <= 20 or 24 <= day <= 30:
            suffix = "th"
        else:
            suffix = ["st", "nd", "rd"][day % 10 - 1]
        
        # Get month name
        month_names = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ]
        month_name = month_names[date_obj.month - 1]
        
        return f"{day_name} {day}{suffix} {month_name} {date_obj.year}"
    except Exception:
        return date_str  # Return original if parsing fails

async def check_snap(playwright, route_name, base_url):
    browser = await playwright.chromium.launch()
    page = await browser.new_page()
    results = []

    for i in range(1, 9):
        date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
        url = base_url.format(date=date)
        print(f"[Snap] Checking {route_name}: {url}")

        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_timeout(5000)

            # Try multiple selectors to find price elements
            price_blocks = await page.query_selector_all("div[data-testid$='-price'], [data-testid*='price'], .price, [class*='price']")
            
            print(f"[DEBUG] Found {len(price_blocks)} price blocks for {date}")
            
            if not price_blocks:
                # Fallback: try to find any text that looks like a price
                all_text = await page.inner_text("body")
                price_pattern = re.findall(r'€\s*\d+[\.,]?\d*|\d+[\.,]?\d*\s*€', all_text)
                if price_pattern:
                    print(f"[DEBUG] Found prices in text: {price_pattern[:3]}...")
                    # Create dummy blocks for debugging
                    price_blocks = [None] * len(price_pattern)

            offers = []
            for block in price_blocks:
                try:
                    if block:
                        price_text = (await block.inner_text()).strip()
                    else:
                        # Use dummy price for debugging
                        price_text = "€XX (debug)"
                except Exception as e:
                    print(f"[DEBUG] Error getting price text: {e}")
                    continue

                if block:
                    try:
                        info = await block.evaluate("""
                            (el) => {
                                function findContainer(node) {
                                    let cur = node;
                                    for (let i = 0; i < 8 && cur; i++) {
                                        const hasPrice = cur.querySelector("[data-testid$='-price'], [data-testid*='price'], .price, [class*='price']");
                                        const hasTime = cur.querySelector("[data-testid*='time'], time, [class*='time'], [class*='hour'], [class*='departure'], [class*='schedule']");
                                        if (hasPrice && (hasTime || i > 0)) return cur;
                                        cur = cur.parentElement;
                                    }
                                    return node;
                                }
                                
                                function findTimeElements(container) {
                                    const timeSelectors = [
                                        "[data-testid*='time']", 
                                        "time", 
                                        "[class*='time']", 
                                        "[class*='hour']", 
                                        "[class*='departure']", 
                                        "[class*='schedule']",
                                        "[class*='depart']",
                                        "[class*='arrival']",
                                        "[class*='clock']",
                                        "[class*='moment']"
                                    ];
                                    
                                    let timeElements = [];
                                    timeSelectors.forEach(selector => {
                                        const elements = container.querySelectorAll(selector);
                                        elements.forEach(el => {
                                            if (el.innerText && el.innerText.trim()) {
                                                timeElements.push(el.innerText.trim());
                                            }
                                        });
                                    });
                                    
                                    // Also look for time patterns in the container text
                                    const containerText = container.innerText || '';
                                    const timePatterns = [
                                        // Simple time patterns without special characters
                                        /(\\d{1,2}:\\d{2})\\s*-\\s*(\\d{1,2}:\\d{2})/gi,
                                        /(\\d{1,2})h(\\d{2})\\s*-\\s*(\\d{1,2})h(\\d{2})/gi,
                                        /(\\d{1,2})\\s*-\\s*(\\d{1,2})/gi,
                                        // Look for any text containing time ranges
                                        /(\\d{1,2}:\\d{2})\\s*and\\s*(\\d{1,2}:\\d{2})/gi,
                                        /(\\d{1,2}:\\d{2})\\s*to\\s*(\\d{1,2}:\\d{2})/gi
                                    ];
                                    
                                    timePatterns.forEach(pattern => {
                                        const matches = containerText.match(pattern);
                                        if (matches) {
                                            timeElements.push(...matches);
                                        }
                                    });
                                    
                                    return timeElements;
                                }
                                
                                function checkAvailability(container) {
                                    const containerText = (container.innerText || '').toLowerCase();
                                    
                                    // Check the main page content for availability messages
                                    const mainPage = document.querySelector('body') || document.body;
                                    const pageText = mainPage.innerText || '';
                                    
                                    const unavailableIndicators = [
                                        'unavailable', 'sold out', 'full', 'no seats', 'booking closed',
                                        'not available', 'exhausted', 'no tickets available',
                                        'no snap tickets available', 'no availability'
                                    ];
                                    
                                    // Check both container and main page
                                    const containerHasUnavailable = unavailableIndicators.some(indicator => containerText.includes(indicator));
                                    const pageHasUnavailable = unavailableIndicators.some(indicator => pageText.includes(indicator));
                                    
                                    return !containerHasUnavailable && !pageHasUnavailable;
                                }
                                
                                const container = findContainer(el);
                                const timeElements = findTimeElements(container);
                                const labelEl = container.querySelector("[data-testid*='band'], [data-testid*='period'], [class*='morning'], [class*='afternoon'], [class*='matin'], [class*='apres']");
                                const isAvailable = checkAvailability(container);
                                
                                console.log('DEBUG: Container found:', container ? 'yes' : 'no');
                                console.log('DEBUG: Time elements found:', timeElements);
                                console.log('DEBUG: Container text preview:', container ? container.innerText.substring(0, 200) : 'none');
                                
                                return {
                                    containerText: container && container.innerText ? container.innerText : '',
                                    timeElements: timeElements,
                                    labelText: labelEl && labelEl.innerText ? labelEl.innerText : '',
                                    isAvailable: isAvailable
                                };
                            }
                        """)
                    except Exception as e:
                        print(f"[DEBUG] Error in evaluate: {e}")
                        info = {}
                else:
                    info = {}

                container_text = info.get("containerText", "") if isinstance(info, dict) else ""
                time_elements = info.get("timeElements", []) if isinstance(info, dict) else []
                label_text = info.get("labelText", "") if isinstance(info, dict) else ""
                is_available = info.get("isAvailable", True) if isinstance(info, dict) else True

                print(f"[DEBUG] Price: {price_text}")
                print(f"[DEBUG] Time elements found: {time_elements}")
                print(f"[DEBUG] Label: {label_text}")
                print(f"[DEBUG] Is available: {is_available}")
                print(f"[DEBUG] Container text: {container_text[:200]}...")
                
                # Skip if the offer is not available
                if not is_available:
                    print(f"[DEBUG] Skipping unavailable offer: {price_text}")
                    continue
                
                # Try to find time range from multiple sources
                time_range = None
                for time_text in time_elements:
                    print(f"[DEBUG] Trying to parse time element: '{time_text}'")
                    time_range = _parse_time_range_from_text(time_text)
                    if time_range:
                        print(f"[DEBUG] Found time range from element: {time_range}")
                        break
                
                if not time_range:
                    print(f"[DEBUG] No time range from elements, trying container text...")
                    time_range = _parse_time_range_from_text(container_text)
                    if time_range:
                        print(f"[DEBUG] Found time range from container: {time_range}")
                    else:
                        print(f"[DEBUG] No time range found anywhere")
                
                band = _infer_band(label_text, time_range)
                
                # If no band detected, try to infer from time or assign default
                if not band:
                    if time_range:
                        band = _infer_band("", time_range)
                        print(f"[DEBUG] Inferred band from time: {band}")
                    if not band:
                        band = "morning"  # Default fallback
                        print(f"[DEBUG] Using default band: {band}")
                
                print(f"[DEBUG] Final time range: {time_range}, Assigned band: {band}")
                
                # Only add offers that have a valid price (not debug prices) AND a time range
                if price_text != "€XX (debug)" and time_range:
                    offers.append({
                        "band": band,
                        "price_text": price_text,
                        "time_range": time_range
                    })
                    print(f"[DEBUG] Added offer: band={band}, price={price_text}, time={time_range}")
                else:
                    if price_text == "€XX (debug)":
                        print(f"[DEBUG] Skipping debug price")
                    else:
                        print(f"[DEBUG] Skipping offer without time range: price={price_text}, time={time_range}")

            print(f"[DEBUG] Total offers found: {len(offers)}")
            
            if offers:
                entry = {"route": route_name, "date": date, "url": url, "morning": None, "afternoon": None}
                for band in ["morning", "afternoon"]:
                    band_offers = [o for o in offers if o["band"] == band]
                    if band_offers:
                        # Select lowest price and merge time ranges
                        best_price_offer = min(band_offers, key=lambda o: _price_to_float(o["price_text"]))
                        merged_range = _merge_time_ranges([o["time_range"] for o in band_offers if o["time_range"]]) if any(o["time_range"] for o in band_offers) else None
                        entry[band] = {
                            "price_text": best_price_offer["price_text"],
                            "time_range": merged_range,
                            "url": url,
                        }
                if entry["morning"] or entry["afternoon"]:
                    results.append(entry)
                    print(f"[DEBUG] Added entry for {date}: morning={entry['morning'] is not None}, afternoon={entry['afternoon'] is not None}")
            else:
                print(f"[DEBUG] No offers found for {date}")

        except Exception as e:
            print(f"Erreur SNAP pour {route_name} le {date} : {e}")

    await browser.close()
    return results

def send_email(available_entries):
    if not available_entries:
        return

    def build_table(rows):
        # rows: list of dict with keys date, morning, afternoon
        parts = []
        parts.append('<table style="border-collapse:collapse;width:100%;max-width:720px;font-family:Arial,Helvetica,sans-serif">')
        # header
        th_style = 'style="border:1px solid #ddd;padding:8px;text-align:left;background:#f7f7f7"'
        td_style = 'style="border:1px solid #ddd;padding:8px;text-align:left"'
        parts.append(f"<tr><th {th_style}>Date</th><th {th_style}>Morning</th><th {th_style}>Afternoon</th></tr>")
        for r in rows:
            def cell_content(slot):
                if not slot:
                    return "—<br/><small>no availability for now</small>"
                price_html = f'<a href="{slot["url"]}">{slot["price_text"]}</a>'
                if slot.get("time_range"):
                    start, end = slot["time_range"]
                    return f"{price_html}<br/><small>between {start} and {end}</small>"
                return f"{price_html}<br/><small>no availability for now</small>"
            parts.append(
                f"<tr>"
                f"<td {td_style}>{_format_date_for_display(r['date'])}</td>"
                f"<td {td_style}>{cell_content(r.get('morning'))}</td>"
                f"<td {td_style}>{cell_content(r.get('afternoon'))}</td>"
                f"</tr>"
            )
        parts.append("</table>")
        return "".join(parts)

    header = (
        "<div style=\"font-family:Arial,Helvetica,sans-serif\">"
        "<h2>🤖 The bot for cheap tickets between Amsterdam and Paris 🤖</h2>"
        "<p>🚄 Eurostar Snap availability</p>"
        "</div>"
    )

    sections = []
    for route in ["Paris → Amsterdam", "Amsterdam → Paris"]:
        route_entries = [e for e in available_entries if e["route"] == route]
        if not route_entries:
            continue
        # sort by date
        route_entries_sorted = sorted(route_entries, key=lambda e: e["date"])
        table_html = build_table(route_entries_sorted)
        
        # Add emojis to city names
        route_with_emojis = route.replace("Paris", "🗼 Paris 🗼").replace("Amsterdam", "☕ Amsterdam ☕")
        
        sections.append(
            f"<h3 style=\"font-family:Arial,Helvetica,sans-serif\">{route_with_emojis}</h3>" + table_html
        )

    message = header + "".join(sections)

    msg = MIMEText(message, "html", "utf-8")
    msg["Subject"] = "🤖 The bot for cheap tickets between Amsterdam and Paris 🤖"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT

    recipients = [r.strip() for r in EMAIL_RECIPIENT.split(",") if r.strip()]
    print(f"[mail] To: {recipients}")
    # Try HTTP via SendGrid first if configured
    if SENDGRID_API_KEY:
        ok, err = _send_via_sendgrid(EMAIL_SENDER, EMAIL_FROM_NAME, recipients, msg["Subject"], message)
        if ok:
            print("[mail] Email sent via SendGrid ✔")
            return
        else:
            print(f"[mail] SendGrid failed: {err}. Falling back to SMTP…")
    # SMTP robust attempts (IPv4-first + alt host + global retries)
    max_global_retries = 2
    for attempt in range(1, max_global_retries+1):
        print(f"[mail] Connecting SMTP {SMTP_SERVER}:{SMTP_PORT} (attempt {attempt}/{max_global_retries})")
        ok = _smtp_send_all_addrs(SMTP_SERVER, SMTP_PORT, EMAIL_SENDER, EMAIL_PASSWORD, recipients, msg.as_string(), ipv4_only=FORCE_IPV4_ONLY)
        if ok:
            return
        print("[mail] Trying ALT host…")
        ok = _smtp_send_all_addrs(SMTP_SERVER_ALT, SMTP_PORT, EMAIL_SENDER, EMAIL_PASSWORD, recipients, msg.as_string(), ipv4_only=FORCE_IPV4_ONLY)
        if ok:
            return
        if attempt < max_global_retries:
            print("[mail] Global retry in 5s…"); _time.sleep(5)
    print("[mail] Email delivery failed after all attempts. Continuing without crash.")

def main():
    async def run():
        async with async_playwright() as playwright:
            snap_1 = await check_snap(playwright, "Paris → Amsterdam", SNAP_PARIS_TO_AMS)
            snap_2 = await check_snap(playwright, "Amsterdam → Paris", SNAP_AMS_TO_PARIS)
            all_available = snap_1 + snap_2
            print(f"ALL_AVAILABLE: {all_available}")
            send_email(all_available)

    asyncio.run(run())

if __name__ == "__main__":
    main()
