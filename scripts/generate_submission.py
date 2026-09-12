"""Gera uma submissão usando o conversor oficial inalterado."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bracis_jusbrasil.cases import build_case_index
from bracis_jusbrasil.citations import CitationDetector, CitationParser, CitationResolver, arbitrate_citations
from bracis_jusbrasil.database import connect_database, get_challenge_data_dir, get_database_path
from bracis_jusbrasil.submission import build_submission_record


def run_pipeline():
    """Executa o pipeline estrutural e devolve apenas seus outputs finais."""
    data_dir = get_challenge_data_dir()
    texts = {path.stem: path.read_text(encoding="utf-8") for path in sorted((data_dir / "txt").glob("*.txt"))}
    detector, parser = CitationDetector(), CitationParser()
    raw, final_outputs = [], {}
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        for document_id, text in sorted(texts.items()):
            candidates = detector.detect(text)
            parsed = tuple(parser.parse(candidate, context=text) for candidate in candidates)
            resolutions = tuple(resolver.resolve(item) for item in parsed)
            raw.extend((document_id, candidate, parsed_item, result)
                       for candidate, parsed_item, result in zip(candidates, parsed, resolutions))
            final_outputs[document_id] = arbitrate_citations(
                text, candidates, parsed, resolutions, primary_identities=resolver.primary_identities
            )
    return texts, raw, final_outputs


def write_submission(destination: Path, texts, final_outputs) -> None:
    """Serializa outputs finais e delega a escrita CSV ao conversor oficial."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="bracis-submission-") as temporary:
        source = Path(temporary)
        for document_id in sorted(texts):
            (source / f"{document_id}.json").write_text(
                json.dumps(build_submission_record(document_id, final_outputs[document_id]), ensure_ascii=False),
                encoding="utf-8",
            )
        completed = subprocess.run(
            [sys.executable, str(get_challenge_data_dir() / "json_to_submission.py"), str(source), str(destination)],
            cwd=ROOT,
            text=True,
        )
    if completed.returncode:
        raise RuntimeError("O conversor oficial falhou ao gerar a submissão.")


def generate_submission(destination: Path) -> None:
    """Executa o pipeline e gera a submissão no destino solicitado."""
    texts, _, final_outputs = run_pipeline()
    write_submission(destination, texts, final_outputs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "destination",
        nargs="?",
        type=Path,
        default=ROOT / "submissions" / "submission_v9_h4_rcl_conf085.csv",
    )
    args = parser.parse_args()
    generate_submission(args.destination)


if __name__ == "__main__":
    main()
