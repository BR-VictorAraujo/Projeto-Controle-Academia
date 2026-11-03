-- =============================================================================
-- GymFlow web v1.7.0 - Migração 001: LGPD (termo de uso e privacidade)
-- Arquivo : migrations/001_v1.7.0_lgpd_up.sql
-- Rollback: migrations/001_v1.7.0_lgpd_down.sql
-- Banco   : PostgreSQL 16 (usa CREATE OR REPLACE TRIGGER, PG >= 14)
--
-- Cria a estrutura do termo único (versões imutáveis) e das respostas dos
-- usuários (append-only). NÃO publica nenhuma versão do termo: versões são
-- publicadas por migrações geradas com tools/gerar_migracao_termo.py.
-- Sem versão publicada, o sistema se comporta exatamente como antes.
--
-- Idempotente e transacional. Funciona em banco sem as tabelas e em banco onde
-- db.create_all() já criou as tabelas a partir dos models (sem triggers).
-- Nenhuma tabela existente é alterada.
--
-- Aplicar:
--   psql -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f 001_v1.7.0_lgpd_up.sql
-- =============================================================================

-- Evita que o psql no Windows use WIN1252 como codificação do cliente.
SET client_encoding = 'UTF8';

BEGIN;

-- -----------------------------------------------------------------------------
-- Recusa se encontrar a estrutura da 001 ANTIGA (modelo com vários termos),
-- que nunca foi para QAS/produção. Nesse caso aplique antes o down 001.
-- -----------------------------------------------------------------------------
DO $antiga$
BEGIN
    IF to_regclass('lgpd_termo') IS NOT NULL
       OR to_regclass('lgpd_termo_auditoria') IS NOT NULL
       OR EXISTS (SELECT 1 FROM pg_attribute
                   WHERE attrelid = to_regclass('lgpd_termo_versao')
                     AND attname IN ('termo_id', 'status') AND NOT attisdropped)
       OR EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = to_regclass('lgpd_manifestacao')
                     AND conname = 'ck_lgpd_manifestacao_valor'
                     AND pg_get_constraintdef(oid) NOT LIKE '%nao_aceito%') THEN
        RAISE EXCEPTION 'Estrutura da migração 001 ANTIGA encontrada (lgpd_termo / lgpd_termo_auditoria / colunas antigas). Faça pg_dump, aplique 001_v1.7.0_lgpd_down.sql e rode este up novamente.';
    END IF;
END;
$antiga$;

-- -----------------------------------------------------------------------------
-- Funções auxiliares temporárias (schema pg_temp: somem ao fim da sessão)
-- -----------------------------------------------------------------------------

-- Retorna os attnums (ordenados) das colunas informadas.
CREATE FUNCTION pg_temp.lgpd_attnums(p_tabela regclass, p_colunas text[])
RETURNS smallint[] LANGUAGE sql STABLE AS $f$
    SELECT array_agg(a.attnum ORDER BY a.attnum)::smallint[]
      FROM pg_attribute a
     WHERE a.attrelid = p_tabela
       AND a.attname = ANY (p_colunas)
       AND NOT a.attisdropped;
$f$;

-- CHECK: cria se não existir constraint com esse nome na tabela.
CREATE FUNCTION pg_temp.lgpd_garante_check(p_tabela regclass, p_nome text, p_expr text)
RETURNS void LANGUAGE plpgsql AS $f$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = p_tabela AND conname = p_nome AND contype = 'c') THEN
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I CHECK (%s)', p_tabela, p_nome, p_expr);
    END IF;
END;
$f$;

-- UNIQUE: cria se não existir UNIQUE/PK (ou índice único) com o mesmo conjunto de colunas.
CREATE FUNCTION pg_temp.lgpd_garante_unique(p_tabela regclass, p_nome text, p_colunas text[])
RETURNS void LANGUAGE plpgsql AS $f$
DECLARE
    v_cols smallint[] := pg_temp.lgpd_attnums(p_tabela, p_colunas);
BEGIN
    IF v_cols IS NULL OR cardinality(v_cols) <> cardinality(p_colunas) THEN
        RAISE EXCEPTION 'Tabela % sem as colunas %', p_tabela, p_colunas;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
         WHERE i.indrelid = p_tabela
           AND i.indisunique
           AND i.indpred IS NULL
           AND i.indexprs IS NULL
           AND (SELECT array_agg(k ORDER BY k) FROM unnest(i.indkey::smallint[]) k) = v_cols
    ) THEN
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I UNIQUE (%s)', p_tabela, p_nome,
                       (SELECT string_agg(quote_ident(c), ', ') FROM unnest(p_colunas) c));
    END IF;
END;
$f$;

