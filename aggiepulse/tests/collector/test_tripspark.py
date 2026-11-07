import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from aggiepulse.collector.tripspark import (
    TripSparkClient,
    TripSparkError,
    _new_session,
    extract_token,
)
from aggiepulse.common.paths import USER_AGENT

HTML = (Path(__file__).parent / "fixtures" / "route_map.html").read_text()


def response(payload=None, *, status=200, html=None):
    result = requests.Response()
    result.status_code = status
    result.headers["Content-Type"] = (
        "text/html" if html is not None else "application/json"
    )
    result._content = (html if html is not None else json.dumps(payload)).encode()
    result.url = "https://aggiespirit.ts.tamu.edu/RouteMap"
    return result


def session(post_response):
    result = MagicMock(spec=requests.Session)
    result.headers = {}
    result.get.return_value = response(html=HTML)
    result.post.return_value = post_response
    return result


def test_extract_saved_html_token():
    assert extract_token(HTML) == "fixture-verification-token"
    assert extract_token(HTML.replace('"', "'")) == "fixture-verification-token"


@pytest.mark.parametrize(
    "html", ["<html>login</html>", HTML.replace("Zml4dHVy", "A===")]
)
def test_missing_or_invalid_token(html):
    with pytest.raises(ValueError):
        extract_token(html)


def test_session_headers_and_tls():
    with _new_session() as client_session:
        assert client_session.verify is True
        assert client_session.headers["User-Agent"] == USER_AGENT
        assert client_session.headers["X-Requested-With"] == "XMLHttpRequest"


def test_session_reuse_and_all_routes_form(base_payload, vehicles_payload):
    sess = session(response(base_payload))
    client = TripSparkClient(session_factory=lambda: sess)
    assert client.get_base_data() == base_payload
    assert sess.post.call_args.kwargs["data"] == b""
    assert sess.headers["RequestVerificationToken"] == "fixture-verification-token"
    sess.post.return_value = response(vehicles_payload)
    keys = [route["key"] for route in base_payload["routes"]]
    assert client.get_vehicles(keys + keys) == vehicles_payload
    assert sess.post.call_args.kwargs["data"] == [("routeKeys[]", key) for key in keys]
    sess.get.assert_called_once()
    assert client.last_http_status == 200
    client.close()
    sess.close.assert_called_once()


@pytest.mark.parametrize(
    "bad",
    [
        response(status=403),
        response(status=500),
        response(html="<html>expired</html>"),
        response({"not": "vehicles"}),
    ],
)
def test_errors_refresh_session_before_retry(bad):
    sessions = [session(bad), session(response([]))]
    wait = MagicMock(return_value=False)
    client = TripSparkClient(session_factory=iter(sessions).__next__, wait=wait)
    assert client.get_vehicles(["route-guid"]) == []
    sessions[0].close.assert_called_once()
    sessions[1].get.assert_called_once()
    wait.assert_called_once_with(2)


def test_malformed_json_refreshes():
    malformed = response([])
    malformed._content = b"{broken"
    sessions = [session(malformed), session(response([]))]
    client = TripSparkClient(
        session_factory=iter(sessions).__next__, wait=lambda _: False
    )
    assert client.get_vehicles(["route"]) == []
    sessions[0].close.assert_called_once()


def test_exceptions_retry_with_capped_backoff():
    sess = session(response([]))
    sess.get.side_effect = requests.Timeout("timed out")
    wait = MagicMock(return_value=False)
    client = TripSparkClient(
        session_factory=lambda: sess,
        wait=wait,
        max_attempts=3,
        backoff_cap=5,
    )
    for _ in range(2):
        with pytest.raises(TripSparkError) as caught:
            client.get_vehicles(["route"])
        assert caught.value.http_status == 0
    assert [call.args[0] for call in wait.call_args_list] == [2, 4, 5, 5, 5]
    assert sess.get.call_count == 6


def test_interrupted_backoff_stops_retry():
    sess = session(response(status=503))
    client = TripSparkClient(session_factory=lambda: sess, wait=lambda _: True)
    with pytest.raises(TripSparkError, match="stopping"):
        client.get_vehicles(["route"])
    assert sess.post.call_count == 1


def test_no_empty_routes_request():
    sess = session(response([]))
    client = TripSparkClient(session_factory=lambda: sess)
    with pytest.raises(ValueError, match="route key"):
        client.get_vehicles([])
    sess.get.assert_not_called()
