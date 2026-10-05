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
| `postgres` | Persistent storage for accounts and tournament records. |
| `redis` | Disposable cache used by the server. |

The TMX web and server images are separate images and should use the same
`IMAGE_TAG`. Both contain a compatible version of the Competition Factory.
The images and Compose file provide the core tournament management stack.
CourtHive services such as the public viewer, query service, persons,
declarations, assistant, and score relay are outside this stack; features that
depend on them need those services configured separately.

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

4. Open <http://localhost:8080/tmx/> and sign in.

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

For access from other machines, put HTTPS in front of the web service, set
`PUBLIC_ORIGIN` to the external origin, and set `BIND_ADDRESS` to an address your
reverse proxy can reach. PostgreSQL and Redis are accessible only within the
Compose network. If the application images are private, authenticate Docker to
the registry before starting the stack.

## Build the images locally

[`sources.env`](sources.env) pins full commits of TMX (`TMX_REF`) and
competition-factory-server (`SERVER_REF`). The pinned projects must declare the
same `tods-competition-factory` version. Change those commits to select a
different upstream pair; no local upstream checkout is needed.

With `.env` configured as above, build both images from the pinned commits and
run them using the [local Compose override](compose.local.yaml):

```sh
docker compose --env-file .env --env-file sources.env \
  -f compose.yaml -f compose.local.yaml build
docker compose --env-file .env --env-file sources.env \
  -f compose.yaml -f compose.local.yaml up -d
```

The override uses local image names and prevents Compose from pulling the
application images; `IMAGE_TAG` applies only to the prebuilt images. Use the
same administrator command and URL from the previous section. To run the
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
