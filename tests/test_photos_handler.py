import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, Iterable, Optional

import pytest
from botocore.stub import ANY, Stubber

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Ensure environment variables are populated before importing the handler.
os.environ.setdefault("PHOTO_TABLE_NAME", "PhotoTable")
os.environ.setdefault("PHOTO_BUCKET_NAME", "PhotoBucket")
os.environ.setdefault("ALLOWED_FAMILY_IDS", "family-123")
os.environ.setdefault("STAGE_NAME", "dev")

from handlers import photos  # noqa: E402  pylint: disable=wrong-import-position


@pytest.fixture(autouse=True)
def reset_allowed_family_ids(monkeypatch):
    monkeypatch.setenv("ALLOWED_FAMILY_IDS", "family-123")
    photos.ALLOWED_FAMILY_IDS = {"family-123"}
    yield
    photos.ALLOWED_FAMILY_IDS = {"family-123"}


def make_event(
    method: str,
    path: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[str | Dict] = None,
    *,
    cookies: Optional[Iterable[str]] = None,
    query: Optional[Dict[str, str]] = None,
    is_base64: bool = False,
    stage: Optional[str] = None,
):
    if isinstance(body, dict):
        body_payload: Optional[str] = json.dumps(body)
    else:
        body_payload = body

    query_params = query or None
    raw_query = urllib.parse.urlencode(query) if query else None

    request_context = {"http": {"method": method}}
    if stage:
        request_context["stage"] = stage

    return {
        "requestContext": request_context,
        "rawPath": path,
        "headers": headers or {},
        "body": body_payload,
        "isBase64Encoded": is_base64,
        "cookies": list(cookies) if cookies else None,
        "queryStringParameters": query_params,
        "rawQueryString": raw_query,
    }


def test_requires_family_id_header():
    event = make_event("GET", "/photos")
    response = photos.handler(event, None)
    assert response["statusCode"] == 403
    assert "message" in json.loads(response["body"])


def test_generate_presigned_upload(monkeypatch):
    event = make_event(
        "POST",
        "/photos/upload-url",
        headers={"x-family-id": "family-123"},
        body={"contentType": "image/png", "title": "Snowball"},
    )

    def fake_presign(**kwargs):
        assert kwargs["Params"]["ContentType"] == "image/png"
        assert kwargs["Params"]["Bucket"] == os.environ["PHOTO_BUCKET_NAME"]
        return "https://example.com/upload"

    monkeypatch.setattr(photos.s3_client, "generate_presigned_url", fake_presign)

    response = photos.handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 201
    assert body["uploadUrl"] == "https://example.com/upload"
    assert body["contentType"] == "image/png"
    assert body["photoId"]
    assert body["objectKey"].startswith("family-123/")


def test_record_photo_metadata():
    event = make_event(
        "POST",
        "/photos",
        headers={"x-family-id": "family-123"},
        body={
            "photoId": "abc-123",
            "objectKey": "family-123/abc-123.jpg",
            "title": "Mittens",
        },
    )

    with Stubber(photos.dynamodb_client) as stubber:
        stubber.add_response(
            "put_item",
            {},
            {
                "TableName": os.environ["PHOTO_TABLE_NAME"],
                "Item": ANY,
                "ConditionExpression": "attribute_not_exists(PhotoId)",
            },
        )
        response = photos.handler(event, None)

    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["photoId"] == "abc-123"


def test_list_photos_returns_items():
    event = make_event(
        "GET",
        "/photos",
        headers={"x-family-id": "family-123"},
    )

    expected_params = {
        "TableName": os.environ["PHOTO_TABLE_NAME"],
        "KeyConditionExpression": "FamilyId = :family_id",
        "ExpressionAttributeValues": {":family_id": {"S": "family-123"}},
        "ScanIndexForward": False,
    }

    response_items = {
        "Items": [
            {
                "FamilyId": {"S": "family-123"},
                "PhotoId": {"S": "abc"},
                "ObjectKey": {"S": "family-123/abc.jpg"},
                "UploadedAt": {"S": "2024-01-01T00:00:00Z"},
                "ContentType": {"S": "image/jpeg"},
            }
        ]
    }

    with Stubber(photos.dynamodb_client) as stubber:
        stubber.add_response("query", response_items, expected_params)
        response = photos.handler(event, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["items"][0]["objectKey"] == "family-123/abc.jpg"


def test_home_page_prompts_for_family_identifier():
    event = make_event("GET", "/")
    response = photos.handler(event, None)

    assert response["statusCode"] == 200
    assert "text/html" in response["headers"]["Content-Type"]
    assert "Sign in to see your family cats" in response["body"]
    assert 'action="/session"' in response["body"]


def test_home_page_handles_stage_prefix():
    event = make_event("GET", "/dev", stage="dev")
    response = photos.handler(event, None)

    assert response["statusCode"] == 200
    assert "Sign in to see your family cats" in response["body"]
    assert 'action="/dev/session"' in response["body"]


def test_home_page_handles_stage_when_context_missing():
    event = make_event("GET", "/dev")
    response = photos.handler(event, None)

    assert response["statusCode"] == 200
    assert 'action="/dev/session"' in response["body"]


def test_home_page_handles_stage_with_query():
    event = make_event("GET", "/dev", stage="dev", query={"status": "welcome"})
    response = photos.handler(event, None)

    assert response["statusCode"] == 200
    assert "Sign in to see your family cats" in response["body"]


def test_session_login_sets_cookie(monkeypatch):
    event = make_event(
        "POST",
        "/session",
        headers={"content-type": "application/x-www-form-urlencoded"},
        body="family_id=family-123",
    )

    response = photos.handler(event, None)

    assert response["statusCode"] == 303
    assert "cookies" in response
    assert any(cookie.startswith("family_id=") for cookie in response["cookies"])
    assert response["headers"]["Location"] == "/?status=welcome"


def test_session_login_with_stage_sets_cookie(monkeypatch):
    event = make_event(
        "POST",
        "/dev/session",
        headers={"content-type": "application/x-www-form-urlencoded"},
        body="family_id=family-123",
        stage="dev",
    )

    response = photos.handler(event, None)

    assert response["statusCode"] == 303
    assert any(cookie.startswith("family_id=") for cookie in response.get("cookies", []))
    assert response["headers"]["Location"] == "/dev?status=welcome"


def test_photo_content_redirects_to_presigned_url(monkeypatch):
    event = make_event(
        "GET",
        "/photos/abc/content",
        cookies=["family_id=family-123"],
    )

    expected_get_item = {
        "TableName": os.environ["PHOTO_TABLE_NAME"],
        "Key": {
            "FamilyId": {"S": "family-123"},
            "PhotoId": {"S": "abc"},
        },
    }

    get_item_response = {
        "Item": {
            "FamilyId": {"S": "family-123"},
            "PhotoId": {"S": "abc"},
            "ObjectKey": {"S": "family-123/abc.jpg"},
        }
    }

    with Stubber(photos.dynamodb_client) as dynamo_stub:
        dynamo_stub.add_response("get_item", get_item_response, expected_get_item)
        monkeypatch.setattr(
            photos.s3_client,
            "generate_presigned_url",
            lambda **kwargs: "https://example.com/download",
        )

        response = photos.handler(event, None)

    assert response["statusCode"] == 302
    assert response["headers"]["Location"] == "https://example.com/download"