-- FK ON DELETE RESTRICT: cria se não existir FK na coluna apontando para a tabela.
-- Se existir com outra ação de exclusão (ex.: NO ACTION, quando o model não
-- declara ondelete), adiciona a FK RESTRICT nomeada ao lado da existente e emite
-- WARNING (nada é removido).
CREATE FUNCTION pg_temp.lgpd_garante_fk(p_tabela regclass, p_nome text, p_coluna text,
                                        p_ref regclass, p_ref_coluna text)
RETURNS void LANGUAGE plpgsql AS $f$
DECLARE
    v_col smallint[] := pg_temp.lgpd_attnums(p_tabela, ARRAY[p_coluna]);
BEGIN
    IF v_col IS NULL THEN
        RAISE EXCEPTION 'Tabela % sem a coluna %', p_tabela, p_coluna;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid = p_tabela AND contype = 'f'
                  AND conkey = v_col AND confrelid = p_ref AND confdeltype = 'r') THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid = p_tabela AND contype = 'f'
                  AND conkey = v_col AND confrelid = p_ref) THEN
        RAISE WARNING 'FK existente em %.% -> % nao e ON DELETE RESTRICT; adicionando % (RESTRICT). Corrija o model.',
                      p_tabela, p_coluna, p_ref, p_nome;
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = p_tabela AND conname = p_nome) THEN
            p_nome := p_nome || '_restrict';
        END IF;
    END IF;
    EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %s (%I) ON DELETE RESTRICT',
                   p_tabela, p_nome, p_coluna, p_ref, p_ref_coluna);
END;
$f$;

-- Garante DEFAULT e NOT NULL (idempotente; cobre model que usou default= em vez de server_default=).
CREATE FUNCTION pg_temp.lgpd_garante_coluna(p_tabela regclass, p_coluna text,
                                            p_not_null boolean, p_default text)
RETURNS void LANGUAGE plpgsql AS $f$
BEGIN
    IF pg_temp.lgpd_attnums(p_tabela, ARRAY[p_coluna]) IS NULL THEN
        RAISE EXCEPTION 'Tabela % sem a coluna % (model divergente da migracao)', p_tabela, p_coluna;
    END IF;
    IF p_default IS NOT NULL THEN
        EXECUTE format('ALTER TABLE %s ALTER COLUMN %I SET DEFAULT %s', p_tabela, p_coluna, p_default);
    END IF;
    IF p_not_null THEN
        EXECUTE format('ALTER TABLE %s ALTER COLUMN %I SET NOT NULL', p_tabela, p_coluna);
    END IF;
END;
$f$;


-- -----------------------------------------------------------------------------
-- schema_migracao
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migracao (
    versao      varchar(40) PRIMARY KEY,
    descricao   text,
    aplicada_em timestamptz DEFAULT now()
);
DO $do$
BEGIN
    PERFORM pg_temp.lgpd_garante_coluna('schema_migracao', 'descricao',   false, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('schema_migracao', 'aplicada_em', false, 'now()');
END;
$do$;

-- -----------------------------------------------------------------------------
-- lgpd_termo_versao: versões do termo único. Toda versão é publicada no INSERT
-- e é imutável (UPDATE/DELETE/TRUNCATE bloqueados por trigger).
-- Versão vigente = maior publicado_em (desempate: maior id).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lgpd_termo_versao (
    id            serial      PRIMARY KEY,
    versao        varchar(20) NOT NULL,
    conteudo      text        NOT NULL,
    conteudo_hash char(64)    NOT NULL,
    publicado_em  timestamptz NOT NULL DEFAULT now()
);
DO $do$
BEGIN
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'versao',        true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'conteudo',      true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'conteudo_hash', true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'publicado_em',  true, 'now()');
    PERFORM pg_temp.lgpd_garante_unique('lgpd_termo_versao', 'uq_lgpd_termo_versao_versao', ARRAY['versao']);
END;
$do$;

-- -----------------------------------------------------------------------------
-- lgpd_manifestacao: respostas dos usuários (append-only)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lgpd_manifestacao (
    id              bigserial   PRIMARY KEY,
    usuario_id      integer     NOT NULL,
    termo_versao_id integer     NOT NULL,
    manifestacao    varchar(20) NOT NULL,
    conteudo_hash   char(64)    NOT NULL,
    app_versao      varchar(20) NOT NULL,
    registrado_em   timestamptz NOT NULL DEFAULT now()
);
DO $do$
BEGIN
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'usuario_id',      true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'termo_versao_id', true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'manifestacao',    true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'conteudo_hash',   true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'app_versao',      true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'registrado_em',   true, 'now()');
    PERFORM pg_temp.lgpd_garante_fk('lgpd_manifestacao', 'lgpd_manifestacao_usuario_id_fkey',      'usuario_id',      'usuarios',          'id');
    PERFORM pg_temp.lgpd_garante_fk('lgpd_manifestacao', 'lgpd_manifestacao_termo_versao_id_fkey', 'termo_versao_id', 'lgpd_termo_versao', 'id');
    PERFORM pg_temp.lgpd_garante_check('lgpd_manifestacao', 'ck_lgpd_manifestacao_valor',
            $c$manifestacao IN ('aceito', 'nao_aceito')$c$);
