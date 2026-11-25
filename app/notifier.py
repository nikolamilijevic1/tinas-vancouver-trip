from __future__ import annotations

import asyncio
import os
import smtplib
from datetime import date
from email.message import EmailMessage
from typing import Any

import httpx


def _short_date(iso_date: str) -> str:
    try:
        return date.fromisoformat(iso_date).strftime("%b %-d")
    except Exception:
        return iso_date


def _fmt_dur(minutes: int) -> str:
    h, m = divmod(max(0, int(minutes)), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def is_email_configured() -> bool:
    if os.getenv("RESEND_API_KEY"):
        return True
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"):
        return True
    return False


def build_deal_email(
    *,
    flights: list[dict[str, Any]],
    window_start: str,
    window_end: str,
    reason: str,
) -> tuple[str, str, str]:
    top = flights[0]
    subject = f"Ottawa to Vancouver from CA${top['price_cad']} ({_short_date(top['depart_date'])} – {_short_date(top['return_date'])})"

    text_lines = [
        reason,
        f"Window: {_short_date(window_start)} – {_short_date(window_end)}",
        "",
    ]
    html_rows = []
    for f in flights[:4]:
        stops = "Nonstop" if f["stops"] == 0 else f"{f['stops']} stop"
        dur = _fmt_dur(f["total_duration_minutes"])
        dates_str = f"{_short_date(f['depart_date'])} – {_short_date(f['return_date'])} ({f['nights']} nights)"
        text_lines.append(
            f"- CA${f['price_cad']} · {f['primary_airline']} · {stops} ({dur}) · {dates_str}\n  {f['booking_url']}"
        )
        html_rows.append(
            f"""
            <tr>
              <td style="padding:12px 0;border-bottom:1px solid #e5e7eb;">
                <div style="font-size:15px;font-weight:600;color:#111827;">
                  CA${f['price_cad']} &nbsp;·&nbsp; {f['primary_airline']}
                </div>
                <div style="font-size:13px;color:#4b5563;margin-top:2px;">
                  {dates_str} &nbsp;·&nbsp; {stops} ({dur}) &nbsp;·&nbsp; {f['origin']} → {f['destination']}
                </div>
              </td>
              <td style="padding:12px 0;border-bottom:1px solid #e5e7eb;text-align:right;vertical-align:middle;">
                <a href="{f['booking_url']}" style="display:inline-block;padding:8px 14px;background:#111827;color:#ffffff;text-decoration:none;border-radius:8px;font-size:13px;font-weight:500;">View flight</a>
              </td>
            </tr>
            """
        )

    html = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#111827;">
      <h2 style="margin:0 0 6px;font-size:20px;font-weight:600;">Ottawa → Vancouver flight update</h2>
      <p style="margin:0 0 18px;font-size:14px;color:#4b5563;">{reason}</p>
      <table style="width:100%;border-collapse:collapse;">
        {''.join(html_rows)}
      </table>
      <p style="margin:20px 0 0;font-size:12px;color:#6b7280;">Prices are round-trip for 1 adult in CAD from Google Flights.</p>
    </div>
    """
    return subject, "\n".join(text_lines), html


async def send_email(to_email: str, subject: str, text_body: str, html_body: str) -> tuple[bool, str]:
    to_email = to_email.strip()
    if not to_email or "@" not in to_email:
        return False, "Please enter a valid email address."

    resend_key = os.getenv("RESEND_API_KEY", "").strip()
    if resend_key:
        from_addr = os.getenv("EMAIL_FROM", "Flight Alerts <onboarding@resend.dev>").strip()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {resend_key}"},
                    json={
                        "from": from_addr,
                        "to": [to_email],
                        "subject": subject,
                        "text": text_body,
                        "html": html_body,
                    },
                )
                if 200 <= resp.status_code < 300:
                    return True, f"Email sent to {to_email}"
                return False, f"Email provider error ({resp.status_code}): {resp.text[:160]}"
        except Exception as exc:
            return False, f"Failed to send email: {exc}"

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASS", "").strip()
    if smtp_host and smtp_user and smtp_pass:
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        from_addr = os.getenv("EMAIL_FROM", smtp_user).strip()

        def _send_sync():
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = from_addr
            msg["To"] = to_email
            msg.set_content(text_body)
            msg.add_alternative(html_body, subtype="html")
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)

        try:
            await asyncio.to_thread(_send_sync)
            return True, f"Email sent to {to_email}"
        except Exception as exc:
            return False, f"SMTP error: {exc}"

    return False, "Alert saved. Add RESEND_API_KEY or SMTP_* env vars on the host to send emails."
