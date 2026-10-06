<p align="center"><img src="assets/banner.svg" alt="TMX Docker: all-in-one TMX service" width="480"></p>

# TMX Docker

[TMX](https://github.com/CourtHive/TMX) is CourtHive's tournament management
application. Tournament staff can manage participants and entries, create events
and draws, schedule matches and courts, enter scores, and produce reports. It
uses the [Competition Factory](https://github.com/CourtHive/competition-factory)
for tournament rules and records.

This repository packages TMX with the
[competition-factory-server](https://github.com/CourtHive/competition-factory-server)
as a self-hosted Docker Compose application. CourtHive has [decided against a
Docker deployment path for TMX](https://github.com/CourtHive/TMX/pull/1404#issuecomment-5781297529),
so this is an independent wrapper around the upstream projects. It does not
change their source code or require local checkouts of them.

## What runs

| Service | Purpose |
| --- | --- |
| `web` (`tmx-docker-web`) | The built TMX browser application, served by nginx. nginx also forwards API and Socket.IO requests to `server` on the same origin. |
| `server` (`tmx-docker-server`) | The Competition Factory server, which handles accounts and tournament records and applies tournament mutations. |
| `manager` (`tmx-manager`) | A small Flask administration UI for providers, user accounts, provider memberships, and tournament access. It calls the CF server API and is built from this repository. |
| `postgres` | Persistent storage for accounts and tournament records. |
| `redis` | Disposable cache used by the server. |

The TMX web and server images are separate images and should use the same
`IMAGE_TAG`. Both contain a compatible version of the Competition Factory.
The images and Compose file provide the core tournament management stack.
CourtHive services such as the public viewer, query service, persons,
declarations, assistant, and score relay are outside this stack; features that
depend on them need those services configured separately.

## CourtHive components and versions

| Original repository | How this stack uses it | Version selection |
| --- | --- | --- |
| [TMX](https://github.com/CourtHive/TMX) | Builds the browser application in the `web` image. | `TMX_REF` in [`sources.env`](sources.env) pins a full upstream commit for local builds and published images. |
| [competition-factory-server](https://github.com/CourtHive/competition-factory-server) | Builds the `server` image, which stores accounts and tournament records and exposes the API used by TMX and the manager. | `SERVER_REF` in [`sources.env`](sources.env) pins a separate full upstream commit. |
| [Competition Factory](https://github.com/CourtHive/competition-factory) (`tods-competition-factory`) | Supplies tournament rules and record operations to both TMX and the server; the manager uses it through the server API. | Each pinned upstream project declares its package version. CI requires the two declarations to match before publishing the image pair. |
| [courthive-components](https://github.com/CourtHive/courthive-components) | The manager serves its CSS locally for `button`, form field, select, menu, and notification styles. TMX uses the library in its own build. | The manager's copied CSS bundle is fixed at **6.2.0** in [`manager/static/courthive-components.css`](manager/static/courthive-components.css), independently of the TMX and server pins. |

The weekly [source update workflow](.github/workflows/update-sources.yml) selects
the newest TMX and server commits that declare the same Competition Factory
version, runs the stack smoke test, then updates `sources.env`. The
[publishing workflow](.github/workflows/publish-images.yml) builds both images
from those commits. In Compose, `IMAGE_TAG=latest` selects the latest published
pair; `IMAGE_TAG=sha-<this-repository-commit>` selects a specific pair. The
manager image is built locally from this repository and does not use
`IMAGE_TAG`.

## Manager

`manager` is a helper container developed in this repository. It is not a
CourtHive component. Its Flask web UI calls the Competition Factory server's
REST API and starts with the rest of the Compose stack. Open it at
<http://localhost:8081/> by default and sign in with the initial CF server
administrator account.

The manager serves the CSS bundle from
[courthive-components](https://github.com/CourtHive/courthive-components)
version 6.2.0 locally. Its forms, buttons, menus, and notices use the shared
classes; TMX's own dependency version is determined by its pinned source
commit. The Flask application has no Node.js runtime or external stylesheet
request. The bundled stylesheet's license is in
[`manager/COURTHIVE-COMPONENTS-LICENSE`](manager/COURTHIVE-COMPONENTS-LICENSE).

The UI provides:

- **Providers:** Create providers, view their users and roles, and add, change,
  or remove provider memberships.
- **Users:** Create and delete accounts and change passwords. Generated and
  reset passwords are displayed once so they can be given to the user.
- **Tournaments:** Browse tournaments nested under their provider and grant or
  revoke user access as Director, Scorer, or Observer.

The address is configured with `MANAGER_BIND_ADDRESS` and `MANAGER_HTTP_PORT`
in `.env`. Set `MANAGER_COOKIE_SECURE=true` when serving the UI through HTTPS.

## Run with Docker Compose

1. Copy the example configuration and set two different, strong secrets:

   ```sh
   cp .env.example .env
   openssl rand -hex 32  # use the output for PG_PASSWORD
   openssl rand -hex 32  # use a different output for JWT_SECRET
   ```

2. Edit `.env`, then start the stack:

   ```sh
   docker compose up -d
   ```

3. Create the first administrator:

   ```sh
   docker compose exec server node src/scripts/admin-user.mjs create --email admin@example.com --password 'choose-a-password'
   ```

4. Open the management UI at <http://localhost:8081/> and sign in with the
   **same administrator email and password** from step 3. Use **Add new...**
   on the Providers page to create a provider with a unique abbreviation.

5. Open TMX at <http://localhost:8080/tmx/> and sign in. Select the provider
   when creating a tournament. Find it nested under that provider in the
   management UI to assign users to it.

## Configuration

The settings in [`.env.example`](.env.example) are:

| Variable | Purpose |
| --- | --- |
| `PG_PASSWORD` | Required password for the PostgreSQL database. |
| `JWT_SECRET` | Required secret for server authentication tokens; use a different value from `PG_PASSWORD`. |
| `IMAGE_TAG` | Tag used by both application images. `latest` follows updates; a `sha-...` tag keeps a specific image pair. |
| `HTTP_PORT` | Host port for TMX; defaults to `8080`. |
| `BIND_ADDRESS` | Host address to bind; defaults to `127.0.0.1` for local access only. |
| `PUBLIC_ORIGIN` | Browser-visible origin, such as `http://localhost:8080` or `https://tmx.example.com`. It must match the address used to reach the application. |
| `MANAGER_HTTP_PORT` | Host port for the management UI; defaults to `8081`. |
| `MANAGER_BIND_ADDRESS` | Host address for the management UI; defaults to `127.0.0.1`. |
| `MANAGER_COOKIE_SECURE` | Set to `true` when the management UI is served through HTTPS. |

For access from other machines, put HTTPS in front of the web service, set
`PUBLIC_ORIGIN` to the external origin, and set `BIND_ADDRESS` to an address your
reverse proxy can reach. For remote management UI access, also expose
`MANAGER_BIND_ADDRESS` through an HTTPS reverse proxy and set
`MANAGER_COOKIE_SECURE=true`. PostgreSQL and Redis are accessible only within the
Compose network. If the application images are private, authenticate Docker to
the registry before starting the stack.

## Build the images locally

[`sources.env`](sources.env) pins full commits of TMX (`TMX_REF`) and
competition-factory-server (`SERVER_REF`). The pinned projects must declare the
same `tods-competition-factory` version. Change those commits to select a
different upstream pair; no local upstream checkout is needed.

With `.env` configured as above, build the web and server images from the
pinned commits and run the stack using the
[local Compose override](compose.local.yaml):

```sh
docker compose --env-file .env --env-file sources.env \
  -f compose.yaml -f compose.local.yaml build
docker compose --env-file .env --env-file sources.env \
  -f compose.yaml -f compose.local.yaml up -d
```

The override uses local image names and prevents Compose from pulling the
application images; `IMAGE_TAG` applies only to the prebuilt images. The
management UI is built locally by either Compose command. Use the same
administrator command and URLs from the previous section. To run the
repository's integration check against the pinned pair, use
`bash scripts/smoke_stack.sh sources.env`; it starts a temporary stack
and removes its test database afterward.

## Data and updates

PostgreSQL data persists in the `postgres-data` volume. The server applies
database migrations on startup. Back up the database before changing image
tags or rebuilding from different source commits:

```sh
docker compose exec -T postgres pg_dump -U tmx -d tmx > tmx-backup.sql
```

For prebuilt images, change `IMAGE_TAG` in `.env` and run `docker compose up -d`
to use the selected pair. For a local build, change `sources.env`, then repeat
the build and start commands above. `docker compose down` stops the stack while
keeping the PostgreSQL volume.
