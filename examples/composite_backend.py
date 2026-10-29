# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "deepagents>=0.7.32",
#     "langchain-anthropic",
#     "deepagent-backend[s3,postgres]",
# ]
# [tool.uv.sources]
# deepagent-backend = { path = "..", editable = true }
# ///
"""
Composite Backend Example - Hybrid S3 + PostgreSQL Storage

This advanced example shows how to create a DeepAgent with a
CompositeBackend that routes different paths to different storage backends.

Use cases:
- Store large binary files in S3, metadata in PostgreSQL
- Keep sensitive data in PostgreSQL, public assets in S3
- Use ephemeral state for working files, persistent storage for results

Prerequisites:
- Garage and PostgreSQL running (`docker compose up -d garage postgres`)

Usage:
    uv run examples/composite_backend.py
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from langchain_anthropic import ChatAnthropic

from deepagent_backend import PostgresBackend, PostgresConfig, S3Backend, S3Config

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def create_s3_backend() -> S3Backend:
    """Create S3 backend for large files and assets."""
    config = S3Config(
        bucket="test-bucket",
        prefix="agent-assets",
        endpoint_url="http://localhost:3900",
        access_key_id="GK0123456789abcdef0123456789abcdef",
        secret_access_key=os.environ.get(
            "GARAGE_SECRET_ACCESS_KEY",
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        ),
        use_ssl=False,
    )
    return S3Backend(config)


def create_postgres_config() -> PostgresConfig:
    """Create PostgreSQL config for structured data."""
    return PostgresConfig(
        host="localhost",
        port=5432,
        database="deepagents_test",
        user="postgres",
        password=os.environ.get("POSTGRES_PASSWORD", "postgres"),
        table="agent_files",
    )


def create_default_model() -> ChatAnthropic:
    """Create a Claude model configured for DeepAgent prompt caching."""
    return ChatAnthropic(
        model_name="claude-sonnet-4-5-20250929",
        max_tokens=20000,
        betas=["prompt-caching-2024-07-31"],
    )


@asynccontextmanager
async def composite_backend():
    """Create a composite backend factory with multiple storage routes.

    Route configuration:
    - /assets/ → S3 (large files, binary data)
    - /data/ → PostgreSQL (structured data, queries)
    - /memories/ → PostgreSQL (persistent across sessions)
    - Everything else → Ephemeral state (temporary working files)
    """
    s3_backend = create_s3_backend()
    pg_backend = PostgresBackend(create_postgres_config())

    try:
        await pg_backend.initialize()

        def backend_factory(runtime):
            return CompositeBackend(
                default=StateBackend(),
                routes={
                    "/assets/": s3_backend,
                    "/data/": pg_backend,
                    "/memories/": pg_backend,
                },
            )

        yield backend_factory

    finally:
        await pg_backend.close()


async def main():
    """Run a DeepAgent with hybrid S3 + PostgreSQL storage."""

    async with composite_backend() as backend_factory:
        agent = create_deep_agent(
            model=create_default_model(),
            backend=backend_factory,
            system_prompt="""You are a data processing assistant with hybrid storage.

Storage routing:
- /assets/ → S3 storage for large files, images, binary data
- /data/ → PostgreSQL for structured data and analysis results
- /memories/ → PostgreSQL for persistent notes and preferences
- Other paths → Ephemeral working space (temporary files)

When processing data:
1. Store input/output files in /assets/ for durability
2. Save analysis results to /data/ for querying
3. Keep personal notes in /memories/ for future reference
4. Use temporary paths for intermediate processing""",
        )

        print("Running DeepAgent with Composite Backend")
        print("=" * 60)
        print("Routes:")
        print("  /assets/   → S3 (large files)")
        print("  /data/     → PostgreSQL (structured data)")
        print("  /memories/ → PostgreSQL (persistent memory)")
        print("  /other/    → Ephemeral state")
        print("=" * 60)

        result = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": """Set up a project with hybrid storage:

1. Create /assets/sample_data.csv with some sample data
2. Create /data/analysis_config.json with analysis parameters
3. Save a note to /memories/project_notes.md about this project
4. Create /scratch/temp_notes.txt as a working file

Then explain where each file is stored and why.""",
                    }
                ]
            }
        )

        for message in result["messages"]:
            if hasattr(message, "content") and message.content:
                print(f"\n{message.type}: {message.content[:800]}...")


async def long_term_memory_example():
    """Example: Simulating persistent agent memory with a composite backend."""

    async with composite_backend() as backend_factory:
        # First interaction - agent learns user preferences
        agent = create_deep_agent(
            model=create_default_model(),
            backend=backend_factory,
            system_prompt="""You are a personalized assistant with long-term memory.

Store user preferences and learned information in /memories/.
This data persists across conversations.

When you learn something about the user, save it to:
- /memories/preferences.md for user preferences
- /memories/context.md for important context
- /memories/history.md for conversation highlights""",
        )

        print("Long-term Memory Example")
        print("=" * 60)

        # Interaction 1: learn user preferences
        print("\n[Interaction 1] Learning user preferences...")
        await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "I prefer Python over JavaScript, and I like detailed explanations."
                            " Remember this for our future conversations."
                        ),
                    }
                ]
            }
        )

        # Interaction 2: revisit the same persistent backend within this script run
        print("\n[Interaction 2] Using remembered preferences...")
        await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Read my preferences from /memories/ and recommend"
                            " a good framework for building web APIs."
                        ),
                    }
                ]
            }
        )

        print("\n" + "=" * 60)
        print("This demonstrates how /memories/ can live in PostgreSQL-backed storage.")
        print("Restart the script to verify persistence across separate runs.")


if __name__ == "__main__":
    asyncio.run(main())

    # Optional advanced demo:
    # asyncio.run(long_term_memory_example())
