# deepagent-backend-azure-blob

Azure Blob Storage storage backend for [Deep Agents](https://github.com/langchain-ai/deepagents).
Part of the [`deepagent-backend`](https://pypi.org/project/deepagent-backend/) family.

## Install

```bash
pip install "deepagent-backend[azure-blob]"
# or, equivalently, just this backend:
pip install deepagent-backend-azure-blob
```

## Usage

```python
from deepagent_backend_azure_blob import AzureBlobBackend, AzureBlobConfig

# or, via the umbrella package:
from deepagent_backend import AzureBlobBackend, AzureBlobConfig
```

## Documentation

- [Azure Blob configuration and lifecycle](https://github.com/anocerino-ai/deepagent-backend/blob/main/wiki/azure-blob.md)
- [Runnable examples](https://github.com/anocerino-ai/deepagent-backend/tree/main/examples)
- [All backends and installation options](https://github.com/anocerino-ai/deepagent-backend)
