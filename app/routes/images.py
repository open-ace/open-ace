"""
Open ACE - Image Routes

API routes for image upload, retrieval, and management.
"""

import logging
import os
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request, send_file

from app.auth.decorators import admin_required, auth_required
from app.models.uploaded_image import UploadedImage
from app.services.image_service import get_image_service
from app.services.storage_quota_service import get_storage_quota_service

logger = logging.getLogger(__name__)

images_bp = Blueprint("images", __name__)
image_service = get_image_service()
quota_service = get_storage_quota_service()


@images_bp.route("/images/upload", methods=["POST"])
@auth_required()
def api_upload_image():
    """
    Upload an image file.

    Accepts multipart/form-data with 'file' parameter.
    Optional parameters: session_id, project_id.

    Returns:
        JSON with image_id, stored_path, expires_at, preview_url.
    """
    # Check if file is present
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    # Get optional parameters
    session_id = request.form.get("session_id")
    project_id = request.form.get("project_id", type=int)
    tenant_id = g.user.get("tenant_id") if g.user else None

    # Read file content
    file_content = file.read()
    filename = file.filename
    mime_type = file.content_type or "application/octet-stream"

    # Upload
    uploaded_image, error = image_service.upload_image(
        user_id=g.user_id,
        file_content=file_content,
        filename=filename,
        mime_type=mime_type,
        session_id=session_id,
        project_id=project_id,
        tenant_id=tenant_id,
    )

    if error:
        return jsonify({"error": error}), 400

    if not uploaded_image:
        return jsonify({"error": "Failed to upload image"}), 500

    return jsonify({
        "success": True,
        "image": {
            "id": uploaded_image.id,
            "filename": uploaded_image.filename,
            "stored_path": uploaded_image.stored_path,
            "file_size": uploaded_image.file_size,
            "mime_type": uploaded_image.mime_type,
            "width": uploaded_image.width,
            "height": uploaded_image.height,
            "expires_at": uploaded_image.expires_at.isoformat() if uploaded_image.expires_at else None,
            "preview_url": uploaded_image.get_preview_url(),
            "is_svg": uploaded_image.is_svg,
        }
    }), 201


@images_bp.route("/images/<int:image_id>", methods=["GET"])
@auth_required()
def api_get_image(image_id: int):
    """
    Get image information by ID.

    User must own the image or be admin.

    Returns:
        JSON with image details.
    """
    # Check if user is admin
    is_admin = g.user_role == "admin"

    # Get image
    if is_admin:
        image = image_service.get_image_by_id(image_id)
    else:
        image = image_service.get_image(image_id, g.user_id)

    if not image:
        return jsonify({"error": "Image not found"}), 404

    return jsonify({
        "success": True,
        "image": image.to_dict()
    })


@images_bp.route("/images/<int:image_id>", methods=["DELETE"])
@auth_required()
def api_delete_image(image_id: int):
    """
    Delete an image by ID.

    User must own the image or be admin.

    Returns:
        JSON with success status.
    """
    # Check if user is admin
    is_admin = g.user_role == "admin"

    # For admin, get image first to update storage quota
    if is_admin:
        image = image_service.get_image_by_id(image_id)
        if not image:
            return jsonify({"error": "Image not found"}), 404

        # Delete file directly
        try:
            if os.path.exists(image.stored_path):
                os.remove(image.stored_path)
        except Exception as e:
            logger.warning(f"Failed to delete file: {e}")

        # Delete database record
        from app.repositories.database import adapt_sql

        query = adapt_sql("DELETE FROM uploaded_images WHERE id = ?")
        image_service.db.execute(query, (image_id,))

        # Update user storage
        if image.user_id:
            quota_service.update_storage_used(image.user_id, -image.file_size)

        return jsonify({"success": True})

    # For regular user, use standard delete with ownership check
    success, error = image_service.delete_image(image_id, g.user_id)

    if error:
        return jsonify({"error": error}), 400

    return jsonify({"success": True})


@images_bp.route("/images/list", methods=["GET"])
@auth_required()
def api_list_images():
    """
    List images for current user.

    Query parameters:
        session_id: Filter by session ID
        limit: Max results (default 50)
        offset: Result offset (default 0)

    Returns:
        JSON with list of images.
    """
    session_id = request.args.get("session_id")
    limit = request.args.get("limit", default=50, type=int)
    offset = request.args.get("offset", default=0, type=int)

    # Cap limit
    limit = min(limit, 100)

    images = image_service.list_user_images(
        user_id=g.user_id,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )

    return jsonify({
        "success": True,
        "images": [img.to_dict() for img in images],
        "count": len(images),
    })


@images_bp.route("/images/serve/<int:image_id>", methods=["GET"])
@auth_required()
def api_serve_image(image_id: int):
    """
    Serve image file for viewing/download.

    User must own the image or be admin.
    SVG files are served with Content-Disposition: attachment for security.

    Returns:
        Image file content.
    """
    # Check if user is admin
    is_admin = g.user_role == "admin"

    # Get image
    if is_admin:
        image = image_service.get_image_by_id(image_id)
    else:
        image = image_service.get_image(image_id, g.user_id)

    if not image:
        return jsonify({"error": "Image not found"}), 404

    # Check if file exists
    if not os.path.exists(image.stored_path):
        return jsonify({"error": "Image file not found"}), 404

    # Determine if we should force download (for SVG security)
    config = image_service.get_config()
    as_attachment = image.is_svg and config.svg_force_download

    try:
        if as_attachment:
            return send_file(
                image.stored_path,
                mimetype=image.mime_type,
                as_attachment=True,
                download_name=image.filename,
            )
        else:
            return send_file(
                image.stored_path,
                mimetype=image.mime_type,
            )
    except Exception as e:
        logger.error(f"Failed to serve image: {e}")
        return jsonify({"error": "Failed to serve image"}), 500


@images_bp.route("/images/storage-status", methods=["GET"])
@admin_required
def api_storage_status():
    """
    Get storage status for monitoring (admin only).

    Returns:
        JSON with total storage used, user stats, and disk space info.
    """
    config = image_service.get_config()
    storage_path = config.storage_path

    # Expand path
    if storage_path.startswith("~"):
        storage_path = os.path.expanduser(storage_path)

    # Get storage status
    status = quota_service.get_storage_status()

    # Check disk space
    disk_ok, disk_warning = quota_service.check_disk_space(
        storage_path,
        config.space_threshold_pct
    )

    status["disk_space_ok"] = disk_ok
    status["disk_space_warning"] = disk_warning
    status["storage_path"] = storage_path
    status["threshold_pct"] = config.space_threshold_pct

    return jsonify({
        "success": True,
        "status": status
    })


@images_bp.route("/images/quota", methods=["GET"])
@auth_required()
def api_user_quota():
    """
    Get current user's storage quota and usage.

    Returns:
        JSON with quota, used, and remaining bytes.
    """
    quota = quota_service.get_user_quota(g.user_id)
    used = quota_service.get_user_storage_used(g.user_id)
    remaining = quota - used

    return jsonify({
        "success": True,
        "quota": {
            "quota_bytes": quota,
            "quota_mb": round(quota / (1024 * 1024), 2),
            "used_bytes": used,
            "used_mb": round(used / (1024 * 1024), 2),
            "remaining_bytes": remaining,
            "remaining_mb": round(remaining / (1024 * 1024), 2),
            "usage_percentage": round((used / quota * 100) if quota > 0 else 0, 1),
        }
    })