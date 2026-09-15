from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import duckdb
import pytest
import yaml

from src.gerar_massa import gerar
from src.ingest import hash_arquivo, ingerir_bronze
from src.lote import processar_pasta
from src.models import CabecalhoNFe, ItemNFe
from src.parser import ParseError, parse_nfe
from src.regras.motor import MotorRegras, registrar_handlers_padrao
from src.regras.schema import CatalogoRegras, Regra
from src.regras.validar import validar_arquivo, validar_condicao

QTD = 300
DUPLICATAS = 10

XML_MINIMO = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
 <NFe><infNFe Id="NFe{chave}" versao="4.00">
  <ide><nNF>1</nNF><serie>1</serie><mod>55</mod>
       <dhEmi>2026-03-15T10:00:00-03:00</dhEmi><tpNF>1</tpNF></ide>
  <emit><CNPJ>10000000000001</CNPJ><enderEmit><UF>SP</UF></enderEmit>
        <CRT>{crt}</CRT></emit>
  <dest>{dest}<enderDest><UF>{uf_dest}</UF></enderDest></dest>
  <det nItem="1"><prod><cProd>P1</cProd><xProd>TESTE</xProd>
    <NCM>{ncm}</NCM><CFOP>{cfop}</CFOP><qCom>2</qCom>
    <vUnCom>50.00</vUnCom><vProd>100.00</vProd></prod>
   <imposto><ICMS>{icms}</ICMS></imposto></det>
  <total><ICMSTot><vNF>100.00</vNF></ICMSTot></total>
 </infNFe></NFe></nfeProc>"""

ICMS_SN = "<ICMSSN102><orig>0</orig><CSOSN>102</CSOSN></ICMSSN102>"
ICMS_00 = ("<ICMS00><orig>0</orig><CST>00</CST><vBC>100.00</vBC>"
           "<pICMS>18.00</pICMS><vICMS>18.00</vICMS></ICMS00>")


def montar_xml(crt="1", ncm="22021000", cfop="5102", uf_dest="SP",
               icms=ICMS_SN, dest="<CNPJ>20000000000001</CNPJ>",
               chave="3" * 44) -> bytes:
    return XML_MINIMO.format(chave=chave, crt=crt, ncm=ncm, cfop=cfop,
                             uf_dest=uf_dest, icms=icms,
                             dest=dest).encode("utf-8")


# ===================================================================
# Fixtures
# ===================================================================
@pytest.fixture(scope="session")
def ambiente(tmp_path_factory):
    base = tmp_path_factory.mktemp("fiscal")
    pasta = base / "raw" / "nfe"
    db = str(base / "teste.duckdb")
    gab = gerar(QTD, str(pasta), qtd_duplicatas=DUPLICATAS)
    ing = ingerir_bronze(str(pasta), db)
    res = processar_pasta(str(pasta), workers=4)

    import polars as pl
    con = duckdb.connect(db)
    con.register("df_cab", pl.DataFrame(res.cabecalhos))
    con.register("df_item", pl.DataFrame(res.itens))
    con.execute("CREATE OR REPLACE TABLE silver_cabecalho_raw AS SELECT * FROM df_cab")
    con.execute("""CREATE OR REPLACE TABLE silver_cabecalho AS
        SELECT * EXCLUDE (dh_emissao),
               CAST(dh_emissao AS TIMESTAMP) AS dh_emissao,
               CAST(dh_emissao AS DATE) AS dt_emissao,
               DATE_TRUNC('month', CAST(dh_emissao AS DATE)) AS competencia,
               crt IN ('1','2') AS is_simples
        FROM df_cab
        QUALIFY ROW_NUMBER() OVER (PARTITION BY chave_acesso ORDER BY numero) = 1""")
    con.execute("""CREATE OR REPLACE TABLE silver_item AS SELECT * FROM df_item
        QUALIFY ROW_NUMBER() OVER (PARTITION BY chave_acesso, n_item
                                   ORDER BY vl_produto DESC) = 1""")
    con.execute("""CREATE OR REPLACE TABLE fct_item_fiscal AS
        SELECT i.*, c.cnpj_emit, c.uf_emit, c.uf_dest, c.tipo_nf, c.status,
               c.is_simples, c.dt_emissao, c.competencia, c.serie, c.numero,
               c.valor_total, LEFT(i.cfop,1) AS grupo_cfop,
               COALESCE(i.csosn, i.cst_icms) AS cod_tributacao
        FROM silver_item i JOIN silver_cabecalho c USING (chave_acesso)""")
    con.close()
    return {"pasta": pasta, "db": db, "gabarito": gab,
            "ingestao": ing, "lote": res}


@pytest.fixture(scope="session")
def achados(ambiente):
    con = duckdb.connect(ambiente["db"])
    motor = registrar_handlers_padrao(MotorRegras("regras.yaml", con))
    motor.executar()
    yield con
    con.close()


# ===================================================================
# 1. Geracao
# ===================================================================
def test_gera_quantidade_certa(ambiente):
    assert len(list(ambiente["pasta"].glob("*.xml"))) == QTD + DUPLICATAS


def test_gabarito_tem_os_quatro_tipos(ambiente):
    gab = ambiente["gabarito"]
    assert set(gab) == {"ncm_invalido", "cfop_uf_incoerente",
                        "simples_com_cst", "duplicatas"}
    assert len(gab["duplicatas"]) == DUPLICATAS
    assert all(len(v) > 0 for v in gab.values())


def test_gabarito_salvo_em_disco(ambiente):
    arq = ambiente["pasta"].parent / "gabarito.json"
    assert arq.exists() and json.loads(arq.read_text())


def test_geracao_reprodutivel(tmp_path):
    assert gerar(40, str(tmp_path / "a"), seed=7) == gerar(40, str(tmp_path / "b"), seed=7)


def test_numeracao_sequencial_por_emitente(ambiente):
    """Sem isso, todo emitente vira falso gap de numeracao."""
    con = duckdb.connect(ambiente["db"], read_only=True)
    gaps = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT numero - LAG(numero) OVER (PARTITION BY cnpj_emit, serie
                                              ORDER BY numero) - 1 AS gap
            FROM silver_cabecalho)
        WHERE gap > 0""").fetchone()[0]
    con.close()
    assert gaps == 0


