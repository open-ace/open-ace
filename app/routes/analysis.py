#!/usr/bin/env python3
"""
Open ACE - Analysis Routes

API routes for usage analysis and reporting.
"""

from flask import Blueprint, jsonify, request

from app.services.analysis_service import AnalysisService

analysis_bp = Blueprint('analysis', __name__)
analysis_service = AnalysisService()


@analysis_bp.route('/analysis/key-metrics')
def api_key_metrics():
    """Get key metrics for the dashboard."""
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    host = request.args.get('host')

    metrics = analysis_service.get_key_metrics(
        start_date=start_date,
        end_date=end_date,
        host_name=host
    )
    return jsonify(metrics)


@analysis_bp.route('/analysis/hourly-usage')
def api_hourly_usage():
    """Get hourly usage breakdown."""
    date = request.args.get('date')
    tool = request.args.get('tool')
    host = request.args.get('host')

    result = analysis_service.get_hourly_usage(
        date=date,
        tool_name=tool,
        host_name=host
    )
    return jsonify(result)


@analysis_bp.route('/analysis/daily-hourly-usage')
def api_daily_hourly_usage():
    """Get daily and hourly usage patterns."""
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    host = request.args.get('host')

    result = analysis_service.get_daily_hourly_usage(
        start_date=start_date,
        end_date=end_date,
        host_name=host
    )
    return jsonify(result)


@analysis_bp.route('/analysis/peak-usage')
def api_peak_usage():
    """Get peak usage periods."""
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    host = request.args.get('host')

    result = analysis_service.get_peak_usage(
        start_date=start_date,
        end_date=end_date,
        host_name=host
    )
    return jsonify(result)


@analysis_bp.route('/analysis/user-ranking')
def api_user_ranking():
    """Get user ranking by token usage."""
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    host = request.args.get('host')
    limit = request.args.get('limit', 10, type=int)

    result = analysis_service.get_user_ranking(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        limit=limit
    )
    return jsonify(result)


@analysis_bp.route('/analysis/conversation-stats')
def api_conversation_stats():
    """Get conversation statistics."""
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    host = request.args.get('host')

    result = analysis_service.get_conversation_stats(
        start_date=start_date,
        end_date=end_date,
        host_name=host
    )
    return jsonify(result)


@analysis_bp.route('/analysis/user-segmentation')
def api_user_segmentation():
    """Get user segmentation data."""
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    host = request.args.get('host')

    result = analysis_service.get_user_segmentation(
        start_date=start_date,
        end_date=end_date,
        host_name=host
    )
    return jsonify(result)


@analysis_bp.route('/analysis/tool-comparison')
def api_tool_comparison():
    """Get tool comparison data."""
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    host = request.args.get('host')

    result = analysis_service.get_tool_comparison(
        start_date=start_date,
        end_date=end_date,
        host_name=host
    )
    return jsonify(result)


@analysis_bp.route('/analysis/anomaly-detection')
def api_anomaly_detection():
    """Get anomaly detection results."""
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    host = request.args.get('host')

    # Placeholder for anomaly detection
    return jsonify({'anomalies': []})


@analysis_bp.route('/analysis/recommendations')
def api_recommendations():
    """Get usage optimization recommendations."""
    host = request.args.get('host')

    result = analysis_service.get_recommendations(host_name=host)
    return jsonify({'recommendations': result})
