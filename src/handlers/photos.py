"""Lambda handler for the Family Cat Photos API."""
from __future__ import annotations

import base64
import html
import io
import json
import logging
import os
import urllib.parse
import uuid
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as EMAIL_POLICY
from http.cookies import SimpleCookie
from typing import Any, Dict, Iterable, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

AWS_REGION = (
    os.getenv("AWS_REGION")
    or os.getenv("AWS_DEFAULT_REGION")
    or "us-east-1"
)

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

s3_client = boto3.client("s3", region_name=AWS_REGION)
dynamodb_client = boto3.client("dynamodb", region_name=AWS_REGION)

UPLOAD_URL_TTL_SECONDS = 15 * 60  # 15 minutes
HTML_CONTENT_TYPE = "text/html; charset=utf-8"


def _build_response(
    status_code: int,
    body: Any = None,
    *,
    headers: Optional[Dict[str, str]] = None,
    content_type: str = "application/json",
    cookies: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Construct a response object for API Gateway."""

    response_headers = {
        "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    }
    if headers:
        response_headers.update(headers)

    if content_type:
        response_headers["Content-Type"] = content_type

    if content_type == "application/json":
        body_text = json.dumps(body or {})
    elif isinstance(body, bytes):
        body_text = base64.b64encode(body).decode("ascii")
    else:
        body_text = body or ""

    response: Dict[str, Any] = {
        "statusCode": status_code,
        "headers": response_headers,
        "body": body_text,
    }

    if cookies:
        response["cookies"] = list(cookies)

    return response


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


def _parse_query_string(event: Dict[str, Any]) -> Dict[str, str]:
    params = event.get("queryStringParameters")
    if params:
        return {key: value for key, value in params.items() if value is not None}
    raw = event.get("rawQueryString")
    if raw:
        return {key: value[0] for key, value in urllib.parse.parse_qs(raw).items()}
    return {}


def _parse_cookies(event: Dict[str, Any]) -> Dict[str, str]:
    cookie_jar: Dict[str, str] = {}
    header = None
    headers = event.get("headers") or {}
    if headers:
        header = headers.get("cookie") or headers.get("Cookie")
    cookie_list = event.get("cookies") or []
    if header:
        cookie_list = list(cookie_list) + [header]

    for raw_cookie in cookie_list:
        try:
            parsed = SimpleCookie()
            parsed.load(raw_cookie)
        except (ValueError, KeyError):
            continue
        for key, morsel in parsed.items():
            cookie_jar[key] = morsel.value
    return cookie_jar


def _parse_form_data(event: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
    headers = event.get("headers") or {}
    content_type = headers.get("content-type") or headers.get("Content-Type") or ""
    body = event.get("body")
    if body is None:
        return {}, {}

    if event.get("isBase64Encoded"):
        body_bytes = base64.b64decode(body)
    else:
        if isinstance(body, bytes):
            body_bytes = body
        else:
            body_bytes = body.encode("utf-8")

    if content_type.startswith("application/x-www-form-urlencoded"):
        parsed = urllib.parse.parse_qs(body_bytes.decode("utf-8"))
        return {key: values[0] for key, values in parsed.items()}, {}

    if content_type.startswith("multipart/form-data"):
        parser = BytesParser(policy=EMAIL_POLICY)
        message_bytes = b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body_bytes
        form_message = parser.parsebytes(message_bytes)
        fields: Dict[str, str] = {}
        files: Dict[str, Dict[str, Any]] = {}
        for part in form_message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_param("filename", header="content-disposition")
            if filename:
                files[name] = {
                    "filename": filename,
                    "content_type": part.get_content_type(),
                    "data": part.get_payload(decode=True) or b"",
                }
            else:
                fields[name] = part.get_content()
        return fields, files

    return {}, {}


def _family_id_from_event(event: Dict[str, Any], form_fields: Optional[Dict[str, str]] = None) -> Optional[str]:
    headers = event.get("headers") or {}
    family_id = headers.get("x-family-id") or headers.get("X-Family-Id")
    if family_id:
        return family_id

    cookies = _parse_cookies(event)
    if cookies.get("family_id"):
        return cookies["family_id"]

    query = _parse_query_string(event)
    if query.get("family_id"):
        return query["family_id"]

    if form_fields and form_fields.get("family_id"):
        return form_fields["family_id"]

    return None


def _validate_family_id(family_id: Optional[str]) -> str:
    if not family_id:
        raise PermissionError("Missing family identifier")
    if ALLOWED_FAMILY_IDS and family_id not in ALLOWED_FAMILY_IDS:
        raise PermissionError("Family id not authorized")
    return family_id


def _extract_family_id(event: Dict[str, Any], form_fields: Optional[Dict[str, str]] = None) -> str:
    family_id = _family_id_from_event(event, form_fields=form_fields)
    return _validate_family_id(family_id)


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    LOGGER.debug("Received event: %s", json.dumps(event))

    request_context = event.get("requestContext") or {}
    method = (request_context.get("http") or {}).get("method", "")
    raw_path = event.get("rawPath", "/")

    stage = request_context.get("stage")
    path = raw_path or "/"
    if stage and stage != "$default":
        stage_prefix = f"/{stage}"
        if path == stage_prefix:
            path = "/"
        elif path.startswith(stage_prefix + "/"):
            path = path[len(stage_prefix):] or "/"

    path = path.rstrip("/") or "/"

    if method == "GET" and path == "/":
        return _handle_home(event)

    if method == "POST" and path == "/session":
        return _handle_session(event)

    if method == "POST" and path == "/session/logout":
        return _handle_logout(event)

    if method == "POST" and path == "/photos/form-upload":
        return _handle_form_photo_upload(event)

    if method == "GET" and path.startswith("/photos/") and path.endswith("/content"):
        return _handle_photo_content(event, path)

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


def _cookie_attributes(event: Dict[str, Any]) -> str:
    headers = event.get("headers") or {}
    proto = headers.get("x-forwarded-proto") or headers.get("X-Forwarded-Proto") or "http"
    attributes = ["Path=/", "HttpOnly", "SameSite=Lax"]
    if proto.lower() == "https":
        attributes.append("Secure")
    return "; ".join(attributes)


def _handle_session(event: Dict[str, Any]) -> Dict[str, Any]:
    form_fields, _ = _parse_form_data(event)
    family_id = (form_fields.get("family_id") or "").strip()

    try:
        valid_family = _validate_family_id(family_id)
    except PermissionError as exc:
        html_body = _render_home_html(None, error=str(exc))
        return _build_response(403, html_body, content_type=HTML_CONTENT_TYPE)

    cookie_value = f"family_id={urllib.parse.quote(valid_family)}; {_cookie_attributes(event)}"
    headers = {"Location": "/?status=welcome"}
    return _build_response(303, "", headers=headers, cookies=[cookie_value], content_type="text/plain")


def _handle_logout(event: Dict[str, Any]) -> Dict[str, Any]:
    expires = "Thu, 01 Jan 1970 00:00:00 GMT"
    cookie_value = f"family_id=deleted; {_cookie_attributes(event)}; Expires={expires}; Max-Age=0"
    headers = {"Location": "/?status=goodbye"}
    return _build_response(303, "", headers=headers, cookies=[cookie_value], content_type="text/plain")


def _handle_home(event: Dict[str, Any]) -> Dict[str, Any]:
    query = _parse_query_string(event)
    status = query.get("status")
    message = None
    if status == "welcome":
        family_id = _family_id_from_event(event)
        if family_id:
            message = f"Signed in as {html.escape(family_id)}"
    elif status == "uploaded":
        message = "Photo uploaded successfully"
    elif status == "goodbye":
        message = "Signed out"
    elif status:
        message = html.escape(status)

    error = query.get("error")
    if error:
        error = html.escape(error)

    family_id = _family_id_from_event(event)
    photos: List[Dict[str, Any]] = []
    load_error = None

    if family_id:
        try:
            photos = _query_photos(family_id)
        except ClientError as exc:
            LOGGER.error("Failed to load photos for HTML view: %s", exc)
            load_error = "Unable to load photos right now."

    body = _render_home_html(family_id, message=message, error=error or load_error, photos=photos)
    return _build_response(200, body, content_type=HTML_CONTENT_TYPE)


def _render_home_html(
    family_id: Optional[str],
    *,
    message: Optional[str] = None,
    error: Optional[str] = None,
    photos: Optional[List[Dict[str, Any]]] = None,
) -> str:
    photos = photos or []

    alerts: List[str] = []
    if message:
        alerts.append(f'<div class="alert alert-success">{message}</div>')
    if error:
        alerts.append(f'<div class="alert alert-error">{error}</div>')

    if family_id:
        welcome = f"<p class=\"welcome\">Viewing photos for <strong>{html.escape(family_id)}</strong></p>"
        logout_form = (
            "<form method=\"POST\" action=\"/session/logout\">"
            "<button type=\"submit\">Sign out</button>"
            "</form>"
        )
        upload_form = f"""
        <section class="panel">
          <h2>Upload a new cat photo</h2>
          <form method="POST" action="/photos/form-upload" enctype="multipart/form-data">
            <input type="hidden" name="family_id" value="{html.escape(family_id)}">
            <label>Photo file
              <input type="file" name="photo" accept="image/*" required>
            </label>
            <label>Title
              <input type="text" name="title" maxlength="120">
            </label>
            <label>Description
              <textarea name="description" rows="3" maxlength="500"></textarea>
            </label>
            <label>Taken at (optional)
              <input type="datetime-local" name="taken_at">
            </label>
            <button type="submit">Upload photo</button>
          </form>
        </section>
        """
        if photos:
            gallery_items = []
            for item in photos:
                title = html.escape(item.get("title") or "Untitled cat photo")
                description = html.escape(item.get("description") or "")
                uploaded_at = html.escape(item.get("uploadedAt") or "")
                content_url = f"/photos/{urllib.parse.quote(item['photoId'])}/content"
                gallery_items.append(
                    f"<figure>\n"
                    f"  <img src=\"{content_url}\" alt=\"{title}\" loading=\"lazy\" referrerpolicy=\"no-referrer\">\n"
                    f"  <figcaption>\n"
                    f"    <strong>{title}</strong><br>\n"
                    f"    <small>Uploaded at {uploaded_at}</small><br>\n"
                    f"    <span>{description}</span>\n"
                    f"  </figcaption>\n"
                    f"</figure>"
                )
            gallery = "<section class=\"gallery\">" + "".join(gallery_items) + "</section>"
        else:
            gallery = "<p class=\"empty\">No cat photos yet. Upload your first one!</p>"

        body = """
        <main>
          {alerts}
          {welcome}
          {logout}
          {upload}
          <section class="panel">
            <h2>Your photos</h2>
            {gallery}
          </section>
        </main>
        """.format(
            alerts="".join(alerts),
            welcome=welcome,
            logout=logout_form,
            upload=upload_form,
            gallery=gallery,
        )
    else:
        login_form = """
        <section class="panel">
          {alerts}
          <h2>Sign in to see your family cats</h2>
          <form method="POST" action="/session">
            <label>Family identifier
              <input type="text" name="family_id" required autofocus>
            </label>
            <button type="submit">Continue</button>
          </form>
        </section>
        """.format(alerts="".join(alerts))
        body = "<main>" + login_form + "</main>"

    return """
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Family Cat Photos</title>
        <style>
          body {{ font-family: system-ui, sans-serif; margin: 0; padding: 0; background: #f7f7f7; color: #222; }}
          header {{ background: #3f51b5; color: #fff; padding: 1.5rem; text-align: center; }}
          main {{ max-width: 960px; margin: 2rem auto; padding: 0 1rem; }}
          .panel {{ background: #fff; padding: 1.5rem; border-radius: 0.75rem; box-shadow: 0 8px 24px rgba(0,0,0,0.08); margin-bottom: 1.5rem; }}
          label {{ display: block; margin-bottom: 0.75rem; font-weight: 600; }}
          input[type="text"], input[type="datetime-local"], input[type="file"], textarea {{ width: 100%; padding: 0.5rem; margin-top: 0.35rem; border-radius: 0.5rem; border: 1px solid #ccc; }}
          button {{ background: #3f51b5; color: #fff; border: none; border-radius: 0.5rem; padding: 0.75rem 1.5rem; cursor: pointer; }}
          button:hover {{ background: #303f9f; }}
          .gallery {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1.5rem; }}
          figure {{ margin: 0; background: #fafafa; padding: 1rem; border-radius: 0.75rem; box-shadow: inset 0 0 0 1px rgba(0,0,0,0.05); }}
          figure img {{ width: 100%; height: auto; border-radius: 0.5rem; object-fit: cover; }}
          figcaption {{ margin-top: 0.75rem; }}
          .alert {{ padding: 0.75rem 1rem; border-radius: 0.5rem; margin-bottom: 1rem; }}
          .alert-success {{ background: #e8f5e9; color: #256029; }}
          .alert-error {{ background: #ffebee; color: #c62828; }}
          .welcome {{ margin-bottom: 1rem; }}
          .empty {{ color: #666; }}
        </style>
      </head>
      <body>
        <header>
          <h1>Family Cat Photos</h1>
          <p>Private space to share your favorite feline moments.</p>
        </header>
        {body}
      </body>
    </html>
    """.strip().format(body=body)


def _handle_form_photo_upload(event: Dict[str, Any]) -> Dict[str, Any]:
    form_fields, files = _parse_form_data(event)

    try:
        family_id = _extract_family_id(event, form_fields=form_fields)
    except PermissionError as exc:
        html_body = _render_home_html(None, error=str(exc))
        return _build_response(403, html_body, content_type=HTML_CONTENT_TYPE)

    file_field = files.get("photo")
    if not file_field or not file_field.get("data"):
        html_body = _render_home_html(family_id, error="Please choose an image to upload")
        return _build_response(400, html_body, content_type=HTML_CONTENT_TYPE)

    title = (form_fields.get("title") or "").strip() or None
    description = (form_fields.get("description") or "").strip() or None
    taken_at = (form_fields.get("taken_at") or "").strip() or None

    content_type = file_field.get("content_type") or "image/jpeg"
    extension = _content_type_to_extension(content_type)
    if not extension:
        extension = _content_type_to_extension("image/jpeg") or ".jpg"

    photo_id = str(uuid.uuid4())
    object_key = f"{family_id}/{photo_id}{extension}"

    try:
        s3_client.put_object(
            Bucket=PHOTO_BUCKET_NAME,
            Key=object_key,
            Body=file_field["data"],
            ContentType=content_type,
        )
    except ClientError as exc:
        LOGGER.error("Failed to upload photo object: %s", exc)
        html_body = _render_home_html(family_id, error="Could not store the photo. Please try again.")
        return _build_response(500, html_body, content_type=HTML_CONTENT_TYPE)

    try:
        _persist_photo_metadata(
            family_id,
            photo_id,
            object_key,
            title=title,
            description=description,
            content_type=content_type,
            taken_at=taken_at,
        )
    except PermissionError as exc:
        html_body = _render_home_html(family_id, error=str(exc))
        return _build_response(403, html_body, content_type=HTML_CONTENT_TYPE)
    except ClientError as exc:
        LOGGER.error("Failed to record photo metadata: %s", exc)
        html_body = _render_home_html(family_id, error="Unable to save photo details.")
        return _build_response(500, html_body, content_type=HTML_CONTENT_TYPE)

    headers = {"Location": "/?status=uploaded"}
    return _build_response(303, "", headers=headers, content_type="text/plain")


def _handle_photo_content(event: Dict[str, Any], path: str) -> Dict[str, Any]:
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 3:
        return _build_response(404, {"message": "Not Found"})
    photo_id = urllib.parse.unquote(segments[1])

    try:
        family_id = _extract_family_id(event)
    except PermissionError:
        return _build_response(302, "", headers={"Location": "/"}, content_type="text/plain")

    try:
        item = dynamodb_client.get_item(
            TableName=PHOTO_TABLE_NAME,
            Key={
                "FamilyId": {"S": family_id},
                "PhotoId": {"S": photo_id},
            },
        ).get("Item")
    except ClientError as exc:
        LOGGER.error("Failed to fetch photo metadata: %s", exc)
        return _build_response(500, {"message": "Unable to fetch photo"})

    if not item:
        return _build_response(404, {"message": "Photo not found"})

    object_key = item.get("ObjectKey", {}).get("S")
    if not object_key:
        return _build_response(404, {"message": "Photo not found"})

    try:
        download_url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": PHOTO_BUCKET_NAME, "Key": object_key},
            ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        )
    except ClientError as exc:
        LOGGER.error("Failed to create download URL: %s", exc)
        return _build_response(500, {"message": "Unable to fetch photo"})

    headers = {
        "Location": download_url,
        "Cache-Control": "private, max-age=30",
    }
    return _build_response(302, "", headers=headers, content_type="text/plain")


def _query_photos(family_id: str) -> List[Dict[str, Any]]:
    response = dynamodb_client.query(
        TableName=PHOTO_TABLE_NAME,
        KeyConditionExpression="FamilyId = :family_id",
        ExpressionAttributeValues={":family_id": {"S": family_id}},
        ScanIndexForward=False,
    )
    items = []
    for item in response.get("Items", []):
        items.append(
            {
                "photoId": item["PhotoId"]["S"],
                "objectKey": item["ObjectKey"]["S"],
                "title": item.get("Title", {}).get("S"),
                "description": item.get("Description", {}).get("S"),
                "uploadedAt": item.get("UploadedAt", {}).get("S"),
                "contentType": item.get("ContentType", {}).get("S"),
            }
        )
    return items


def _list_photos(family_id: str) -> Dict[str, Any]:
    try:
        items = _query_photos(family_id)
    except ClientError as exc:
        LOGGER.error("Failed to query photo metadata: %s", exc)
        return _build_response(500, {"message": "Unable to list photos"})
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

    try:
        result = _persist_photo_metadata(
            family_id,
            str(payload["photoId"]),
            str(payload["objectKey"]),
            title=payload.get("title"),
            description=payload.get("description"),
            content_type=payload.get("contentType"),
            taken_at=payload.get("takenAt"),
        )
    except PermissionError as exc:
        return _build_response(403, {"message": str(exc)})
    except ClientError as exc:
        if exc.response["Error"].get("Code") == "ConditionalCheckFailedException":
            return _build_response(409, {"message": "Photo already recorded"})
        LOGGER.error("Failed to persist metadata: %s", exc)
        return _build_response(500, {"message": "Unable to save metadata"})

    return _build_response(201, result)


def _persist_photo_metadata(
    family_id: str,
    photo_id: str,
    object_key: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    content_type: Optional[str] = None,
    taken_at: Optional[str] = None,
) -> Dict[str, str]:
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

    dynamodb_client.put_item(
        TableName=PHOTO_TABLE_NAME,
        Item=item,
        ConditionExpression="attribute_not_exists(PhotoId)",
    )

    return {"photoId": photo_id, "objectKey": object_key}


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
