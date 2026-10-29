# deepagent-backend-gcs

Google Cloud Storage storage backend for [Deep Agents](https://github.com/langchain-ai/deepagents).
Part of the [`deepagent-backend`](https://pypi.org/project/deepagent-backend/) family.

## Install

```bash
pip install "deepagent-backend[gcs]"
# or, equivalently, just this backend:
pip install deepagent-backend-gcs
```

## Usage

```python
from deepagent_backend_gcs import GCSBackend, GCSConfig

# or, via the umbrella package:
from deepagent_backend import GCSBackend, GCSConfig
```

## Documentation

- [GCS configuration and behavior](https://github.com/anocerino-ai/deepagent-backend/blob/main/wiki/gcs.md)
- [Runnable examples](https://github.com/anocerino-ai/deepagent-backend/tree/main/examples)
- [All backends and installation options](https://github.com/anocerino-ai/deepagent-backend)
