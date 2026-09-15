from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

class ItemNFe(BaseModel):
    chave_acesso: str = Field(min_length=44, max_length=44)
    n_item: int = Field(ge=1)
    c_prod: str
    descricao: str
    ncm: str
    cfop: str
    qtd: float = Field(ge=0)
    vl_unit: float = Field(ge=0)
    vl_produto: float = Field(ge=0)
    vl_icms: float = Field(default=0.0, ge=0)
    cst_icms: str | None = None
    csosn: str | None = None

    @field_validator("ncm")
    @classmethod
    def normalize_ncm(cls, v: str) -> str:
        """NCM tem exatamente 8 digitos. Nao completar com zeros.
        """
        v = (v or "").strip()
        if not re.fullmatch(r"\d{8}", v):
            raise ValueError(f"NCM deve ter exatamente 8 dígitos: {v!r}")
        return v

    @field_validator("cfop")
    @classmethod
    def valida_cfop(cls, v: str) -> str:
        v = (v or "").strip()
        if not re.fullmatch(r"[1-7]\d{3}", v):
            raise ValueError(f"CFOP inválido: {v!r}")
        return v

    @property
    def grupo_cfop(self) -> str:
        return self.cfop[0]

    @property
    def cod_tributacao(self) -> str | None:
        return self.csosn or self.cst_icms

class CabecalhoNFe(BaseModel):
    chave_acesso: str = Field(min_length=44, max_length=44)
    numero: int = Field(ge=1)
    serie: int = Field(ge=0)
    modelo: Literal["55", "65", "57"]
    dh_emissao: datetime
    tipo_nf: Literal["0", "1"]
    cnpj_emit: str
    uf_emit: str = Field(min_length=2, max_length=2)
    cnpj_dest: str  | None = None
    uf_dest: str | None = None
    crt: Literal["1", "2", "3", "4"] | None = None
    valor_total: float = Field(ge=0)
    status: str = "AUTORIZADA"

    @field_validator("chave_acesso")
    @classmethod
    def chave_numerica(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("chave de acesso deve conter apenas dígitos")
        return v

    @property
    def is_simples(self) -> bool:
        """CRT 1 (Simples) ou 2 (Simples, excesso de sublimite)."""
        return self.crt in ("1", "2")

    @property
    def competencia(self) -> str:
        return self.dh_emissao.strftime("%Y-%m-01")