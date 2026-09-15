-- 1. Gap de numeracao: nota faltando na captura -----------------------
CREATE OR REPLACE VIEW v_gap_numeracao AS
WITH seq AS (
    SELECT chave_acesso, cnpj_emit, serie, numero,
        numero - LAG(numero) OVER (PARTITION BY cnpj_emit, serie
                                    ORDER BY numero) - 1 AS gap
    FROM silver_cabecalho
    WHERE modelo = '55'
)
SELECT chave_acesso, cnpj_emit, serie, numero, gap
FROM seq
WHERE gap > 0;

-- 2. Duplicidade de chave (tecnica) -----------------------------------
CREATE OR REPLACE VIEW v_duplicidade_chave AS
SELECT chave_acesso, ANY_VALUE(cnpj_emit) AS cnpj_emit, COUNT(*) AS ocorrencias
FROM silver_cabecalho_raw
GROUP BY chave_acesso
HAVING COUNT(*) > 1;

-- 3. Duplicidade de numeracao: mesmo emitente/serie/numero ------------
CREATE OR REPLACE VIEW v_duplicidade_numeracao AS
SELECT cnpj_emit, serie, numero,
        COUNT(DISTINCT chave_acesso) AS chaves_distintas,
        ANY_VALUE(chave_acesso)      AS chave_acesso,
FROM silver_cabecalho
GROUP BY cnpj_emit, serie, numero
HAVING COUNT(DISTINCT chave_acesso) > 1;

-- 4. Duplicidade economica: mesma operacao, dia e valor ---------------
-- Gera falso positivo legitimo (varejo pode repetir ticket)
CREATE OR REPLACE VIEW v_duplicidade_economica AS
SELECT cnpj_emit, cnpj_dest, dt_emissao, valor_total,
        COUNT(*)                AS ocorrencias,
        ANY_VALUE(chave_acesso) AS chave_acesso
FROM silver_cabecalho
WHERE valor_total > 100
GROUP BY cnpj_emit, cnpj_dest, dt_emissao, valor_total
HAVING COUNT(*) > 1;

-- 5. NCM fora do padrao de 8 digitos ----------------------------------
CREATE OR REPLACE VIEW v_ncm_invalido AS
SELECT chave_acesso, n_item, cnpj_emit, ncm, descricao, vl_produto
FROM fct_item_fiscal
WHERE ncm IS NULL OR NOT REGEXP_MATCHES(ncm, '^[0-9]{8}$')
    OR ncm IN ('00000000', '99999999');

-- 6. CFOP interestadual com mesma UF -----------------------------------
CREATE OR REPLACE VIEW v_cfop_uf_incoerente AS
SELECT chave_acesso, n_item, cnpj_emit, cfop, uf_emit, uf_dest, vl_produto
FROM fct_item_fiscal
WHERE LEFT(cfop, 1) = '6' AND uf_emit = uf_dest;

-- 7. Preco unitario outlier dentro do NCM ------------------------------
CREATE OR REPLACE VIEW v_preco_outlier AS
WITH ranked AS (
    SELECT chave_acesso, n_item, cnpj_emit, ncm, descricao,
            vl_unit, vl_produto,
            PERCENT_RANK() OVER (PARTITION BY ncm ORDER BY vl_unit) AS pct,
            COUNT(*)        OVER (PARTITION BY ncm)                 AS n_ncm
    FROM fct_item_fiscal
    WHERE REGEXP_MATCHES(ncm, '^[0-9]{8}$')
)
SELECT * FROM ranked
WHERE n_ncm >= 30 AND (pct > 0.995 OR pct < 0.005);

-- 8. Queda abrupta de faturamento --------------------------------------
CREATE OR REPLACE VIEW v_queda_faturamento AS
WITH mensal AS (
    SELECT cnpj_emit, competencia, SUM(valor_total) AS faturamento
    FROM silver_cabecalho
    WHERE status = 'AUTORIZADA'
    GROUP BY 1, 2
),
variacao AS (
    SELECT *,
            LAG(faturamento) OVER (PARTITION BY cnpj_emit
                                    ORDER BY competencia) AS anterior
    FROM mensal
)
SELECT cnpj_emit, competencia, faturamento, anterior,
        ROUND(faturamento / NULLIF(anterior, 0) - 1, 4) AS variacao
FROM variacao
WHERE anterior > 0 AND faturamento / anterior < 0.4;

-- 9. Divergencia entre total da nota e soma dos itens -------------------
CREATE OR REPLACE VIEW v_total_divergente AS
SELECT c.chave_acesso, c.cnpj_emit, c.valor_total,
        ROUND(SUM(i.vl_produto), 2)                     AS soma_itens,
        ROUND(ABS(c.valor_total - SUM(i.vl_produto)), 2) AS diferenca
FROM silver_cabecalho c
JOIN silver_item i USING (chave_acesso)
GROUP BY c.chave_acesso, c.cnpj_emit, c.valor_total
HAVING ABS(c.valor_total - SUM(i.vl_produto)) > 0.05;

-- 10. Cliente sem emissao recente --------------------------------------
CREATE OR REPLACE VIEW v_sem_emissao AS
WITH ultima AS (
    SELECT cnpj_emit, MAX(dt_emissao) AS ultima_nf,
            (SELECT MAX(dt_emissao) FROM silver_cabecalho) AS referencia
    FROM silver_cabecalho
    GROUP BY cnpj_emit
)
SELECT cnpj_emit, ultima_nf, referencia,
        DATE_DIFF('day', ultima_nf, referencia) AS dias_sem_emitir
FROM ultima
WHERE DATE_DIFF('day', ultima_nf, referencia) > 45;

-- =====================================================================
-- Consolidacao
-- =====================================================================

CREATE OR REPLACE VIEW v_achados_sql AS
SELECT 'GAP_NUMERACAO'      AS regra, 'MEDIA'   AS severidade,
        chave_acesso, cnpj_emit, 0.0 AS vl_base FROM v_gap_numeracao
UNION ALL
SELECT 'DUP_CHAVE',             'CRITICA', chave_acesso, cnpj_emit, 0.0
FROM v_duplicidade_chave
UNION ALL
SELECT 'DUP_NUMERACAO',         'CRITICA', chave_acesso, cnpj_emit, 0.0
FROM v_duplicidade_numeracao
UNION ALL
SELECT 'DUP_ECONOMICA',          'MEDIA', chave_acesso, cnpj_emit, valor_total
FROM v_duplicidade_economica
UNION ALL
SELECT 'NCM_INVALIDO',            'ALTA', chave_acesso, cnpj_emit, vl_produto
FROM v_ncm_invalido
UNION ALL
SELECT 'CFOP_UF',                   'ALTA', chave_acesso, cnpj_emit, vl_produto
FROM v_cfop_uf_incoerente
UNION ALL
SELECT 'PRECO_OUTLIER',             'BAIXA', chave_acesso, cnpj_emit, vl_produto
FROM v_preco_outlier
UNION ALL
SELECT 'TOTAL_DIVERGENTE',           'ALTA', chave_acesso, cnpj_emit, diferenca
FROM v_total_divergente;

