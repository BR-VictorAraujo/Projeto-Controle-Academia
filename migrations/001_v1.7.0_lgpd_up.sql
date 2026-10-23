-- =============================================================================
-- GymFlow web v1.7.0 - Migração 001: LGPD (ciência e consentimento)
-- Arquivo : migrations/001_v1.7.0_lgpd_up.sql
-- Rollback: migrations/001_v1.7.0_lgpd_down.sql
-- Banco   : PostgreSQL 16 (usa CREATE OR REPLACE TRIGGER, PG >= 14)
--
-- Idempotente: pode ser executada mais de uma vez. Funciona em banco sem as
-- tabelas e em banco onde db.create_all() já criou as tabelas a partir dos
-- models (sem triggers). Nenhuma tabela existente é alterada; apenas são
-- inseridas 2 chaves em configuracoes (ON CONFLICT DO NOTHING).
--
-- Aplicar:
--   psql -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f 001_v1.7.0_lgpd_up.sql
-- =============================================================================

-- O texto do seed é UTF-8. Sem isto, o psql no Windows pode usar WIN1252 como
-- codificação do cliente e corromper acentos (o hash deixaria de bater).
SET client_encoding = 'UTF8';

BEGIN;

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
    PERFORM pg_temp.lgpd_garante_coluna('schema_migracao', 'aplicada_em', false, 'now()');
END;
$do$;

-- -----------------------------------------------------------------------------
-- lgpd_termo
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lgpd_termo (
    id               serial PRIMARY KEY,
    codigo           varchar(40)  NOT NULL,
    titulo           varchar(150) NOT NULL,
    tipo             varchar(20)  NOT NULL,
    finalidade       text         NOT NULL,
    bloqueia_sistema boolean      NOT NULL DEFAULT true,
    ativo            boolean      NOT NULL DEFAULT true,
    criado_em        timestamptz  NOT NULL DEFAULT now()
);
DO $do$
BEGIN
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo', 'codigo',           true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo', 'titulo',           true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo', 'tipo',             true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo', 'finalidade',       true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo', 'bloqueia_sistema', true, 'true');
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo', 'ativo',            true, 'true');
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo', 'criado_em',        true, 'now()');
    PERFORM pg_temp.lgpd_garante_unique('lgpd_termo', 'lgpd_termo_codigo_key', ARRAY['codigo']);
    PERFORM pg_temp.lgpd_garante_check('lgpd_termo', 'ck_lgpd_termo_codigo',
           $c$codigo ~ '^[a-z0-9_]+$'$c$);
    PERFORM pg_temp.lgpd_garante_check('lgpd_termo', 'ck_lgpd_termo_tipo',
           $c$tipo IN ('ciencia', 'consentimento')$c$);
END;
$do$;

-- -----------------------------------------------------------------------------
-- lgpd_termo_versao
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lgpd_termo_versao (
    id                 serial PRIMARY KEY,
    termo_id           integer     NOT NULL,
    versao             varchar(20) NOT NULL,
    conteudo           text        NOT NULL,
    conteudo_hash      char(64)    NOT NULL,
    alteracao_material boolean     NOT NULL DEFAULT true,
    resumo_alteracoes  text,
    status             varchar(20) NOT NULL DEFAULT 'rascunho',
    vigencia_inicio    timestamptz,
    criado_por         integer,
    criado_em          timestamptz NOT NULL DEFAULT now(),
    publicado_por      integer,
    publicado_em       timestamptz
);
DO $do$
BEGIN
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'termo_id',           true,  NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'versao',             true,  NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'conteudo',           true,  NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'conteudo_hash',      true,  NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'alteracao_material', true,  'true');
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'resumo_alteracoes',  false, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'status',             true,  $d$'rascunho'$d$);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'vigencia_inicio',    false, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'criado_por',         false, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'criado_em',          true,  'now()');
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'publicado_por',      false, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_versao', 'publicado_em',       false, NULL);
    PERFORM pg_temp.lgpd_garante_fk('lgpd_termo_versao', 'lgpd_termo_versao_termo_id_fkey',      'termo_id',      'lgpd_termo', 'id');
    PERFORM pg_temp.lgpd_garante_fk('lgpd_termo_versao', 'lgpd_termo_versao_criado_por_fkey',    'criado_por',    'usuarios',   'id');
    PERFORM pg_temp.lgpd_garante_fk('lgpd_termo_versao', 'lgpd_termo_versao_publicado_por_fkey', 'publicado_por', 'usuarios',   'id');
    PERFORM pg_temp.lgpd_garante_unique('lgpd_termo_versao', 'uq_lgpd_versao_termo_versao', ARRAY['termo_id', 'versao']);
    PERFORM pg_temp.lgpd_garante_check('lgpd_termo_versao', 'ck_lgpd_versao_status',
           $c$status IN ('rascunho', 'publicada')$c$);
    PERFORM pg_temp.lgpd_garante_check('lgpd_termo_versao', 'ck_lgpd_versao_vigencia',
           $c$status <> 'publicada' OR vigencia_inicio IS NOT NULL$c$);
