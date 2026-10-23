# Relatório de entrega: GymFlow web 1.7.0 (LGPD: ciência e consentimento)

Data: 2026-10-23. Situação: implementado e testado no Docker de desenvolvimento.
**Ainda não foi validado em QAS e não há commit nem tag.**

## Decisões de escopo

| Tema | Decisão |
|---|---|
| Titular | Somente **funcionários** (`usuarios`), pelo gate após o login. Os alunos não têm login e ficaram fora desta versão. |
| Ciência × consentimento | O Aviso de Privacidade usa **"Li e estou ciente"**, que não é consentimento. "Concordo"/"Não concordo" vale só para termos do tipo consentimento cadastrados pela academia. Nenhum consentimento vem pré-cadastrado. |
| Migração | Scripts SQL numerados em `migrations/`, aplicados com `psql` (sem Alembic). |
| Exclusão | FK `RESTRICT`: não é possível excluir um usuário que tenha manifestação. A exclusão de alunos não foi alterada. |
| Ativação | A exigência começa **desligada** e o admin liga depois de publicar o termo. |

Constatação da análise: o `CLAUDE.md` descreve como existentes um gate LGPD, os termos
`consentimento`/`privacidade`/`uso` e o fluxo `lgpd_delete_request`. **Nada disso existia no código.**
A v1.7.0 foi feita do zero.

## Arquivos

### Alterados
| Arquivo | Mudança | Justificativa |
|---|---|---|
| `app/models.py` | 5 models novos (`LgpdTermo`, `LgpdTermoVersao`, `LgpdManifestacao`, `LgpdTermoAuditoria`, `SchemaMigracao`) | Espelham as tabelas da migração, para que o ORM e o `create_all` fiquem coerentes com o banco |
| `app/__init__.py` | Registra o blueprint `lgpd` e o `before_request` do gate | Bloqueio feito no backend, para todas as rotas |
| `app/routes/config.py` | As 4 rotas de `/usuarios` passam a usar `perfil_required('admin')`, com proteção contra autobloqueio | Sem isso, qualquer operador se promovia a admin e chegava à administração LGPD |
| `app/routes/auth.py` | `perfil_required()` e `registrar_log_sem_commit()` (sem IP) | Não havia controle por perfil. O `registrar_log` existente faz commit interno e quebraria a atomicidade do registro |
| `app/templates/base.html` | Itens de menu "Privacidade" e "Termos LGPD" (admin) | Navegação. Só aparecem com a migração aplicada |
| `app/version.py` | 1.7.0 / 2026-10-23 | A versão é gravada em cada manifestação |
| `CHANGELOG.md` | Entrada [1.7.0] | Convenção do projeto |

### Criados
| Arquivo | Conteúdo |
|---|---|
| `app/lgpd.py` | Regras: hash canônico, versão vigente e de referência, situação do usuário, registro atômico e idempotente, gate, `@requer_consentimento`, CSRF |
| `app/routes/lgpd.py` | Blueprint `/privacidade`: telas do titular e administração (admin) |
| `app/templates/lgpd/*.html` | 17 telas: 5 do titular, no estilo do login, e as de administração, no estilo das telas de configuração |
| `migrations/001_v1.7.0_lgpd_up.sql` / `_down.sql` | Migração idempotente e transacional, com triggers de imutabilidade. O down é protegido contra uso acidental |
| `migrations/opcional_role_app.sql` | Usuário de banco dedicado, sem UPDATE/DELETE nas tabelas LGPD (opcional, não aplicado) |
| `migrations/README.md` | Aplicação, verificação e rollback do banco |
| `docs/lgpd/modelo_aviso_privacidade.txt` | Modelo do aviso, com campos `[PREENCHER]`. A publicação fica bloqueada até que sejam preenchidos |
| `docs/lgpd/INSTALACAO_E_ATUALIZACAO.md` | Atualização, retorno à versão anterior e desligamento emergencial |
| `docs/lgpd/CONFIGURACAO_DO_TERMO.md` | Guia para a academia |
| `docs/lgpd/DADOS_E_ACESSOS.md` | Dados armazenados, perfis e separação de responsabilidades |
| `tests/lgpd_v170/teste_cenarios.py`, `README.md`, `resultado_execucao.txt` | Testes de aceitação e resultado |

