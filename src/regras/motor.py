"""Motor de regras: le o YAML, gera SQL, grava achados priorizados.

Regras `tipo: sql` viram um WHERE. Regras `tipo: python` sao handlers
registrados por decorator - usadas quando a logica precisa de window
function e nao cabe num WHERE simples.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import yaml

from .schema import PESO_SEVERIDADE, CatalogoRegras, Regra

COLUNAS_PADRAO = (
    "id_regra, nome_regra, severidade, categoria, "
    "chave_acesso, n_item, cnpj_emit, vl_base, impacto_rs"
)


class MotorRegras:
    def __init__(self, caminho_yaml: str, con: duckdb.DuckDBPyConnection):
        dados = yaml.safe_load(Path(caminho_yaml).read_text(encoding="utf-8"))
        self.catalogo = CatalogoRegras(regras=dados["regras"])
        self.con = con
        self._handlers: dict[str, callable] = {}

    # ------------------------------------------------------------ handlers
    def registrar(self, nome: str):
        """Decorator: associa um nome de condicao a uma funcao geradora de SQL."""
        def deco(fn):
            self._handlers[nome] = fn
            return fn
        return deco

    # ------------------------------------------------------------ execucao
    @staticmethod
    def _sql_regra(r: Regra, tabela: str) -> str:
        valor = r.campo_valor or "0"
        return f"""
        SELECT '{r.id}'         AS id_regra,
               '{r.nome}'       AS nome_regra,
               '{r.severidade}' AS severidade,
               '{r.categoria}'  AS categoria,
               chave_acesso,
               n_item,
               cnpj_emit,
               CAST({valor} AS DOUBLE)                      AS vl_base,
               CAST({valor} * {r.aliquota_risco} AS DOUBLE) AS impacto_rs
        FROM {tabela}
        WHERE ({r.condicao})
        """

    def executar(self, tabela: str = "fct_item_fiscal",
                 competencia: date | None = None) -> int:
        d = competencia or date.today()
        partes: list[str] = []

        for r in self.catalogo.vigentes(d):
            if r.tipo == "sql":
                partes.append(self._sql_regra(r, tabela))
            else:
                handler = self._handlers.get(r.condicao)
                if handler is None:
                    raise KeyError(
                        f"Regra {r.id}: handler '{r.condicao}' nao registrado")
                partes.append(handler(self.con, tabela, r))

        if not partes:
            self.con.execute("""
                CREATE OR REPLACE TABLE achados (
                    id_regra VARCHAR, nome_regra VARCHAR, severidade VARCHAR,
                    categoria VARCHAR, chave_acesso VARCHAR, n_item BIGINT,
                    cnpj_emit VARCHAR, vl_base DOUBLE, impacto_rs DOUBLE)
            """)
            self._criar_view_priorizada()
            return 0

        self.con.execute(
            "CREATE OR REPLACE TABLE achados AS "
            + "\nUNION ALL\n".join(partes))
        self._criar_view_priorizada()
        return self.con.execute("SELECT COUNT(*) FROM achados").fetchone()[0]

    # ------------------------------------------------------- priorizacao
    def _criar_view_priorizada(self) -> None:
        casos = " ".join(
            f"WHEN '{s}' THEN {p}" for s, p in PESO_SEVERIDADE.items())
        self.con.execute(f"""
            CREATE OR REPLACE VIEW v_achados_priorizados AS
            WITH base AS (
                SELECT *,
                       CASE severidade {casos} ELSE 0.1 END AS peso_sev,
                       COUNT(*) OVER (PARTITION BY cnpj_emit) AS recorrencia
                FROM achados
            )
            SELECT *,
                   ROUND(0.5 * PERCENT_RANK() OVER (ORDER BY impacto_rs)
                       + 0.3 * peso_sev
                       + 0.2 * PERCENT_RANK() OVER (ORDER BY recorrencia), 4) AS score
            FROM base
        """)

    def resumo(self):
        return self.con.sql("""
            SELECT severidade, categoria,
                   COUNT(*)                  AS qtd,
                   COUNT(DISTINCT cnpj_emit) AS clientes,
                   ROUND(SUM(impacto_rs), 2) AS impacto_rs
            FROM achados
            GROUP BY 1, 2
            ORDER BY impacto_rs DESC
        """)


# =====================================================================
# Handlers Python: logica que nao cabe num WHERE
# =====================================================================
def registrar_handlers_padrao(motor: MotorRegras) -> MotorRegras:

    @motor.registrar("duplicidade_chave")
    def _dup_chave(con, tabela, r: Regra) -> str:
        return f"""
        SELECT '{r.id}', '{r.nome}', '{r.severidade}', '{r.categoria}',
               chave_acesso, NULL::BIGINT AS n_item,
               ANY_VALUE(cnpj_emit) AS cnpj_emit,
               0.0::DOUBLE, 0.0::DOUBLE
        FROM silver_cabecalho_raw
        GROUP BY chave_acesso
        HAVING COUNT(*) > 1
        """

    @motor.registrar("gap_numeracao")
    def _gap(con, tabela, r: Regra) -> str:
        return f"""
        SELECT '{r.id}', '{r.nome}', '{r.severidade}', '{r.categoria}',
               chave_acesso, NULL::BIGINT, cnpj_emit, 0.0::DOUBLE, 0.0::DOUBLE
        FROM (
            SELECT chave_acesso, cnpj_emit,
                   numero - LAG(numero) OVER (PARTITION BY cnpj_emit, serie
                                              ORDER BY numero) - 1 AS gap
            FROM silver_cabecalho
        )
        WHERE gap > 0
        """

    @motor.registrar("sem_emissao")
    def _sem_emissao(con, tabela, r: Regra) -> str:
        dias = r.parametros.get("dias", 45)
        return f"""
        SELECT '{r.id}', '{r.nome}', '{r.severidade}', '{r.categoria}',
               NULL::VARCHAR, NULL::BIGINT, cnpj_emit, 0.0::DOUBLE, 0.0::DOUBLE
        FROM (
            SELECT cnpj_emit, MAX(dt_emissao) AS ultima,
                   (SELECT MAX(dt_emissao) FROM silver_cabecalho) AS ref
            FROM silver_cabecalho GROUP BY cnpj_emit
        )
        WHERE DATE_DIFF('day', ultima, ref) > {dias}
        """

    @motor.registrar("preco_outlier")
    def _outlier(con, tabela, r: Regra) -> str:
        minimo = r.parametros.get("min_amostra", 30)
        return f"""
        SELECT '{r.id}', '{r.nome}', '{r.severidade}', '{r.categoria}',
               chave_acesso, n_item, cnpj_emit,
               vl_produto::DOUBLE,
               (vl_produto * {r.aliquota_risco})::DOUBLE
        FROM (
            SELECT *,
                   PERCENT_RANK() OVER (PARTITION BY ncm ORDER BY vl_unit) AS pct,
                   COUNT(*)      OVER (PARTITION BY ncm)                   AS n_ncm
            FROM {tabela}
            WHERE regexp_matches(ncm, '^[0-9]{{8}}$')
        )
        WHERE n_ncm >= {minimo} AND (pct > 0.995 OR pct < 0.005)
        """

    @motor.registrar("total_divergente")
    def _divergente(con, tabela, r: Regra) -> str:
        tol = r.parametros.get("tolerancia", 0.05)
        return f"""
        SELECT '{r.id}', '{r.nome}', '{r.severidade}', '{r.categoria}',
               chave_acesso, NULL::BIGINT, cnpj_emit,
               diferenca::DOUBLE, (diferenca * {r.aliquota_risco})::DOUBLE
        FROM (
            SELECT c.chave_acesso, c.cnpj_emit,
                   ABS(c.valor_total - SUM(i.vl_produto)) AS diferenca
            FROM silver_cabecalho c
            JOIN silver_item i USING (chave_acesso)
            GROUP BY c.chave_acesso, c.cnpj_emit, c.valor_total
        )
        WHERE diferenca > {tol}
        """

    return motor