END;
$do$;
CREATE INDEX IF NOT EXISTS ix_lgpd_termo_versao_termo_status_vig
    ON lgpd_termo_versao (termo_id, status, vigencia_inicio);

-- -----------------------------------------------------------------------------
-- lgpd_manifestacao (append-only)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lgpd_manifestacao (
    id              bigserial PRIMARY KEY,
    usuario_id      integer     NOT NULL,
    termo_versao_id integer     NOT NULL,
    manifestacao    varchar(20) NOT NULL,
    conteudo_hash   char(64)    NOT NULL,
    app_versao      varchar(20) NOT NULL,
    canal           varchar(20) NOT NULL DEFAULT 'web',
    registrado_em   timestamptz NOT NULL DEFAULT now()
);
DO $do$
BEGIN
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'usuario_id',      true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'termo_versao_id', true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'manifestacao',    true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'conteudo_hash',   true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'app_versao',      true, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'canal',           true, $d$'web'$d$);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_manifestacao', 'registrado_em',   true, 'now()');
    PERFORM pg_temp.lgpd_garante_fk('lgpd_manifestacao', 'lgpd_manifestacao_usuario_id_fkey',      'usuario_id',      'usuarios',          'id');
    PERFORM pg_temp.lgpd_garante_fk('lgpd_manifestacao', 'lgpd_manifestacao_termo_versao_id_fkey', 'termo_versao_id', 'lgpd_termo_versao', 'id');
    PERFORM pg_temp.lgpd_garante_check('lgpd_manifestacao', 'ck_lgpd_manifestacao_valor',
           $c$manifestacao IN ('ciente', 'concordo', 'nao_concordo')$c$);
END;
$do$;
CREATE INDEX IF NOT EXISTS ix_lgpd_manifestacao_usuario_versao
    ON lgpd_manifestacao (usuario_id, termo_versao_id, id DESC);

-- -----------------------------------------------------------------------------
-- lgpd_termo_auditoria (append-only)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lgpd_termo_auditoria (
    id              bigserial PRIMARY KEY,
    termo_id        integer,
    termo_versao_id integer,
    acao            varchar(40) NOT NULL,
    usuario_id      integer,
    detalhes        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    registrado_em   timestamptz NOT NULL DEFAULT now()
);
DO $do$
BEGIN
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_auditoria', 'termo_id',        false, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_auditoria', 'termo_versao_id', false, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_auditoria', 'acao',            true,  NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_auditoria', 'usuario_id',      false, NULL);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_auditoria', 'detalhes',        true,  $d$'{}'::jsonb$d$);
    PERFORM pg_temp.lgpd_garante_coluna('lgpd_termo_auditoria', 'registrado_em',   true,  'now()');
    PERFORM pg_temp.lgpd_garante_fk('lgpd_termo_auditoria', 'lgpd_termo_auditoria_termo_id_fkey',        'termo_id',        'lgpd_termo',        'id');
    PERFORM pg_temp.lgpd_garante_fk('lgpd_termo_auditoria', 'lgpd_termo_auditoria_termo_versao_id_fkey', 'termo_versao_id', 'lgpd_termo_versao', 'id');
    PERFORM pg_temp.lgpd_garante_fk('lgpd_termo_auditoria', 'lgpd_termo_auditoria_usuario_id_fkey',      'usuario_id',      'usuarios',          'id');
