from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

Severidade = Literal["CRITICA", "ALTA", "MEDIA", "BAIXA"]
Categoria = Literal["CLASSIFICACAO", "DUPLICIDADE", "CAPTURA",
                    "CALCULO", "CADASTRO"]

PESO_SEVERIDADE: dict[str, float] = {
    "CRITICA": 1.0, "ALTA": 0.7, "MEDIA": 0.4, "BAIXA": 0.1,
}


class Regra(BaseModel):
    id: str = Field(pattern=r"^R\d{3}$")
    nome: str
    descricao: str = ""
    categoria: Categoria
    severidade: Severidade
    tipo: Literal["sql", "python"] = "sql"
    condicao: str
    campo_valor: str | None = None
    aliquota_risco: float = Field(default=0.0, ge=0, le=1)
    ativa: bool = True
    vigencia_inicio: date = date(2000, 1, 1)
    vigencia_fim: date | None = None
    parametros: dict[str, Any] = Field(default_factory=dict)

    def vigente_em(self, d: date) -> bool:
        return (self.ativa
                and self.vigencia_inicio <= d
                and (self.vigencia_fim is None or d <= self.vigencia_fim))

    @property
    def peso(self) -> float:
        return PESO_SEVERIDADE[self.severidade]


class CatalogoRegras(BaseModel):
    regras: list[Regra]

    def vigentes(self, d: date) -> list[Regra]:
        return [r for r in self.regras if r.vigente_em(d)]

    def por_id(self, rid: str) -> Regra:
        for r in self.regras:
            if r.id == rid:
                return r
        raise KeyError(f"regra {rid} nao encontrada")

    def ids(self) -> set[str]:
        return {r.id for r in self.regras}