from __future__ import annotations

import sys
from pathlib import Path

import sqlglot
import yaml

from .schema import CatalogoRegras

COLUNAS_PERMITIDAS = {
    "chave_acesso", "n_item", "c_prod", "descricao", "ncm", "cfop",
    "qtd", "vl_unit", "vl_produto", "vl_icms", "cst_icms", "csosn",
    "cnpj_emit", "uf_emit", "uf_dest", "tipo_nf", "status", "is_simples",
    "dt_emissao", "competencia", "serie", "numero", "valor_total",
    "grupo_cfop", "cod_tributacao",
}

PROIBIDOS = {"DROP", "DELETE", "INSERT", "UPDATE", "ATTACH",
             "COPY", "CREATE", "ALTER", "PRAGMA", "INSTALL"}


def validar_condicao(condicao: str, regra_id: str) -> list[str]:
    erros: list[str] = []
    upper = condicao.upper()

    for termo in PROIBIDOS:
        if f" {termo} " in f" {upper} " or upper.startswith(termo):
            erros.append(f"{regra_id}: comando proibido '{termo}'")

    try:
        expr = sqlglot.parse_one(f"SELECT 1 WHERE {condicao}", dialect="duckdb")
    except Exception as e:
        return erros + [f"{regra_id}: SQL invalido - {e}"]

    usadas = {c.name for c in expr.find_all(sqlglot.exp.Column)}
    desconhecidas = usadas - COLUNAS_PERMITIDAS
    if desconhecidas:
        erros.append(f"{regra_id}: colunas desconhecidas {sorted(desconhecidas)}")
    return erros


def validar_arquivo(caminho: str) -> list[str]:
    dados = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
    catalogo = CatalogoRegras(regras=dados["regras"])   # valida o schema

    erros: list[str] = []
    vistos: set[str] = set()

    for r in catalogo.regras:
        if r.id in vistos:
            erros.append(f"{r.id}: id duplicado")
        vistos.add(r.id)

        if r.tipo == "sql":
            erros += validar_condicao(r.condicao, r.id)
        if r.campo_valor and r.campo_valor not in COLUNAS_PERMITIDAS:
            erros.append(f"{r.id}: campo_valor desconhecido '{r.campo_valor}'")
        if r.aliquota_risco > 0 and not r.campo_valor:
            erros.append(f"{r.id}: aliquota_risco sem campo_valor")
        if r.vigencia_fim and r.vigencia_fim < r.vigencia_inicio:
            erros.append(f"{r.id}: vigencia_fim anterior ao inicio")

    return erros


def main() -> None:
    caminho = sys.argv[1] if len(sys.argv) > 1 else "regras.yaml"
    erros = validar_arquivo(caminho)
    if erros:
        print(f"{len(erros)} problema(s) em {caminho}:")
        for e in erros:
            print(f"  - {e}")
        sys.exit(1)
    print(f"{caminho}: todas as regras validas")


if __name__ == "__main__":
    main()