END;
$do$;

-- -----------------------------------------------------------------------------
-- Funções de trigger
-- -----------------------------------------------------------------------------

-- Versão publicada é imutável (UPDATE/DELETE bloqueados). Rascunho pode ser
-- editado/excluído. Ao publicar (INSERT já publicada ou UPDATE rascunho->publicada)
-- publicado_em é forçado para now(). Em INSERT/UPDATE, conteudo_hash precisa ser o
-- SHA-256 do conteúdo armazenado (a aplicação grava o texto já canonicalizado).
CREATE OR REPLACE FUNCTION fn_lgpd_termo_versao_protege()
RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.status = 'publicada' THEN
        RAISE EXCEPTION 'lgpd_termo_versao %: versão publicada não pode ser alterada nem excluída', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;

    IF NEW.conteudo_hash IS DISTINCT FROM encode(sha256(convert_to(NEW.conteudo, 'UTF8')), 'hex') THEN
        RAISE EXCEPTION 'lgpd_termo_versao: conteudo_hash não corresponde ao SHA-256 do conteúdo'
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.status = 'publicada' THEN
        NEW.publicado_em := now();
    END IF;

    RETURN NEW;
END;
$f$;

-- Validação de manifestação (BEFORE INSERT).
CREATE OR REPLACE FUNCTION fn_lgpd_manifestacao_valida()
RETURNS trigger LANGUAGE plpgsql AS $f$
DECLARE
    v_status text;
    v_hash   text;
    v_tipo   text;
BEGIN
    NEW.registrado_em := now();

    SELECT v.status, v.conteudo_hash, t.tipo
      INTO v_status, v_hash, v_tipo
      FROM lgpd_termo_versao v
      JOIN lgpd_termo t ON t.id = v.termo_id
     WHERE v.id = NEW.termo_versao_id
       FOR SHARE OF v;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'lgpd_manifestacao: versão de termo % inexistente', NEW.termo_versao_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF v_status <> 'publicada' THEN
        RAISE EXCEPTION 'lgpd_manifestacao: versão % não está publicada', NEW.termo_versao_id
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.conteudo_hash::text IS DISTINCT FROM v_hash THEN
        RAISE EXCEPTION 'lgpd_manifestacao: conteudo_hash difere do hash da versão %', NEW.termo_versao_id
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_tipo = 'ciencia' AND NEW.manifestacao <> 'ciente' THEN
        RAISE EXCEPTION 'lgpd_manifestacao: termo de ciência aceita apenas ciente'
            USING ERRCODE = 'check_violation';
    ELSIF v_tipo = 'consentimento' AND NEW.manifestacao NOT IN ('concordo', 'nao_concordo') THEN
        RAISE EXCEPTION 'lgpd_manifestacao: termo de consentimento aceita apenas concordo ou nao_concordo'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$f$;

-- Auditoria de termos: força registrado_em (BEFORE INSERT).
CREATE OR REPLACE FUNCTION fn_lgpd_auditoria_registra()
RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    NEW.registrado_em := now();
    IF NEW.detalhes IS NULL THEN
        NEW.detalhes := '{}'::jsonb;
    END IF;
    RETURN NEW;
END;
$f$;

-- Bloqueio genérico de UPDATE/DELETE/TRUNCATE nas tabelas append-only.
CREATE OR REPLACE FUNCTION fn_lgpd_somente_insercao()
RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    RAISE EXCEPTION '%: tabela somente de inserção (append-only); % não permitido', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$f$;

