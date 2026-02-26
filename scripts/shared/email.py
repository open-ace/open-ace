#!/usr/bin/env python3
"""
AI Token Usage - Email Module

Provides email sending functionality for the ai_token_usage project.
"""

import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Dict, List, Optional


def send_email(
    subject: str,
    body: str,
    smtp_config: Dict,
    to_email: str,
    from_email: Optional[str] = None
) -> bool:
    """
    Send an email with the given subject and body.

    Args:
        subject: Email subject
        body: Email body content
        smtp_config: SMTP server configuration
        to_email: Recipient email address
        from_email: Sender email address (optional, uses config if not provided)

    Returns:
        True if email sent successfully, False otherwise
    """
    try:
        smtp_server = smtp_config.get('smtp_server', 'smtp.gmail.com')
        smtp_port = smtp_config.get('smtp_port', 587)
        use_tls = smtp_config.get('use_tls', True)
        username = smtp_config.get('smtp_username', '')
        password = smtp_config.get('smtp_password', '')

        if not from_email:
            from_email = smtp_config.get('from_email', username)

        if not from_email or not to_email:
            print("Error: Missing email addresses")
            return False

        # Create message
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Connect to SMTP server
        # Port 465 usually means SSL, ports 587/25 usually mean TLS
        use_ssl = smtp_port == 465
        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        else:
            if use_tls:
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP(smtp_server, smtp_port)

        # Login if authentication required
        if username and password:
            server.login(username, password)

        # Send email
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()

        print(f"Email sent successfully to {to_email}")
        return True

    except Exception as e:
        print(f"Error sending email: {e}")
        return False


def format_report_email(
    summary: Dict[str, Dict],
    daily_data: List[Dict],
    tool_name: Optional[str] = None
) -> str:
    """
    Format the email body from usage data.

    Args:
        summary: Summary statistics by tool
        daily_data: Daily usage data
        tool_name: Optional filter for specific tool

    Returns:
        Formatted email body as string
    """
    lines = []
    lines.append("=" * 60)
    lines.append("AI Token Usage Report")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)

    # Tool selection indicator
    if tool_name:
        lines.append(f"\nTool: {tool_name.upper()}")
    else:
        lines.append(f"\nTools: {', '.join(k.upper() for k in summary.keys())}")

    # Summary section
    lines.append("\n" + "-" * 40)
    lines.append("SUMMARY")
    lines.append("-" * 40)

    for tool, stats in summary.items():
        if tool_name and tool != tool_name:
            continue
        lines.append(f"\n{tool.upper()}:")
        lines.append(f"  Days tracked:    {stats['days_count']}")
        lines.append(f"  Total tokens:    {stats['total_tokens']:,}")
        lines.append(f"  Avg per day:     {stats['avg_tokens']:,.0f}")
        lines.append(f"  Date range:      {stats['first_date']} to {stats['last_date']}")

    # Daily details section
    lines.append("\n" + "-" * 40)
    lines.append("DAILY DETAILS")
    lines.append("-" * 40)

    # Sort by date descending
    sorted_data = sorted(daily_data, key=lambda x: x['date'], reverse=True)

    current_tool = None
    for entry in sorted_data:
        if tool_name and entry['tool_name'] != tool_name:
            continue

        if current_tool != entry['tool_name']:
            current_tool = entry['tool_name']
            lines.append(f"\n[{current_tool.upper()}]")

        tokens = entry['tokens_used']
        input_tok = entry.get('input_tokens', 0)
        output_tok = entry.get('output_tokens', 0)

        lines.append(f"  {entry['date']}: {tokens:,} total")
        if input_tok > 0 or output_tok > 0:
            lines.append(f"    - Input: {input_tok:,}, Output: {output_tok:,}")
        if entry.get('models_used'):
            lines.append(f"    - Models: {', '.join(entry['models_used'])}")

    lines.append("\n" + "=" * 60)
    lines.append("End of Report")
    lines.append("=" * 60)

    return "\n".join(lines)


def test_email_config(smtp_config: Dict) -> bool:
    """
    Test if email configuration is valid by attempting to connect.

    Args:
        smtp_config: SMTP server configuration

    Returns:
        True if connection successful, False otherwise
    """
    try:
        smtp_server = smtp_config.get('smtp_server', 'smtp.gmail.com')
        smtp_port = smtp_config.get('smtp_port', 587)
        use_tls = smtp_config.get('use_tls', True)
        username = smtp_config.get('smtp_username', '')
        password = smtp_config.get('smtp_password', '')

        # Port 465 usually means SSL, ports 587/25 usually mean TLS
        use_ssl = smtp_port == 465
        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        else:
            if use_tls:
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP(smtp_server, smtp_port)

        if username and password:
            server.login(username, password)

        server.quit()
        return True

    except Exception as e:
        print(f"Email connection test failed: {e}")
        return False
