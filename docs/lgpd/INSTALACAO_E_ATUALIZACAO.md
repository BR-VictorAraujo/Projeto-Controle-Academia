# GymFlow web 1.7.0: instalação, atualização e retorno

Este roteiro vale para QAS e produção, e deve ser executado **primeiro em QAS**. O deploy continua
sendo feito por cópia manual de arquivos, e os serviços continuam no NSSM.

## O que muda no servidor

- Código novo: `app/lgpd.py`, `app/routes/lgpd.py` e `app/templates/lgpd/`, mais pequenas alterações
  em `app/__init__.py`, `app/models.py`, `app/routes/auth.py`, `app/templates/base.html` e
  `app/version.py`.
- Banco: tabelas novas, criadas pela migração `migrations/001_v1.7.0_lgpd_up.sql`. **Nenhuma tabela
  existente é alterada.** Também são inseridas duas chaves em `configuracoes`.
- Não há dependência Python nova. O `requirements.txt` não mudou.
- O FingerPoint (desktop) não muda.

## Atualização

1. **Backup completo do banco** (obrigatório):
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -U <usuario> -d <banco> -F c -f "D:\backup\gymflow_antes_v1.7.0.dump"
   ```
2. **Backup da pasta atual do sistema**: copie a pasta inteira para, por exemplo, `...\backup_v1.6.0\`.
3. **Pare o serviço web** no NSSM.
4. **Aplique a migração**, seguindo os detalhes e a verificação em `migrations/README.md`:
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f migrations\001_v1.7.0_lgpd_up.sql
   ```
   O script roda numa única transação: ou aplica tudo, ou nada. Pode ser executado de novo sem
   duplicar nada.
5. **Copie os arquivos da v1.7.0** e não sobrescreva o `.env` nem as pastas `app/static/fotos` e
   `app/static/documentos`.
6. **Inicie o serviço** e confira:
   - o login funciona e o rodapé mostra a versão 1.7.0;
   - o menu mostra "Privacidade" e, para o admin, "Termos LGPD";
   - **nada é bloqueado ainda**, porque a exigência começa desligada (`lgpd_gate_ativo = 0`).
7. **Configure e publique o termo** conforme `docs/lgpd/CONFIGURACAO_DO_TERMO.md`, depois da
   validação da academia.
8. **Só então ligue a exigência**, em Termos LGPD → Configuração.

Se o serviço for iniciado com o código novo **antes** da migração, o `db.create_all()` cria as
tabelas vazias e a área de privacidade fica marcada como "indisponível". Nesse caso, aplique a
migração (passo 4) e reinicie o serviço. O sistema não fica bloqueado em nenhuma hipótese.

## Retorno à versão anterior

O retorno é feito **pelo código**, e as tabelas LGPD ficam no banco. A versão anterior simplesmente
as ignora: ela não tem o gate e não lê essas tabelas. Assim, as manifestações já registradas, que
são a prova de ciência e consentimento, continuam guardadas.

1. Pare o serviço web.
2. Restaure a pasta do backup do passo 2.
3. Inicie o serviço.

**Não execute** o `001_v1.7.0_lgpd_down.sql` em produção. Ele apaga as manifestações e só existe
para dev e QAS. Por segurança, ele se recusa a rodar se houver manifestações, a menos que seja
forçado (ver `migrations/README.md`).

Se a falha tiver sido **na migração**, nada foi aplicado, porque a transação é desfeita. Basta
manter a versão anterior e verificar a mensagem de erro.

Só use o restore completo do banco (`pg_restore` do dump do passo 1) em último caso. Ele desfaz
**tudo** o que foi gravado depois do backup, inclusive passagens e pagamentos.

## Desligar a exigência sem voltar a versão

Em uma emergência (por exemplo, um termo publicado com erro e ninguém conseguindo trabalhar), o admin
pode desligar a exigência em Termos LGPD → Configuração. Se o próprio admin estiver bloqueado,
use direto no banco:

```sql
UPDATE configuracoes SET valor = '0' WHERE chave = 'lgpd_gate_ativo';
```

Os registros não são afetados. Corrija o termo publicando uma nova versão e religue a exigência.
