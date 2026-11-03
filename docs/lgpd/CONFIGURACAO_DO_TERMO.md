# Termo de uso e privacidade: funcionamento e publicação

## Como funciona para quem usa o sistema

- Na primeira vez que um usuário entra (operador ou admin) depois que existe um termo publicado,
  aparece um **pop-up** sobre o dashboard. Ele mostra o texto (com rolagem), a versão e dois botões:
  - **Aceito:** o pop-up some e o sistema fica liberado. Nos próximos logins ele não aparece mais.
  - **Não aceito:** a recusa é registrada, a sessão é encerrada e o usuário volta ao login com uma
    mensagem genérica. No próximo login o pop-up aparece de novo.
- O pop-up não tem botão de fechar. Enquanto o usuário não aceita, as demais telas voltam para o dashboard.
- Fechar o navegador sem responder não grava nada.
- Quando uma **nova versão** é publicada, todos precisam aceitar de novo.
- A recusa nunca exclui a conta nem os dados.

## O que a academia vê

**Configurações → LGPD** (somente perfil admin):
- o termo vigente em somente leitura: versão, data de publicação, texto e hash SHA-256;
- abaixo, a tabela de respostas: usuário, resposta (Aceito/Não aceito), data/hora e versão, das mais
  recentes para as mais antigas.

A tela não permite editar o termo nem alterar, excluir ou exportar respostas.

## Publicar uma versão do termo (somente o desenvolvedor)

O texto **não é editável pela interface**. Cada versão é publicada por um script SQL que vai junto
com a atualização do sistema.

1. Copie `docs/lgpd/termo/MODELO.txt` para `docs/lgpd/termo/termo_v1.0.txt` e preencha todos os
   `[PREENCHER ...]` com os dados fornecidos pela academia. Remova o parágrafo "ATENÇÃO".
   - O texto deve ser validado pela academia e, de preferência, por um profissional jurídico ou
     especialista em proteção de dados.
2. Gere a migração:
   ```bash
   python tools/gerar_migracao_termo.py docs/lgpd/termo/termo_v1.0.txt 1.0
   ```
   - O script recusa textos que ainda tenham `[PREENCHER`, além de versões repetidas ou inválidas.
   - Ele cria `migrations/NNN_termo_v1.0.sql` e mostra o hash SHA-256.
3. Aplique e teste em QAS:
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f migrations\NNN_termo_v1.0.sql
   ```
4. Aplique em produção no deploy.

A partir desse momento o pop-up passa a aparecer para todos. Para alterar o texto depois, crie
`termo_v1.1.txt` (ou `2.0`) e repita o processo. Versões publicadas não podem ser alteradas nem
excluídas, nem direto no banco.

Detalhes técnicos: `migrations/README.md`.