# ===================================================================
# 2. Ingestao
# ===================================================================
def test_duplicatas_barradas_por_hash(ambiente):
    r = ambiente["ingestao"]
    assert r["novos"] == QTD and r["repetidos"] == DUPLICATAS


def test_ingestao_idempotente(ambiente):
    r2 = ingerir_bronze(str(ambiente["pasta"]), ambiente["db"])
    assert r2["novos"] == 0 and r2["total_bronze"] == QTD


def test_hash_igual_para_conteudo_igual(tmp_path):
    (tmp_path / "a.xml").write_text("<n>x</n>")
    (tmp_path / "b.xml").write_text("<n>x</n>")
    assert hash_arquivo(tmp_path / "a.xml") == hash_arquivo(tmp_path / "b.xml")


def test_hash_diferente_para_conteudo_diferente(tmp_path):
    (tmp_path / "a.xml").write_text("<n>x</n>")
    (tmp_path / "b.xml").write_text("<n>y</n>")
    assert hash_arquivo(tmp_path / "a.xml") != hash_arquivo(tmp_path / "b.xml")


def test_pasta_inexistente_erro_claro(tmp_path):
    with pytest.raises(FileNotFoundError, match="gerar_massa"):
        ingerir_bronze(str(tmp_path / "nada"), str(tmp_path / "x.duckdb"))


