# deepagent-backend-redis

Redis / Valkey storage backend for [Deep Agents](https://github.com/langchain-ai/deepagents).
Part of the [`deepagent-backend`](https://pypi.org/project/deepagent-backend/) family.

## Install

```bash
pip install "deepagent-backend[redis]"
# or, equivalently, just this backend:
pip install deepagent-backend-redis
```

## Usage

```python
from deepagent_backend_redis import RedisBackend, RedisConfig

# or, via the umbrella package:
from deepagent_backend import RedisBackend, RedisConfig
```

## Documentation

- [Redis / Valkey configuration and behavior](https://github.com/anocerino-ai/deepagent-backend/blob/main/wiki/redis-valkey.md)
- [Runnable examples](https://github.com/anocerino-ai/deepagent-backend/tree/main/examples)
- [All backends and installation options](https://github.com/anocerino-ai/deepagent-backend)
