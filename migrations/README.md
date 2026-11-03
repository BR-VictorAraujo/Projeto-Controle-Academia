# Migrações SQL do GymFlow

Scripts SQL numerados, aplicados manualmente com `psql`. Não há Alembic.
O que já foi aplicado fica registrado na tabela `schema_migracao` (criada pela 001).

| Arquivo | Versão do app | Descrição |
|---|---|---|
| `001_v1.7.0_lgpd_up.sql` / `001_v1.7.0_lgpd_down.sql` | web 1.7.0 | Estrutura LGPD: `lgpd_termo_versao` (versões imutáveis do termo), `lgpd_manifestacao` (respostas, append-only), `schema_migracao`. **Não publica termo.** |
| `NNN_termo_v<versao>.sql` | - | Publica uma versão do termo. Gerado por `tools/gerar_migracao_termo.py`. Sem rollback. |
| `opcional_role_app.sql` | - | **Opcional, não aplicado automaticamente.** Role `gymflow_app` sem permissão de alterar as tabelas LGPD. |

Sem nenhuma versão de termo publicada, o sistema se comporta exatamente como antes.
Com uma versão publicada, todo usuário precisa responder "Aceito" no pop-up para usar o sistema.

## Requisitos

- PostgreSQL 16 (a 001 usa `CREATE OR REPLACE TRIGGER`, disponível a partir do 14).
- `psql` do PostgreSQL instalado. No Windows, normalmente
  `C:\Program Files\PostgreSQL\16\bin\psql.exe`.
- Os scripts executam `SET client_encoding = 'UTF8'`; não é preciso mudar a codificação do console.
  Funcionam salvos com LF ou CRLF.

## 1. Estrutura (001) - uma vez, junto com o deploy da v1.7.0

Ordem: QAS primeiro, depois produção.

1. Backup (obrigatório):
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -U <usuario> -d <banco> -F c -f "D:\backup\gymflow_antes_001.dump"
   ```
2. Aplicar:
   ```powershell
   cd <pasta do sistema>\migrations
   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f 001_v1.7.0_lgpd_up.sql
   ```
   Roda em uma única transação e é idempotente (`NOTICE ... already exists, skipping` é normal).
   Funciona também se o `db.create_all()` da v1.7.0 já tiver criado as tabelas.
   Se encontrar a estrutura da 001 antiga (só existiu no dev), recusa e pede para aplicar o down antes.
3. Copiar o código da v1.7.0 e reiniciar o serviço (NSSM).
4. Verificar:
   ```sql
   SELECT * FROM schema_migracao;                                   -- 1.7.0_001_lgpd
   SELECT tgrelid::regclass, tgname FROM pg_trigger
    WHERE NOT tgisinternal AND tgrelid::regclass::text LIKE 'lgpd_%' ORDER BY 1, 2;   -- 6 triggers
   SELECT count(*) FROM lgpd_termo_versao;                          -- 0 (nenhum termo publicado)
   ```

## 2. Publicar uma versão do termo (somente o desenvolvedor)

O texto não é editável pela interface. Cada versão é um arquivo versionado no git e uma migração.

1. **Editar o texto**: copie `docs/lgpd/termo/MODELO.txt` para `docs/lgpd/termo/termo_v<versao>.txt`
   (ex.: `termo_v1.0.txt`), complete todos os trechos `[PREENCHER ...]`, remova o parágrafo de
   ATENÇÃO e faça a revisão do texto com a academia. Salve em UTF-8.
2. **Gerar a migração** (só biblioteca padrão do Python):
   ```powershell
   python tools\gerar_migracao_termo.py docs\lgpd\termo\termo_v1.0.txt 1.0
   ```
   A ferramenta recusa o texto se ainda houver `[PREENCHER`, canonicaliza o texto (mesma regra de
   `app/lgpd.py`), imprime o SHA-256 e gera `migrations/NNN_termo_v1.0.sql` (NNN = próximo número livre).
   Recusa também versão repetida. Para teste, `--saida <pasta>` grava em outro diretório.
3. **Testar em QAS**: backup, aplicar a migração gerada, entrar no sistema e conferir o pop-up, o
   texto e o hash na aba Configurações > LGPD.
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f 002_termo_v1.0.sql
   ```
4. **Produção**: aplicar a mesma migração junto com o deploy (backup antes). Commitar o `.txt`
   e o `.sql` gerado.

Cuidados:
- Publicar é **irreversível** pelo banco: a versão não pode ser alterada nem excluída. Para corrigir
  o texto, publique uma nova versão (todos precisarão aceitar de novo).
- A migração gerada é idempotente; se a versão já existir com outro texto, ela aborta.
- O trigger confere que o hash gravado é o SHA-256 do texto; se a codificação for corrompida
  no caminho, a migração falha em vez de publicar texto errado.

## Regras garantidas pelo banco

- `lgpd_termo_versao`: só INSERT. UPDATE, DELETE e TRUNCATE geram erro. `conteudo_hash` precisa ser
  o SHA-256 do `conteudo`; `publicado_em` é sempre a hora do servidor. `versao` é única.
  Versão vigente = maior `publicado_em` (desempate: maior `id`).
- `lgpd_manifestacao`: só INSERT. Resposta só `aceito` ou `nao_aceito`; `conteudo_hash` igual ao da
  versão; `registrado_em` é sempre a hora do servidor.
- FKs `ON DELETE RESTRICT`: não é possível excluir usuário que tenha resposta registrada.
- Um superusuário pode desabilitar triggers; para uma segunda camada, ver `opcional_role_app.sql`.

## Rollback da 001 (down) - DESTRUTIVO

Remove as versões do termo e **as respostas de todos os usuários**, os triggers e as funções. Remove
também os objetos da 001 antiga (`lgpd_termo`, `lgpd_termo_auditoria`, funções antigas e as chaves
`lgpd_gate_ativo`/`lgpd_contato_privacidade`), se existirem, e as linhas da 001 e das `NNN_termo_v*`
em `schema_migracao` (a tabela só é removida se ficar vazia).
Usar apenas em dev/QAS, sempre após `pg_dump`, com o serviço web parado (o `db.create_all()` da
v1.7.0 recriaria as tabelas vazias).

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f 001_v1.7.0_lgpd_down.sql
```

Se houver respostas registradas, o script recusa. Para forçar (consciente da perda):

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U <usuario> -d <banco> -v ON_ERROR_STOP=1 `
  -c "SET gymflow.forcar_down = 'on'" -f 001_v1.7.0_lgpd_down.sql
```

Verificação após o down:

```sql
SELECT to_regclass('lgpd_termo_versao'), to_regclass('lgpd_manifestacao'), to_regclass('lgpd_termo');  -- NULL
SELECT count(*) FROM configuracoes WHERE chave LIKE 'lgpd_%';                                          -- 0
```

## Teste manual de imutabilidade (opcional, em dev/QAS)

Cada comando deve terminar em `ERROR`; nada é gravado:

```sql
BEGIN; TRUNCATE lgpd_termo_versao; ROLLBACK;
BEGIN; TRUNCATE lgpd_manifestacao; ROLLBACK;
BEGIN; UPDATE lgpd_termo_versao SET versao = versao; ROLLBACK;
BEGIN; DELETE FROM lgpd_manifestacao; ROLLBACK;
```