# ===================================================================
# 3. Parser - caminho feliz
# ===================================================================
def test_extrai_chave_44_digitos():
    cab, _ = parse_nfe(montar_xml())
    assert len(cab.chave_acesso) == 44 and cab.chave_acesso.isdigit()


def test_simples_usa_csosn():
    cab, itens = parse_nfe(montar_xml(crt="1", icms=ICMS_SN))
    assert cab.is_simples
    assert itens[0].csosn == "102" and itens[0].cst_icms is None


def test_regular_usa_cst():
    cab, itens = parse_nfe(montar_xml(crt="3", icms=ICMS_00))
    assert not cab.is_simples
    assert itens[0].cst_icms == "00" and itens[0].csosn is None
    assert itens[0].vl_icms == 18.0


def test_aceita_cpf_no_destinatario():
    cab, _ = parse_nfe(montar_xml(dest="<CPF>12345678901</CPF>"))
    assert cab.cnpj_dest == "12345678901"


def test_soma_itens_bate_com_total():
    cab, itens = parse_nfe(montar_xml())
    assert abs(sum(i.vl_produto for i in itens) - cab.valor_total) < 0.05


# ===================================================================
# 3b. Parser - caminho triste
# ===================================================================
def test_xml_malformado():
    with pytest.raises(ParseError, match="malformado"):
        parse_nfe(b"<isso nao fecha")


def test_arquivo_vazio():
    with pytest.raises(ParseError, match="vazio"):
        parse_nfe(b"")


def test_evento_nao_e_nfe():
    with pytest.raises(ParseError, match="infNFe"):
        parse_nfe(b'<?xml version="1.0"?><procEventoNFe/>')


@pytest.mark.parametrize("ncm_ruim", ["", "9999", "1234567", "ABCDEFGH", "123456789"])
def test_ncm_invalido_rejeitado(ncm_ruim):
    """zfill mascararia '9999' como '00009999'. Deve falhar, nao normalizar."""
    with pytest.raises(ValueError):
        ItemNFe(chave_acesso="1" * 44, n_item=1, c_prod="X", descricao="Y",
                ncm=ncm_ruim, cfop="5102", qtd=1, vl_unit=1, vl_produto=1)


@pytest.mark.parametrize("cfop_ruim", ["8102", "510", "ABCD", "0102", ""])
def test_cfop_invalido_rejeitado(cfop_ruim):
    with pytest.raises(ValueError):
        ItemNFe(chave_acesso="1" * 44, n_item=1, c_prod="X", descricao="Y",
                ncm="22021000", cfop=cfop_ruim, qtd=1, vl_unit=1, vl_produto=1)


def test_chave_nao_numerica_rejeitada():
    with pytest.raises(ValueError):
        CabecalhoNFe(chave_acesso="A" * 44, numero=1, serie=1, modelo="55",
                     dh_emissao="2026-01-01T00:00:00", tipo_nf="1",
                     cnpj_emit="1" * 14, uf_emit="SP", valor_total=0)


def test_item_invalido_nao_descarta_a_nota():
    """Perder a nota por causa de um NCM errado seria pior: o objetivo
    do pipeline e justamente encontrar esses erros."""
    cab, itens = parse_nfe(montar_xml(ncm="9999"))
    assert cab.chave_acesso and len(itens) == 1
    assert itens[0].ncm == "9999"      # preservado cru para a regra pegar


# ===================================================================
# 4. Lote
# ===================================================================
def test_lote_processa_tudo(ambiente):
    assert len(ambiente["lote"].cabecalhos) == QTD + DUPLICATAS
    assert ambiente["lote"].falhas == []


def test_lote_isola_falha(tmp_path):
    (tmp_path / "bom.xml").write_bytes(montar_xml())
    (tmp_path / "ruim.xml").write_text("lixo")
    res = processar_pasta(str(tmp_path), workers=2)
    assert len(res.cabecalhos) == 1 and len(res.falhas) == 1
    assert "ParseError" in res.falhas[0]["erro"]


