# GymFlow web 1.7.0: instalação, atualização e retorno

Este roteiro vale para QAS e produção, e deve ser executado **primeiro em QAS**. O deploy continua
sendo feito por cópia manual de arquivos, e os serviços continuam no NSSM.

## O que muda no servidor

- **Código:** os arquivos novos são `app/lgpd.py`, `app/routes/lgpd.py` e
  `app/templates/lgpd/` (pop-up, tela de Configurações → LGPD e fundo neutro do dashboard). Há também
  pequenas alterações em `app/__init__.py`, `app/models.py`, `app/routes/auth.py`,
  `app/routes/config.py`, `app/templates/base.html`, nas telas de configuração (nova aba) e em
  `app/version.py`.
- **Banco:** três tabelas novas (`lgpd_termo_versao`, `lgpd_manifestacao` e `schema_migracao`), criadas
  pela `migrations/001_v1.7.0_lgpd_up.sql`. **Nenhuma tabela existente é alterada.**
- **Termo:** cada versão é publicada por uma migração própria (`migrations/NNN_termo_v<versao>.sql`),
  gerada pelo desenvolvedor. Ver `docs/lgpd/CONFIGURACAO_DO_TERMO.md`.
- **O que não muda:** nenhuma dependência Python nova e o FingerPoint continua como está.
- **Mudança de comportamento:** a tela de Usuários passa a ser **exclusiva do admin**.

## Atualização

1. **Backup completo do banco** (obrigatório):
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -U <usuario> -d <banco> -F c -f "D:\backup\gymflow_antes_v1.7.0.dump"
   ```
2. **Backup da pasta atual do sistema:** copie a pasta inteira, por exemplo para `...\backup_v1.6.0\`.
3. **Pare o serviço web** no NSSM.
4. **Aplique a estrutura:**
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f migrations\001_v1.7.0_lgpd_up.sql
   ```
   O script roda numa única transação: ou aplica tudo, ou nada. Pode ser executado de novo sem
   problema.
5. **Aplique a migração do termo** (`migrations\NNN_termo_v1.0.sql`), se ela já tiver sido gerada com
   o texto aprovado pela academia. Sem ela, o sistema funciona normalmente e só não exibe o pop-up.
6. **Copie os arquivos da v1.7.0**, sem sobrescrever o `.env` nem as pastas `app/static/fotos` e
   `app/static/documentos`.
7. **Inicie o serviço** e confira:
   - o login funciona;
   - com o termo publicado, o pop-up aparece no primeiro acesso;
   - o admin vê Configurações → LGPD.

Se o serviço iniciar com o código novo **antes** do passo 4, o `db.create_all()` cria as tabelas vazias
sem as proteções do banco. Nesse caso, aplique o passo 4 em seguida (ele completa o que faltar) e
reinicie o serviço.

## Retorno à versão anterior

O retorno é feito **pelo código**, e as tabelas LGPD ficam no banco. A versão anterior simplesmente
as ignora, e os aceites já registrados continuam guardados.

1. Pare o serviço web.
2. Restaure a pasta do backup do passo 2.
3. Inicie o serviço.

**Não execute** o `001_v1.7.0_lgpd_down.sql` em produção. Ele apaga os registros de aceite e se recusa
a rodar se houver algum, a menos que seja forçado. Ele existe apenas para dev e QAS.

Se a falha tiver sido na migração, nada foi aplicado, porque a transação é desfeita. Só use o restore
completo do banco (`pg_restore`) em último caso: ele desfaz **tudo** o que foi gravado depois do backup.

## Desligar o pop-up em emergência

Não há chave liga/desliga pela interface. O pop-up depende de existir uma versão publicada, e versões
não podem ser apagadas. Em emergência, volte o código para a versão anterior (seção acima). Os registros
não são afetados.
