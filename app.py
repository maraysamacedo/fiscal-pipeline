from __future__ import annotations

import altair as alt
import duckdb
import polars as pl
import streamlit as st

DB = "fiscal.duckdb"

st.set_page_config(page_title="Saude Fiscal", layout="wide",
                   initial_sidebar_state="collapsed")


@st.cache_resource
def conexao():
    """cache_resource: objetos que nao devem ser copiados (conexoes)."""
    return duckdb.connect(DB, read_only=True)


@st.cache_data(ttl=300)
def consultar(query: str) -> pl.DataFrame:
    """cache_data: resultados, cacheados por 5 minutos."""
    return conexao().sql(query).pl()


def existe(tabela: str) -> bool:
    try:
        conexao().sql(f"SELECT 1 FROM {tabela} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


st.title("Painel de Saude Fiscal")

st.caption("Inconsistencias em documentos fiscais eletronicos, "
           "priorizadas por impacto financeiro estimado")

if not existe("achados"):
    st.error("Tabela `achados` nao encontrada. Rode: `make auditar`")
    st.stop()

# ------------------------------------------------------------- filtros
col_f1, col_f2 = st.columns([2, 3])
severidades = col_f1.multiselect(
    "Severidade", ["CRITICA", "ALTA", "MEDIA", "BAIXA"],
    default=["CRITICA", "ALTA"])
if not severidades:
    st.warning("Selecione ao menos uma severidade.")
    st.stop()
filtro = "','".join(severidades)

# ------------------------------------------------ NIVEL 1: executivo
kpi = consultar(f"""
    SELECT COUNT(*)                       AS achados,
           COUNT(DISTINCT cnpj_emit)      AS clientes,
           COALESCE(SUM(impacto_rs), 0)   AS risco,
           COUNT(DISTINCT id_regra)       AS regras
    FROM achados WHERE severidade IN ('{filtro}')
""").row(0, named=True)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Achados", f"{kpi['achados']:,}".replace(",", "."))
c2.metric("Clientes afetados", kpi["clientes"])
c3.metric("Risco estimado", f"R$ {kpi['risco']:,.2f}",
          help="Estimativa para priorizacao: valor x aliquota de risco "
               "da regra. Nao e apuracao fiscal.")
c4.metric("Regras disparadas", kpi["regras"])

st.divider()

# ---------------------------------------------- NIVEL 2: operacional
col_a, col_b = st.columns(2)

with col_a:
    st.subheader("Regras por impacto")
    df = consultar(f"""
        SELECT nome_regra, severidade, COUNT(*) AS qtd,
               ROUND(SUM(impacto_rs), 2) AS impacto
        FROM achados WHERE severidade IN ('{filtro}')
        GROUP BY 1, 2 ORDER BY impacto DESC LIMIT 10
    """)
    if df.height:
        df = df.with_columns(
            pl.col("nome_regra").str.slice(0, 34).alias("regra_curta"))
        st.altair_chart(
            alt.Chart(df.to_pandas()).mark_bar().encode(
                x=alt.X("impacto:Q", title="Impacto estimado (R$)",
                        axis=alt.Axis(format=",.0f")),
                y=alt.Y("regra_curta:N", sort="-x", title=None),
                color=alt.Color("severidade:N", legend=None),
                tooltip=["nome_regra", "qtd", "impacto"],
            ).properties(height=260),
            use_container_width=True)

with col_b:
    st.subheader("Clientes por risco")
    df_cli = consultar(f"""
        SELECT cnpj_emit, COUNT(*) AS achados,
               ROUND(SUM(impacto_rs), 2) AS risco
        FROM achados WHERE severidade IN ('{filtro}')
        GROUP BY 1 ORDER BY risco DESC LIMIT 10
    """)
    st.dataframe(
        df_cli, use_container_width=True, hide_index=True,
        column_config={
            "cnpj_emit": st.column_config.TextColumn("CNPJ", width="medium"),
            "achados": st.column_config.NumberColumn("Achados", width="small"),
            "risco": st.column_config.NumberColumn(
                "Risco (R$)", format="R$ %.2f", width="medium"),
        })

st.divider()

# ------------------------------------------------- NIVEL 3: detalhe
st.subheader("Detalhe por cliente")
clientes = consultar(
    "SELECT DISTINCT cnpj_emit FROM achados ORDER BY 1")["cnpj_emit"].to_list()
escolhido = st.selectbox("Cliente", clientes)

st.dataframe(consultar(f"""
    SELECT id_regra, nome_regra, severidade, chave_acesso, n_item,
           ROUND(impacto_rs, 2) AS impacto, ROUND(score, 4) AS score
    FROM v_achados_priorizados
    WHERE cnpj_emit = '{escolhido}'
    ORDER BY score DESC LIMIT 300
"""), use_container_width=True, hide_index=True)

# -------------------------------------------- Saude do pipeline
if existe("pipeline_metricas"):
    st.divider()
    st.subheader("Saude do pipeline")
    met = consultar("""
        SELECT etapa, ROUND(AVG(duracao_s), 3) AS media_s,
               ROUND(MAX(duracao_s), 3) AS max_s, COUNT(*) AS execucoes,
               SUM(CASE WHEN status = 'ERRO' THEN 1 ELSE 0 END) AS erros
        FROM pipeline_metricas GROUP BY 1 ORDER BY media_s DESC
    """)
    st.dataframe(met, use_container_width=True, hide_index=True)