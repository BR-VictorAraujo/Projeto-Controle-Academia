# Migrações SQL do GymFlow

Scripts SQL numerados, aplicados manualmente com `psql`. Não há Alembic.
Cada migração tem um `_up.sql` (aplica) e um `_down.sql` (rollback).

| Ordem | Arquivo | Versão do app | Descrição |
|---|---|---|---|
| 001 | `001_v1.7.0_lgpd_up.sql` / `001_v1.7.0_lgpd_down.sql` | web 1.7.0 | LGPD: termos, versões, manifestações (append-only), auditoria de termos, `schema_migracao`, 2 chaves em `configuracoes` |
| - | `opcional_role_app.sql` | - | **Opcional, não aplicado automaticamente.** Role `gymflow_app` sem UPDATE/DELETE/TRUNCATE nas tabelas append-only |

O controle do que já foi aplicado fica na tabela `schema_migracao` (criada pela 001).
A aplicação verifica a linha `1.7.0_001_lgpd` para habilitar as telas de privacidade.

## Requisitos

- PostgreSQL 16 (a 001 usa `CREATE OR REPLACE TRIGGER`, disponível a partir do 14).
- `psql` do PostgreSQL instalado no servidor. No Windows, normalmente em
  `C:\Program Files\PostgreSQL\16\bin\psql.exe`.
- Arquivos `.sql` em UTF-8 (sem BOM). Os scripts executam `SET client_encoding = 'UTF8'`,
  então não é preciso mudar a codificação do console (`chcp`).

## Ordem de aplicação (QAS primeiro, depois produção)

1. **Backup** do banco (obrigatório):
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -U <usuario> -d <banco> -F c -f "D:\backup\gymflow_antes_001.dump"
   ```
2. **Aplicar a migração** (pode ser antes ou depois de copiar o código da v1.7.0; o script
   também funciona se o `db.create_all()` da v1.7.0 já tiver criado as tabelas):
   ```powershell
   cd <pasta do sistema>\migrations
   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f 001_v1.7.0_lgpd_up.sql
   ```
   O script roda em uma única transação (`BEGIN ... COMMIT`): ou aplica tudo ou nada.
   É idempotente: rodar de novo não duplica nada (mensagens `NOTICE ... already exists, skipping` são normais).
3. Copiar o código da v1.7.0 e reiniciar o serviço (NSSM).
4. **Verificar** (abaixo).

## Verificação após o up

```sql
-- migração registrada
SELECT * FROM schema_migracao WHERE versao = '1.7.0_001_lgpd';

-- 7 triggers
SELECT tgrelid::regclass AS tabela, tgname
  FROM pg_trigger
 WHERE NOT tgisinternal AND tgrelid::regclass::text LIKE 'lgpd_%'
 ORDER BY 1, 2;

-- seed: termo aviso_privacidade + versão 1.0 em rascunho
SELECT t.codigo, v.versao, v.status, v.conteudo_hash
  FROM lgpd_termo t JOIN lgpd_termo_versao v ON v.termo_id = t.id;

-- configurações (gate desligado)
SELECT chave, valor FROM configuracoes WHERE chave LIKE 'lgpd_%';
```

O `conteudo_hash` da versão 1.0 deve ser
`fd0a859c6185599f210f63df1858e82ff0657cf642e7294ea0b9dd71cf4cc6c7`, o mesmo valor calculado
em Python a partir de `docs/lgpd/modelo_aviso_privacidade.txt` (canonicalização da spec, §3).
Se for diferente, a codificação do texto foi corrompida na aplicação: não publique o termo.

A versão 1.0 é um **modelo** com trechos `[PREENCHER ...]`. Edite o rascunho pela tela de
administração antes de publicar (a aplicação bloqueia a publicação enquanto houver `[PREENCHER`).
Só ligue o gate (`lgpd_gate_ativo`) depois de publicar.

## Regras garantidas pelo banco (001)

- `lgpd_manifestacao` e `lgpd_termo_auditoria`: somente INSERT. UPDATE, DELETE e TRUNCATE geram erro.
  `registrado_em` é sempre a hora do servidor de banco.
- `lgpd_manifestacao`: só é aceita em versão `publicada`, com `conteudo_hash` igual ao da versão e
  manifestação compatível com o tipo do termo (`ciencia` -> `ciente`; `consentimento` -> `concordo`/`nao_concordo`).
- `lgpd_termo_versao`: versão publicada não pode ser alterada nem excluída; rascunho pode.
  Ao publicar, `publicado_em` é forçado para `now()`. Em todo INSERT/UPDATE, `conteudo_hash` precisa
  ser o SHA-256 do `conteudo` gravado.
- FKs `ON DELETE RESTRICT`: não é possível excluir usuário que tenha manifestação/auditoria registrada.
- Observação: um superusuário pode desabilitar triggers. Para uma segunda camada de proteção,
  ver `opcional_role_app.sql`.

## Rollback (down) - DESTRUTIVO

Remove as tabelas LGPD **com as manifestações**. Use apenas em banco sem manifestações reais
(dev/QAS) e sempre após `pg_dump`. Antes, volte o código para a versão anterior ou pare o serviço
(o `db.create_all()` da v1.7.0 recriaria as tabelas vazias).

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f 001_v1.7.0_lgpd_down.sql
```

Se `lgpd_manifestacao` tiver linhas, o script recusa a execução. Para forçar (consciente da perda):

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U <usuario> -d <banco> -v ON_ERROR_STOP=1 `
  -c "SET gymflow.forcar_down = 'on'" -f 001_v1.7.0_lgpd_down.sql
```

O down remove: os 7 triggers, as tabelas `lgpd_manifestacao`, `lgpd_termo_auditoria`,
`lgpd_termo_versao`, `lgpd_termo`, as 4 funções `fn_lgpd_*`, as chaves `lgpd_gate_ativo` e
`lgpd_contato_privacidade` de `configuracoes` e a linha `1.7.0_001_lgpd` de `schema_migracao`
(a tabela `schema_migracao` só é removida se ficar vazia).

Verificação após o down:

```sql
SELECT to_regclass('lgpd_termo'), to_regclass('lgpd_manifestacao');   -- ambos NULL
SELECT count(*) FROM configuracoes WHERE chave LIKE 'lgpd_%';           -- 0
```

## Testes manuais de imutabilidade (opcional, em dev/QAS)

Todos devem terminar em `ERROR` e nada é gravado (transação desfeita no final):

```sql
BEGIN;
UPDATE lgpd_manifestacao SET canal = 'x';
ROLLBACK;
BEGIN;
TRUNCATE lgpd_manifestacao;
ROLLBACK;
BEGIN;
TRUNCATE lgpd_termo_auditoria;
ROLLBACK;
```