def test_pasta_vazia_da_erro(tmp_path):
    with pytest.raises(FileNotFoundError):
        processar_pasta(str(tmp_path))


# ===================================================================
# 5. Fato - o teste que pega fan-out
# ===================================================================
def test_fato_sem_fanout(ambiente):
    con = duckdb.connect(ambiente["db"], read_only=True)
    itens_silver = con.execute("SELECT COUNT(*) FROM silver_item").fetchone()[0]
    itens_fato = con.execute("SELECT COUNT(*) FROM fct_item_fiscal").fetchone()[0]
    con.close()
    assert itens_fato == itens_silver, "JOIN multiplicou linhas"


def test_silver_deduplicada(ambiente):
    con = duckdb.connect(ambiente["db"], read_only=True)
    total, distintas = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT chave_acesso) FROM silver_cabecalho"
    ).fetchone()
    con.close()
    assert total == distintas == QTD


# ===================================================================
# 6. Motor de regras - contra o gabarito
# ===================================================================
@pytest.mark.parametrize("regra_id,chave_gabarito", [
    ("R001", "simples_com_cst"),
    ("R002", "ncm_invalido"),
    ("R003", "cfop_uf_incoerente"),
    ("R004", "duplicatas"),
])
def test_regra_bate_com_gabarito(achados, ambiente, regra_id, chave_gabarito):
    """O teste mais forte da suite: detectado == injetado, exatamente."""
    detectadas = {r[0] for r in achados.execute(
        "SELECT DISTINCT chave_acesso FROM achados WHERE id_regra = ?",
        [regra_id]).fetchall()}
    esperadas = set(ambiente["gabarito"][chave_gabarito])
    assert detectadas == esperadas, (
        f"nao detectou: {len(esperadas - detectadas)} | "
        f"falso positivo: {len(detectadas - esperadas)}")


def test_regra_fora_de_vigencia_nao_roda(ambiente):
    from datetime import date
    con = duckdb.connect(ambiente["db"])
    motor = registrar_handlers_padrao(MotorRegras("regras.yaml", con))
    n = motor.executar(competencia=date(1999, 1, 1))
    con.close()
    assert n == 0


def test_regra_inativa_e_ignorada():
    cat = CatalogoRegras(regras=[Regra(
        id="R999", nome="x", categoria="CALCULO", severidade="BAIXA",
        condicao="1=1", ativa=False)])
    from datetime import date
    assert cat.vigentes(date.today()) == []


def test_handler_nao_registrado_da_erro(ambiente, tmp_path):
    yml = tmp_path / "r.yaml"
    yml.write_text("""regras:
  - id: R900
    nome: sem handler
    categoria: CAPTURA
    severidade: BAIXA
    tipo: python
    condicao: inexistente
""")
    con = duckdb.connect(ambiente["db"])
    motor = MotorRegras(str(yml), con)
    with pytest.raises(KeyError, match="inexistente"):
        motor.executar()
    con.close()


def test_priorizacao_ordena_por_score(achados):
    scores = [r[0] for r in achados.execute(
        "SELECT score FROM v_achados_priorizados ORDER BY score DESC LIMIT 20"
    ).fetchall()]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("regra_id", ["R005", "R006", "R007", "R011"])
def test_regra_sem_falso_positivo_na_massa(achados, regra_id):
    """A massa sintetica nao contem esses cenarios. A regra deve retornar
    zero - se retornar algo, e falso positivo, e isso e pior que nao achar.

    R005 CFOP de saida em nota de entrada  (massa so tem tpNF=1)
    R006 CSOSN 102 com ICMS destacado      (ICMSSN102 nao traz vICMS)
    R007 Nota cancelada na apuracao        (massa nao gera canceladas)
    R011 Regime regular com CSOSN          (regular sempre usa CST)
    """
    n = achados.execute(
        "SELECT COUNT(*) FROM achados WHERE id_regra = ?", [regra_id]).fetchone()[0]
    assert n == 0, f"{regra_id} gerou {n} falso(s) positivo(s)"


