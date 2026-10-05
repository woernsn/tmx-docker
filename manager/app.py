"""Small administration UI for the Competition Factory server API."""

import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import wraps

from flask import Flask, g, redirect, render_template, request, url_for


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
API_URL = os.environ.get("TMX_API_URL", "http://server:8383").rstrip("/")
COOKIE_SECURE = os.environ.get("MANAGER_COOKIE_SECURE", "false").lower() == "true"
SESSION_LIFETIME = 3 * 60 * 60
sessions = {}
sessions_lock = threading.Lock()


class APIError(Exception):
    pass


class APIUnauthorized(APIError):
    pass


def api(path, data, token=None, method="POST"):
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        API_URL + path,
        data=json.dumps(data).encode(),
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise APIUnauthorized("Your server session expired. Please sign in again.") from exc
        try:
            detail = json.load(exc)
            message = detail.get("message") or detail.get("error") or str(exc)
        except (ValueError, AttributeError):
            message = str(exc)
        raise APIError(str(message)) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise APIError(f"Could not reach the CF server: {exc}") from exc
    if not isinstance(result, dict):
        raise APIError("Unexpected response from the CF server")
    if result.get("error") or result.get("success") is False:
        raise APIError(str(result.get("error") or result.get("message") or "Request failed"))
    return result


def new_session(token=None, email=None):
    sid = secrets.token_urlsafe(32)
    now = time.time()
    entry = {"token": token, "email": email, "csrf": secrets.token_urlsafe(32), "expires": now + SESSION_LIFETIME}
    with sessions_lock:
        for expired_id in [key for key, value in sessions.items() if value["expires"] <= now]:
            sessions.pop(expired_id, None)
        sessions[sid] = entry
    g.session_id, g.session = sid, entry


@app.before_request
def load_session():
    sid = request.cookies.get("manager_session")
    with sessions_lock:
        entry = sessions.get(sid)
        if entry and entry["expires"] <= time.time():
            sessions.pop(sid, None)
            entry = None
    g.session_id, g.session = (sid, entry) if entry else (None, None)


@app.after_request
def secure_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'"
    if g.get("clear_cookie"):
        response.delete_cookie("manager_session")
    elif g.session_id:
        response.set_cookie(
            "manager_session", g.session_id, max_age=SESSION_LIFETIME,
            httponly=True, secure=COOKIE_SECURE, samesite="Strict",
        )
    return response


def render(page, **context):
    return render_template(page, csrf=g.session["csrf"], **context)


