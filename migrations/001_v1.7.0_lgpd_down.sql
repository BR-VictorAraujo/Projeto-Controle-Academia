-- #############################################################################
-- #                                                                           #
-- #   ATENÇÃO: SCRIPT DESTRUTIVO - ROLLBACK DA MIGRAÇÃO 001 (v1.7.0 LGPD)      #
-- #                                                                           #
-- #   Remove DEFINITIVAMENTE: termos, versões, MANIFESTAÇÕES dos usuários,     #
-- #   auditoria de termos, triggers, funções e as chaves de configuração       #
-- #   lgpd_gate_ativo e lgpd_contato_privacidade.                             #
-- #                                                                           #
-- #   As manifestações são a prova de ciência/consentimento dos titulares.     #
-- #   Usar SOMENTE em banco sem manifestações reais (dev/QAS) e SEMPRE após    #
-- #   um pg_dump completo do banco:                                           #
-- #                                                                           #
-- #     pg_dump -U <usuario> -d <banco> -F c -f backup_antes_down_001.dump     #
-- #                                                                           #
-- #   O script RECUSA a execução se lgpd_manifestacao tiver linhas. Para       #
-- #   forçar (somente se tiver certeza e com backup), na MESMA sessão psql:    #
-- #                                                                           #
-- #     SET gymflow.forcar_down = 'on';                                       #
-- #     \i 001_v1.7.0_lgpd_down.sql                                           #
-- #                                                                           #
-- #   Antes do down, volte o código da aplicação para a versão anterior        #
-- #   (v1.5.x) ou pare o serviço: o app v1.7.0 com db.create_all() recriaria   #
-- #   as tabelas vazias na próxima inicialização.                             #
-- #                                                                           #
-- #############################################################################

SET client_encoding = 'UTF8';

BEGIN;

-- [GUARDA-INICIO]
DO $guarda$
DECLARE
    v_qtd    bigint;
    v_forcar text := coalesce(current_setting('gymflow.forcar_down', true), '');
BEGIN
    IF to_regclass('public.lgpd_manifestacao') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM public.lgpd_manifestacao' INTO v_qtd;
        IF v_qtd > 0 AND v_forcar <> 'on' THEN
            RAISE EXCEPTION 'Down 001 recusado: lgpd_manifestacao possui % linha(s). Faça pg_dump e, se tiver certeza, execute SET gymflow.forcar_down = ''on''; na mesma sessão.', v_qtd;
        END IF;
        IF v_qtd > 0 THEN
            RAISE WARNING 'Down 001 FORÇADO: % manifestação(ões) serão removidas.', v_qtd;
        END IF;
    END IF;
END;
$guarda$;
-- [GUARDA-FIM]

-- Triggers
DROP TRIGGER IF EXISTS lgpd_termo_versao_protege         ON lgpd_termo_versao;
DROP TRIGGER IF EXISTS lgpd_manifestacao_valida          ON lgpd_manifestacao;
DROP TRIGGER IF EXISTS lgpd_manifestacao_imutavel        ON lgpd_manifestacao;
DROP TRIGGER IF EXISTS lgpd_manifestacao_sem_truncate    ON lgpd_manifestacao;
DROP TRIGGER IF EXISTS lgpd_termo_auditoria_registra     ON lgpd_termo_auditoria;
DROP TRIGGER IF EXISTS lgpd_termo_auditoria_imutavel     ON lgpd_termo_auditoria;
DROP TRIGGER IF EXISTS lgpd_termo_auditoria_sem_truncate ON lgpd_termo_auditoria;

-- Tabelas (ordem respeita as FKs RESTRICT; sem CASCADE)
DROP TABLE IF EXISTS lgpd_manifestacao;
DROP TABLE IF EXISTS lgpd_termo_auditoria;
DROP TABLE IF EXISTS lgpd_termo_versao;
DROP TABLE IF EXISTS lgpd_termo;

-- Funções
DROP FUNCTION IF EXISTS fn_lgpd_termo_versao_protege();
DROP FUNCTION IF EXISTS fn_lgpd_manifestacao_valida();
DROP FUNCTION IF EXISTS fn_lgpd_auditoria_registra();
DROP FUNCTION IF EXISTS fn_lgpd_somente_insercao();

-- Configurações criadas pela migração
DELETE FROM configuracoes WHERE chave IN ('lgpd_gate_ativo', 'lgpd_contato_privacidade');

-- Registro da migração. A tabela schema_migracao só é removida se ficar vazia
-- (migrações futuras também a utilizam).
DO $sm$
BEGIN
    IF to_regclass('public.schema_migracao') IS NOT NULL THEN
        DELETE FROM public.schema_migracao WHERE versao = '1.7.0_001_lgpd';
        IF NOT EXISTS (SELECT 1 FROM public.schema_migracao) THEN
            EXECUTE 'DROP TABLE public.schema_migracao';
        END IF;
    END IF;
END;
$sm$;

COMMIT;
