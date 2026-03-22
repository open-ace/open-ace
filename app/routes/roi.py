#!/usr/bin/env python3
"""
Open ACE - ROI API Routes

API endpoints for ROI analysis and cost optimization.
"""

import logging
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from app.modules.analytics.cost_optimizer import CostOptimizer
from app.modules.analytics.roi_calculator import ROICalculator

logger = logging.getLogger(__name__)

roi_bp = Blueprint('roi', __name__)


# ==================== ROI Analysis ====================

@roi_bp.route('/roi', methods=['GET'])
def get_roi():
    """Get ROI metrics for a period."""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        user_id = request.args.get('user_id', type=int)
        tool_name = request.args.get('tool_name')

        # Default to last 30 days if not specified
        if not start_date or not end_date:
            end_date = datetime.utcnow().strftime('%Y-%m-%d')
            start_date = (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d')

        calculator = ROICalculator()
        roi = calculator.calculate_roi(start_date, end_date, user_id, tool_name)

        return jsonify({
            'success': True,
            'data': roi.to_dict()
        })
    except Exception as e:
        logger.error(f"Error calculating ROI: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@roi_bp.route('/roi/trend', methods=['GET'])
def get_roi_trend():
    """Get ROI trend over months."""
    try:
        months = request.args.get('months', default=6, type=int)
        user_id = request.args.get('user_id', type=int)

        calculator = ROICalculator()
        trends = calculator.get_roi_trend(months, user_id)

        return jsonify({
            'success': True,
            'data': [t.to_dict() for t in trends]
        })
    except Exception as e:
        logger.error(f"Error getting ROI trend: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@roi_bp.route('/roi/by-tool', methods=['GET'])
def get_roi_by_tool():
    """Get ROI breakdown by tool."""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        if not start_date or not end_date:
            end_date = datetime.utcnow().strftime('%Y-%m-%d')
            start_date = (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d')

        calculator = ROICalculator()
        roi_by_tool = calculator.get_roi_by_tool(start_date, end_date)

        return jsonify({
            'success': True,
            'data': {k: v.to_dict() for k, v in roi_by_tool.items()}
        })
    except Exception as e:
        logger.error(f"Error getting ROI by tool: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@roi_bp.route('/roi/by-user', methods=['GET'])
def get_roi_by_user():
    """Get ROI breakdown by user."""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        if not start_date or not end_date:
            end_date = datetime.utcnow().strftime('%Y-%m-%d')
            start_date = (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d')

        calculator = ROICalculator()
        roi_by_user = calculator.get_roi_by_user(start_date, end_date)

        return jsonify({
            'success': True,
            'data': {str(k): v.to_dict() for k, v in roi_by_user.items()}
        })
    except Exception as e:
        logger.error(f"Error getting ROI by user: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@roi_bp.route('/roi/cost-breakdown', methods=['GET'])
def get_cost_breakdown():
    """Get detailed cost breakdown."""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        user_id = request.args.get('user_id', type=int)

        if not start_date or not end_date:
            end_date = datetime.utcnow().strftime('%Y-%m-%d')
            start_date = (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d')

        calculator = ROICalculator()
        breakdown = calculator.get_cost_breakdown(start_date, end_date, user_id)

        return jsonify({
            'success': True,
            'data': {
                'breakdown': [b.to_dict() for b in breakdown],
                'total_cost': round(sum(b.total_cost for b in breakdown), 4),
            }
        })
    except Exception as e:
        logger.error(f"Error getting cost breakdown: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@roi_bp.route('/roi/daily-costs', methods=['GET'])
def get_daily_costs():
    """Get daily cost data for charting."""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        user_id = request.args.get('user_id', type=int)

        if not start_date or not end_date:
            end_date = datetime.utcnow().strftime('%Y-%m-%d')
            start_date = (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d')

        calculator = ROICalculator()
        daily_costs = calculator.get_daily_costs(start_date, end_date, user_id)

        return jsonify({
            'success': True,
            'data': daily_costs
        })
    except Exception as e:
        logger.error(f"Error getting daily costs: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@roi_bp.route('/roi/summary', methods=['GET'])
def get_roi_summary():
    """Get ROI summary statistics."""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        user_id = request.args.get('user_id', type=int)

        if not start_date or not end_date:
            end_date = datetime.utcnow().strftime('%Y-%m-%d')
            start_date = (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d')

        calculator = ROICalculator()
        summary = calculator.get_summary_stats(start_date, end_date, user_id)

        return jsonify({
            'success': True,
            'data': summary
        })
    except Exception as e:
        logger.error(f"Error getting ROI summary: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Cost Optimization ====================

@roi_bp.route('/optimization/suggestions', methods=['GET'])
def get_optimization_suggestions():
    """Get cost optimization suggestions."""
    try:
        days = request.args.get('days', default=30, type=int)

        optimizer = CostOptimizer()
        suggestions = optimizer.analyze(days)

        return jsonify({
            'success': True,
            'data': [s.to_dict() for s in suggestions]
        })
    except Exception as e:
        logger.error(f"Error getting optimization suggestions: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@roi_bp.route('/optimization/cost-trend', methods=['GET'])
def get_optimization_cost_trend():
    """Get cost trend for optimization analysis."""
    try:
        days = request.args.get('days', default=30, type=int)

        optimizer = CostOptimizer()
        trend = optimizer.get_cost_trend(days)

        return jsonify({
            'success': True,
            'data': trend
        })
    except Exception as e:
        logger.error(f"Error getting cost trend: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@roi_bp.route('/optimization/efficiency', methods=['GET'])
def get_efficiency_report():
    """Get efficiency analysis report."""
    try:
        days = request.args.get('days', default=30, type=int)

        optimizer = CostOptimizer()
        report = optimizer.get_efficiency_report(days)

        return jsonify({
            'success': True,
            'data': report
        })
    except Exception as e:
        logger.error(f"Error getting efficiency report: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
