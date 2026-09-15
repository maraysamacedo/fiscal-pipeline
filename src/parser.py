from __future__ import annotations

from lxml import etree

from .models import CabecalhoNFe, ItemNFe

NS = {"n": "http://www.portalfiscal.inf.br/nfe"}

class ParseError(Exception):
    """XML que nao e uma NF-e processavel."""

def _txt(node, path: str, default: str | None = None) -> str | None:
    if node is None:
        return default
    el = node.find(path, NS)
    if el is None or el.text is None:
        return default
    return el.text.strip() or default

def _num(node, path: str, default: float = 0.0) -> float:
    v = _txt(node, path)
    try:
        return float(v) if v is not None else default
    except ValueError:
        return default

def _extrair_tributacao(det) -> tuple[str | None, str | None, float]:
    """Devolve (cst, csosn, vl_icms) de qualquer grupo de ICMS."""
    icms = det.find("n:imposto/n:ICMS", NS)
    if icms is None or len(icms) == 0:
        return None, None, 0.0
    grupo = icms[0]
    return (
        _txt(grupo, "n:CST"),
        _txt(grupo, "n:CSOSN"),
        _num(grupo, "n:vICMS"),
    )

def parse_nfe(xml: str | bytes) -> tuple[CabecalhoNFe, list[ItemNFe]]:
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    if not xml.strip():
        raise ParseError("arquivo vazio")

    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError as e:
        raise ParseError(f"XML malformado: {e}") from e

    inf = root.find(".//n:infNFe", NS)
    if inf is None:
        raise ParseError("infNFe ausente - provavelmente é um evento, não uma NF-e")

    chave = (inf.get("Id") or "").removeprefix("NFe")
    emit = inf.find("n:emit", NS)
    dest = inf.find("n:dest", NS)

    try:
        cab = CabecalhoNFe(
            chave_acesso=chave,
            numero=int(_txt(inf, "n:ide/n:nNF", "0")),
            serie=int(_txt(inf, "n:ide/n:serie", "0")),
            modelo=_txt(inf, "n:ide/n:mod", "55"),
            dh_emissao=_txt(inf, "n:ide/n:dhEmi") or _txt(inf, "n:ide/n:dEmi"),
            tipo_nf=_txt(inf, "n:ide/n:tpNF", "1"),
            cnpj_emit=_txt(emit, "n:CNPJ") or _txt(dest, "n:CPF"),
            uf_emit=_txt(emit, "n:enderEmit/n:UF", ""),
            cnpj_dest=_txt(dest, "n:CNPJ") or _txt(dest, "n:CPF"),
            uf_dest=_txt(dest, "n:enderDest/n:UF"),
            crt=_txt(emit, "n:CRT"),
            valor_total=_num(inf, "n:total/n:ICMSTot/n:vNF"),
        )
    except Exception as e:
        raise ParseError(f"cabeçalho inválido ({chave[:12]}...): {e}") from e

    itens: list[ItemNFe] = []
    for det in inf.findall("n:det", NS):
        prod = det.find("n:prod", NS)
        cst, csosn, vl_icms = _extrair_tributacao(det)
        try:
            itens.append(ItemNFe(
                chave_acesso=chave,
                n_item=int(det.get("nItem", "0")),
                c_prod=_txt(prod, "n:cProd", ""),
                descricao=_txt(prod, "n:xProd", ""),
                ncm=_txt(prod, "n:NCM", ""),
                cfop=_txt(prod, "n:CFOP", ""),
                qtd=_num(prod, "n:qCom"),
                vl_unit=_num(prod, "n:vUnCom"),
                vl_produto=_num(prod, "n:vProd"),
                vl_icms=vl_icms,
                cst_icms=cst,
                csosn=csosn,
            ))
        except Exception as e:
            # Item invalido nao descarta a nota: registra como item bruto.
            itens.append(_item_bruto(chave, det, prod, cst, csosn, vl_icms, str(e)))

    return cab, itens

def _item_bruto(chave, det, prod, cst, csosn, vl_icms, erro) -> ItemNFe:
    return ItemNFe.model_construct(
        chave_acesso=chave,
        n_item=int(det.get("nItem", "0")),
        c_prod=_txt(prod, "n:cProd", ""),
        descricao=_txt(prod, "n:xProd", ""),
        ncm=(_txt(prod, "n:NCM") or ""),
        cfop=(_txt(prod, "n:CFOP") or ""),
        qtd=_num(prod, "n:qCom"),
        vl_unit=_num(prod, "n:vUnCom"),
        vl_produto=_num(prod, "n:vProd"),
        vl_icms=vl_icms,
        cst_icms=cst,
        csosn=csosn,
    )



