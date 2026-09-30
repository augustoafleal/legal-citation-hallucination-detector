"""Gera uma submissão a partir de um SQLite e de uma pasta TXT fornecidos."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bracis_jusbrasil.cases import build_case_index
from bracis_jusbrasil.citations import CitationDetector, CitationParser, CitationResolver, arbitrate_citations
from bracis_jusbrasil.database import connect_database
from bracis_jusbrasil.submission import build_submission_record
from bracis_jusbrasil.submission_csv import write_submission_csv


def _validate_inputs(db_path: Path, txt_dir: Path) -> None:
    if not db_path.is_file():
        raise ValueError(f"DB inexistente ou inválido: {db_path}")
    if not txt_dir.is_dir():
        raise ValueError(f"Pasta TXT inexistente ou inválida: {txt_dir}")


def run_pipeline(db_path: Path, txt_dir: Path):
    """Execute V11 sobre as entradas recebidas, em ordem determinística."""
    _validate_inputs(db_path, txt_dir)
    paths = sorted(txt_dir.glob("*.txt"))
    texts = {path.stem: path.read_text(encoding="utf-8") for path in paths}
    detector, parser = CitationDetector(), CitationParser()
    raw, final_outputs = [], {}
    with connect_database(db_path, read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        for document_id, text in sorted(texts.items()):
            candidates = detector.detect(text)
            parsed = tuple(parser.parse(candidate, context=text) for candidate in candidates)
            resolutions = tuple(resolver.resolve(item) for item in parsed)
            raw.extend(
                (document_id, candidate, parsed_item, result)
                for candidate, parsed_item, result in zip(candidates, parsed, resolutions)
            )
            final_outputs[document_id] = arbitrate_citations(
                text, candidates, parsed, resolutions, primary_identities=resolver.primary_identities
            )
    return texts, raw, final_outputs


def write_submission(destination: Path, texts, final_outputs) -> None:
    """Serialize os outputs arbitrados sem depender de arquivos do desafio."""
    records = (build_submission_record(document_id, final_outputs[document_id]) for document_id in sorted(texts))
    write_submission_csv(destination, records)


def generate_submission(db_path: Path, txt_dir: Path, destination: Path) -> None:
    texts, _, final_outputs = run_pipeline(db_path, txt_dir)
    write_submission(destination, texts, final_outputs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", type=Path)
    parser.add_argument("txt_dir", type=Path)
    parser.add_argument("output_path", type=Path)
    args = parser.parse_args()
    try:
        generate_submission(args.db_path, args.txt_dir, args.output_path)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
