"""Exibe um resumo read-only do banco canônico do desafio."""

from __future__ import annotations

from bracis_jusbrasil.database import connect_database, get_database_path


def main() -> None:
    """Executa verificações curtas e não mutáveis no SQLite distribuído."""
    database_path = get_database_path()

    with connect_database(database_path) as connection:
        integrity_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
        total_documentos = connection.execute(
            "SELECT COUNT(*) FROM documentos"
        ).fetchone()[0]
        por_natureza = connection.execute(
            """
            SELECT natureza, COUNT(*) AS total
            FROM documentos
            GROUP BY natureza
            ORDER BY natureza
            """
        ).fetchall()
        acordaos_por_tribunal = connection.execute(
            """
            SELECT tribunal, COUNT(*) AS total
            FROM documentos
            WHERE natureza = 'acordao'
            GROUP BY tribunal
            ORDER BY tribunal
            """
        ).fetchall()
        fts_disponivel = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'documentos_fts'
            """
        ).fetchone()

    print(f"Database: {database_path}")
    print(f"integrity_check: {integrity_check}")
    print()
    print(f"documentos: {total_documentos}")
    for row in por_natureza:
        print(f"{row['natureza']}: {row['total']}")
    print()
    for row in acordaos_por_tribunal:
        print(f"{row['tribunal']}: {row['total']}")
    print()
    print(f"FTS5: {'available' if fts_disponivel else 'unavailable'}")


if __name__ == "__main__":
    main()
