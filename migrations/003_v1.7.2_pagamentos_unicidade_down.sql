-- =============================================================================
-- GymFlow web v1.7.2 - Rollback da migração 003
-- Arquivo : migrations/003_v1.7.2_pagamentos_unicidade_down.sql
--
-- Remove os índices criados pela 003 e o registro em schema_migracao.
-- Não altera nem apaga dados de pagamentos ou logs.
-- Antes do down, volte o código para a v1.7.1: com a v1.7.2 no ar, a proteção
-- contra lançamento duplicado fica só na trava da assinatura.
--
--   psql -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f 003_v1.7.2_pagamentos_unicidade_down.sql
-- =============================================================================

SET client_encoding = 'UTF8';

BEGIN;

DROP INDEX IF EXISTS uq_pagamentos_payment_id_gateway;
DROP INDEX IF EXISTS ix_log_integracao_pagamento_criado_em;

DO $sm$
BEGIN
    IF to_regclass('public.schema_migracao') IS NOT NULL THEN
        DELETE FROM public.schema_migracao WHERE versao = '003_v1.7.2_pagamentos_unicidade';
    END IF;
END;
$sm$;

COMMIT;
