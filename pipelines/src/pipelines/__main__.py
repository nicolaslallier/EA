"""`python -m pipelines`: what the worker container runs — serve the flows."""

from prefect import serve

from pipelines.catalogue import alimenter_catalogue
from pipelines.references import rafraichir_references

if __name__ == "__main__":
    serve(
        alimenter_catalogue.to_deployment(name="manuel", concurrency_limit=1),  # type: ignore[arg-type]  # sync context: not a coroutine
        rafraichir_references.to_deployment(name="manuel", concurrency_limit=1),  # type: ignore[arg-type]
    )
