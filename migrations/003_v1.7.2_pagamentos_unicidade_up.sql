-- =============================================================================
-- GymFlow web v1.7.2 - Migração 003: unicidade dos pagamentos do gateway
-- Arquivo : migrations/003_v1.7.2_pagamentos_unicidade_up.sql
-- Rollback: migrations/003_v1.7.2_pagamentos_unicidade_down.sql
-- Banco   : PostgreSQL 16
--
-- 1. Índice UNIQUE parcial uq_pagamentos_payment_id_gateway em
--    pagamentos(payment_id_gateway) WHERE payment_id_gateway IS NOT NULL:
--    a mesma cobrança do gateway (chave do pedido Cielo / payment.id Asaas)
--    nunca gera dois lançamentos. Linhas com chave NULL (lançamentos manuais e
--    antigos) não são afetadas.
-- 2. Índice ix_log_integracao_pagamento_criado_em (tela de logs ordena por data).
--
-- Se já houver duas ou mais linhas com a mesma chave não nula, a migração
-- ABORTA listando as chaves. Nenhum dado é apagado ou alterado.
--
-- Idempotente e transacional. Funciona também quando o db.create_all() da
-- v1.7.2 já criou os índices (instalação nova).
--
-- Aplicar:
--   psql -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f 003_v1.7.2_pagamentos_unicidade_up.sql
-- =============================================================================

-- Evita que o psql no Windows use WIN1252 como codificação do cliente.
SET client_encoding = 'UTF8';

BEGIN;

-- -----------------------------------------------------------------------------
-- Recusa se houver duplicatas pela chave do gateway (não altera nada)
-- -----------------------------------------------------------------------------
DO $duplicatas$
DECLARE
    v_lista text;
BEGIN
    SELECT string_agg(format('%s (%s lançamentos: ids %s)', payment_id_gateway, qtd, ids), '; '
                      ORDER BY payment_id_gateway)
      INTO v_lista
      FROM (SELECT payment_id_gateway, count(*) AS qtd,
                   string_agg(id::text, ', ' ORDER BY id) AS ids
              FROM pagamentos
             WHERE payment_id_gateway IS NOT NULL
             GROUP BY payment_id_gateway
            HAVING count(*) > 1) d;

    IF v_lista IS NOT NULL THEN
        RAISE EXCEPTION 'Migração 003 abortada: há pagamentos duplicados pela chave do gateway (payment_id_gateway): %. Nada foi alterado. Revise esses lançamentos no Financeiro e rode a migração de novo.', v_lista;
    END IF;
END;
$duplicatas$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pagamentos_payment_id_gateway
    ON pagamentos (payment_id_gateway)
    WHERE payment_id_gateway IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_log_integracao_pagamento_criado_em
    ON log_integracao_pagamento (criado_em);

-- -----------------------------------------------------------------------------
-- Registro da migração. A versão é o nome do arquivo sem _up.sql: assim o
-- installer/atualizar_banco.py reconhece a 003 como aplicada e não a roda de novo.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migracao (
    versao      varchar(40) PRIMARY KEY,
    descricao   text,
    aplicada_em timestamptz DEFAULT now()
);

INSERT INTO schema_migracao (versao, descricao)
VALUES ('003_v1.7.2_pagamentos_unicidade',
        'Pagamentos: índice único parcial em payment_id_gateway; índice em log_integracao_pagamento(criado_em)')
ON CONFLICT (versao) DO NOTHING;

COMMIT;