END;
$do$;
CREATE INDEX IF NOT EXISTS ix_lgpd_manifestacao_usuario_versao
    ON lgpd_manifestacao (usuario_id, termo_versao_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_lgpd_manifestacao_registrado_em
    ON lgpd_manifestacao (registrado_em DESC);

-- -----------------------------------------------------------------------------
-- Funções de trigger
-- -----------------------------------------------------------------------------

-- Publicação de versão (BEFORE INSERT): conteudo_hash precisa ser o SHA-256 do
-- conteúdo gravado; publicado_em é sempre a hora do servidor de banco.
CREATE OR REPLACE FUNCTION fn_lgpd_termo_versao_valida()
RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    IF NEW.conteudo_hash::text IS DISTINCT FROM encode(sha256(convert_to(NEW.conteudo, 'UTF8')), 'hex') THEN
        RAISE EXCEPTION 'lgpd_termo_versao: conteudo_hash não corresponde ao SHA-256 do conteúdo'
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.publicado_em := now();
    RETURN NEW;
END;
$f$;

-- Resposta do usuário (BEFORE INSERT): registrado_em forçado; a versão precisa
-- existir e o hash apresentado precisa ser igual ao da versão.
CREATE OR REPLACE FUNCTION fn_lgpd_manifestacao_valida()
RETURNS trigger LANGUAGE plpgsql AS $f$
DECLARE
    v_hash text;
BEGIN
    NEW.registrado_em := now();

    SELECT v.conteudo_hash INTO v_hash
      FROM lgpd_termo_versao v
     WHERE v.id = NEW.termo_versao_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'lgpd_manifestacao: versão de termo % inexistente', NEW.termo_versao_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF NEW.conteudo_hash::text IS DISTINCT FROM v_hash THEN
        RAISE EXCEPTION 'lgpd_manifestacao: conteudo_hash difere do hash da versão %', NEW.termo_versao_id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$f$;

-- Bloqueio genérico de UPDATE/DELETE/TRUNCATE (tabelas imutáveis/append-only).
CREATE OR REPLACE FUNCTION fn_lgpd_somente_insercao()
RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    RAISE EXCEPTION '%: tabela somente de inserção; % não permitido', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$f$;

-- -----------------------------------------------------------------------------
-- Triggers (CREATE OR REPLACE TRIGGER: idempotente)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TRIGGER lgpd_termo_versao_valida
    BEFORE INSERT ON lgpd_termo_versao
    FOR EACH ROW EXECUTE FUNCTION fn_lgpd_termo_versao_valida();

CREATE OR REPLACE TRIGGER lgpd_termo_versao_imutavel
    BEFORE UPDATE OR DELETE ON lgpd_termo_versao
    FOR EACH ROW EXECUTE FUNCTION fn_lgpd_somente_insercao();

CREATE OR REPLACE TRIGGER lgpd_termo_versao_sem_truncate
    BEFORE TRUNCATE ON lgpd_termo_versao
    FOR EACH STATEMENT EXECUTE FUNCTION fn_lgpd_somente_insercao();

CREATE OR REPLACE TRIGGER lgpd_manifestacao_valida
    BEFORE INSERT ON lgpd_manifestacao
    FOR EACH ROW EXECUTE FUNCTION fn_lgpd_manifestacao_valida();

CREATE OR REPLACE TRIGGER lgpd_manifestacao_imutavel
    BEFORE UPDATE OR DELETE ON lgpd_manifestacao
    FOR EACH ROW EXECUTE FUNCTION fn_lgpd_somente_insercao();

CREATE OR REPLACE TRIGGER lgpd_manifestacao_sem_truncate
    BEFORE TRUNCATE ON lgpd_manifestacao
    FOR EACH STATEMENT EXECUTE FUNCTION fn_lgpd_somente_insercao();

-- -----------------------------------------------------------------------------
-- Registro da migração (o app verifica esta linha)
-- -----------------------------------------------------------------------------
INSERT INTO schema_migracao (versao, descricao)
VALUES ('1.7.0_001_lgpd', 'LGPD: versões do termo (imutáveis) e respostas dos usuários (append-only)')
ON CONFLICT (versao) DO NOTHING;

COMMIT;
