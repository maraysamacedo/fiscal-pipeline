from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import duckdb

DDL = """
CREATE TABLE IF NOT EXISTS bronze_arquivo (
    sha256      VARCHAR PRIMARY KEY,
    nome        VARCHAR,
    conteudo    VARCHAR,
    tamanho     BIGINT,
    dh_ingestao TIMESTAMP
)
"""

def hash_arquivo(caminho: Path) -> str:
    """SHA-256 do conteúdo. Dois arquivos com nomes diferentes e o mesmo
    conteúdo produzem o mesmo hash — é assim que a duplicata é detectada."""
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()

def ingerir_bronze(pasta: str = "data/raw/nfe",
                   db: str = "fiscal.duckdb") -> dict[str, int]:
    """Carrega XMLs para bronze. Idempotente: rodar N vezes = rodar 1 vez."""
    caminho = Path(pasta)
    if not caminho.exists():
        raise FileNotFoundError(
            f"Pasta{pasta} não existe. Rode primeiro: python -m src.gerar_massa")

    con = duckdb.connect(db)
    con.execute(DDL)

    # carrega os hashes já existentes de uma vez, em vez de consultar por arquivo
    existentes = {r[0] for r in con.execute(
        "SELECT sha256 FROM bronze_arquivo").fetchall()}

    novos: list[tuple] = []
    repetidos = 0
    agora = datetime.now(timezone.utc)

    for arq in sorted(caminho.rglob("*.xml")):
        h = hash_arquivo(arq)
        if h in existentes:
            repetidos += 1
            continue
        existentes.add(h)
        novos.append((h, arq.name, arq.read_text(encoding="utf-8"),
                      arq.stat().st_size, agora))

    if novos:
        con.executemany(
            "INSERT INTO bronze_arquivo VALUES (?, ?, ?, ?, ?)", novos)

    total = con.execute("SELECT COUNT(*) FROM bronze_arquivo").fetchone()[0]
    con.close

    return {"novos": len(novos), "repetidos": repetidos, "total_bronze": total}

def main() -> None:
    p = argparse.ArgumentParser(description="Ingere XMLs na camada bronze.")
    p.add_argument("--pasta", default="data/raw/nfe")
    p.add_argument("--db", default="fiscal.duckdb")
    args = p.parse_args()

    r = ingerir_bronze(args.pasta, args.db)
    print(f"novos:      {r['novos']:>6}")
    print(f"já existiam: {r['repetidos']:>6}   (dedup por hash)")
    print(f"total bronze: {r['total_bronze']:>5}")


if __name__ == "__main__":
    main()