def authorized(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.session or not g.session["token"]:
            return redirect(url_for("login"))
        try:
            return view(*args, **kwargs)
        except APIUnauthorized:
            with sessions_lock:
                sessions.pop(g.session_id, None)
            g.clear_cookie = True
            return redirect(url_for("login"))
        except APIError as exc:
            return render("error.html", error=str(exc)), 502
    return wrapped


def require_csrf():
    if not g.session or not secrets.compare_digest(request.form.get("csrf", ""), g.session["csrf"]):
        return render_template("error.html", error="The form expired. Reload the page and try again."), 400
    return None


def provider_list():
    result = api("/provider/allproviders", {}, g.session["token"])
    return sorted((item["value"] for item in result.get("providers", [])), key=lambda p: p.get("organisationName", ""))


def provider_page(selected_id=None, tournament_id=None, offset=0, create_mode=False, message=None, error=None):
    providers = provider_list()
    selected = next((p for p in providers if p.get("organisationId") == selected_id), None) if not create_mode else None
    if selected_id and not selected and not create_mode:
        return render("error.html", error="Select an existing provider."), 400
    if not selected and providers and not create_mode:
        selected = providers[0]
    create_mode = create_mode or not providers
    members = []
    users = []
    tournaments = []
    paging = {}
    tournament_record = None
    assignments = []
    eligible = []
    if selected:
        result = api("/provider/my-calendars", {
            "providerAbbr": selected["organisationAbbreviation"],
            "limit": 50, "offset": offset,
        }, g.session["token"])
        tournaments = next((item.get("tournaments", []) for item in result.get("calendars", [])
                            if item.get("providerAbbr") == selected["organisationAbbreviation"]), [])
        paging = result.get("paging", {})
        if tournament_id:
            tournament_record, actual_provider_id = tournament_context(tournament_id)
            if actual_provider_id != selected["organisationId"]:
                return render("error.html", error="Tournament does not belong to this provider."), 400
            if not any(item.get("tournamentId") == tournament_id for item in tournaments):
                tournaments.insert(0, {"tournamentId": tournament_id, "tournament": tournament_record})
            result = api("/factory/assignments/list", {"tournamentId": tournament_id}, g.session["token"])
            assignments = result.get("assignments", [])
            result = api("/factory/assignments/eligible-users", {
                "providerId": selected["organisationId"]}, g.session["token"])
            eligible = result.get("users", [])
        else:
            result = api("/factory/assignments/eligible-users", {
                "providerId": selected["organisationId"]}, g.session["token"])
            members = result.get("users", [])
            result = api("/auth/allusers", {}, g.session["token"])
            users = [item["value"] for item in result.get("users", [])]
    return render("providers.html", providers=providers, selected=selected,
                  create_mode=create_mode, members=members, users=users,
                  tournaments=tournaments, paging=paging, offset=offset,
                  tournament_id=tournament_id, tournament_record=tournament_record,
                  assignments=assignments, eligible=eligible,
                  message=message, error=error)


def user_page(selected_id=None, selected_email=None, create_mode=False):
    result = api("/auth/allusers", {}, g.session["token"])
    users = sorted((item["value"] for item in result.get("users", [])), key=lambda user: user.get("email", ""))
    selected = None
    if not create_mode:
        selected = next((user for user in users if
                         user.get("userId") == selected_id or user.get("email") == selected_email), None)
        if (selected_id or selected_email) and not selected:
            return render("error.html", error="Select an existing user."), 400
        if not selected and users:
            selected = users[0]
    notice = g.session.pop("user_notice", {})
    return render("users.html", users=users, selected=selected,
                  create_mode=create_mode or not users, message=notice.get("message"),
                  shown_password=notice.get("password"), password_note=notice.get("note"))


def tournament_context(tournament_id):
    result = api("/factory/fetch", {"tournamentId": tournament_id}, g.session["token"])
    record = result.get("tournamentRecords", {}).get(tournament_id)
    if not record:
        raise APIError("Tournament not found or unavailable")
    provider_id = (record.get("parentOrganisation") or {}).get("organisationId")
    if not provider_id:
        raise APIError("Tournament has no parent provider")
    return record, provider_id


@app.get("/health")
def health():
    return "ok"


@app.get("/")
def index():
    return redirect(url_for("providers"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if g.session and g.session["token"]:
            return redirect(url_for("providers"))
        if not g.session:
            new_session()
        return render("login.html")
    rejected = require_csrf()
    if rejected:
        return rejected
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    if not email or not password:
        return render("login.html", error="Enter your email and password."), 400
    try:
        result = api("/auth/login", {"email": email, "password": password})
        token = result.get("token")
        if not token:
            raise APIError("The server did not issue a login token")
        # The manager performs global provider/user administration, so require
        # the same super-admin access that the CF server grants to its first admin.
        api("/provider/allproviders", {}, token)
    except APIError as exc:
        return render("login.html", error=str(exc)), 401
    with sessions_lock:
        sessions.pop(g.session_id, None)
    new_session(token, email=email.lower())
    return redirect(url_for("providers"))


@app.post("/logout")
@authorized
def logout():
    rejected = require_csrf()
    if rejected:
        return rejected
    with sessions_lock:
        sessions.pop(g.session_id, None)
    g.clear_cookie = True
    return redirect(url_for("login"))


@app.route("/providers", methods=["GET", "POST"])
@authorized
def providers():
    if request.method == "POST":
        rejected = require_csrf()
        if rejected:
            return rejected
        abbreviation = request.form.get("abbreviation", "").strip().upper()
        name = request.form.get("name", "").strip()
        if not abbreviation or not name:
            return provider_page(create_mode=True, error="Enter an abbreviation and name."), 400
        result = api("/provider/add", {
            "organisationAbbreviation": abbreviation,
            "organisationName": name,
        }, g.session["token"])
        provider_id = result.get("providerId")
        if not provider_id:
            raise APIError("The server did not return a provider ID")
        return redirect(url_for("providers", id=provider_id, created=1))
    selected_id = request.args.get("id")
    message = "Provider created." if selected_id and request.args.get("created") == "1" else None
    try:
        offset = max(0, int(request.args.get("offset", "0")))
    except ValueError:
        offset = 0
    return provider_page(selected_id, tournament_id=request.args.get("tournament"), offset=offset,
                         create_mode=request.args.get("new") == "1", message=message)


@app.route("/users", methods=["GET", "POST"])
@authorized
def users():
    if request.method == "POST":
        rejected = require_csrf()
        if rejected:
            return rejected
        email = request.form.get("email", "").strip()
        if not email:
            return render("error.html", error="Enter a valid email."), 400
        result = api("/auth/admin-create-user", {
            "email": email, "roles": ["client"],
        }, g.session["token"])
        g.session["user_notice"] = {
            "message": f"User created: {email}", "password": result.get("password"),
            "note": "Give it to the user securely. They will be asked to change it at first sign in.",
        }
        return redirect(url_for("users", email=email.lower()))
    return user_page(selected_id=request.args.get("id"), selected_email=request.args.get("email"),
                     create_mode=request.args.get("new") == "1")


@app.post("/users/reset-password")
@authorized
def reset_password():
    rejected = require_csrf()
    if rejected:
        return rejected
    email = request.form.get("email", "").strip()
    password = request.form.get("new_password", "")
    confirmation = request.form.get("confirm_password", "")
    if not email or password != confirmation:
        return render("error.html", error="Select a user and make sure the passwords match."), 400
    result = api("/auth/admin-reset-password", {"email": email, "newPassword": password}, g.session["token"])
    g.session["user_notice"] = {
        "message": f"Password changed for {email}", "password": result.get("password"),
        "note": "Give this password to the user securely. Existing sessions for that user are invalidated.",
    }
    return redirect(url_for("users", email=email.lower()))


@app.post("/users/remove")
@authorized
def remove_user():
    rejected = require_csrf()
    if rejected:
        return rejected
    email = request.form.get("email", "").strip().lower()
    confirmation = request.form.get("confirm_email", "").strip().lower()
    if not email or email != confirmation:
        return render("error.html", error="Enter the selected email to confirm removal."), 400
    if email == g.session.get("email"):
        return render("error.html", error="You cannot remove the account you are signed in with."), 400
    api("/auth/remove", {"email": email}, g.session["token"])
    g.session["user_notice"] = {"message": f"User removed: {email}"}
    return redirect(url_for("users"))


@app.post("/providers/associate")
@authorized
def associate_user():
    rejected = require_csrf()
    if rejected:
        return rejected
    user_id = request.form.get("user_id", "").strip()
    provider_id = request.form.get("provider_id", "").strip()
    role = request.form.get("role", "")
    if not user_id or not provider_id or role not in ("DIRECTOR", "PROVIDER_ADMIN"):
        return render("error.html", error="Select a user, provider, and role."), 400
    if provider_id not in {p["organisationId"] for p in provider_list()}:
        return render("error.html", error="Select an existing provider."), 400
    api(f"/provisioner/users/{urllib.parse.quote(user_id, safe='')}/providers/{urllib.parse.quote(provider_id, safe='')}",
        {"providerRole": role}, g.session["token"], method="PUT")
    return redirect(url_for("providers", id=provider_id))


@app.post("/providers/disassociate")
@authorized
def disassociate_user():
    rejected = require_csrf()
    if rejected:
        return rejected
    user_id = request.form.get("user_id", "").strip()
    provider_id = request.form.get("provider_id", "").strip()
    if not user_id or provider_id not in {p["organisationId"] for p in provider_list()}:
        return render("error.html", error="Select a user and provider."), 400
    api(f"/provisioner/users/{urllib.parse.quote(user_id, safe='')}/providers/{urllib.parse.quote(provider_id, safe='')}",
        {}, g.session["token"], method="DELETE")
    return redirect(url_for("providers", id=provider_id))


@app.get("/tournaments")
@authorized
def tournaments():
    tournament_id = request.args.get("id", "").strip()
    if tournament_id:
        _, provider_id = tournament_context(tournament_id)
        return redirect(url_for("providers", id=provider_id, tournament=tournament_id))
    abbreviation = request.args.get("provider", "").strip()
    if abbreviation:
        provider = next((item for item in provider_list()
                         if item.get("organisationAbbreviation") == abbreviation), None)
        if not provider:
            return render("error.html", error="Select an existing provider."), 400
        return redirect(url_for("providers", id=provider["organisationId"]))
    return redirect(url_for("providers"))


@app.post("/tournaments/grant")
@authorized
def grant():
    rejected = require_csrf()
    if rejected:
        return rejected
    tournament_id = request.form.get("tournament_id", "").strip()
    email = request.form.get("email", "").strip()
    role = request.form.get("role", "")
    if not tournament_id or not email or role not in ("DIRECTOR", "SCORER", "OBSERVER"):
        return render("error.html", error="Select a tournament, user, and role."), 400
    _, provider_id = tournament_context(tournament_id)
    api("/factory/assignments/grant", {"tournamentId": tournament_id, "userEmail": email,
        "providerId": provider_id, "role": role}, g.session["token"])
    return redirect(url_for("providers", id=provider_id, tournament=tournament_id))


@app.post("/tournaments/revoke")
@authorized
def revoke():
    rejected = require_csrf()
    if rejected:
        return rejected
    tournament_id = request.form.get("tournament_id", "").strip()
    email = request.form.get("email", "").strip()
    if not tournament_id or not email:
        return render("error.html", error="Select a tournament and user."), 400
    _, provider_id = tournament_context(tournament_id)
    api("/factory/assignments/revoke", {"tournamentId": tournament_id,
        "userEmail": email, "providerId": provider_id}, g.session["token"])
    return redirect(url_for("providers", id=provider_id, tournament=tournament_id))
