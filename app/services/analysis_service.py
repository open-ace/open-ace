#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - Analysis Service

Business logic for usage analysis and reporting.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.repositories.message_repo import MessageRepository
from app.repositories.usage_repo import UsageRepository
from app.utils.cache import cached
from app.utils.helpers import get_today, get_days_ago

logger = logging.getLogger(__name__)


class AnalysisService:
    """Service for analysis-related business logic."""

    def __init__(
        self,
        usage_repo: Optional[UsageRepository] = None,
        message_repo: Optional[MessageRepository] = None
    ):
        """
        Initialize service.

        Args:
            usage_repo: Optional UsageRepository instance.
            message_repo: Optional MessageRepository instance.
        """
        self.usage_repo = usage_repo or UsageRepository()
        self.message_repo = message_repo or MessageRepository()

    @cached(ttl=60, key_prefix='analysis', skip_args=[0])
    def get_batch_analysis(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None
    ) -> Dict:
        """
        Get all analysis data in a single optimized call.

        This method fetches all required data once and reuses it for
        multiple analysis calculations, reducing database queries.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.

        Returns:
            Dict: Combined analysis data.
        """
        if not start_date:
            start_date = get_days_ago(30)
        if not end_date:
            end_date = get_today()

        # Fetch all required data ONCE - use lightweight query for better performance
        usage_data = self.message_repo.get_daily_range_lightweight(start_date, end_date, host_name=host_name)
        user_tokens = self.message_repo.get_user_token_totals(
            start_date=start_date,
            end_date=end_date,
            host_name=host_name
        )
        tool_stats = self.message_repo.get_tool_token_totals(
            start_date=start_date,
            end_date=end_date,
            host_name=host_name
        )
        daily_data = self.message_repo.get_daily_token_totals(start_date, end_date, host_name)
        hourly_data = self.message_repo.get_hourly_usage(start_date, end_date, host_name)
        # Use lightweight query for conversation stats instead of full history
        conversation_stats = self.message_repo.get_conversation_stats_summary(host_name=host_name)

        # Calculate all metrics from shared data
        total_tokens = sum(u.get('tokens_used', 0) for u in usage_data)
        total_input = sum(u.get('input_tokens', 0) for u in usage_data)
        total_output = sum(u.get('output_tokens', 0) for u in usage_data)
        # Count messages (each row in daily_messages is a message)
        total_requests = len(usage_data)

        # If tokens are 0 from usage_data, get from messages
        if total_tokens == 0 and user_tokens:
            total_tokens = sum(u.get('total_tokens', 0) for u in user_tokens)
            total_input = sum(u.get('total_input_tokens', 0) for u in user_tokens)
            total_output = sum(u.get('total_output_tokens', 0) for u in user_tokens)

        # Get unique tools and hosts
        tools = set()
        hosts = set()
        for u in usage_data:
            if u.get('tool_name'):
                tools.add(u['tool_name'])
            if u.get('host_name'):
                hosts.add(u['host_name'])

        # Top tools
        top_tools = []
        if tool_stats:
            for ts in tool_stats[:5]:
                top_tools.append({
                    'tool': ts.get('tool_name', 'unknown'),
                    'count': ts.get('total_tokens', 0)
                })

        # Sessions and averages
        total_sessions = len(set((u.get('date'), u.get('tool_name')) for u in usage_data))
        total_messages = sum(u.get('message_count', 0) for u in user_tokens) if user_tokens else total_requests

        # Key metrics
        key_metrics = {
            'total_tokens': total_tokens,
            'total_input_tokens': total_input,
            'total_output_tokens': total_output,
            'total_requests': total_requests,
            'total_messages': total_messages,
            'unique_tools': len(tools) if tools else 1,
            'unique_hosts': len(hosts) if hosts else 1,
            'top_tools': top_tools,
            'top_hosts': [{'host': h, 'count': 0} for h in hosts][:5] if hosts else [],
            'total_sessions': total_sessions if total_sessions > 0 else 1,
            'avg_tokens_per_session': total_tokens / total_sessions if total_sessions > 0 else 0,
            'avg_messages_per_session': total_messages / total_sessions if total_sessions > 0 else 0,
            'date_range': {'start': start_date, 'end': end_date}
        }

        # Daily/Hourly usage
        daily_totals = {}
        for u in usage_data:
            date = u.get('date')
            if date not in daily_totals:
                daily_totals[date] = {'date': date, 'tokens': 0, 'requests': 0}
            daily_totals[date]['tokens'] += u.get('tokens_used', 0)
            daily_totals[date]['requests'] += 1  # Each row is a message

        if all(d['tokens'] == 0 for d in daily_totals.values()) if daily_totals else True:
            for m in daily_data:
                date = m.get('date')
                if date in daily_totals:
                    daily_totals[date]['tokens'] = m.get('total_tokens', 0)
                else:
                    daily_totals[date] = {
                        'date': date,
                        'tokens': m.get('total_tokens', 0),
                        'requests': m.get('message_count', 0)
                    }

        hourly_result = [{'hour': int(h['hour']), 'tokens': h['tokens'] or 0, 'requests': h['requests'] or 0} for h in hourly_data]

        daily_hourly_usage = {
            'daily': list(daily_totals.values()),
            'hourly': hourly_result
        }

        # Peak usage
        daily_totals_for_peak = {}
        for d in daily_data:
            date = d.get('date')
            tokens = d.get('total_tokens', 0)
            if date:
                daily_totals_for_peak[date] = tokens

        sorted_days = sorted(daily_totals_for_peak.items(), key=lambda x: x[1], reverse=True) if daily_totals_for_peak else []
        peak_days = [{'date': d, 'tokens': t} for d, t in sorted_days[:5]]

        hourly_totals = {}
        for h in hourly_data:
            hour = int(h.get('hour', 0))
            if hour not in hourly_totals:
                hourly_totals[hour] = 0
            hourly_totals[hour] += h.get('tokens', 0) or 0

        sorted_hours = sorted(hourly_totals.items(), key=lambda x: x[1], reverse=True) if hourly_totals else []
        peak_hours = [{'hour': h, 'avg_tokens': t} for h, t in sorted_hours[:5]]

        peak_usage = {
            'peak_days': peak_days,
            'peak_hours': peak_hours,
            'peak_day': sorted_days[0][0] if sorted_days else None,
            'peak_tokens': sorted_days[0][1] if sorted_days else 0,
            'average_daily': sum(daily_totals_for_peak.values()) / len(daily_totals_for_peak) if daily_totals_for_peak else 0
        }

        # User ranking
        users = []
        for i, user_data in enumerate(user_tokens[:10]):
            username = user_data.get('sender_name', 'Unknown')
            if username and '.local' in username:
                username = username.split('-')[0]
            users.append({
                'user_id': i + 1,
                'username': username,
                'tokens': user_data.get('total_tokens', 0),
                'requests': user_data.get('message_count', 0)
            })

        user_ranking = {'users': users}

        # Conversation stats - already computed by lightweight query
        # conversation_stats is directly from get_conversation_stats_summary

        # Tool comparison
        tools_comparison = []
        for tool_data in tool_stats:
            total_t_tokens = tool_data.get('total_tokens', 0)
            message_count = tool_data.get('message_count', 0)
            tools_comparison.append({
                'tool_name': tool_data.get('tool_name', 'unknown'),
                'total_tokens': total_t_tokens,
                'total_requests': message_count,
                'total_input_tokens': tool_data.get('total_input_tokens', 0),
                'total_output_tokens': tool_data.get('total_output_tokens', 0),
                'avg_tokens_per_request': total_t_tokens / message_count if message_count > 0 else 0
            })
        tools_comparison.sort(key=lambda x: x['total_tokens'], reverse=True)

        tool_comparison = {'tools': tools_comparison}

        # User segmentation
        segments = {'high': 0, 'medium': 0, 'low': 0, 'dormant': 0}
        for user_data in user_tokens:
            tokens = user_data.get('total_tokens', 0)
            if tokens > 10000:
                segments['high'] += 1
            elif tokens >= 1000:
                segments['medium'] += 1
            else:
                segments['low'] += 1

        user_segmentation = segments

        return {
            'key_metrics': key_metrics,
            'daily_hourly_usage': daily_hourly_usage,
            'peak_usage': peak_usage,
            'user_ranking': user_ranking,
            'conversation_stats': conversation_stats,
            'tool_comparison': tool_comparison,
            'user_segmentation': user_segmentation
        }

    @cached(ttl=60, key_prefix='analysis', skip_args=[0])
    def get_key_metrics(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None
    ) -> Dict:
        """
        Get key metrics for the dashboard.
        
        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.
            
        Returns:
            Dict: Key metrics data.
        """
        if not start_date:
            start_date = get_days_ago(30)
        if not end_date:
            end_date = get_today()

        usage_data = self.usage_repo.get_daily_range(start_date, end_date, host_name=host_name)

        # Also get message data for tokens (since daily_usage may have 0 tokens)
        user_tokens = self.message_repo.get_user_token_totals(
            start_date=start_date,
            end_date=end_date,
            host_name=host_name
        )

        total_tokens = sum(u.get('tokens_used', 0) for u in usage_data)
        total_input = sum(u.get('input_tokens', 0) for u in usage_data)
        total_output = sum(u.get('output_tokens', 0) for u in usage_data)
        total_requests = sum(u.get('request_count', 0) for u in usage_data)

        # If tokens are 0 from usage_data, get from messages
        if total_tokens == 0 and user_tokens:
            total_tokens = sum(u.get('total_tokens', 0) for u in user_tokens)
            total_input = sum(u.get('total_input_tokens', 0) for u in user_tokens)
            total_output = sum(u.get('total_output_tokens', 0) for u in user_tokens)

        # Get unique tools and hosts
        tools = set()
        hosts = set()
        for u in usage_data:
            if u.get('tool_name'):
                tools.add(u['tool_name'])
            if u.get('host_name'):
                hosts.add(u['host_name'])

        # Get top tools by token usage from messages (has real token data)
        tool_stats = self.message_repo.get_tool_token_totals(
            start_date=start_date,
            end_date=end_date,
            host_name=host_name
        )

        top_tools = []
        if tool_stats:
            for ts in tool_stats[:5]:
                top_tools.append({
                    'tool': ts.get('tool_name', 'unknown'),
                    'count': ts.get('total_tokens', 0)
                })
        else:
            # Fallback to usage_data if no message data
            tool_totals = {}
            for u in usage_data:
                tool = u.get('tool_name', 'unknown')
                if tool not in tool_totals:
                    tool_totals[tool] = 0
                tool_totals[tool] += u.get('tokens_used', 0)

            # If tool_totals are all 0, use request_count instead
            if all(v == 0 for v in tool_totals.values()):
                for u in usage_data:
                    tool = u.get('tool_name', 'unknown')
                    tool_totals[tool] = tool_totals.get(tool, 0) + u.get('request_count', 0)

            top_tools = sorted(
                [{'tool': k, 'count': v} for k, v in tool_totals.items()],
                key=lambda x: x['count'],
                reverse=True
            )[:5]

        # Calculate sessions and averages
        total_sessions = len(set(
            (u.get('date'), u.get('tool_name')) for u in usage_data
        ))

        # Count total messages from user_tokens
        total_messages = sum(u.get('message_count', 0) for u in user_tokens) if user_tokens else total_requests

        return {
            'total_tokens': total_tokens,
            'total_input_tokens': total_input,
            'total_output_tokens': total_output,
            'total_requests': total_requests,
            'total_messages': total_messages,
            'unique_tools': len(tools) if tools else 1,
            'unique_hosts': len(hosts) if hosts else 1,
            'top_tools': top_tools,
            'top_hosts': [{'host': h, 'count': 0} for h in hosts][:5] if hosts else [],
            'total_sessions': total_sessions if total_sessions > 0 else 1,
            'avg_tokens_per_session': total_tokens / total_sessions if total_sessions > 0 else 0,
            'avg_messages_per_session': total_messages / total_sessions if total_sessions > 0 else 0,
            'date_range': {
                'start': start_date,
                'end': end_date
            }
        }

    def get_hourly_usage(
        self,
        date: Optional[str] = None,
        tool_name: Optional[str] = None,
        host_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Get hourly usage breakdown.
        
        Args:
            date: Date string (defaults to today).
            tool_name: Optional tool name filter.
            host_name: Optional host name filter.
            
        Returns:
            List[Dict]: Hourly usage data.
        """
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')

        # This would require timestamp analysis from messages
        # For now, return placeholder
        return []

    @cached(ttl=60, key_prefix='analysis', skip_args=[0])
    def get_daily_hourly_usage(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None
    ) -> Dict:
        """
        Get daily and hourly usage patterns.
        
        Returns:
            Dict: Daily and hourly usage patterns.
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')

        usage_data = self.usage_repo.get_daily_range(start_date, end_date, host_name=host_name)

        # Aggregate by date
        daily_totals = {}
        for u in usage_data:
            date = u.get('date')
            if date not in daily_totals:
                daily_totals[date] = {
                    'date': date,
                    'tokens': 0,
                    'requests': 0
                }
            daily_totals[date]['tokens'] += u.get('tokens_used', 0)
            daily_totals[date]['requests'] += u.get('request_count', 0)

        # If all tokens are 0, get from messages
        if all(d['tokens'] == 0 for d in daily_totals.values()) if daily_totals else True:
            # Get daily tokens from messages
            msg_daily = self.message_repo.get_daily_token_totals(start_date, end_date, host_name)
            for m in msg_daily:
                date = m.get('date')
                if date in daily_totals:
                    daily_totals[date]['tokens'] = m.get('total_tokens', 0)
                else:
                    daily_totals[date] = {
                        'date': date,
                        'tokens': m.get('total_tokens', 0),
                        'requests': m.get('message_count', 0)
                    }

        # Get hourly usage from messages
        hourly_data = self.message_repo.get_hourly_usage(start_date, end_date, host_name)

        # Format hourly data
        hourly_result = []
        for h in hourly_data:
            hourly_result.append({
                'hour': int(h['hour']),
                'tokens': h['tokens'] or 0,
                'requests': h['requests'] or 0
            })

        return {
            'daily': list(daily_totals.values()),
            'hourly': hourly_result
        }

    @cached(ttl=60, key_prefix='analysis', skip_args=[0])
    def get_peak_usage(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None
    ) -> Dict:
        """
        Get peak usage periods.
        
        Returns:
            Dict: Peak usage information with peak_days array.
        """
        if not start_date:
            start_date = get_days_ago(30)
        if not end_date:
            end_date = get_today()

        # Get daily token totals from messages (has real token data)
        daily_data = self.message_repo.get_daily_token_totals(start_date, end_date, host_name)

        if not daily_data:
            return {'peak_days': [], 'peak_hours': []}

        # Aggregate by date
        daily_totals = {}
        for d in daily_data:
            date = d.get('date')
            tokens = d.get('total_tokens', 0)
            if date:
                daily_totals[date] = tokens

        if not daily_totals:
            return {'peak_days': [], 'peak_hours': []}

        # Sort days by tokens and get top 5
        sorted_days = sorted(daily_totals.items(), key=lambda x: x[1], reverse=True)
        peak_days = [{'date': d, 'tokens': t} for d, t in sorted_days[:5]]

        # Get hourly usage for peak hours
        hourly_data = self.message_repo.get_hourly_usage(start_date, end_date, host_name)
        hourly_totals = {}
        for h in hourly_data:
            hour = int(h.get('hour', 0))
            if hour not in hourly_totals:
                hourly_totals[hour] = 0
            hourly_totals[hour] += h.get('tokens', 0) or 0

        # Sort hours by tokens and get top 5
        sorted_hours = sorted(hourly_totals.items(), key=lambda x: x[1], reverse=True)
        peak_hours = [{'hour': h, 'avg_tokens': t} for h, t in sorted_hours[:5]]

        return {
            'peak_days': peak_days,
            'peak_hours': peak_hours,
            'peak_day': sorted_days[0][0] if sorted_days else None,
            'peak_tokens': sorted_days[0][1] if sorted_days else 0,
            'average_daily': sum(daily_totals.values()) / len(daily_totals) if daily_totals else 0
        }

    @cached(ttl=60, key_prefix='analysis', skip_args=[0])
    def get_user_ranking(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
        limit: int = 10
    ) -> Dict:
        """
        Get user ranking by token usage.
        
        Returns:
            Dict: User rankings with users array.
        """
        if not start_date:
            start_date = get_days_ago(30)
        if not end_date:
            end_date = get_today()

        # Get user token usage from messages
        user_tokens = self.message_repo.get_user_token_totals(
            start_date=start_date,
            end_date=end_date,
            host_name=host_name
        )

        # Sort by tokens and limit
        users = []
        for i, user_data in enumerate(user_tokens[:limit]):
            # Clean up username - remove machine-generated suffixes
            username = user_data.get('sender_name', 'Unknown')
            if username and '.local' in username:
                # Remove machine suffix like "rhuang-RichdeMacBook-Pro.local-openclaw"
                # Just use the first part before any hyphen
                username = username.split('-')[0]

            users.append({
                'user_id': i + 1,
                'username': username,
                'tokens': user_data.get('total_tokens', 0),
                'requests': user_data.get('message_count', 0)
            })

        return {'users': users}

    @cached(ttl=60, key_prefix='analysis', skip_args=[0])
    def get_conversation_stats(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None
    ) -> Dict:
        """
        Get conversation statistics.
        
        Returns:
            Dict: Conversation statistics.
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')

        # Get conversation history
        conversations = self.message_repo.get_conversation_history(
            host_name=host_name,
            limit=1000
        )

        total_conversations = len(conversations)
        total_messages = sum(c.get('message_count', 0) for c in conversations)
        total_tokens = sum(c.get('total_tokens', 0) for c in conversations)

        return {
            'total_conversations': total_conversations,
            'total_messages': total_messages,
            'total_tokens': total_tokens,
            'average_messages_per_conversation': total_messages / total_conversations if total_conversations > 0 else 0,
            'average_tokens_per_conversation': total_tokens / total_conversations if total_conversations > 0 else 0
        }

    @cached(ttl=60, key_prefix='analysis', skip_args=[0])
    def get_tool_comparison(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None
    ) -> Dict:
        """
        Get tool comparison data.
        
        Returns:
            Dict: Tool comparison data with tools array.
        """
        if not start_date:
            start_date = get_days_ago(30)
        if not end_date:
            end_date = get_today()

        # Get tool stats from messages (has real token data)
        tool_stats = self.message_repo.get_tool_token_totals(
            start_date=start_date,
            end_date=end_date,
            host_name=host_name
        )

        # Convert to array format expected by frontend
        tools = []
        for tool_data in tool_stats:
            total_tokens = tool_data.get('total_tokens', 0)
            message_count = tool_data.get('message_count', 0)
            tools.append({
                'tool_name': tool_data.get('tool_name', 'unknown'),
                'total_tokens': total_tokens,
                'total_requests': message_count,
                'total_input_tokens': tool_data.get('total_input_tokens', 0),
                'total_output_tokens': tool_data.get('total_output_tokens', 0),
                'avg_tokens_per_request': total_tokens / message_count if message_count > 0 else 0
            })

        # Sort by total tokens descending
        tools.sort(key=lambda x: x['total_tokens'], reverse=True)

        return {'tools': tools}

    def get_recommendations(
        self,
        host_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Get usage optimization recommendations.
        
        Returns:
            List[Dict]: List of recommendations.
        """
        recommendations = []

        # Get recent usage data
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

        usage_data = self.usage_repo.get_daily_range(start_date, end_date, host_name=host_name)

        if not usage_data:
            return [{'type': 'info', 'message': 'No usage data available for analysis'}]

        # Analyze usage patterns
        total_tokens = sum(u.get('tokens_used', 0) for u in usage_data)
        total_input = sum(u.get('input_tokens', 0) for u in usage_data)
        total_output = sum(u.get('output_tokens', 0) for u in usage_data)

        # Check input/output ratio
        if total_input > 0 and total_output > 0:
            ratio = total_output / total_input
            if ratio < 0.5:
                recommendations.append({
                    'type': 'optimization',
                    'message': 'Consider optimizing prompts to reduce input token usage',
                    'details': f'Current output/input ratio: {ratio:.2f}'
                })

        # Check for tools with high usage
        tool_totals = {}
        for u in usage_data:
            tool = u.get('tool_name', 'unknown')
            if tool not in tool_totals:
                tool_totals[tool] = 0
            tool_totals[tool] += u.get('tokens_used', 0)

        if tool_totals:
            top_tool = max(tool_totals, key=tool_totals.get)
            top_usage = tool_totals[top_tool]
            if total_tokens > 0 and (top_usage / total_tokens) > 0.7:
                recommendations.append({
                    'type': 'info',
                    'message': f'High concentration of usage on {top_tool}',
                    'details': f'{top_tool} accounts for {top_usage/total_tokens*100:.1f}% of total tokens'
                })

        if not recommendations:
            recommendations.append({
                'type': 'success',
                'message': 'Usage patterns look healthy'
            })

        return recommendations

    def get_user_segmentation(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None
    ) -> Dict:
        """
        Get user segmentation based on token usage.

        Segments:
        - High: >10K tokens
        - Medium: 1K-10K tokens
        - Low: <1K tokens
        - Dormant: No activity in the period

        Returns:
            Dict: User segmentation data.
        """
        if not start_date:
            start_date = get_days_ago(30)
        if not end_date:
            end_date = get_today()

        # Get user token usage from messages
        user_tokens = self.message_repo.get_user_token_totals(
            start_date=start_date,
            end_date=end_date,
            host_name=host_name
        )

        # Segment users
        segments = {
            'high': 0,      # >10K tokens
            'medium': 0,    # 1K-10K tokens
            'low': 0,       # <1K tokens
            'dormant': 0    # No activity
        }

        for user_data in user_tokens:
            tokens = user_data.get('total_tokens', 0)
            if tokens > 10000:
                segments['high'] += 1
            elif tokens >= 1000:
                segments['medium'] += 1
            else:
                segments['low'] += 1

        return segments

    def detect_anomalies(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None,
        anomaly_type: Optional[str] = None,
        severity: Optional[str] = None
    ) -> Dict:
        """
        Detect usage anomalies.

        Anomaly types:
        1. usage_spike: Daily usage exceeds 2 standard deviations above mean
        2. usage_drop: Daily usage below 50% of average

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            host_name: Optional host name filter.
            anomaly_type: Optional filter by anomaly type ('spike', 'drop').
            severity: Optional filter by severity ('high', 'medium', 'low').

        Returns:
            Dict: Anomaly detection results with anomalies list.
        """
        if not start_date:
            start_date = get_days_ago(30)
        if not end_date:
            end_date = get_today()

        # Get daily token totals from messages
        daily_data = self.message_repo.get_daily_token_totals(start_date, end_date, host_name)

        if not daily_data or len(daily_data) < 3:
            return {
                'anomalies': [],
                'summary': {
                    'total': 0,
                    'high': 0,
                    'medium': 0,
                    'low': 0
                }
            }

        # Calculate statistics
        tokens = [d.get('total_tokens', 0) for d in daily_data]
        avg_tokens = sum(tokens) / len(tokens)

        # Calculate standard deviation
        if len(tokens) > 1:
            variance = sum((t - avg_tokens) ** 2 for t in tokens) / len(tokens)
            std_dev = variance ** 0.5
        else:
            std_dev = 0

        anomalies = []

        # Detect anomalies
        for d in daily_data:
            token = d.get('total_tokens', 0)
            date = d.get('date')

            if std_dev > 0:
                deviation = (token - avg_tokens) / std_dev

                # Usage spike: more than 2 standard deviations above mean
                if deviation > 2:
                    anomaly = {
                        'date': date,
                        'tokens': token,
                        'expected': round(avg_tokens),
                        'deviation': round(deviation, 2),
                        'type': 'spike',
                        'severity': 'high' if deviation > 3 else 'medium'
                    }

                    # Apply filters
                    if anomaly_type and anomaly['type'] != anomaly_type:
                        continue
                    if severity and anomaly['severity'] != severity:
                        continue

                    anomalies.append(anomaly)

                # Usage drop: below 50% of average
                elif token < avg_tokens * 0.5 and avg_tokens > 0:
                    anomaly = {
                        'date': date,
                        'tokens': token,
                        'expected': round(avg_tokens),
                        'deviation': round((avg_tokens - token) / avg_tokens * 100, 1),
                        'type': 'drop',
                        'severity': 'low'
                    }

                    # Apply filters
                    if anomaly_type and anomaly['type'] != anomaly_type:
                        continue
                    if severity and anomaly['severity'] != severity:
                        continue

                    anomalies.append(anomaly)

        # Sort by date descending
        anomalies.sort(key=lambda x: x['date'], reverse=True)

        # Calculate summary
        summary = {
            'total': len(anomalies),
            'high': sum(1 for a in anomalies if a['severity'] == 'high'),
            'medium': sum(1 for a in anomalies if a['severity'] == 'medium'),
            'low': sum(1 for a in anomalies if a['severity'] == 'low')
        }

        return {
            'anomalies': anomalies,
            'summary': summary,
            'statistics': {
                'average': round(avg_tokens),
                'std_deviation': round(std_dev),
                'data_points': len(daily_data)
            }
        }

    def get_anomaly_trend(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        host_name: Optional[str] = None
    ) -> Dict:
        """
        Get anomaly trend over time.

        Returns:
            Dict: Anomaly trend data grouped by date.
        """
        anomaly_data = self.detect_anomalies(start_date, end_date, host_name)
        anomalies = anomaly_data.get('anomalies', [])

        # Group anomalies by date
        trend_by_date = {}
        for anomaly in anomalies:
            date = anomaly['date']
            if date not in trend_by_date:
                trend_by_date[date] = {
                    'date': date,
                    'count': 0,
                    'spikes': 0,
                    'drops': 0
                }
            trend_by_date[date]['count'] += 1
            if anomaly['type'] == 'spike':
                trend_by_date[date]['spikes'] += 1
            else:
                trend_by_date[date]['drops'] += 1

        # Sort by date
        trend = sorted(trend_by_date.values(), key=lambda x: x['date'])

        return {
            'trend': trend,
            'total_anomalies': len(anomalies)
        }
