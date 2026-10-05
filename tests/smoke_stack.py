"""Exercise nginx, authentication, factory mutations, and PostgreSQL together."""

import json
import http.cookiejar
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4


BASE = os.environ["PUBLIC_ORIGIN"].rstrip("/")
MANAGER_BASE = f"http://{os.environ.get('MANAGER_BIND_ADDRESS', '127.0.0.1')}:{os.environ.get('MANAGER_HTTP_PORT', '8081')}"


def manager_client():
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))

    def page(path, fields=None):
        payload = None if fields is None else urllib.parse.urlencode(fields).encode()
        with opener.open(MANAGER_BASE + path, data=payload, timeout=30) as response:
            return response.read().decode()

    def csrf(html):
        match = re.search(r'name="csrf" value="([^"]+)"', html)
        assert match, "Management form has no CSRF token"
        return match.group(1)

    login_page = page("/login")
    login_response = page("/login", {
        "csrf": csrf(login_page),
        "email": "smoke@example.invalid",
        "password": os.environ["SMOKE_PASSWORD"],
    })
    assert "Providers" in login_response, "Management login failed"
    assert len(cookies) == 1 and next(iter(cookies)).name == "manager_session"
    assert next(iter(cookies)).value != os.environ["SMOKE_PASSWORD"]
    try:
        page("/providers", {"abbreviation": "NOPE", "name": "Missing CSRF token"})
    except urllib.error.HTTPError as error:
        assert error.code == 400, "Management UI accepted a mutation without a CSRF token"
    else:
        raise AssertionError("Management UI accepted a mutation without a CSRF token")
    return page, csrf


