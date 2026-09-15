"""Classificador de NCM: baseline classico antes de qualquer LLM.

Principio: LLM escolhe qual regra aplicar, codigo aplica a regra.

"""
from __future__ import annotations

import argparse
import re
import unicodedata

import duckdb
import numpy as np
import polars as pl
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer

ABREVIACOES = {
    r"\bcx\b": "caixa", r"\bun\b": "unidade", r"\bpc\b": "peca",
    r"\bkg\b": "quilograma", r"\bml\b": "mililitro", r"\bcp\b": "comprimido",
    r"\bpol\b": "polegadas", r"\btam\b": "tamanho", r"\bmg\b": "miligrama",
}


def normalizar(texto: str) -> str:
    """minusculas, sem acento, sem pontuacao, abreviacoes expandidas."""
    t = unicodedata.normalize("NFKD", str(texto).lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    for padrao, sub in ABREVIACOES.items():
        t = re.sub(padrao, sub, t)
    return re.sub(r"\s+", " ", t).strip()


def construir_pipeline(analyzer: str = "char_wb") -> Pipeline:
    ngram = (3, 5) if analyzer.startswith("char") else (1, 2)
    return Pipeline([
        ("tfidf", TfidfVectorizer(analyzer=analyzer, ngram_range=ngram,
                                  min_df=1, sublinear_tf=True)),
        # metric="cosine": distancia em [0, 2] para vetores TF-IDF, o que
        # torna a conversao para similaridade previsivel. Com a metrica
        # euclidiana padrao a distancia nao tem teto interpretavel.
        ("knn", KNeighborsClassifier(n_neighbors=5, weights="distance",
                                     metric="cosine")),
    ])


def carregar_dados(db: str = "fiscal.duckdb") -> tuple[list[str], list[str]]:
    con = duckdb.connect(db, read_only=True)
    df = con.sql("""
        SELECT descricao, ncm FROM fct_item_fiscal
        WHERE regexp_matches(ncm, '^[0-9]{8}$') AND descricao <> ''
    """).pl()
    con.close()
    return df["descricao"].to_list(), df["ncm"].to_list()


def comparar_analyzers(X: list[str], y: list[str]) -> None:
    """Nao acredite que char_wb e melhor: meca."""
    Xn = [normalizar(d) for d in X]
    print("\n--- char_wb vs word (validacao cruzada, 5 folds) ---")
    for analyzer in ("char_wb", "word"):
        scores = cross_val_score(construir_pipeline(analyzer), Xn, y,
                                 cv=5, scoring="accuracy")
        print(f"  {analyzer:8s}  {scores.mean():.4f}  (+/- {scores.std():.4f})")


def similaridade_maxima(pipe: Pipeline, descricao: str) -> float:
    vec = pipe.named_steps["tfidf"].transform([normalizar(descricao)])
    dist, _ = pipe.named_steps["knn"].kneighbors(vec, n_neighbors=1)
    return float(max(0.0, min(1.0, 1.0 - dist[0][0])))


def prever_com_confianca(pipe: Pipeline, descricao: str, top_k: int = 3,
                         limiar_similaridade: float = 0.35):
    proba = pipe.predict_proba([normalizar(descricao)])[0]
    classes = pipe.named_steps["knn"].classes_
    sim = similaridade_maxima(pipe, descricao)
    fator = 1.0 if sim >= limiar_similaridade else sim / limiar_similaridade

    idx = np.argsort(proba)[::-1][:top_k]
    return [{"ncm": classes[i],
             "confianca": float(proba[i]) * fator,
             "similaridade": round(sim, 3)} for i in idx]


def avaliar(pipe: Pipeline, X_teste: list[str], y_teste: list[str]) -> pl.DataFrame:
    """Acuracia por faixa de confianca - e, sobretudo, calibracao."""
    Xn = [normalizar(d) for d in X_teste]
    proba = pipe.predict_proba(Xn)
    classes = pipe.named_steps["knn"].classes_
    preditos = classes[np.argmax(proba, axis=1)]
    confiancas = proba.max(axis=1)

    df = pl.DataFrame({"pred": preditos, "real": y_teste, "conf": confiancas})
    df = df.with_columns((pl.col("pred") == pl.col("real")).alias("acerto"))

    print(f"\nAcuracia geral:        {df['acerto'].mean():.4f}")
    print(f"Cobertura (conf>0.90): {(df['conf'] > 0.90).mean():.4f}")
    alta = df.filter(pl.col("conf") > 0.90)
    if alta.height:
        print(f"Acuracia em conf>0.90: {alta['acerto'].mean():.4f}")

    faixas = (df.with_columns(
                  pl.col("conf").cut([0.5, 0.7, 0.9]).alias("faixa"))
                .group_by("faixa")
                .agg([pl.len().alias("n"),
                      pl.col("acerto").mean().alias("acuracia_real"),
                      pl.col("conf").mean().alias("confianca_media")])
                .sort("faixa"))

    print("\n--- Calibracao ---")
    print("A confianca reportada corresponde a acuracia real?")
    print(faixas)
    print("\nSe acuracia_real << confianca_media, o threshold de automacao")
    print("esta mandando erro para producao. Ajuste antes de automatizar.")
    return faixas


def rotear(confianca: float) -> str:
    if confianca > 0.90:
        return "AUTO"
    if confianca >= 0.70:
        return "REVISAO"
    return "ESPECIALISTA"


def main() -> None:
    p = argparse.ArgumentParser(description="Classificador de NCM (baseline).")
    p.add_argument("--db", default="fiscal.duckdb")
    p.add_argument("--comparar", action="store_true")
    args = p.parse_args()

    X, y = carregar_dados(args.db)
    print(f"amostras: {len(X)} | NCMs distintos: {len(set(y))}")

    if args.comparar:
        comparar_analyzers(X, y)

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y)

    pipe = construir_pipeline()
    pipe.fit([normalizar(d) for d in X_tr], y_tr)
    avaliar(pipe, X_te, y_te)

    print("\n--- Exemplos ---")
    for desc in ["refri lata 350", "paracetamol 750 mg",
                 "notebook 14 polegadas", "produto totalmente desconhecido xyz"]:
        top = prever_com_confianca(pipe, desc)
        rota = rotear(top[0]["confianca"])
        print(f"  {desc:38s} -> {top[0]['ncm']} "
              f"conf={top[0]['confianca']:.2f} "
              f"sim={top[0]['similaridade']:.2f} [{rota}]")


if __name__ == "__main__":
    main()