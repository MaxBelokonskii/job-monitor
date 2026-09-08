from job_monitor.security import APP_TOKEN, TOKEN_HEADER


def test_api_without_token_is_forbidden(raw_client):
    assert raw_client.get("/api/config").status_code == 403


def test_api_with_token_works(raw_client):
    response = raw_client.get("/api/config", headers={TOKEN_HEADER: APP_TOKEN})
    assert response.status_code == 200


def test_api_with_wrong_token_is_forbidden(raw_client):
    response = raw_client.get("/api/config", headers={TOKEN_HEADER: "wrong"})
    assert response.status_code == 403


def test_foreign_host_is_rejected(raw_client):
    response = raw_client.get("/", headers={"Host": "attacker.example"})
    assert response.status_code == 400


def test_index_carries_the_token(client):
    body = client.get("/").text
    assert APP_TOKEN in body


def test_static_files_need_no_token(raw_client):
    assert raw_client.get("/static/app.js").status_code == 200


def test_api_with_non_ascii_token_is_forbidden(raw_client):
    # httpx encodes str header values as ASCII client-side; a hostile client
    # is not so constrained, so send raw non-ASCII bytes directly to exercise
    # the same code path the server would see over the wire.
    response = raw_client.get(
        "/api/config", headers={TOKEN_HEADER: "привет".encode("utf-8")}
    )
    assert response.status_code == 403
