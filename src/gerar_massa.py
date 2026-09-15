from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

CFOPS_INTERNO = ["5102", "5405", "5933"]
CFOPS_INTERESTADUAL = ["6102", "6108", "6404"]
NCMS_RUINS = ["00000000", "9999", "1234567", ""]
UFS = ["SP", "RJ", "MG", "PR", "SC", "DF"]

CATALOGO: list[tuple[str, str]] = [
    ("REFRIGERANTE COLA LATA 350ML",      "22021000"),
    ("REFRIG LATA 350 ML COLA",           "22021000"),
    ("REFRIGERANTE GUARANA PET 2L",       "22021000"),
    ("AGUA TONICA LATA 350ML",            "22021000"),
    ("PARACETAMOL 750MG CX 20CP",         "30049099"),
    ("PARACETAMOL 750 MG 20 COMPRIMIDOS", "30049099"),
    ("DIPIRONA SODICA 500MG CX 10CP",     "30049099"),
    ("IBUPROFENO 400MG BLISTER",          "30049099"),
    ("NOTEBOOK 14POL 8GB SSD 256",        "84713012"),
    ("NOTEBOOK 15 POLEGADAS 16GB",        "84713012"),
    ("COMPUTADOR PORTATIL 14POL",         "84713012"),
    ("ULTRABOOK I5 8GB",                  "84713012"),
    ("CAMISETA ALGODAO TAM M",            "61091000"),
    ("CAMISETA MALHA ALGODAO G",          "61091000"),
    ("BLUSA ALGODAO MANGA CURTA",         "61091000"),
    ("T-SHIRT ALGODAO TAM P",             "61091000"),
    ("CADEIRA ESCRITORIO GIRATORIA",      "94036000"),
    ("CADEIRA GIRATORIA COM BRACOS",      "94036000"),
    ("POLTRONA ESCRITORIO ERGONOMICA",    "94036000"),
    ("MESA ESCRITORIO MDF 120CM",         "94036000"),
]

TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
 <NFe><infNFe Id="NFe{chave}" versao="4.00">
  <ide><cUF>35</cUF><nNF>{numero}</nNF><serie>{serie}</serie><mod>55</mod>
       <dhEmi>{dh}</dhEmi><tpNF>1</tpNF></ide>
  <emit><CNPJ>{cnpj_emit}</CNPJ><xNome>Emitente {emit_id}</xNome>
        <enderEmit><UF>{uf_emit}</UF></enderEmit>
        <CRT>{crt}</CRT></emit>
  <dest><CNPJ>{cnpj_dest}</CNPJ><xNome>Destinatario</xNome>
        <enderDest><UF>{uf_dest}</UF></enderDest></dest>
{itens}
  <total><ICMSTot><vNF>{total:.2f}</vNF></ICMSTot></total>
 </infNFe></NFe>
