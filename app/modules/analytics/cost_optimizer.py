#!/usr/bin/env python3
"""
Open ACE - Cost Optimizer Module

Analyzes usage patterns and provides cost optimization suggestions.
Identifies opportunities for cost savings and efficiency improvements.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from app.repositories.database import Database

logger = logging.getLogger(__name__)


class OptimizationType(Enum):
    """Types of optimization suggestions."""
    MODEL_SWITCH = 'model_switch'
    USAGE_PATTERN = 'usage_pattern'
    QUOTA_ADJUSTMENT = 'quota_adjustment'
    TOOL_CONSOLIDATION = 'tool_consolidation'
    TIME_OPTIMIZATION = 'time_optimization'
    TOKEN_OPTIMIZATION = 'token_optimization'


class Priority(Enum):
    """Priority levels for suggestions."""
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'


@dataclass
class OptimizationSuggestion:
    """A cost optimization suggestion."""
    suggestion_id: str
    suggestion_type: str
    title: str
    description: str
    potential_savings: float
    priority: str
    action_items: List[str] = field(default_factory=list)
    affected_users: List[int] = field(default_factory=list)
    affected_tools: List[str] = field(default_factory=list)
    implementation_effort: str = 'medium'  # low, medium, high
    current_cost: float = 0.0
    optimized_cost: float = 0.0
    savings_percentage: float = 0.0
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'suggestion_id': self.suggestion_id,
            'suggestion_type': self.suggestion_type,
            'title': self.title,
            'description': self.description,
            'potential_savings': round(self.potential_savings, 2),
            'priority': self.priority,
            'action_items': self.action_items,
            'affected_users': self.affected_users,
            'affected_tools': self.affected_tools,
            'implementation_effort': self.implementation_effort,
            'current_cost': round(self.current_cost, 4),
            'optimized_cost': round(self.optimized_cost, 4),
            'savings_percentage': round(self.savings_percentage, 2),
            'created_at': self.created_at.isoformat(),
        }


@dataclass
class UsagePattern:
    """Usage pattern analysis result."""
    pattern_type: str
    description: str
    frequency: int
    impact: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'pattern_type': self.pattern_type,
            'description': self.description,
            'frequency': self.frequency,
            'impact': self.impact,
            'details': self.details,
        }


class CostOptimizer:
    """Cost optimization analyzer for AI usage."""

    # Model pricing (per 1K tokens)
    MODEL_PRICING = {
        'claude-3-opus': {'input': 0.015, 'output': 0.075},
        'claude-3-sonnet': {'input': 0.003, 'output': 0.015},
        'claude-3-haiku': {'input': 0.00025, 'output': 0.00125},
        'claude-3-5-sonnet': {'input': 0.003, 'output': 0.015},
        'claude-3-5-haiku': {'input': 0.001, 'output': 0.005},
        'qwen-max': {'input': 0.02, 'output': 0.06},
        'qwen-plus': {'input': 0.004, 'output': 0.012},
        'qwen-turbo': {'input': 0.002, 'output': 0.006},
        'gpt-4': {'input': 0.03, 'output': 0.06},
        'gpt-4-turbo': {'input': 0.01, 'output': 0.03},
        'gpt-4o': {'input': 0.005, 'output': 0.015},
        'gpt-4o-mini': {'input': 0.00015, 'output': 0.0006},
        'gpt-3.5-turbo': {'input': 0.0005, 'output': 0.0015},
    }

    # Model hierarchy (expensive to cheap)
    MODEL_HIERARCHY = {
        'claude': ['claude-3-opus', 'claude-3-5-sonnet', 'claude-3-sonnet', 'claude-3-5-haiku', 'claude-3-haiku'],
        'qwen': ['qwen-max', 'qwen-plus', 'qwen-turbo'],
        'gpt': ['gpt-4', 'gpt-4-turbo', 'gpt-4o', 'gpt-4o-mini', 'gpt-3.5-turbo'],
    }

    # Thresholds
    SHORT_REQUEST_THRESHOLD = 500  # tokens
    HIGH_COST_USER_THRESHOLD = 100.0  # USD
    LOW_USAGE_THRESHOLD = 0.2  # 20% of average

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize Cost Optimizer.

        Args:
            db: Optional Database instance.
        """
        self.db = db or Database()

    def analyze(self, days: int = 30) -> List[OptimizationSuggestion]:
        """
        Analyze usage and generate optimization suggestions.

        Args:
            days: Number of days to analyze.

        Returns:
            List of OptimizationSuggestion objects.
        """
        suggestions = []

        end_date = datetime.utcnow().strftime('%Y-%m-%d')
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

        # Get usage data
        usage_data = self._get_usage_data(start_date, end_date)

        # Analyze different aspects
        suggestions.extend(self._analyze_model_usage(usage_data, start_date, end_date))
        suggestions.extend(self._analyze_usage_patterns(usage_data))
        suggestions.extend(self._analyze_quota_efficiency(usage_data))
        suggestions.extend(self._analyze_tool_usage(usage_data))
        suggestions.extend(self._analyze_token_efficiency(usage_data))

        # Sort by potential savings
        suggestions.sort(key=lambda x: x.potential_savings, reverse=True)

        return suggestions

    def _get_usage_data(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Get comprehensive usage data."""
        # Overall statistics
        overall = self.db.fetch_one('''
            SELECT
                COUNT(*) as total_requests,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        ''', (start_date, end_date))

        # By model
        by_model = self.db.fetch_all('''
            SELECT tool_name, model,
                   COUNT(*) as requests,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   AVG(input_tokens + output_tokens) as avg_tokens_per_request
            FROM daily_usage
            WHERE date >= ? AND date <= ?
            GROUP BY tool_name, model
        ''', (start_date, end_date))

        # By user
        by_user = self.db.fetch_all('''
            SELECT user_id,
                   COUNT(*) as requests,
                   SUM(input_tokens + output_tokens) as total_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ? AND user_id IS NOT NULL
            GROUP BY user_id
        ''', (start_date, end_date))

        # By hour
        by_hour = self.db.fetch_all('''
            SELECT strftime('%H', timestamp) as hour,
                   COUNT(*) as requests
            FROM daily_usage
            WHERE date >= ? AND date <= ?
            GROUP BY hour
            ORDER BY requests DESC
        ''', (start_date, end_date))

        return {
            'overall': overall,
            'by_model': by_model,
            'by_user': by_user,
            'by_hour': by_hour,
        }

    def _analyze_model_usage(
        self,
        data: Dict,
        start_date: str,
        end_date: str
    ) -> List[OptimizationSuggestion]:
        """Analyze model usage for optimization opportunities."""
        suggestions = []

        for row in data['by_model']:
            model = row.get('model') or 'unknown'
            avg_tokens = row.get('avg_tokens_per_request') or 0
            requests = row.get('requests') or 0
            input_tokens = row.get('input_tokens') or 0
            output_tokens = row.get('output_tokens') or 0

            # Check for expensive model on simple tasks
            if self._is_expensive_model(model) and avg_tokens < self.SHORT_REQUEST_THRESHOLD:
                cheaper_model = self._find_cheaper_alternative(model)
                if cheaper_model:
                    savings = self._calculate_model_savings(
                        model, cheaper_model, input_tokens, output_tokens
                    )

                    if savings > 1.0:  # Only suggest if savings > $1
                        suggestions.append(OptimizationSuggestion(
                            suggestion_id=f'model_switch_{model}_{cheaper_model}',
                            suggestion_type=OptimizationType.MODEL_SWITCH.value,
                            title=f"Switch to {cheaper_model} for simple tasks",
                            description=f"Model {model} is used for short requests (avg {avg_tokens:.0f} tokens). "
                                      f"Consider using {cheaper_model} for tasks under {self.SHORT_REQUEST_THRESHOLD} tokens.",
                            potential_savings=savings,
                            priority=Priority.HIGH.value,
                            action_items=[
                                f"Route requests under {self.SHORT_REQUEST_THRESHOLD} tokens to {cheaper_model}",
                                "Keep complex tasks on current model",
                                "Implement automatic model selection based on task complexity",
                            ],
                            affected_tools=[row.get('tool_name')] if row.get('tool_name') else [],
                            current_cost=self._calculate_cost(model, input_tokens, output_tokens),
                            optimized_cost=self._calculate_cost(cheaper_model, input_tokens, output_tokens),
                            savings_percentage=self._savings_percentage(savings, input_tokens, output_tokens, model),
                        ))

        return suggestions

    def _analyze_usage_patterns(self, data: Dict) -> List[OptimizationSuggestion]:
        """Analyze usage patterns for optimization."""
        suggestions = []

        # Analyze peak hours
        if data['by_hour']:
            peak_hours = sorted(data['by_hour'], key=lambda x: x['requests'], reverse=True)[:3]
            peak_hours_list = [h['hour'] for h in peak_hours]

            total_requests = data['overall']['total_requests'] or 1
            peak_requests = sum(h['requests'] for h in peak_hours)
            peak_percentage = peak_requests / total_requests * 100

            if peak_percentage > 50:
                suggestions.append(OptimizationSuggestion(
                    suggestion_id='time_optimization_peak',
                    suggestion_type=OptimizationType.TIME_OPTIMIZATION.value,
                    title="Optimize usage time distribution",
                    description=f"Peak hours ({', '.join(peak_hours_list)}:00) concentrate {peak_percentage:.1f}% of requests. "
                              "Distributing usage more evenly can improve response times.",
                    potential_savings=0,  # Time optimization doesn't directly save money
                    priority=Priority.MEDIUM.value,
                    action_items=[
                        "Schedule batch tasks during off-peak hours",
                        "Implement request queuing for non-urgent tasks",
                        "Monitor response times and adjust scheduling",
                    ],
                ))

        return suggestions

    def _analyze_quota_efficiency(self, data: Dict) -> List[OptimizationSuggestion]:
        """Analyze quota allocation efficiency."""
        suggestions = []

        if data['by_user']:
            total_tokens = sum(u['total_tokens'] or 0 for u in data['by_user'])
            user_count = len(data['by_user'])
            avg_tokens = total_tokens / user_count if user_count > 0 else 0

            # Find low usage users
            low_usage_users = [
                u['user_id'] for u in data['by_user']
                if (u['total_tokens'] or 0) < avg_tokens * self.LOW_USAGE_THRESHOLD
            ]

            if len(low_usage_users) > user_count * 0.3:
                suggestions.append(OptimizationSuggestion(
                    suggestion_id='quota_adjustment_low_usage',
                    suggestion_type=OptimizationType.QUOTA_ADJUSTMENT.value,
                    title="Optimize quota allocation",
                    description=f"Found {len(low_usage_users)} users with usage below 20% of average. "
                              "Consider reallocating unused quotas.",
                    potential_savings=0,
                    priority=Priority.LOW.value,
                    action_items=[
                        "Review quota settings for low-usage users",
                        "Consider quota pooling or reallocation",
                        "Implement quota expiration and recycling",
                    ],
                    affected_users=low_usage_users[:10],
                ))

        return suggestions

    def _analyze_tool_usage(self, data: Dict) -> List[OptimizationSuggestion]:
        """Analyze tool usage patterns."""
        suggestions = []

        tools = set(row.get('tool_name') for row in data['by_model'] if row.get('tool_name'))

        if len(tools) > 2:
            suggestions.append(OptimizationSuggestion(
                suggestion_id='tool_consolidation',
                suggestion_type=OptimizationType.TOOL_CONSOLIDATION.value,
                title="Consider tool consolidation",
                description=f"Currently using {len(tools)} different AI tools. "
                          "Consolidation may enable volume discounts.",
                potential_savings=0,
                priority=Priority.LOW.value,
                action_items=[
                    "Evaluate usage frequency and cost per tool",
                    "Negotiate volume discounts with providers",
                    "Consider standardizing on primary tools",
                ],
                affected_tools=list(tools),
            ))

        return suggestions

    def _analyze_token_efficiency(self, data: Dict) -> List[OptimizationSuggestion]:
        """Analyze token usage efficiency."""
        suggestions = []

        total_input = data['overall']['total_input_tokens'] or 0
        total_output = data['overall']['total_output_tokens'] or 0
        total_tokens = total_input + total_output

        if total_tokens > 0:
            output_ratio = total_output / total_tokens

            # High input ratio might indicate verbose prompts
            if output_ratio < 0.3:
                suggestions.append(OptimizationSuggestion(
                    suggestion_id='token_optimization_input',
                    suggestion_type=OptimizationType.TOKEN_OPTIMIZATION.value,
                    title="Optimize prompt efficiency",
                    description=f"Output ratio is only {output_ratio*100:.1f}%. "
                              "Consider optimizing prompts to reduce input tokens.",
                    potential_savings=total_input * 0.00001 * 0.3,  # Estimate 30% reduction
                    priority=Priority.MEDIUM.value,
                    action_items=[
                        "Review and optimize prompt templates",
                        "Remove unnecessary context from prompts",
                        "Use prompt caching where available",
                    ],
                ))

        return suggestions

    def _is_expensive_model(self, model: str) -> bool:
        """Check if model is expensive."""
        expensive_models = ['claude-3-opus', 'gpt-4', 'qwen-max']
        model_lower = model.lower() if model else ''
        return any(e in model_lower for e in expensive_models)

    def _find_cheaper_alternative(self, model: str) -> Optional[str]:
        """Find a cheaper alternative model."""
        model_lower = model.lower() if model else ''

        for prefix, hierarchy in self.MODEL_HIERARCHY.items():
            if prefix in model_lower:
                try:
                    idx = next(i for i, m in enumerate(hierarchy) if m in model_lower)
                    if idx < len(hierarchy) - 1:
                        return hierarchy[idx + 1]
                except StopIteration:
                    pass

        return None

    def _calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost for token usage."""
        pricing = self.MODEL_PRICING.get(model, {'input': 0.01, 'output': 0.03})
        return (input_tokens / 1000 * pricing['input'] +
                output_tokens / 1000 * pricing['output'])

    def _calculate_model_savings(
        self,
        current_model: str,
        cheaper_model: str,
        input_tokens: int,
        output_tokens: int
    ) -> float:
        """Calculate savings from switching models."""
        current_cost = self._calculate_cost(current_model, input_tokens, output_tokens)
        cheaper_cost = self._calculate_cost(cheaper_model, input_tokens, output_tokens)
        return current_cost - cheaper_cost

    def _savings_percentage(
        self,
        savings: float,
        input_tokens: int,
        output_tokens: int,
        model: str
    ) -> float:
        """Calculate savings percentage."""
        cost = self._calculate_cost(model, input_tokens, output_tokens)
        return (savings / cost * 100) if cost > 0 else 0

    def get_cost_trend(self, days: int = 30) -> List[Dict[str, Any]]:
        """
        Get daily cost trend.

        Args:
            days: Number of days.

        Returns:
            List of daily cost data.
        """
        end_date = datetime.utcnow().strftime('%Y-%m-%d')
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

        rows = self.db.fetch_all('''
            SELECT date,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
            GROUP BY date
            ORDER BY date
        ''', (start_date, end_date))

        trend = []
        for row in rows:
            input_tokens = row.get('input_tokens') or 0
            output_tokens = row.get('output_tokens') or 0

            # Use average pricing for trend
            cost = (input_tokens / 1000 * 0.005 + output_tokens / 1000 * 0.015)

            trend.append({
                'date': row.get('date'),
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'cost': round(cost, 4),
            })

        return trend

    def get_efficiency_report(self, days: int = 30) -> Dict[str, Any]:
        """
        Get efficiency analysis report.

        Args:
            days: Number of days to analyze.

        Returns:
            Dict with efficiency metrics.
        """
        end_date = datetime.utcnow().strftime('%Y-%m-%d')
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

        data = self._get_usage_data(start_date, end_date)

        total_input = data['overall']['total_input_tokens'] or 0
        total_output = data['overall']['total_output_tokens'] or 0
        total_tokens = total_input + total_output
        total_requests = data['overall']['total_requests'] or 0

        # Calculate efficiency metrics
        avg_tokens_per_request = total_tokens / total_requests if total_requests > 0 else 0
        output_ratio = (total_output / total_tokens * 100) if total_tokens > 0 else 0

        # Model distribution
        model_distribution = {}
        for row in data['by_model']:
            model = row.get('model') or 'unknown'
            tokens = (row.get('input_tokens') or 0) + (row.get('output_tokens') or 0)
            model_distribution[model] = tokens

        return {
            'period_days': days,
            'total_tokens': total_tokens,
            'total_requests': total_requests,
            'avg_tokens_per_request': round(avg_tokens_per_request, 2),
            'output_ratio': round(output_ratio, 2),
            'input_output_ratio': round(total_input / total_output, 2) if total_output > 0 else 0,
            'model_distribution': model_distribution,
            'unique_models': len(model_distribution),
            'unique_tools': len(set(r.get('tool_name') for r in data['by_model'] if r.get('tool_name'))),
        }
