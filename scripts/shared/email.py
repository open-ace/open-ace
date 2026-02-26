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
    from_email: Optional[str] = None,
    is_html: bool = False
) -> bool:
    """
    Send an email with the given subject and body.

    Args:
        subject: Email subject
        body: Email body content
        smtp_config: SMTP server configuration
        to_email: Recipient email address
        from_email: Sender email address (optional, uses config if not provided)
        is_html: Whether the body is HTML format (default: False)

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

        # Attach body with correct content type
        if is_html:
            msg.attach(MIMEText(body, 'html', 'utf-8'))
        else:
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

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


def format_tokens(tokens: int) -> str:
    """Format token count with human-readable units (K, M, B)."""
    if tokens >= 1_000_000_000:
        return f"{tokens / 1_000_000_000:.2f}B"
    elif tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}M"
    elif tokens >= 1_000:
        return f"{tokens / 1_000:.2f}K"
    else:
        return str(tokens)


def format_report_email(
    summary: Dict[str, Dict],
    daily_data: List[Dict],
    tool_name: Optional[str] = None,
    report_date: Optional[str] = None
) -> str:
    """
    Format the email body from usage data.

    Args:
        summary: Summary statistics by tool
        daily_data: Daily usage data
        tool_name: Optional filter for specific tool
        report_date: Optional date for the report (shows which day's data this is)

    Returns:
        Formatted email body as HTML string
    """
    # Date display
    display_date = report_date or "today"

    # Build HTML table for daily data (tools used on report date)
    daily_tables = ""
    if daily_data:
        # Sort by tool name
        sorted_tools = sorted(set(e['tool_name'] for e in daily_data))
        for tool in sorted_tools:
            tool_entries = [e for e in daily_data if e['tool_name'] == tool]
            # Sort by date descending (most recent first)
            tool_entries = sorted(tool_entries, key=lambda x: x['date'], reverse=True)

            daily_tables += f"""
            <tr>
                <td class="tool-name">{tool.upper()}</td>
                <td class="tool-data">"""
            for entry in tool_entries:
                tokens = format_tokens(entry['tokens_used'])
                input_tok = format_tokens(entry.get('input_tokens', 0))
                output_tok = format_tokens(entry.get('output_tokens', 0))
                daily_tables += f"""
                <div class="daily-entry">
                    <span class="date">{entry['date']}</span>
                    <span class="tokens">{tokens}</span>
                    {input_tok != '0' or output_tok != '0' f'<span class="details">I: {input_tok} / O: {output_tok}</span>' or ''}
                </div>"""
            daily_tables += """
                </td>
            </tr>"""
    else:
        daily_tables = f"""
            <tr>
                <td colspan="2" class="no-data">No usage data available for {display_date}</td>
            </tr>"""

    # Build HTML table for summary (all-time stats)
    summary_rows = ""
    for tool, stats in sorted(summary.items(), key=lambda x: x[1]['total_tokens'], reverse=True):
        summary_rows += f"""
            <tr>
                <td class="summary-tool">{tool.upper()}</td>
                <td class="summary-days">{stats['days_count']} days</td>
                <td class="summary-tokens">{format_tokens(stats['total_tokens'])}</td>
                <td class="summary-avg">{format_tokens(int(stats['avg_tokens']))}/day</td>
            </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background-color: #f5f5f5;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background-color: #ffffff;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0;
            font-size: 24px;
            font-weight: 600;
        }}
        .header p {{
            margin: 8px 0 0;
            opacity: 0.9;
            font-size: 14px;
        }}
        .content {{
            padding: 25px;
        }}
        .section {{
            margin-bottom: 30px;
        }}
        .section-title {{
            background-color: #f8f9fa;
            padding: 12px 15px;
            border-left: 4px solid #667eea;
            font-weight: 600;
            font-size: 16px;
            margin-bottom: 15px;
            color: #333;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        td {{
            padding: 12px 15px;
            border-bottom: 1px solid #eee;
        }}
        tr:last-child td {{
            border-bottom: none;
        }}
        .tool-name {{
            font-weight: 600;
            color: #333;
            width: 120px;
            vertical-align: top;
        }}
        .tool-data {{
            color: #666;
        }}
        .daily-entry {{
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            border-bottom: 1px dashed #eee;
        }}
        .daily-entry:last-child {{
            border-bottom: none;
        }}
        .date {{
            color: #888;
            font-size: 13px;
        }}
        .tokens {{
            font-weight: 600;
            color: #333;
        }}
        .details {{
            color: #999;
            font-size: 12px;
            margin-left: 10px;
        }}
        .summary-tool {{
            font-weight: 600;
            color: #333;
        }}
        .summary-days {{
            color: #888;
            text-align: center;
        }}
        .summary-tokens {{
            font-weight: 600;
            color: #667eea;
            text-align: right;
        }}
        .summary-avg {{
            color: #999;
            text-align: right;
        }}
        .no-data {{
            text-align: center;
            color: #999;
            padding: 30px !important;
        }}
        .footer {{
            background-color: #f8f9fa;
            padding: 20px 30px;
            text-align: center;
            color: #999;
            font-size: 12px;
            border-top: 1px solid #eee;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
        }}
        .badge-today {{
            background-color: #e3f2fd;
            color: #1976d2;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>AI Token Usage Report</h1>
            <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <span class="badge badge-today">Report Date: {display_date}</span>
        </div>

        <div class="content">
            <div class="section">
                <div class="section-title">Today's Usage by Tool</div>
                <table>
                    {daily_tables}
                </table>
            </div>

            <div class="section">
                <div class="section-title">All-Time Summary</div>
                <table>
                    <tr>
                        <th style="text-align: left; padding: 10px 15px; color: #666; font-weight: 500;">Tool</th>
                        <th style="text-align: center; padding: 10px 15px; color: #666; font-weight: 500;">Days</th>
                        <th style="text-align: right; padding: 10px 15px; color: #666; font-weight: 500;">Total</th>
                        <th style="text-align: right; padding: 10px 15px; color: #666; font-weight: 500;">Avg/Day</th>
                    </tr>
                    {summary_rows}
                </table>
            </div>
        </div>

        <div class="footer">
            <p>End of Report</p>
            <p>AI Token Usage Tracking System</p>
        </div>
    </div>
</body>
</html>"""

    return html


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