-- -----------------------------------------------------------------------------
-- Triggers (CREATE OR REPLACE TRIGGER: idempotente, PostgreSQL >= 14)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TRIGGER lgpd_termo_versao_protege
    BEFORE INSERT OR UPDATE OR DELETE ON lgpd_termo_versao
    FOR EACH ROW EXECUTE FUNCTION fn_lgpd_termo_versao_protege();

CREATE OR REPLACE TRIGGER lgpd_manifestacao_valida
    BEFORE INSERT ON lgpd_manifestacao
    FOR EACH ROW EXECUTE FUNCTION fn_lgpd_manifestacao_valida();

CREATE OR REPLACE TRIGGER lgpd_manifestacao_imutavel
    BEFORE UPDATE OR DELETE ON lgpd_manifestacao
    FOR EACH ROW EXECUTE FUNCTION fn_lgpd_somente_insercao();

CREATE OR REPLACE TRIGGER lgpd_manifestacao_sem_truncate
    BEFORE TRUNCATE ON lgpd_manifestacao
    FOR EACH STATEMENT EXECUTE FUNCTION fn_lgpd_somente_insercao();

CREATE OR REPLACE TRIGGER lgpd_termo_auditoria_registra
    BEFORE INSERT ON lgpd_termo_auditoria
    FOR EACH ROW EXECUTE FUNCTION fn_lgpd_auditoria_registra();

CREATE OR REPLACE TRIGGER lgpd_termo_auditoria_imutavel
    BEFORE UPDATE OR DELETE ON lgpd_termo_auditoria
    FOR EACH ROW EXECUTE FUNCTION fn_lgpd_somente_insercao();

CREATE OR REPLACE TRIGGER lgpd_termo_auditoria_sem_truncate
    BEFORE TRUNCATE ON lgpd_termo_auditoria
    FOR EACH STATEMENT EXECUTE FUNCTION fn_lgpd_somente_insercao();

-- -----------------------------------------------------------------------------
-- Configurações (tabela existente; nada é sobrescrito)
-- -----------------------------------------------------------------------------
INSERT INTO configuracoes (chave, valor, descricao) VALUES
    ('lgpd_gate_ativo', '0', 'LGPD: exigir manifestação sobre os termos após o login (1 = ligado)'),
    ('lgpd_contato_privacidade', '', 'LGPD: canal de contato exibido na tela de recusa')
