"""Processamento em lote com paralelismo e falha isolada.

Principio: um XML corrompido nunca derruba o lote. Vira quarentena.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .parser import parse_nfe


@dataclass
class ResultadoLote:
    cabecalhos: list[dict] = field(default_factory=list)
    itens: list[dict] = field(default_factory=list)
    falhas: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cabecalhos)


def _worker(caminho: str) -> tuple:
    try:
        cab, itens = parse_nfe(Path(caminho).read_bytes())
        return ("ok", caminho,
                cab.model_dump(mode="json"),
                [i.model_dump(mode="json") for i in itens])
    except Exception as e:
        return ("erro", caminho, f"{type(e).__name__}: {e}", None)


def processar_pasta(pasta: str, workers: int = 8) -> ResultadoLote:
    arquivos = [str(p) for p in sorted(Path(pasta).rglob("*.xml"))]
    if not arquivos:
        raise FileNotFoundError(f"nenhum XML em {pasta}")

    res = ResultadoLote()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futuros = [ex.submit(_worker, a) for a in arquivos]
        for fut in as_completed(futuros):
            status, caminho, dado, itens = fut.result()
            if status == "ok":
                res.cabecalhos.append(dado)
                res.itens.extend(itens)
            else:
                res.falhas.append({"arquivo": caminho, "erro": dado})
    return res