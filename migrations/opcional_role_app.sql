-- =============================================================================
-- OPCIONAL - NÃO É APLICADO AUTOMATICAMENTE E NÃO FAZ PARTE DA MIGRAÇÃO 001
-- Role dedicado para a aplicação GymFlow (web)
--
-- Motivação: hoje o app conecta com um superusuário/dono das tabelas. Superusuário
-- ignora GRANT/REVOKE, então a proteção das tabelas LGPD depende só dos triggers
-- (que um superusuário também pode desabilitar). Com um role dedicado, sem
-- UPDATE/DELETE/TRUNCATE em lgpd_termo_versao e lgpd_manifestacao (e sem INSERT
-- em lgpd_termo_versao), a imutabilidade passa a ter duas camadas: permissão + trigger.
--
-- Pré-requisitos:
--   1. Migração 001_v1.7.0_lgpd_up.sql já aplicada.
--   2. Executar como superusuário (ou dono das tabelas), no banco do GymFlow:
--        psql -U postgres -d <banco> -v ON_ERROR_STOP=1 -v senha_app="'<SENHA_FORTE>'" -f opcional_role_app.sql
--   3. Depois, trocar a string de conexão do app (SQLALCHEMY_DATABASE_URI / .env)
--      para o usuário gymflow_app, reiniciar o serviço (NSSM) e validar em QAS.
--
-- Limitações / cuidados:
--   - Com este role o app NÃO consegue criar tabelas: db.create_all() passa a ser
--     inócuo para tabelas existentes, mas falha se um model novo exigir tabela nova.
--     Toda tabela nova deve vir por migração aplicada pelo dono do banco.
--   - Tabelas criadas no futuro pelo dono recebem os GRANTs por ALTER DEFAULT
--     PRIVILEGES (abaixo); revise os REVOKEs específicos a cada migração.
--   - O FingerPoint (bio_db.py) também acessa o banco direto; avalie se ele deve
--     usar outro role (pendência da v1.6.0 /api/v1).
--   - Rollback deste script: ver bloco ao final (comentado).
-- =============================================================================

SET client_encoding = 'UTF8';

BEGIN;

DO $r$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gymflow_app') THEN
        CREATE ROLE gymflow_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END;
$r$;

-- Senha passada por variável do psql (-v senha_app="'...'"); nunca grave a senha neste arquivo.
ALTER ROLE gymflow_app PASSWORD :senha_app;

GRANT CONNECT ON DATABASE :"DBNAME" TO gymflow_app;
GRANT USAGE ON SCHEMA public TO gymflow_app;
REVOKE CREATE ON SCHEMA public FROM gymflow_app;

-- Acesso geral às tabelas existentes do sistema
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO gymflow_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gymflow_app;
REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM gymflow_app;

-- LGPD: versões do termo são publicadas só pelo desenvolvedor (migração aplicada
-- pelo dono do banco) -> o app apenas lê.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON lgpd_termo_versao FROM gymflow_app;

-- LGPD: respostas dos usuários são append-only -> somente SELECT e INSERT
REVOKE UPDATE, DELETE, TRUNCATE ON lgpd_manifestacao FROM gymflow_app;

-- Controle de migrações: somente leitura para o app
REVOKE INSERT, UPDATE, DELETE ON schema_migracao FROM gymflow_app;

-- Tabelas/sequências criadas futuramente pelo role que executa este script
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gymflow_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO gymflow_app;

COMMIT;

-- -----------------------------------------------------------------------------
-- Rollback deste script (manual; executar como superusuário depois de voltar a
-- string de conexão do app para o usuário anterior):
--
-- BEGIN;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM gymflow_app;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM gymflow_app;
-- REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM gymflow_app;
-- REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM gymflow_app;
-- REVOKE ALL ON SCHEMA public FROM gymflow_app;
-- REVOKE CONNECT ON DATABASE <banco> FROM gymflow_app;
-- ALTER ROLE gymflow_app NOLOGIN;   -- desativa o login; a remoção do role fica a critério do Victor
-- COMMIT;
-- -----------------------------------------------------------------------------
