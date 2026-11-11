<p align="center"><img src="assets/banner.svg" alt="TMX Docker: all-in-one TMX service" width="650"></p>

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
require local checkouts of them. The image builds apply the upstream source
changes documented at the end of this README.

## What runs

- `web` (`tmx-docker-web`) serves TMX at `/`, the public viewer at `/pub/`, and the manager at `/manage/`.
  Nginx forwards account routes to `hiveid` and tournament routes to `server`.
- `server` (`tmx-docker-server`) stores account and tournament records.
  It applies tournament mutations.
- `pub` (`tmx-docker-pub`) serves the [courthive-public](https://github.com/CourtHive/courthive-public) browser application for published tournaments.
- `hiveid` (`tmx-docker-hiveid`) provides the [courthive-hiveid](https://github.com/CourtHive/courthive-hiveid) account API and signs ES256 tokens.
- `manager` (`tmx-manager`) provides the administration UI for providers, users, and tournaments.
  It uses the server API for provider and tournament actions.
  It uses the HiveID API for account actions.
- `postgres` stores accounts and tournament records.
- `redis` provides a disposable cache for the server.

The TMX web, server, public viewer, and HiveID use the same `IMAGE_TAG`.
TMX, the server, and the public viewer contain a compatible version of the Competition Factory.
The images and Compose file provide the core tournament management stack.
CourtHive services such as the query service, persons,
declarations, assistant, and score relay are outside this stack; features that
depend on them need those services configured separately.

## CourtHive components and versions

- **[TMX](https://github.com/CourtHive/TMX):** The `web` image serves the browser application.
  `TMX_REF` in [`sources.env`](sources.env) pins its upstream release.
- **[competition-factory-server](https://github.com/CourtHive/competition-factory-server):** The `server` image stores account and tournament records.
  `SERVER_REF` pins its upstream release.
- **[courthive-public](https://github.com/CourtHive/courthive-public):** The `pub` image serves published tournaments at `/pub/`.
  `PUB_REF` pins its upstream release.
- **[courthive-hiveid](https://github.com/CourtHive/courthive-hiveid):** The `hiveid` image serves account APIs and signs tokens.
  `HIVEID_REF` pins its upstream release.
- **[Competition Factory](https://github.com/CourtHive/competition-factory):** This package supplies tournament rules and record operations to TMX, the server, and the public viewer.
  CI checks that the three pinned releases declare the same package version.
- **[courthive-components](https://github.com/CourtHive/courthive-components):** The manager serves its CSS from [`manager/static/courthive-components.css`](manager/static/courthive-components.css).
  `COMPONENTS_VERSION` pins this asset separately.

The weekly [source update workflow](.github/workflows/update-sources.yml) selects
the newest release tags within each pinned major version.
TMX, the server, and the public viewer use the same `tods-competition-factory` version.
HiveID updates independently within its pinned major version.
It also selects the newest stable `courthive-components` release within its
pinned major version. It updates the manager's copied CSS and license notice,
runs the stack smoke test, then commits the updated pins and assets. The
[publishing workflow](.github/workflows/publish-images.yml) builds all four images
from those tags. In Compose, `IMAGE_TAG=latest` selects the latest published
set; `IMAGE_TAG=sha-<this-repository-commit>` selects a specific set. The
manager image is built locally from this repository and does not use
`IMAGE_TAG`.

Server v4.9.0 removed its account routes.
HiveID supplies account and session routes for TMX and the manager.
Nginx keeps tournament token, single sign-on, and claim routes on the server.
The public viewer's personal account features require additional CourtHive services.
Self-signup requires the persons service.
Email flows require the optional HiveID email configuration.

## Manager

`manager` is a helper container developed in this repository.
It is not a CourtHive component.
Its Flask web UI calls the Competition Factory server's REST API for provider and tournament actions.
It calls HiveID for password login and user administration.
The manager has no direct access to account tables or signing keys.
Open it at <http://localhost:8080/manage/> by default.
Sign in with the initial CF server administrator account.

The manager serves the CSS bundle from
[courthive-components](https://github.com/CourtHive/courthive-components)
version pinned in [`sources.env`](sources.env) locally. Its forms, buttons,
menus, and notices use the shared classes; TMX's own dependency version comes
from its pinned source tag. The Flask application has no Node.js runtime or
external stylesheet request. The bundled stylesheet's license is in
[`manager/COURTHIVE-COMPONENTS-LICENSE`](manager/COURTHIVE-COMPONENTS-LICENSE).

The UI provides:

- **Providers:** Create providers, view their users and roles, and add, change,
  or remove provider memberships.
- **Users:** Create and delete accounts and change passwords. Generated and
  reset passwords are displayed once so they can be given to the user.
- **Tournaments:** Browse tournaments nested under their provider and grant or
  revoke user access as Director, Scorer, or Observer.

The manager shares the web service's address.
You MUST set `MANAGER_COOKIE_SECURE=true` when serving the UI through HTTPS.
The server and HiveID use the same PostgreSQL database.
HiveID holds the private signing key.
The server receives the public key and calls HiveID when it needs a tournament token.

## Run with Docker Compose

1. Copy the example configuration.
   Generate different values for `PG_PASSWORD`, `JWT_SECRET`, and `HIVEID_SIGNER_TOKEN`.
   Generate an ES256 key pair for HiveID:

   ```sh
   cp .env.example .env
   openssl rand -hex 32 # use the output for PG_PASSWORD
   openssl rand -hex 32 # use the output for JWT_SECRET
   openssl rand -hex 32 # use the output for HIVEID_SIGNER_TOKEN
   openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt -out jwt-private.pem
   openssl ec -in jwt-private.pem -pubout -out jwt-public.pem
   ```

   Set `JWT_PRIVATE_KEY` and `JWT_PUBLIC_KEY` in `.env` to the PEM contents with each line ending written as `\n`.
   These commands print the values to paste into `.env`:

   ```sh
   printf 'JWT_PRIVATE_KEY=%s\n' "$(awk 'BEGIN { ORS="" } { print $0 "\\n" }' jwt-private.pem)"
   printf 'JWT_PUBLIC_KEY=%s\n' "$(awk 'BEGIN { ORS="" } { print $0 "\\n" }' jwt-public.pem)"
   ```

   You MUST keep the private key out of version control.

2. Edit `.env`, then start the stack:

   ```sh
   docker compose up -d
   ```

3. Create the first administrator:

   ```sh
   docker compose exec server node src/scripts/admin-user.mjs create --email admin@example.com --password 'choose-a-password'
   ```

4. Open the management UI at <http://localhost:8080/manage/> and sign in with the
   **same administrator email and password** from step 3. Use **Add new...**
   on the Providers page to create a provider with a unique abbreviation.

5. Open TMX at <http://localhost:8080/> and sign in. Select the provider
   when creating a tournament. Find it nested under that provider in the
   management UI to assign users to it. Published tournaments are available
   through the public viewer at <http://localhost:8080/pub/>.

## Configuration

The settings in [`.env.example`](.env.example) are:

- `PG_PASSWORD`: Password for PostgreSQL.
- `JWT_SECRET`: Secret required by the current server startup configuration.
  The stack rejects legacy HS256 tokens.
- `HIVEID_SIGNER_TOKEN`: Shared secret for server calls to HiveID's private token signer.
- `JWT_KID`: Stable identifier for the current ES256 key pair.
- `JWT_PRIVATE_KEY`: Private signing key given only to HiveID.
- `JWT_PUBLIC_KEY`: Public verification key shared by HiveID and the server.
- `IMAGE_TAG`: Tag used by all four application images.
  `latest` follows updates.
  A `sha-...` tag keeps a specific image set.
- `HTTP_PORT`: Host port for TMX.
  The default is `8080`.
- `BIND_ADDRESS`: Host address to bind.
  The default is `127.0.0.1` for local access.
- `PUBLIC_ORIGIN`: Browser-visible origin, such as `http://localhost:8080` or `https://tmx.example.com`.
  It MUST match the address used to reach the application.
- `MANAGER_COOKIE_SECURE`: You MUST set this to `true` when the management UI is served through HTTPS.

`RESEND_API_KEY` and `EMAIL_FROM` enable HiveID email flows.
`PERSONS_BASE_URL` and `PERSONS_SERVICE_TOKEN` connect HiveID to a separate persons service for self-signup.

For access from other machines, put HTTPS in front of the web service, set
`PUBLIC_ORIGIN` to the external origin, and set `BIND_ADDRESS` to an address your
reverse proxy can reach. For remote management UI access, set
`MANAGER_COOKIE_SECURE=true`. PostgreSQL, Redis, and HiveID are accessible only within the
Compose network. If the application images are private, authenticate Docker to
the registry before starting the stack.

## Build the images locally

[`sources.env`](sources.env) pins release tags of TMX (`TMX_REF`),
competition-factory-server (`SERVER_REF`), courthive-public (`PUB_REF`),
and courthive-hiveid (`HIVEID_REF`).
TMX, the server, and the public viewer use the same `tods-competition-factory` version.
Change those tags to select different upstream versions; no local upstream
checkout is needed. Upstream tags can be moved, so a later build of the same
tag may resolve to a different commit.

With `.env` configured as above, build the web, server, public viewer, and HiveID images from the
pinned tags and run the stack using the
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
repository's integration check against the pinned sources, use
`bash scripts/smoke_stack.sh sources.env`; it starts a temporary stack
and removes its test database afterward.

## Data and updates

PostgreSQL data persists in the `postgres-data` volume. The server applies
database migrations on startup. Back up the database before changing image
tags or rebuilding from different source releases:

```sh
docker compose exec -T postgres pg_dump -U tmx -d tmx > tmx-backup.sql
```

Existing users and bcrypt passwords remain in the shared database when moving from manager identity to HiveID.
Existing manager refresh sessions do not transfer to HiveID.
Users sign in again after the switch to ES256.

For prebuilt images, change `IMAGE_TAG` in `.env` and run `docker compose up -d`
to use the selected image set. For a local build, change `sources.env`, then repeat
the build and start commands above. `docker compose down` stops the stack while
keeping the PostgreSQL volume.

## License

The original code in this repository is licensed under the [MIT License](LICENSE).
Upstream code and bundled third-party assets retain their own terms; this
repository's license does not grant rights to them.

- [TMX v8.38.2](https://github.com/CourtHive/TMX/blob/v8.38.2/LICENSE.txt) has an MIT license.
  The web image includes its `LICENSE.txt`.
- The [competition-factory-server v4.3.0 package](https://github.com/CourtHive/competition-factory-server/blob/v4.3.0/package.json) declares an MIT license.
- [courthive-public v1.22.1](https://github.com/CourtHive/courthive-public/blob/v1.22.1/LICENSE) has an MIT license.
- [courthive-hiveid v0.4.3](https://github.com/CourtHive/courthive-hiveid/blob/v0.4.3/LICENSE) has an MIT license.
- The [Competition Factory package](https://github.com/CourtHive/competition-factory/blob/master/LICENSE) has an MIT license.
- The [courthive-components stylesheet](https://github.com/CourtHive/courthive-components) has an MIT license.
  Its notice is in [`manager/COURTHIVE-COMPONENTS-LICENSE`](manager/COURTHIVE-COMPONENTS-LICENSE).

## Upstream source changes during image builds

The Dockerfiles copy the pinned upstream releases into build stages, then make
these changes before building the images. They do not alter the upstream
repositories.

- [`Dockerfile.web`](Dockerfile.web) removes TMX's `scripts.postinstall`.
  The production web image does not need the Playwright browser downloaded by that script.
- The TMX, server, and public viewer Dockerfiles remove `link:../` overrides from their `pnpm-workspace.yaml` files.
  The Docker build does not include their sibling development checkouts.
- [`scripts/patch_tmx_root.mjs`](scripts/patch_tmx_root.mjs) adds a root-path case to TMX's `src/services/publishing/publicUrl.ts`.
  The web build sets `BASE_URL` to empty because TMX is served at `/`.
- [`Dockerfile.pub`](Dockerfile.pub) adds [`pub-config.js`](pub-config.js) to the public viewer.
  The script points its API and Socket.IO connections at this deployment's origin.
  The build sets `BASE_URL=pub` for assets under `/pub/`.
- [`scripts/patch_hiveid_first_login.mjs`](scripts/patch_hiveid_first_login.mjs) rejects reused first-login tokens in HiveID.
  It also gives database columns priority over stale JSON fields.
  The upstream code otherwise permits first-login token replay and can lose the last selected provider after a password reset.
