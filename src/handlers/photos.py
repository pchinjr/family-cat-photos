"""Lambda handler for the Family Cat Photos API."""
from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

PHOTO_TABLE_NAME = os.environ["PHOTO_TABLE_NAME"]
PHOTO_BUCKET_NAME = os.environ["PHOTO_BUCKET_NAME"]
_ALLOWED_IDS_RAW = os.getenv("ALLOWED_FAMILY_IDS", "").strip()
ALLOWED_FAMILY_IDS = {
    value.strip()
    for value in _ALLOWED_IDS_RAW.split(",")
    if value.strip()
}

s3_client = boto3.client("s3")
dynamodb_client = boto3.client("dynamodb")

UPLOAD_URL_TTL_SECONDS = 15 * 60  # 15 minutes


def _build_response(status_code: int, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
        },
        "body": json.dumps(body or {}),
    }


def _extract_json_body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw_body = event.get("body")
    if not raw_body:
        return {}

    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body)

    try:
        if isinstance(raw_body, bytes):
            raw_body = raw_body.decode("utf-8")
        return json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        LOGGER.warning("Invalid JSON payload: %s", exc)
        raise ValueError("Invalid JSON body") from exc


def _extract_family_id(event: Dict[str, Any]) -> str:
    headers = event.get("headers") or {}
    family_id = headers.get("x-family-id") or headers.get("X-Family-Id")
    if not family_id:
        raise PermissionError("Missing x-family-id header")

    if ALLOWED_FAMILY_IDS and family_id not in ALLOWED_FAMILY_IDS:
        raise PermissionError("Family id not authorized")

    return family_id


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    LOGGER.debug("Received event: %s", json.dumps(event))

    method = (event.get("requestContext") or {}).get("http", {}).get("method", "")
    raw_path = event.get("rawPath", "/")
    path = raw_path.rstrip("/") or "/"

    if method == "GET" and path == "/health":
        return _build_response(200, {"status": "ok"})

    try:
        family_id = _extract_family_id(event)
    except PermissionError as exc:
        return _build_response(403, {"message": str(exc)})

    if method == "GET" and path == "/photos":
        return _list_photos(family_id)

    if method == "POST" and path == "/photos/upload-url":
        return _create_presigned_upload(family_id, event)

    if method == "POST" and path == "/photos":
        return _record_photo_metadata(family_id, event)

    return _build_response(404, {"message": "Not Found"})


def _list_photos(family_id: str) -> Dict[str, Any]:
    try:
        response = dynamodb_client.query(
            TableName=PHOTO_TABLE_NAME,
            KeyConditionExpression="FamilyId = :family_id",
            ExpressionAttributeValues={":family_id": {"S": family_id}},
            ScanIndexForward=False,
        )
    except ClientError as exc:
        LOGGER.error("Failed to query photo metadata: %s", exc)
        return _build_response(500, {"message": "Unable to list photos"})

    items = [
        {
            "photoId": item["PhotoId"]["S"],
            "objectKey": item["ObjectKey"]["S"],
            "title": item.get("Title", {}).get("S"),
            "description": item.get("Description", {}).get("S"),
            "uploadedAt": item.get("UploadedAt", {}).get("S"),
            "contentType": item.get("ContentType", {}).get("S"),
        }
        for item in response.get("Items", [])
    ]

    return _build_response(200, {"items": items})


def _create_presigned_upload(family_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = _extract_json_body(event)
    except ValueError as exc:
        return _build_response(400, {"message": str(exc)})

    content_type = payload.get("contentType", "image/jpeg")
    title = payload.get("title")
    extension = _content_type_to_extension(content_type)
    photo_id = str(uuid.uuid4())
    object_key = f"{family_id}/{photo_id}{extension}"

    try:
        upload_url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": PHOTO_BUCKET_NAME,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        )
    except ClientError as exc:
        LOGGER.error("Failed to generate presigned URL: %s", exc)
        return _build_response(500, {"message": "Unable to create upload URL"})

    body = {
        "photoId": photo_id,
        "objectKey": object_key,
        "uploadUrl": upload_url,
        "title": title,
        "contentType": content_type,
        "expiresInSeconds": UPLOAD_URL_TTL_SECONDS,
    }
    return _build_response(201, body)


def _record_photo_metadata(family_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = _extract_json_body(event)
    except ValueError as exc:
        return _build_response(400, {"message": str(exc)})

    required_fields = {"photoId", "objectKey"}
    if not required_fields.issubset(payload):
        return _build_response(
            400,
            {"message": "Missing required fields", "required": sorted(required_fields)},
        )

    photo_id = str(payload["photoId"])
    object_key = str(payload["objectKey"])
    title = payload.get("title")
    description = payload.get("description")
    content_type = payload.get("contentType")
    taken_at = payload.get("takenAt")
    uploaded_at = datetime.now(timezone.utc).isoformat()

    item = {
        "FamilyId": {"S": family_id},
        "PhotoId": {"S": photo_id},
        "ObjectKey": {"S": object_key},
        "UploadedAt": {"S": uploaded_at},
    }

    if title:
        item["Title"] = {"S": str(title)}
    if description:
        item["Description"] = {"S": str(description)}
    if content_type:
        item["ContentType"] = {"S": str(content_type)}
    if taken_at:
        item["TakenAt"] = {"S": str(taken_at)}

    try:
        dynamodb_client.put_item(
            TableName=PHOTO_TABLE_NAME,
            Item=item,
            ConditionExpression="attribute_not_exists(PhotoId)",
        )
    except ClientError as exc:
        if exc.response["Error"].get("Code") == "ConditionalCheckFailedException":
            return _build_response(409, {"message": "Photo already recorded"})
        LOGGER.error("Failed to persist metadata: %s", exc)
        return _build_response(500, {"message": "Unable to save metadata"})

    return _build_response(201, {"photoId": photo_id, "objectKey": object_key})


def _content_type_to_extension(content_type: str) -> str:
    if not content_type:
        return ""
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }
    return mapping.get(content_type.lower(), "")

