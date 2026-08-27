---
title: Scripts
description: Scripts utilitários e reproduzíveis do bootstrap.
---

# Scripts

## inspect_database.py

`inspect_database.py` executa um sanity check curto no SQLite canônico.

```bash
python scripts/inspect_database.py
```

A saída informa a verificação de integridade, o total de documentos, as
contagens por natureza, os acórdãos por tribunal e a presença da FTS5. O script
usa a conexão read-only do pacote e não altera o banco.