## Testes executados

Os testes rodaram no Docker, em um banco descartável copiado do banco de desenvolvimento, com
**405 usuários e 400 alunos**. Resultado: **54 verificações, 54 OK** (`tests/lgpd_v170/resultado_execucao.txt`).

| # | Cenário | Resultado |
|---|---|---|
| 1 | Usuário existente sem manifestação | Redirecionado para a tela obrigatória; o login sozinho não gera registro |
| 2 | Usuário concorda | Ciência e concordância registradas com todos os campos; acesso liberado; clique duplo gera 1 registro |
| 3 | Usuário não concorda | `nao_concordo` registrado, conta mantida, sistema bloqueado com o contato da academia, XHR recebe 403; pode rever a decisão depois |
| 4 | Fecha antes de responder | Nada gravado; o termo continua pendente no próximo login |
| 5 | Falha de banco | Commit falho: nada gravado e a interface mostra erro (nunca "registrado"). Falha na verificação: 503 (falha fechada) |
| 6 | Novo login após manifestação | Não pergunta de novo |
| 7 | Nova versão | Não material (1.1): não pergunta. Material (2.0): pergunta de novo. Histórico preservado e publicação auditada. Hash divergente ou versão antiga: recusado |
| 8 | Acesso direto | 7 rotas internas redirecionadas, polling recebe 403 JSON, POST sem CSRF recusado, FingerPoint e webhooks isentos |
| 9 | Sem permissão | Operador barrado em todas as telas e ações de admin. UPDATE/DELETE/TRUNCATE direto no banco recusado pelas triggers. Usuário com manifestação não pode ser excluído |
| 10 | Atualização preservando dados | Down → up → up novamente na cópia: contagens de alunos, usuários, acessos, planos, configurações e logs idênticas. Up reaplicado com 808 manifestações não perdeu nenhuma. Down recusado com manifestações |
| 11 | Carga (400+) | Gate: +4,6 ms por requisição (8,7 → 13,9 ms). Registro de 798 manifestações em 3,7 s. Histórico: 0,06 s. Painel "Situação" com 405 usuários: ~1,9 s |

Itens que **não** foram testados aqui e devem ser validados em QAS:
- aparência das telas no navegador, inclusive no celular;
- aplicação da migração com o `psql` do Windows;
- execução sob o serviço NSSM.

## Pontos de atenção

1. **`/usuarios` restrito ao admin (corrigido nesta versão).** Antes, qualquer usuário logado podia
   criar ou promover um admin. Agora só o admin acessa, com proteção contra autobloqueio. Quem for
   rebaixado mantém o acesso até o próximo login (no máximo 1 hora). Testes:
   `tests/lgpd_v170/teste_usuarios_admin.py`, com 18/18 OK.
2. **Superusuário do banco** pode desativar triggers. Veja `migrations/opcional_role_app.sql`.
3. **Painel "Situação"** consulta um usuário por vez (~1,9 s com 405 usuários). É suficiente para o
   volume atual de funcionários.
4. **IP no log de login:** o `registrar_log` existente já gravava o IP no login e no logout. Isso não
   foi alterado e permite, por horário, associar o IP a um aceite.
5. **Fora do escopo, encontrados na análise:**
   - fotos e documentos de alunos servidos em `/static` sem autenticação;
   - `POST /acessos/biometria` sem autenticação devolve dados pessoais;
   - senha do PostgreSQL em `biometria/bio_config.json`;
   - logs do FingerPoint com nomes de alunos versionados no Git;
   - ações destrutivas por GET, sem CSRF;
   - exclusão física de aluno em cascata.
6. **Alunos:** o aceite dos titulares alunos, coletado pela recepção, e o consentimento para
   biometria (dado sensível) ficam para uma próxima versão. O `bio_db.py` grava digitais direto no
   banco, sem passar por nenhuma checagem.
