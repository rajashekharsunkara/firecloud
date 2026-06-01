"""fc-rag CLI — index files and query the local RAG pipeline."""

from pathlib import Path
import click


@click.group()
def cli():
    """fc-rag — Private RAG pipeline for FireCloud docs."""
    pass


@cli.command()
@click.argument("path", type=click.Path(exists=True))
def index(path: str):
    """Index files at PATH into the local vector store."""
    from fc_rag.indexer import index_path

    target = Path(path)
    total_chunks = index_path(target)

    if target.is_file():
        file_count = 1
    else:
        supported = {".txt", ".md", ".py", ".json"}
        file_count = sum(
            1 for f in target.rglob("*")
            if f.is_file() and f.suffix in supported
        )

    click.echo(f"Indexed {total_chunks} chunks from {file_count} files")


@cli.command()
@click.argument("question")
def query(question: str):
    """Query the local RAG pipeline with a natural-language question."""
    from fc_rag.query_engine import query as run_query
    from fc_rag.retriever import retrieve

    answer = run_query(question)
    click.echo(answer)

    results = retrieve(question)
    if results:
        sources = sorted(set(r.filename for r in results))
        click.echo(f"\nSources: {', '.join(sources)}")


if __name__ == "__main__":
    cli()