def request(path, data=None, token=None):
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(BASE + path, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read()
            return body if data is None else json.loads(body)
    except urllib.error.HTTPError as error:
        raise AssertionError(f"{path}: HTTP {error.code}: {error.read().decode()[:1000]}") from error


def expect_success(path, data, token):
    response = request(path, data, token)
    assert response.get("success") is True, f"{path}: {response}"
    return response


def main():
    html = request("/tmx/").decode()
    assert "<html" in html.lower(), "TMX page was not served"
    asset = re.search(r'<script[^>]+src="([^"]+\.js)"', html)
    assert asset, "TMX page has no JavaScript bundle"
    asset_path = urllib.parse.urljoin("/tmx/", asset.group(1))
    bundle = request(asset_path)
    assert bundle and b"<html" not in bundle.lower(), "TMX JavaScript bundle was not served"

    login = request("/auth/login", {
        "email": "smoke@example.invalid",
        "password": os.environ["SMOKE_PASSWORD"],
    })
    token = login.get("token")
    assert token, f"Login did not return a token: {login}"

    page, csrf = manager_client()
    empty_providers_page = page("/providers")
    assert 'class="provider-layout"' in empty_providers_page
    assert "Add new..." in empty_providers_page and "Add a provider" in empty_providers_page
    provider_page = page("/providers", {"csrf": csrf(page("/providers")),
        "abbreviation": "SMOKE", "name": "Compose Smoke Test"})
    assert "Compose Smoke Test" in provider_page, "Management UI did not create a provider"
    assert 'aria-current="page"' in provider_page
    menu = re.search(r'<nav aria-label="Providers">(.*?)</nav>', provider_page, re.S)
    assert menu and re.search(r'<li><a class="provider-menu-link provider-add-link.*?Add new\.\.\.</a></li>\s*</ul>\s*$', menu.group(1), re.S), \
        "Add new is not the last provider menu item"
    add_new_page = page("/providers?new=1")
    assert "Add a provider" in add_new_page and "Add or update role" not in add_new_page
    providers = expect_success("/provider/allproviders", {}, token)["providers"]
    provider_id = next(p["value"]["organisationId"] for p in providers
                       if p["value"]["organisationAbbreviation"] == "SMOKE")
    users_page = page("/users", {"csrf": csrf(page("/users")),
        "email": "director@example.invalid"})
    assert "Password (shown once)" in users_page, "Management UI did not create a user"
    assert "name=\"provider_id\"" not in users_page, "Users page still manages provider membership"
    user_menu = re.search(r'<ul class="user-menu">(.*?)</ul>', users_page, re.S)
    assert user_menu and "Add new..." in re.findall(r"<li>.*?</li>", user_menu.group(1), re.S)[-1], \
        "Add new is not the last user menu item"
    assert "Add a user" in page("/users?new=1")
    users = expect_success("/auth/allusers", {}, token)["users"]
    created_user = next(u["value"] for u in users if u["value"]["email"] == "director@example.invalid")
    selected_user_page = page("/users?id=" + urllib.parse.quote(created_user["userId"]))
    assert "Selected user</p><h2>director@example.invalid</h2>" in selected_user_page
    assert "Password (shown once)" not in selected_user_page, "One-time password remained visible after navigation"
    first_provider_path = "/providers?id=" + urllib.parse.quote(provider_id)
    first_provider_page = page("/providers/associate", {"csrf": csrf(page(first_provider_path)),
        "user_id": created_user["userId"], "provider_id": provider_id, "role": "DIRECTOR"})
    assert re.search(r"<tr><td>director@example.invalid</td><td>DIRECTOR</td>", first_provider_page), \
        "Providers page did not show the user's role"
    second_provider_page = page("/providers", {"csrf": csrf(page("/providers")),
        "abbreviation": "SECOND", "name": "Second Club"})
    assert "Second Club" in second_provider_page
    second_provider_id = next(p["value"]["organisationId"] for p in
                              expect_success("/provider/allproviders", {}, token)["providers"]
                              if p["value"]["organisationAbbreviation"] == "SECOND")
    second_provider_path = "/providers?id=" + urllib.parse.quote(second_provider_id)
    selected_second = page(second_provider_path)
    assert "Second Club</h2>" in selected_second
    assert not re.search(r"<tr><td>director@example.invalid</td><td>DIRECTOR</td>", selected_second)
    selected_first = page(first_provider_path)
    assert "Compose Smoke Test</h2>" in selected_first
    assert re.search(r"<tr><td>director@example.invalid</td><td>DIRECTOR</td>", selected_first)
    second_provider_page = page("/providers/associate", {"csrf": csrf(page(second_provider_path)),
        "user_id": created_user["userId"], "provider_id": second_provider_id, "role": "DIRECTOR"})
    assert re.search(r"<tr><td>director@example.invalid</td><td>DIRECTOR</td>", second_provider_page), \
        "Second provider did not show the user's role"
    eligible = expect_success("/factory/assignments/eligible-users", {"providerId": second_provider_id}, token)["users"]
    assert any(user["email"] == "director@example.invalid" for user in eligible)
    page("/providers/disassociate", {"csrf": csrf(page(second_provider_path)),
        "user_id": created_user["userId"], "provider_id": second_provider_id})
    eligible = expect_success("/factory/assignments/eligible-users", {"providerId": second_provider_id}, token)["users"]
    assert not any(user["email"] == "director@example.invalid" for user in eligible)
    changed_password = uuid4().hex
    reset_page = page("/users/reset-password", {"csrf": csrf(page("/users")),
        "email": "director@example.invalid", "new_password": changed_password,
        "confirm_password": changed_password})
    assert "Password changed for director@example.invalid" in reset_page
    assert changed_password in reset_page, "The new password was not shown to the administrator"
    user_login = request("/auth/login", {"email": "director@example.invalid", "password": changed_password})
    assert user_login.get("mustChangePassword") and user_login.get("limitedToken")
    name = f"Compose smoke {uuid4()}"
    created = expect_success("/factory/generate", {
        "tournamentName": name,
        "tournamentAttributes": {"parentOrganisation": {"organisationId": provider_id}},
        "participantsProfile": {"scaledParticipantsCount": 0},
        "drawProfiles": [],
    }, token)
    tournament_id = created["tournamentRecord"]["tournamentId"]
    provider_overview = page(first_provider_path)
    assert name in provider_overview and tournament_id in provider_overview, \
        "Management UI did not list the tournament under its provider"
    tournament_path = first_provider_path + "&tournament=" + urllib.parse.quote(tournament_id)
    tournament_page = page(tournament_path)
    assert name in tournament_page and "Grant access" in tournament_page, \
        "Management UI did not show tournament access under the provider"
    try:
        page(second_provider_path + "&tournament=" + urllib.parse.quote(tournament_id))
    except urllib.error.HTTPError as error:
        assert error.code == 400, "Tournament opened under the wrong provider"
    else:
        raise AssertionError("Tournament opened under the wrong provider")
    assert "Grant access" in page("/tournaments?id=" + urllib.parse.quote(tournament_id)), \
        "Old tournament link did not redirect to the provider page"
    page("/tournaments/grant", {"csrf": csrf(tournament_page),
        "tournament_id": tournament_id, "email": "director@example.invalid", "role": "DIRECTOR"})
    assignments = expect_success("/factory/assignments/list", {"tournamentId": tournament_id}, token)["assignments"]
    assert any(a["email"] == "director@example.invalid" and a["assignmentRole"] == "DIRECTOR" for a in assignments)
    tournament_page = page(tournament_path)
    assert "director@example.invalid" in tournament_page and "Revoke" in tournament_page
    page("/tournaments/revoke", {"csrf": csrf(tournament_page),
        "tournament_id": tournament_id, "email": "director@example.invalid"})
    assignments = expect_success("/factory/assignments/list", {"tournamentId": tournament_id}, token)["assignments"]
    assert not any(a["email"] == "director@example.invalid" for a in assignments)

    def mutate(method, params):
        return expect_success("/factory", {
            "tournamentId": tournament_id,
            "methods": [{"method": method, "params": params}],
        }, token)

    def fetch():
        result = expect_success("/factory/fetch", {"tournamentId": tournament_id}, token)
        return result["tournamentRecords"][tournament_id]

    assert fetch()["tournamentName"] == name
    players = []
    for number in range(1, 5):
        participant_id = str(uuid4())
        players.append(participant_id)
        mutate("addParticipant", {"participant": {
            "participantId": participant_id,
            "participantType": "INDIVIDUAL",
            "participantRole": "COMPETITOR",
            "participantName": f"Smoke Player {number}",
            "person": {
                "standardGivenName": "Smoke",
                "standardFamilyName": f"Player {number}",
            },
        }})
    saved = fetch()
    assert set(players).issubset({p["participantId"] for p in saved["participants"]})

    event_name = "Smoke Singles"
    mutate("addEvent", {"event": {"eventName": event_name, "eventType": "SINGLES"}})
    event = next(e for e in fetch()["events"] if e["eventName"] == event_name)
    event_id = event["eventId"]
    mutate("addEventEntries", {"eventId": event_id, "participantIds": players})
    event = next(e for e in fetch()["events"] if e["eventId"] == event_id)
    assert set(players).issubset({e["participantId"] for e in event["entries"]})

    # TMX generates a draw locally, then sends addDrawDefinition as a server
    # mutation. Use the factory shipped in the built image for the same flow.
    generate = """
const fs = require('fs');
const { tournamentEngine } = require('tods-competition-factory');
const { tournamentRecord, eventId } = JSON.parse(fs.readFileSync(0, 'utf8'));
tournamentEngine.setState(tournamentRecord);
const result = tournamentEngine.generateDrawDefinition({ eventId, drawSize: 4, automated: true });
if (!result.success || !result.drawDefinition) {
  throw new Error(JSON.stringify(result));
}
process.stdout.write(JSON.stringify(result.drawDefinition));
"""
    generated = subprocess.run(
        ["docker", "compose", "-p", "tmx-smoke", "-f", "compose.yaml",
         "-f", "tests/compose.smoke.yaml", "exec", "-T", "server", "node", "-e", generate],
        input=json.dumps({"tournamentRecord": fetch(), "eventId": event_id}),
        text=True, capture_output=True, check=True,
    )
    draw = json.loads(generated.stdout)
    mutate("addDrawDefinition", {"eventId": event_id, "drawDefinition": draw})
    event = next(e for e in fetch()["events"] if e["eventId"] == event_id)
    draws = event.get("drawDefinitions") or []
    assert draws and draws[0].get("structures"), f"Draw was not saved: {event}"
    try:
        page("/users/remove", {"csrf": csrf(page("/users")),
            "email": "director@example.invalid", "confirm_email": "wrong@example.invalid"})
    except urllib.error.HTTPError as error:
        assert error.code == 400, "User removal accepted the wrong confirmation"
    else:
        raise AssertionError("User removal accepted the wrong confirmation")
    removed_page = page("/users/remove", {"csrf": csrf(page("/users")),
        "email": "director@example.invalid", "confirm_email": "director@example.invalid"})
    assert "User removed: director@example.invalid" in removed_page
    users = expect_success("/auth/allusers", {}, token)["users"]
    assert not any(u["value"]["email"] == "director@example.invalid" for u in users)
    print(f"Smoke test passed: manager login, provider memberships, account deletion, password reset, tournament access; "
          f"tournament {tournament_id}, four players, one event, one draw")


if __name__ == "__main__":
    main()
