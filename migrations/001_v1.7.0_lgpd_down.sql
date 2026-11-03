-- #############################################################################
-- #                                                                           #
-- #   ATENÇÃO: SCRIPT DESTRUTIVO - ROLLBACK DA MIGRAÇÃO 001 (v1.7.0 LGPD)      #
-- #                                                                           #
-- #   Remove DEFINITIVAMENTE as versões publicadas do termo e as RESPOSTAS     #
-- #   (aceito / não aceito) de todos os usuários, além de triggers e funções.  #
-- #   Remove também os objetos da 001 ANTIGA (lgpd_termo,                     #
-- #   lgpd_termo_auditoria, funções antigas e as chaves lgpd_gate_ativo e     #
-- #   lgpd_contato_privacidade), caso existam.                                #
-- #                                                                           #
-- #   As respostas são a prova do aceite do termo. Usar SOMENTE em banco sem  #
-- #   respostas reais (dev/QAS) e SEMPRE após um pg_dump completo:            #
-- #                                                                           #
-- #     pg_dump -U <usuario> -d <banco> -F c -f backup_antes_down_001.dump     #
-- #                                                                           #
-- #   O script RECUSA a execução se lgpd_manifestacao tiver linhas. Para       #
-- #   forçar (somente com backup e certeza), na MESMA sessão do psql:         #
-- #                                                                           #
-- #     psql ... -c "SET gymflow.forcar_down = 'on'" -f 001_v1.7.0_lgpd_down.sql
-- #                                                                           #
-- #   Antes do down, pare o serviço web ou volte o código para a versão       #
-- #   anterior: o db.create_all() da v1.7.0 recriaria as tabelas vazias.      #
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
            RAISE WARNING 'Down 001 FORÇADO: % resposta(s) de usuários serão removidas.', v_qtd;
        END IF;
    END IF;
END;
$guarda$;
-- [GUARDA-FIM]

-- Triggers (001 nova e antiga). Só tenta em tabelas que existem.
DO $trg$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('lgpd_termo_versao',    'lgpd_termo_versao_valida'),
            ('lgpd_termo_versao',    'lgpd_termo_versao_imutavel'),
            ('lgpd_termo_versao',    'lgpd_termo_versao_sem_truncate'),
            ('lgpd_termo_versao',    'lgpd_termo_versao_protege'),
            ('lgpd_manifestacao',    'lgpd_manifestacao_valida'),
            ('lgpd_manifestacao',    'lgpd_manifestacao_imutavel'),
            ('lgpd_manifestacao',    'lgpd_manifestacao_sem_truncate'),
            ('lgpd_termo_auditoria', 'lgpd_termo_auditoria_registra'),
            ('lgpd_termo_auditoria', 'lgpd_termo_auditoria_imutavel'),
            ('lgpd_termo_auditoria', 'lgpd_termo_auditoria_sem_truncate')
        ) AS t(tabela, gatilho)
    LOOP
        IF to_regclass('public.' || r.tabela) IS NOT NULL THEN
            EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', r.gatilho, r.tabela);
        END IF;
    END LOOP;
END;
$trg$;

-- Tabelas (ordem respeita as FKs RESTRICT; sem CASCADE)
DROP TABLE IF EXISTS lgpd_manifestacao;
DROP TABLE IF EXISTS lgpd_termo_auditoria;   -- 001 antiga
DROP TABLE IF EXISTS lgpd_termo_versao;
DROP TABLE IF EXISTS lgpd_termo;             -- 001 antiga

-- Funções (001 nova e antiga)
DROP FUNCTION IF EXISTS fn_lgpd_termo_versao_valida();
DROP FUNCTION IF EXISTS fn_lgpd_manifestacao_valida();
DROP FUNCTION IF EXISTS fn_lgpd_somente_insercao();
DROP FUNCTION IF EXISTS fn_lgpd_termo_versao_protege();   -- 001 antiga
DROP FUNCTION IF EXISTS fn_lgpd_auditoria_registra();     -- 001 antiga

-- Chaves de configuração da 001 antiga (a 001 nova não cria chaves)
DELETE FROM configuracoes WHERE chave IN ('lgpd_gate_ativo', 'lgpd_contato_privacidade');

-- Registros em schema_migracao: a 001 e as migrações de publicação de termo
-- (NNN_termo_v<versao>), cujas versões acabaram de ser removidas. A tabela só é
-- removida se ficar vazia (migrações futuras também a utilizam).
DO $sm$
BEGIN
    IF to_regclass('public.schema_migracao') IS NOT NULL THEN
        DELETE FROM public.schema_migracao
         WHERE versao = '1.7.0_001_lgpd' OR versao ~ '^[0-9]{3}_termo_v';
        IF NOT EXISTS (SELECT 1 FROM public.schema_migracao) THEN
            EXECUTE 'DROP TABLE public.schema_migracao';
        END IF;
    END IF;
END;
$sm$;

COMMIT;
