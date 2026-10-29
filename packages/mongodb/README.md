# deepagent-backend-mongodb

MongoDB storage backend for [Deep Agents](https://github.com/langchain-ai/deepagents).
Part of the [`deepagent-backend`](https://pypi.org/project/deepagent-backend/) family.

## Install

```bash
pip install "deepagent-backend[mongodb]"
# or, equivalently, just this backend:
pip install deepagent-backend-mongodb
```

## Usage

```python
from deepagent_backend_mongodb import MongoDBBackend, MongoDBConfig

# or, via the umbrella package:
from deepagent_backend import MongoDBBackend, MongoDBConfig
```

## Documentation

- [MongoDB configuration and lifecycle](https://github.com/anocerino-ai/deepagent-backend/blob/main/wiki/mongodb.md)
- [Runnable examples](https://github.com/anocerino-ai/deepagent-backend/tree/main/examples)
- [All backends and installation options](https://github.com/anocerino-ai/deepagent-backend)