</nfeProc>"""

ITEM_SIMPLES = """  <det nItem="{n}">
   <prod><cProd>P{cprod}</cProd><xProd>{desc}</xProd><NCM>{ncm}</NCM>
         <CFOP>{cfop}</CFOP><uCom>UN</uCom><qCom>{qtd}</qCom>
         <vUnCom>{vunit:.2f}</vUnCom><vProd>{vprod:.2f}</vProd></prod>
   <imposto><ICMS><ICMSSN102><orig>0</orig><CSOSN>102</CSOSN></ICMSSN102></ICMS></imposto>
  </det>"""

ITEM_REGULAR = """  <det nItem="{n}">
   <prod><cProd>P{cprod}</cProd><xProd>{desc}</xProd><NCM>{ncm}</NCM>
         <CFOP>{cfop}</CFOP><uCom>UN</uCom><qCom>{qtd}</qCom>
         <vUnCom>{vunit:.2f}</vUnCom><vProd>{vprod:.2f}</vProd></prod>
   <imposto><ICMS><ICMS00><orig>0</orig><CST>00</CST>
     <vBC>{vprod:.2f}</vBC><pICMS>18.00</pICMS><vICMS>{vicms:.2f}</vICMS>
   </ICMS00></ICMS></imposto>
  </det>"""


def gerar(qtd_notas: int, destino: str, taxa_erro: float = 0.08,
          qtd_duplicatas: int = 20, qtd_emitentes: int = 40,
          seed: int = 42) -> dict[str, list[str]]:
    """Gera os XMLs e devolve o gabarito dos erros injetados."""
    random.seed(seed)
    pasta = Path(destino)
    pasta.mkdir(parents=True, exist_ok=True)

    gabarito: dict[str, list[str]] = {
        "ncm_invalido": [],
        "cfop_uf_incoerente": [],
        "simples_com_cst": [],
        "duplicatas": [],
    }
    contador: dict[tuple, int] = defaultdict(int)
    base = datetime(2026, 1, 1)

    for seq in range(1, qtd_notas + 1):
        emit_id = seq % qtd_emitentes
        simples = emit_id % 3 != 0
        crt = "1" if simples else "3"
        uf_emit = "DF"
        uf_dest = random.choice(UFS)
        serie = 1

        contador[(emit_id, serie)] += 1
        numero = contador[(emit_id, serie)]

        dh = base + timedelta(days=seq % 200, hours=seq % 24)
        cnpj_emit = f"{10000000000000 + emit_id}"
        chave = (f"35{dh:%y%m}{cnpj_emit}55{serie:03d}"
                 f"{numero:09d}1{seq:08d}0")[:44].ljust(44, "0")

        itens_xml: list[str] = []
        total = 0.0
        for n in range(1, random.randint(1, 4) + 1):
            descricao, ncm = random.choice(CATALOGO)
            cfop = (random.choice(CFOPS_INTERNO) if uf_dest == uf_emit
                    else random.choice(CFOPS_INTERESTADUAL))
            usa_cst_indevido = False

            if random.random() < taxa_erro:
                escolha = random.random()
                if escolha < 0.40:
                    ncm = random.choice(NCMS_RUINS)
                    gabarito["ncm_invalido"].append(chave)
                elif escolha < 0.75:
                    cfop = random.choice(CFOPS_INTERESTADUAL)
                    uf_dest = uf_emit
                    gabarito["cfop_uf_incoerente"].append(chave)
                elif simples:
                    usa_cst_indevido = True
                    gabarito["simples_com_cst"].append(chave)

            qtd = round(random.uniform(1, 20), 2)
            vunit = round(random.uniform(5, 900), 2)
            vprod = round(qtd * vunit, 2)
            total += vprod

            tpl = ITEM_REGULAR if (not simples or usa_cst_indevido) else ITEM_SIMPLES
            itens_xml.append(tpl.format(
                n=n, cprod=f"{emit_id}{n:03d}", desc=descricao,
                ncm=ncm, cfop=cfop, qtd=qtd, vunit=vunit, vprod=vprod,
                vicms=vprod * 0.20))

        xml = TEMPLATE.format(
            chave=chave, numero=numero, serie=serie,
            dh=dh.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
            cnpj_emit=cnpj_emit, emit_id=emit_id, uf_emit=uf_emit, crt=crt,
            cnpj_dest=f"{20000000000000 + (seq % 200)}", uf_dest=uf_dest,
            itens="\n".join(itens_xml), total=total)

        (pasta / f"{chave}.xml").write_text(xml, encoding="utf-8")

    originais = [p for p in sorted(pasta.glob("*.xml"))
                 if not p.name.startswith("dup_")]
    for arq in originais[:qtd_duplicatas]:
        (pasta / f"dup_{arq.name}").write_text(
            arq.read_text(encoding="utf-8"), encoding="utf-8")
        gabarito["duplicatas"].append(arq.stem)

    gabarito = {k: sorted(set(v)) for k, v in gabarito.items()}
    (pasta.parent / "gabarito.json").write_text(
        json.dumps(gabarito, indent=2), encoding="utf-8")
    return gabarito


def main() -> None:
    p = argparse.ArgumentParser(description="Gera NF-e sinteticas para teste.")
    p.add_argument("--qtd", type=int, default=5000)
    p.add_argument("--destino", default="data/raw/nfe")
    p.add_argument("--taxa-erro", type=float, default=0.08)
    p.add_argument("--duplicatas", type=int, default=20)
    p.add_argument("--emitentes", type=int, default=40)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    gab = gerar(args.qtd, args.destino, args.taxa_erro,
                args.duplicatas, args.emitentes, args.seed)

    total = len(list(Path(args.destino).glob("*.xml")))
    print(f"Gerados {total} arquivos em {args.destino}")
    print("Gabarito de erros injetados (em notas):")
    for tipo, chaves in gab.items():
        print(f"  {tipo:22s} {len(chaves):>5}")
    print(f"Gabarito: {Path(args.destino).parent / 'gabarito.json'}")


if __name__ == "__main__":
    main()