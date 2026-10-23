# Dados armazenados, perfis de acesso e separação de responsabilidades (v1.7.0)

## Onde os dados ficam

Os dados ficam todos no banco PostgreSQL instalado no ambiente da academia. A v1.7.0:
- não envia registros de aceite a servidores externos, terceiros ou infraestrutura do desenvolvedor;
- não tem telemetria;
- não tem rotina de exportação, cópia ou sincronização dos registros;
- não cria painel externo nem acesso remoto do desenvolvedor.

As únicas chamadas de saída do sistema continuam sendo as integrações de pagamento já existentes
(Cielo e Asaas), que não recebem dados de aceite. As telas carregam Bootstrap pelo CDN jsDelivr,
como o restante do sistema, e por isso o navegador do usuário se conecta a esse serviço.

## Tabelas e dados

| Tabela | Conteúdo | Dados pessoais |
|---|---|---|
| `lgpd_termo` | Código, título, tipo, finalidade e configuração do termo | Não |
| `lgpd_termo_versao` | Texto exato de cada versão, hash SHA-256, vigência e status | Só o id de quem criou ou publicou |
| `lgpd_manifestacao` | id do usuário, versão do termo, resposta (`ciente`/`concordo`/`nao_concordo`), hash do texto, versão do software, canal e data/hora | id do usuário (titular) |
| `lgpd_termo_auditoria` | Ações administrativas nos termos (criação, edição, publicação, configuração) | id do usuário que agiu |
| `schema_migracao` | Controle das migrações aplicadas | Não |
| `log_auditoria` (já existia) | Uma linha "registrou manifestacao lgpd" com os ids do termo e da versão | Nome de quem agiu, como nos demais logs. A resposta dada e o IP não são gravados. |

Não são armazenados: senha, documentos, IP, geolocalização, navegador ou assinatura.

## Integridade

- `lgpd_manifestacao` e `lgpd_termo_auditoria` só aceitam inclusão. **UPDATE, DELETE e TRUNCATE são
  rejeitados pelo próprio banco** (triggers), mesmo vindos de fora da aplicação.
- Uma versão publicada não pode ser alterada nem excluída. O hash é recalculado e conferido no banco
  a cada gravação.
- A manifestação só é aceita se o hash enviado pela tela for igual ao da versão vigente, o que garante
  que o registro corresponde ao texto exibido.
- A data e a hora vêm do servidor de banco, não do computador do usuário.
- Usuários com manifestação registrada não podem ser excluídos fisicamente (FK `RESTRICT`).
- **Limitação:** um usuário de banco com privilégio de superusuário pode desativar triggers. Hoje a
  aplicação usa um usuário com esse privilégio. `migrations/opcional_role_app.sql` descreve um usuário
  de banco dedicado, sem UPDATE/DELETE nessas tabelas, a ser adotado pela academia quando for
  conveniente.

## Quem acessa o quê

| Perfil | Pode |
|---|---|
| Qualquer usuário logado | Ver os termos vigentes, responder, ver **o próprio** histórico e revogar ou alterar **o próprio** consentimento |
| **admin** | Cadastrar termos e versões, publicar, ver o histórico de todos os usuários, a situação e a auditoria, e ligar ou desligar a exigência |
| Sem login | Nada |

Ninguém pode editar ou excluir manifestações pela aplicação.

A gestão de usuários (`/usuarios`: criar, editar, ativar e desativar) é **exclusiva do admin**
desde a v1.7.0, o que impede um operador de se promover a admin. O admin não pode desativar a si
mesmo nem deixar o sistema sem nenhum admin ativo.

Limitação: o perfil é lido no login. Quem for rebaixado mantém o acesso de admin até sair ou até a
sessão expirar (no máximo 1 hora).

## Separação de responsabilidades

- **Academia:**
  - opera o sistema e o servidor;
  - define as finalidades e as bases legais;
  - redige e aprova o conteúdo dos termos;
  - define quem tem perfil admin;
  - atende às solicitações dos titulares;
  - decide as consequências de uma recusa, inclusive se há outro fundamento legal ou obrigação de
    retenção.
- **Desenvolvedor:**
  - fornece o software e as atualizações;
  - o software não lhe dá acesso remoto nem envia a ele os registros de aceite.
  - Atividades de manutenção que exijam acesso ao servidor ou ao banco da academia, como deploy,
    suporte e restauração de backup, devem ser autorizadas e acompanhadas pela academia caso a caso.
    Durante essas atividades o técnico pode ter contato com dados pessoais, e isso deve ser tratado
    no acordo entre as partes.

Este documento descreve limites **técnicos**. Ele não define a qualificação jurídica de cada parte
(controlador ou operador) nem afirma isenção de responsabilidade: isso depende das atividades
efetivamente realizadas e do contrato entre as partes.
