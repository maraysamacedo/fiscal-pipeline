from __future__ import annotations

import json
import logging 
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

class JsonFormatter(logging.Formatter):
    def formati(self, record: logging.LogRecord) -> str:
        base = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        base.update(getattr(record, "extra_fields", {}))
        return json.dumps(base, ensure_ascii=False)

log =logging.getLogger("fiscal")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(JsonFormatter())
    log.addHandler(_h)
    log.setLevel(logging.INFO)

EXECUCAO_ID = uuid.uuid4().hex[:12]
METRICAS: list[dict] = []

@contextmanager
def etapa(nome: str, **campos):
    t0 = time.perf_counter()
    log.info(f"inicio: {nome}",
             extra={"extra_fields": {"etapa": nome, "execucao_id": EXECUCAO_ID, **campos}})
    try:
        yield
    except Exception as e:
        dur = round(time.perf_counter() - t0, 3)
        METRICAS.append({"execucao_id": EXECUCAO_ID, "etapa": nome,
                        "duracao_s": dur, "status": "ERRO", "erro": repr(e)})

        log.error(f"falha: {nome}",
                  extra={"extra_fields": {"etapa": nome, "duracao_s": dur,
                                          "erro": repr(e)}})
        raise
    else:
        dur = round(time.perf_counter() - t0, 3)
        METRICAS.append({"execucao_id": EXECUCAO_ID, "etapa": nome, 
                         "duracao_s": dur, "status": "OK"})
        log.info(f"fim: {nome}",
                 extra={"extra_fields": {etapa: nome, "duracao_s": dur}})

def gravar_metricas(db: str = "fiscal.duckdb") -> None:
    if not METRICAS:
        return
    import duckdb
    con = duckdb.connect(db)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_metricas (
            execucao_id VARCHAR, etapa VARCHAR, duracao_s DOUBLE,
            status VARCHAR, erro VARCHAR, dh TIMESTAMP DEFAULT current_timestamp)
    """)
    con.executemany(
        "INSERT INTO pipeline_metricas (execucao_id, etapa, duracao_s, status, erro) "
        "VALUES (?, ?, ?, ?, ?)",
        [(m["execucao_id"], m["etapa"], m["duracao_s"], m["status"], m.get("erro"))
         for m in METRICAS])
    con.close()

def salvar_log_jsonl(caminho: str = "data/pipeline.json") -> None:
    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "a", encoding="utf-8") as f:
        for m in METRICAS:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")