"""`python -m pipelines`: what the worker container runs — serve the flows."""

from pipelines.catalogue import alimenter_catalogue

if __name__ == "__main__":
    alimenter_catalogue.serve(name="manuel", limit=1)
