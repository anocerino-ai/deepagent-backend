# deepagent-backend

Remote storage backends for [LangChain Deep Agents](https://github.com/langchain-ai/deepagents):
persist agent files in S3, PostgreSQL, Azure Blob Storage, Google Cloud Storage, MongoDB or Redis/Valkey.

Every backend is its own PyPI distribution, so you install **only the client libraries you actually use**:

```bash
pip install "deepagent-backend[s3]"          # S3 / MinIO
pip install "deepagent-backend[redis]"       # Redis / Valkey
pip install "deepagent-backend[s3,postgres]" # several at once
pip install "deepagent-backend[all]"         # everything
```

| Extra | Distribution | Import package | Backend / config classes | Pulls in |
|---|---|---|---|---|
| `s3` | `deepagent-backend-s3` | `deepagent_backend_s3` | `S3Backend`, `S3Config` | `aioboto3` |
| `postgres` | `deepagent-backend-postgres` | `deepagent_backend_postgres` | `PostgresBackend`, `PostgresConfig` | `psycopg[binary,pool]` |
| `azure-blob` | `deepagent-backend-azure-blob` | `deepagent_backend_azure_blob` | `AzureBlobBackend`, `AzureBlobConfig` | `azure-storage-blob` |
| `gcs` | `deepagent-backend-gcs` | `deepagent_backend_gcs` | `GCSBackend`, `GCSConfig` | `gcloud-aio-storage` |
| `mongodb` | `deepagent-backend-mongodb` | `deepagent_backend_mongodb` | `MongoDBBackend`, `MongoDBConfig` | `motor` |
| `redis` (alias `valkey`) | `deepagent-backend-redis` | `deepagent_backend_redis` | `RedisBackend`, `RedisConfig` | `redis` |
| `all` | all of the above | | | |

`pip install deepagent-backend` alone installs just the shared helpers (`deepagent_backend.common`).
You can also install a single backend directly, e.g. `pip install deepagent-backend-s3`.

## Usage

The umbrella package re-exports every backend lazily, so the import path is the same whichever
ones you installed:

```python
from deepagents import create_deep_agent
from deepagent_backend import S3Backend, S3Config

agent = create_deep_agent(
    model=model,
    backend=S3Backend(S3Config(bucket="my-agent-bucket", prefix="agent-workspace")),
)
```

Importing a backend whose extra is missing fails with an actionable message:

```text
ImportError: S3Backend requires the 's3' backend. Install it with: pip install "deepagent-backend[s3]"
```

Importing from the backend package directly (`from deepagent_backend_s3 import S3Backend`) works too and
never touches the other backends.

## Backend guides

The [wiki index](wiki/README.md) links to the configuration and lifecycle guide for each backend:

| Backend | Guide |
|---|---|
| S3 | [S3](wiki/s3.md) |
| PostgreSQL | [PostgreSQL](wiki/postgresql.md) |
| Azure Blob | [Azure Blob](wiki/azure-blob.md) |
| Google Cloud Storage | [GCS](wiki/gcs.md) |
| MongoDB | [MongoDB](wiki/mongodb.md) |
| Redis / Valkey | [Redis / Valkey](wiki/redis-valkey.md) |

Runnable samples are in [`examples/`](examples/); the low-level example uses Garage and PostgreSQL from
`docker-compose.yml`, while the Deep Agent examples also require an Anthropic API key.

> **Compatibility:** the backends implement the `BackendProtocol` of `deepagents>=0.7.32`: `read` returns a
> `ReadResult` (the middleware adds the line numbers), `ls`/`grep`/`glob` return `LsResult`/`GrepResult`/`GlobResult`,
> `grep` is a literal substring search with `max_count`, `glob` follows the shared Deep Agents matcher, and `write`
> overwrites an existing file (keeping its creation time). Older `deepagents` releases are not supported.

## Repository layout

```text
.
├── pyproject.toml              # umbrella dist `deepagent-backend` (extras) + uv workspace
├── src/deepagent_backend/      # lazy re-exports + shared helpers (common.py)
├── packages/
│   ├── s3/                     # deepagent-backend-s3         -> deepagent_backend_s3
│   ├── postgres/               # deepagent-backend-postgres   -> deepagent_backend_postgres
│   ├── azure-blob/             # deepagent-backend-azure-blob -> deepagent_backend_azure_blob
│   ├── gcs/                    # deepagent-backend-gcs        -> deepagent_backend_gcs
│   ├── mongodb/                # deepagent-backend-mongodb    -> deepagent_backend_mongodb
│   └── redis/                  # deepagent-backend-redis      -> deepagent_backend_redis
├── tests/                      # unit + docker-based integration tests
├── scripts/                    # version-lockstep check, wheel smoke test
└── .pytools/                   # ruff, ty, pytest, coverage, commitizen settings
```

All distributions are released **in lockstep**: same version everywhere, and each extra pins the exact
matching backend version (`scripts/check_versions.py` enforces it in CI).

## Development and examples

Requires [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/); Docker only for integration tests.

```bash
just install           # sync dependencies; does not initialize Git
just test              # unit tests
just test-integration  # Garage, Postgres, Azurite, fake-gcs, MongoDB, Redis via docker-compose.yml
just ci                # lint, format, types, lockfile, versions, coverage
just smoke             # build wheels, then install each extra in a clean venv and verify it
docker compose up -d garage postgres
uv run examples/basic_usage.py
```

The low-level example uses the local Garage/PostgreSQL credentials from Compose by default.
Override them with `GARAGE_SECRET_ACCESS_KEY` and `POSTGRES_PASSWORD` if you customize the services.

### Releasing

1. `just bump` — commitizen bumps the version of every `pyproject.toml` and every cross-pin at once.
2. Push the tag (`vX.Y.Z`) and publish a GitHub release; `.github/workflows/release.yml` builds all
   distributions and publishes each one through its own PyPI trusted publisher.

One-time setup: add a [pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
for each distribution. Use owner `anocerino-ai`, repository `deepagent-backend`, workflow `release.yml`,
and the matching GitHub environment `pypi-<project-name>` (for example, `pypi-deepagent-backend-s3`).
To publish or retry one distribution, run the workflow manually from `main`, enter the tag name (such as
`v0.1.0`), and select its project under `publish_project`. Select `all` only when every publisher is set up
and none of the distributions in that release have already been uploaded.

## Credits

Inspired by [DiTo97/deepagents-backends](https://github.com/DiTo97/deepagents-backends) (MIT) for the idea of
providing storage backends for Deep Agents. Project scaffolding follows
[anocerino-ai/copier-fastapi-service-template](https://github.com/anocerino-ai/copier-fastapi-service-template)
(uv, ruff, ty, just, `.pytools/`).

## License

Released under the MIT license; see [LICENSE](LICENSE).
