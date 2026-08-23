"""Small provider helpers; every network call still receives a transport."""

from __future__ import annotations

import datetime
import email.utils
import json
from typing import Any, Mapping

from mari_components.errors import (
    AuthenticationFailure,
    PermanentFailure,
    RateLimitFailure,
    TransientFailure,
)
from mari_components.http import HttpRequest, HttpResponse, HttpTransport


def send(http: HttpTransport, request: HttpRequest) -> HttpResponse:
    try:
        response = http(request)
    except (TimeoutError, ConnectionError) as error:
        raise TransientFailure("provider request failed") from error
    if response.status in {401, 403}:
        raise AuthenticationFailure(f"provider rejected credentials (HTTP {response.status})")
    if response.status == 429:
        raw = next(
            (value for key, value in response.headers.items() if key.casefold() == "retry-after"), None
        )
        delay = None
        if raw is not None:
            try:
                delay = float(raw)
            except ValueError:
                # Retry-After is also allowed as an HTTP-date.
                try:
                    moment = email.utils.parsedate_to_datetime(raw)
                except (TypeError, ValueError):
                    moment = None
                if moment is not None:
                    if moment.tzinfo is None:
                        moment = moment.replace(tzinfo=datetime.timezone.utc)
                    delay = max(0.0, (moment - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
        raise RateLimitFailure("provider rate limit exceeded", retry_after=delay)
    if response.status in {408, 425} or response.status >= 500:
        raise TransientFailure(f"provider request failed (HTTP {response.status})")
    if response.status >= 400:
        raise PermanentFailure(f"provider request failed (HTTP {response.status})")
    return response


def json_response(http: HttpTransport, request: HttpRequest) -> Any:
    response = send(http, request)
    try:
        return json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PermanentFailure("provider returned invalid JSON") from error


def header(headers: Mapping[str, str], name: str) -> str:
    return next((value for key, value in headers.items() if key.casefold() == name.casefold()), "")
