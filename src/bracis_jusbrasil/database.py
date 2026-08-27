"""Acesso seguro e read-only ao SQLite distribuído com o desafio."""

from __future__ import annotations

import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHALLENGE_DATA_DIR = PROJECT_ROOT / "material_desafio_jusbrasil_bracis"
DATABASE_NAME = "desafio1_bracis.db"


def get_challenge_data_dir() -> Path:
    """Retorna o diretório original da distribuição do desafio."""
    if not CHALLENGE_DATA_DIR.is_dir():
        raise FileNotFoundError(
            f"Diretório do material não encontrado: {CHALLENGE_DATA_DIR}"
        )
    return CHALLENGE_DATA_DIR


def get_database_path() -> Path:
    """Retorna o caminho do SQLite distribuído, sem criá-lo ou copiá-lo."""
    path = get_challenge_data_dir() / DATABASE_NAME
    if not path.is_file():
        raise FileNotFoundError(f"Banco SQLite não encontrado: {path}")
    return path


def connect_database(path: str | Path, read_only: bool = True) -> sqlite3.Connection:
    """Abre o SQLite configurando linhas nomeadas; o padrão é somente leitura."""
    database_path = Path(path).expanduser().resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"Banco SQLite não encontrado: {database_path}")

    if read_only:
        connection = sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection
