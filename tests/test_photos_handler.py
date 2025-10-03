import json
import os
import sys
from pathlib import Path
from typing import Dict

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

from handlers import photos  # noqa: E402  pylint: disable=wrong-import-position


@pytest.fixture(autouse=True)
def reset_allowed_family_ids(monkeypatch):
    monkeypatch.setenv("ALLOWED_FAMILY_IDS", "family-123")
    photos.ALLOWED_FAMILY_IDS = {"family-123"}
    yield
    photos.ALLOWED_FAMILY_IDS = {"family-123"}


def make_event(method: str, path: str, headers: Dict[str, str] | None = None, body: Dict | None = None):
    return {
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "headers": headers or {},
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
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