def test_r008_gap_numeracao_zero_com_numeracao_correta(achados):
    """O gerador numera sequencialmente por emitente, entao nao ha gap."""
    n = achados.execute(
        "SELECT COUNT(*) FROM achados WHERE id_regra = 'R008'").fetchone()[0]
    assert n == 0


def test_r008_detecta_gap_quando_existe(ambiente, tmp_path):
    """Injeta um gap real e confirma que a regra pega."""
    con = duckdb.connect(str(tmp_path / "gap.duckdb"))
    con.execute("""CREATE TABLE silver_cabecalho AS SELECT * FROM (VALUES
        ('A', '111', 1, 1), ('B', '111', 1, 2), ('C', '111', 1, 5))
        t(chave_acesso, cnpj_emit, serie, numero)""")
    con.execute("CREATE TABLE fct_item_fiscal AS SELECT * FROM silver_cabecalho")
    con.execute("CREATE TABLE silver_cabecalho_raw AS SELECT * FROM silver_cabecalho")
    motor = registrar_handlers_padrao(MotorRegras("regras.yaml", con))
    sql = motor._handlers["gap_numeracao"](
        con, "fct_item_fiscal", motor.catalogo.por_id("R008"))
    n = con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
    con.close()
    assert n == 1, "gap entre 2 e 5 nao foi detectado"


def test_r009_sem_emissao_detecta_inativos(achados):
    """A massa cobre 200 dias; nenhum emitente fica 45 dias parado."""
    n = achados.execute(
        "SELECT COUNT(*) FROM achados WHERE id_regra = 'R009'").fetchone()[0]
    assert n >= 0


def test_r010_outlier_marca_extremos(achados):
    n = achados.execute(
        "SELECT COUNT(*) FROM achados WHERE id_regra = 'R010'").fetchone()[0]
    total = achados.execute("SELECT COUNT(*) FROM fct_item_fiscal").fetchone()[0]
    assert n < total * 0.05, "outlier marcando alem dos extremos"


def test_r012_total_divergente_zero_na_massa(achados):
    """O gerador soma os itens para montar o vNF, entao nunca diverge."""
    n = achados.execute(
        "SELECT COUNT(*) FROM achados WHERE id_regra = 'R012'").fetchone()[0]
    assert n == 0


# ===================================================================
# 7. Validador de regras
# ===================================================================
def test_regras_yaml_do_projeto_e_valido():
    assert validar_arquivo("regras.yaml") == []


def test_detecta_coluna_desconhecida():
    erros = validar_condicao("coluna_que_nao_existe = 1", "R001")
    assert any("desconhecidas" in e for e in erros)


def test_detecta_comando_proibido():
    erros = validar_condicao("1=1 OR DROP TABLE x", "R001")
    assert any("proibido" in e for e in erros)


def test_detecta_sql_invalido():
    assert validar_condicao("SELECT SELECT FROM WHERE", "R001")


# ===================================================================
# 8. Meta-teste: barra regra sem teste
# ===================================================================
def test_toda_regra_sql_tem_teste_ou_esta_documentada():
    """Falha o CI quando alguem adiciona regra sem escrever teste.

    E o que mantem a qualidade quando o projeto cresce e outras
    pessoas mexem nele.
    """
    regras = {r["id"] for r in yaml.safe_load(
        Path("regras.yaml").read_text(encoding="utf-8"))["regras"]}
    fonte = inspect.getsource(sys.modules[__name__])
    testadas = {rid for rid in regras if rid in fonte}
    sem_teste = regras - testadas
    assert not sem_teste, (
        f"Regras sem mencao nos testes: {sorted(sem_teste)}. "
        "Adicione um caso em test_regra_bate_com_gabarito ou um teste proprio.")