# BRACIS Jusbrasil

Bootstrap para o desafio Dados do Caça-Alucinações, da BRACIS 2026. O projeto
começa pela exploração reproduzível e exclusivamente read-only do banco
canônico distribuído com o desafio.

## Estrutura

- `src/`: código Python reutilizável.
- `notebooks/`: exploração e experimentos.
- `scripts/`: entrypoints utilitários e reproduzíveis.
- `data/`: referência documentada à localização dos dados do desafio.
- `docs/`: documentação e decisões do projeto.

## Bootstrap

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Jupyter

```bash
jupyter lab
```

## Sanity check

```bash
python scripts/inspect_database.py
```
