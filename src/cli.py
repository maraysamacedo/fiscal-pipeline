from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from .lote import processar_pasta
from .obs import etapa


def cmd_processar(args) -> None:
    with etapa("parse_lote", pasta=args.pasta, workers=args.workers):
        res = processar_pasta(args.pasta, args.workers)

    df_cab = pl.DataFrame(res.cabecalhos)
    df_item = pl.DataFrame(res.itens)

    con = duckdb.connect(args.db)
    con.register("df_cab", df_cab)
    con.register("df_item", df_item)

    con.execute("CREATE OR REPLACE TABLE silver_cabecalho_raw AS SELECT * FROM df_cab")
    con.execute("CREATE OR REPLACE TABLE silver_item_raw AS SELECT * FROM df_item")

    con.execute("""
        CREATE OR REPLACE TABLE silver_cabecalho AS
        SELECT * EXCLUDE (dh_emissao),
               CAST(dh_emissao AS TIMESTAMP)  AS dh_emissao,
               CAST(dh_emissao AS DATE)       AS dt_emissao,
               DATE_TRUNC('month', CAST(dh_emissao AS DATE)) AS competencia,
               crt IN ('1','2')               AS is_simples
        FROM df_cab
        QUALIFY ROW_NUMBER() OVER (PARTITION BY chave_acesso ORDER BY numero) = 1
    """)
    con.execute("""
        CREATE OR REPLACE TABLE silver_item AS
        SELECT * FROM df_item
        QUALIFY ROW_NUMBER() OVER (PARTITION BY chave_acesso, n_item
                                   ORDER BY vl_produto DESC) = 1
    """)
    con.execute("""
        CREATE OR REPLACE TABLE fct_item_fiscal AS
        SELECT i.*, c.cnpj_emit, c.uf_emit, c.uf_dest, c.tipo_nf, c.status,
               c.is_simples, c.dt_emissao, c.competencia, c.serie, c.numero,
               c.valor_total, LEFT(i.cfop, 1) AS grupo_cfop,
               COALESCE(i.csosn, i.cst_icms) AS cod_tributacao
        FROM silver_item i
        JOIN silver_cabecalho c USING (chave_acesso)
    """)

    if res.falhas:
        Path("data").mkdir(exist_ok=True)
        Path("data/quarentena.json").write_text(
            json.dumps(res.falhas, indent=2, ensure_ascii=False), encoding="utf-8")

    con.close()
    print(f"notas:  {len(res.cabecalhos):>6}")
    print(f"itens:  {len(res.itens):>6}")
    print(f"falhas: {len(res.falhas):>6}"
          + ("  -> data/quarentena.json" if res.falhas else ""))


def cmd_auditar(args) -> None:
    from .regras.motor import MotorRegras, registrar_handlers_padrao

    con = duckdb.connect(args.db)
    motor = MotorRegras(args.regras, con)
    registrar_handlers_padrao(motor)

    comp = date.fromisoformat(args.competencia + "-01") if args.competencia else date.today()
    with etapa("motor_regras", competencia=str(comp)):
        motor.executar(competencia=comp)

    print("\n--- Resumo por severidade ---")
    print(motor.resumo())

    ordem = {"CRITICA": 0, "ALTA": 1, "MEDIA": 2, "BAIXA": 3}
    limite = ordem.get(args.min_severidade.upper(), 3)
    permitidas = [s for s, v in ordem.items() if v <= limite]
    lista = "','".join(permitidas)

    print(f"\n--- Top {args.top} achados (severidade >= {args.min_severidade}) ---")
    print(con.sql(f"""
        SELECT id_regra, nome_regra, severidade, cnpj_emit,
               ROUND(impacto_rs, 2) AS impacto_rs, ROUND(score, 4) AS score
        FROM v_achados_priorizados
        WHERE severidade IN ('{lista}')
        ORDER BY score DESC LIMIT {args.top}
    """))
    con.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="fiscal", description="Pipeline fiscal.")
    p.add_argument("--db", default="fiscal.duckdb")
    sub = p.add_subparsers(dest="comando", required=True)

    pp = sub.add_parser("processar", help="XML -> silver -> fato")
    pp.add_argument("pasta", nargs="?", default="data/raw/nfe")
    pp.add_argument("--workers", type=int, default=8)
    pp.set_defaults(func=cmd_processar)

    pa = sub.add_parser("auditar", help="roda o motor de regras")
    pa.add_argument("--regras", default="regras.yaml")
    pa.add_argument("--competencia", default=None, help="AAAA-MM")
    pa.add_argument("--min-severidade", default="BAIXA")
    pa.add_argument("--top", type=int, default=20)
    pa.set_defaults(func=cmd_auditar)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()