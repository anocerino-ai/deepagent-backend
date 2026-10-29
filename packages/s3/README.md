# deepagent-backend-s3

S3-compatible storage backend for [Deep Agents](https://github.com/langchain-ai/deepagents),
including AWS S3, Garage, and MinIO.
Part of the [`deepagent-backend`](https://pypi.org/project/deepagent-backend/) family.

## Install

```bash
pip install "deepagent-backend[s3]"
# or, equivalently, just this backend:
pip install deepagent-backend-s3
```

## Usage

```python
from deepagent_backend_s3 import S3Backend, S3Config

# or, via the umbrella package:
from deepagent_backend import S3Backend, S3Config
```

## Documentation

- [S3 configuration and behavior](https://github.com/anocerino-ai/deepagent-backend/blob/main/wiki/s3.md)
- [Runnable examples](https://github.com/anocerino-ai/deepagent-backend/tree/main/examples)
- [All backends and installation options](https://github.com/anocerino-ai/deepagent-backend)
