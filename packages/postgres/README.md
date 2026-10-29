# deepagent-backend-postgres

PostgreSQL storage backend for [Deep Agents](https://github.com/langchain-ai/deepagents).
Part of the [`deepagent-backend`](https://pypi.org/project/deepagent-backend/) family.

## Install

```bash
pip install "deepagent-backend[postgres]"
# or, equivalently, just this backend:
pip install deepagent-backend-postgres
```

## Usage

```python
from deepagent_backend_postgres import PostgresBackend, PostgresConfig

# or, via the umbrella package:
from deepagent_backend import PostgresBackend, PostgresConfig
```

## Documentation

- [PostgreSQL configuration and lifecycle](https://github.com/anocerino-ai/deepagent-backend/blob/main/wiki/postgresql.md)
- [Runnable examples](https://github.com/anocerino-ai/deepagent-backend/tree/main/examples)
- [All backends and installation options](https://github.com/anocerino-ai/deepagent-backend)