ON CONFLICT (chave) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Seed: termo aviso_privacidade + versão 1.0 em rascunho.
-- Texto = docs/lgpd/modelo_aviso_privacidade.txt. O texto é canonicalizado aqui
-- mesmo (igual ao canonicalizar() do Python, spec §3): \r\n e \r -> \n, sem
-- espaços/tabs no fim das linhas, sem linhas vazias no fim, Unicode NFC. Assim o
-- hash não muda se este arquivo for salvo com CRLF (git autocrlf no Windows).
-- Não publicar sem antes substituir os trechos [PREENCHER ...] (a aplicação
-- bloqueia a publicação).
-- -----------------------------------------------------------------------------
INSERT INTO lgpd_termo (codigo, titulo, tipo, finalidade, bloqueia_sistema, ativo)
VALUES ('aviso_privacidade', 'Aviso de Privacidade', 'ciencia',
        'Informar aos usuários do sistema como seus dados pessoais são tratados pela academia.',
        true, true)
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO lgpd_termo_versao (termo_id, versao, conteudo, conteudo_hash, alteracao_material, status)
SELECT t.id, '1.0', c.conteudo, encode(sha256(convert_to(c.conteudo, 'UTF8')), 'hex'), true, 'rascunho'
  FROM lgpd_termo t
 CROSS JOIN (
    SELECT normalize(
               rtrim(
                   regexp_replace(
                       regexp_replace(
                           replace(replace(s.bruto, E'\r\n', E'\n'), E'\r', E'\n'),
                           E'[ \t]+\n', E'\n', 'g'),
                       E'[ \t]+$', ''),
                   E'\n'),
               NFC) AS conteudo
      FROM (SELECT $termo$AVISO DE PRIVACIDADE AOS USUÁRIOS DO SISTEMA GYMFLOW

ATENÇÃO (remova este parágrafo antes de publicar): este é um MODELO. Todos os trechos marcados com [PREENCHER ...] devem ser completados pela academia, e o texto final deve ser revisado pela academia e, preferencialmente, por um profissional jurídico ou especialista em proteção de dados. Este modelo não garante conformidade com a LGPD.

1. Quem é o responsável pelos seus dados
A academia [PREENCHER: razão social], CNPJ [PREENCHER: CNPJ], com sede em [PREENCHER: endereço], é a responsável (controladora) pelo tratamento dos dados pessoais descritos neste aviso, nos termos da Lei nº 13.709/2018 (LGPD).
O sistema GymFlow é um software utilizado pela academia. Os dados ficam armazenados no ambiente operado pela academia.

2. A quem se aplica
Este aviso se aplica às pessoas que possuem usuário de acesso ao sistema GymFlow (funcionários, colaboradores e administradores autorizados pela academia).

3. Quais dados seus o sistema utiliza
- Nome, nome de usuário e perfil de acesso.
- Senha, armazenada somente em formato protegido (hash), sem possibilidade de leitura.
- Data e hora de criação do usuário, do último acesso e da última troca de senha.
- Registro das ações realizadas no sistema (auditoria), vinculado ao seu usuário.
- Registro das suas manifestações sobre os termos de privacidade (termo, versão, data e hora e resposta).
[PREENCHER: outras categorias de dados, se houver]

4. Para que esses dados são utilizados
- Controlar o acesso ao sistema e garantir que cada pessoa use apenas as funções permitidas ao seu perfil.
- Manter o registro das operações realizadas, para segurança e apuração de erros ou irregularidades.
- Comprovar que você foi informado(a) sobre este aviso e registrar suas manifestações.
[PREENCHER: outras finalidades definidas pela academia e as respectivas bases legais]

5. Armazenamento, acesso e compartilhamento
- Os dados ficam armazenados no banco de dados instalado no ambiente da academia: [PREENCHER: local/servidor].
- O acesso aos registros é restrito às pessoas autorizadas pela academia com perfil de administrador.
- [PREENCHER: com quem os dados são compartilhados, se houver, e por quê; ou declarar que não há compartilhamento]

6. Por quanto tempo os dados são mantidos
[PREENCHER: prazo de retenção de cada categoria de dado e critério de descarte]
Os registros de manifestação sobre os termos são mantidos para comprovação e não são alterados pelas operações comuns do sistema.

7. Seus direitos
Conforme o art. 18 da LGPD, você pode solicitar à academia:
- confirmação da existência de tratamento e acesso aos seus dados;
- correção de dados incompletos, inexatos ou desatualizados;
- anonimização, bloqueio ou eliminação de dados desnecessários, excessivos ou tratados em desconformidade com a lei;
- portabilidade, nos termos da regulamentação;
- informação sobre com quem seus dados foram compartilhados;
- informação sobre a possibilidade de não fornecer consentimento e suas consequências, quando o consentimento for a base legal;
- revogação do consentimento, quando ele for a base legal do tratamento.

8. Como entrar em contato
Para dúvidas ou solicitações sobre seus dados pessoais, procure: [PREENCHER: nome do responsável ou encarregado], pelo canal [PREENCHER: e-mail, telefone ou local de atendimento].

9. Sobre este aviso
Ao clicar em "Li e estou ciente", você registra que leu este aviso. Isso não é um consentimento e não autoriza nenhum tratamento além dos descritos acima. Quando a academia alterar este aviso de forma relevante, uma nova versão será apresentada.$termo$::text AS bruto) s
 ) c
 WHERE t.codigo = 'aviso_privacidade'
ON CONFLICT (termo_id, versao) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Registro da migração (o app verifica esta linha para habilitar a funcionalidade)
-- -----------------------------------------------------------------------------
INSERT INTO schema_migracao (versao, descricao)
VALUES ('1.7.0_001_lgpd', 'LGPD: termos, versões, manifestações (append-only) e auditoria de termos')
ON CONFLICT (versao) DO NOTHING;

COMMIT;
