"""Exercise nginx, authentication, factory mutations, and PostgreSQL together."""

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4


BASE = os.environ["PUBLIC_ORIGIN"].rstrip("/")


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

    provider = expect_success("/provider/add", {
        "organisationAbbreviation": "SMOKE",
        "organisationName": "Compose Smoke Test",
    }, token)
    provider_id = provider["providerId"]
    name = f"Compose smoke {uuid4()}"
    created = expect_success("/factory/generate", {
        "tournamentName": name,
        "tournamentAttributes": {"parentOrganisation": {"organisationId": provider_id}},
        "participantsProfile": {"scaledParticipantsCount": 0},
        "drawProfiles": [],
    }, token)
    tournament_id = created["tournamentRecord"]["tournamentId"]

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
    print(f"Smoke test passed: tournament {tournament_id}, four players, one event, one draw")


if __name__ == "__main__":
    main()
