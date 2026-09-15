from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

EXTRACAO = """
CREATE OR REPLACE TABLE bronze_indexado AS
SELECT
    sha256,
    nome,
    regexp_extract(conteudo, 'Id="NFe(\\d{44})"', 1)                AS chave_acesso,
    CAST(regexp_extract(conteudo, '<nNF>(\\d+)</nNF>', 1) AS INTEGER) AS numero,
    regexp_extract(conteudo, '<CNPJ>(\\d{14})</CNPJ>', 1)            AS cnpj_emit,
    CAST(regexp_extract(conteudo, '<dhEmi>([\\d-]{10})', 1) AS DATE) AS dt_emissao,
    CAST(regexp_extract(conteudo, '<vNF>([\\d.]+)</vNF>', 1) AS DOUBLE) AS valor_total,
    tamanho,
    dh_ingestao,
    conteudo
FROM bronze_arquivo
"""

def exportar(db: str = "fiscal.duckdb",
             destino: str = "data/raw/nfe") -> dict:
    con = duckdb.connect(db)
    con.execute(EXTRACAO)

    falhas = con.execute("""
        SELECT COUNT(*) FROM bronze_indexado
        WHERE chave_acesso IS NULL OR chave_acesso = '' OR dt_emissao IS NULL
    """).fetchone()[0]
    if falhas:
        print(f"aviso: {falhas} arquivos sem chave ou data extraível")

    Path(destino).mkdir(parents=True, exist_ok=True)
    con.execute(f"""
        COPY (
            SELECT *,
                   YEAR(dt_emissao)  AS ano,
                   MONTH(dt_emissao) AS mes
            FROM bronze_indexado
            WHERE dt_emissao IS NOT NULL
        )
        TO '{destino}'
        (FORMAT PARQUET, PARTITION_BY (ano, mes),
         COMPRESSION ZSTD, OVERWRITE_OR_IGNORE)
    """)

    stats= con.execute("""
        SELECT COUNT(*)                       AS linhas,
               COUNT(DISTINCT cnpj_emit)      AS emitentes,
               MIN(dt_emissao)                AS primeira,
               MAX(dt_emissao)                AS ultima,
               ROUND(SUM(valor_total), 2)     AS soma_valores
        FROM bronze_indexado
    """).fetchone()
    con.close()

    return {"linhas": stats[0], "emitentes": stats[1],
            "primeira": stats[2], "ultima": stats[3], "soma": stats[4],
            "particoes": len(list(Path(destino).rglob("*parquet")))}

def tamanho_pasta(p: str | Path) -> int:
    return sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file())

def comparar(origem: str = "data/raw/nfe",
    parquet: str = "data/bronze/nfe",
    db: str = "fiscal.duckdb") -> None:

    import time

    xml_bytes = tamanho_pasta(origem)
    pq_bytes = tamanho_pasta(parquet)

    print("\n--- Espaço em disco ---")
    print(f"XML bruto: {xml_bytes/1024:>10.1f} KB")
    print(f"Parquet:    {pq_bytes/1024:>10.1f} KB")
    print(f"Redução:    {(1 - pq_bytes/xml_bytes)*100:>10.1f} %")

    con = duckdb.connect(db, read_only=True)

    t0 = time.perf_counter()
    con.execute("""
        SELECT COUNT(*) FROM bronze_arquivo
        WHERE conteudo LIKE '%<CFOP>6102</CFOP>%'
    """).fetchone()
    t_scan = time.perf_counter() - t0

    t0 = time.perf_counter()
    con.execute(f"""
        SELECT cnpj_emit, SUM(valor_total)
        FROM read_parquet('{parquet}/**/*.parquet')
        WHERE ano = 2026 AND mes = 3
        GROUP BY 1
    """).fetchall()
    t_colunar = time.perf_counter() - t0
    con.close()

    print("\n--- Velocidade ---")
    print(f"Varredura de texto no XML:        {t_scan*1000:>8.1f} ms")
    print(f"Agregação em Parquet particionado:{t_colunar*1000:>8.1f} ms")
    print("\nAbaixo de ~1.000 notas o Parquet pode sair na frente ou atrás:")
    print("o custo de abrir os arquivos domina e os tempos são ruído.")
    print("O ganho real aparece com volume — a varredura de texto cresce")
    print("linear com o tamanho total, a leitura colunar só com as colunas")
    print("e partições que você pediu.")

def main() -> None:
    p = argparse.ArgumentParser(description="Exporta bronze para Parquet.")
    p.add_argument("--db", default="fiscal.duckdb")
    p.add_argument("--destino", default="data/bronze/nfe")
    p.add_argument("--origem", default="data/raw/nfe")
    p.add_argument("--comparar", action="store_true",
                   help="mostra ganho de espaço e velocidade")

    args = p.parse_args()

    r = exportar(args.db, args.destino)
    print(f"linhas exportadas: {r['linhas']}")
    print(f"emitentes:         {r['emitentes']}")
    print(f"período:           {r['primeira']} a {r['ultima']}")
    print(f"soma dos valores:  R$ {r['soma']:,.2f}")
    print(f"arquivos Parquet:  {r['particoes']}")

    if args.comparar:
        comparar(args.origem, args.destino, args.db)

if __name__ == "__main__":
    main()
