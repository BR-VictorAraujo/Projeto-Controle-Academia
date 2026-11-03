# Relatório de entrega: GymFlow web 1.7.0 (LGPD: termo com aceite em pop-up)

Data: 2026-10-24. Situação: implementado e testado no Docker de desenvolvimento.
**Ainda não foi validado em QAS e não há tag.**

## Escopo final (simplificado a pedido do Victor)

| Tema | Decisão |
|---|---|
| Titular | Somente **funcionários** (`usuarios`). Os alunos não têm login e ficaram fora desta versão. |
| Termo | Um único termo de uso e privacidade, com **"Aceito"** e **"Não aceito"**. |
| Apresentação | **Pop-up obrigatório** sobre o dashboard, no primeiro acesso de qualquer usuário (operador ou admin), sem botão de fechar. Enquanto o aceite está pendente, o dashboard não mostra dados. |
| Recusa | Registrada. A sessão é encerrada e o usuário volta ao login com mensagem genérica; no próximo login, o pop-up aparece de novo. Conta e dados preservados. |
| Texto | **Não editável pela interface.** Só o desenvolvedor publica versões, por migração SQL gerada com `tools/gerar_migracao_termo.py`. Nova versão exige novo aceite. |
| Tela | **Configurações → LGPD** (somente admin): termo vigente em somente leitura e tabela de respostas (usuário, resposta, data/hora e versão). |
| Migração | Scripts SQL numerados em `migrations/`, aplicados com `psql`. |
| Usuários | `/usuarios` restrito ao admin, com proteção contra autobloqueio. |

Uma primeira versão mais ampla (vários termos, ciência × consentimento, telas "Privacidade" e
"Termos LGPD" e chave liga/desliga) foi substituída por este modelo antes de qualquer implantação.

Constatação da análise: o `CLAUDE.md` descreve como existentes um gate LGPD, os termos
`consentimento`/`privacidade`/`uso` e o fluxo `lgpd_delete_request`. **Nada disso existia no código.**

## Arquivos

### Alterados
| Arquivo | Justificativa |
|---|---|
| `app/models.py` | Models `LgpdTermoVersao`, `LgpdManifestacao` e `SchemaMigracao`, espelhando a migração |
| `app/__init__.py` | Registro do blueprint `lgpd` e do `before_request` que bloqueia o uso enquanto o aceite está pendente |
| `app/routes/auth.py` | `perfil_required()`, `registrar_log_sem_commit()` (sem IP) e o dashboard sem dados quando o aceite está pendente |
| `app/routes/config.py` | `/usuarios` restrito ao admin, com proteção contra autobloqueio (antes qualquer operador podia se promover a admin) |
| `app/templates/base.html` | Inclusão do pop-up |
| `app/templates/{configuracoes,usuarios,parametros,planos,tecnologias}.html` | Aba "LGPD", visível só para admin. A barra de abas é repetida em cada template |
| `app/version.py` | 1.7.0. A versão é gravada em cada resposta |
| `CHANGELOG.md` | Entrada [1.7.0] |

### Criados
| Arquivo | Conteúdo |
|---|---|
| `app/lgpd.py` | Hash canônico, versão vigente, verificação de pendência, registro atômico e idempotente, gate e CSRF |
| `app/routes/lgpd.py` | `POST /lgpd/responder` e `GET /configuracoes/lgpd` |
| `app/templates/lgpd/_modal_termo.html` | Pop-up |
| `app/templates/lgpd/dashboard_pendente.html` | Fundo neutro, sem dados, atrás do pop-up |
| `app/templates/lgpd/configuracao_lgpd.html` | Configurações → LGPD |
| `migrations/001_v1.7.0_lgpd_up.sql` / `_down.sql` | Estrutura, triggers de imutabilidade e down protegido |
| `migrations/opcional_role_app.sql` | Usuário de banco restrito (opcional) |
| `migrations/README.md` | Aplicação, publicação de termo e rollback |
| `tools/gerar_migracao_termo.py` | Gera a migração de uma nova versão do termo a partir de um .txt |
| `docs/lgpd/termo/MODELO.txt` | Modelo do termo com `[PREENCHER]` (a ferramenta recusa enquanto houver) |
| `docs/lgpd/*.md` | Instalação, funcionamento e publicação do termo, dados e acessos, e este relatório |
| `tests/lgpd_v170/*` | Testes de aceitação e resultados |
| `.gitattributes` | LF fixo em `migrations/*.sql` e `docs/lgpd/*.txt` (o texto do termo entra no hash) |

## Testes executados

Os testes rodaram no Docker, em bancos descartáveis copiados do banco de desenvolvimento, com
**405 usuários e 400 alunos**.
- `teste_cenarios.py`: **52/52 OK** (`tests/lgpd_v170/resultado_execucao.txt`).
- `teste_usuarios_admin.py`: **18/18 OK**.

| # | Cenário | Resultado |
|---|---|---|
| 0 | Sem termo publicado | Sistema igual ao anterior, sem pop-up |
| 1 | Usuário existente sem aceite | O pop-up aparece para operador e admin, com backdrop estático, sem botão de fechar e sem dados atrás; o login sozinho não registra nada |
| 2 | Aceita | Registro com todos os campos e sem IP; o pop-up some e o sistema fica liberado; clique duplo gera 1 registro |
| 3 | Não aceita | `nao_aceito` registrado; sessão encerrada; login com mensagem genérica; conta mantida; pop-up de novo no próximo login |
| 4 | Fecha antes de responder | Nada gravado; o pop-up volta |
| 5 | Falha de banco | Commit falho: nada gravado e a tela mostra erro (nunca "Termo aceito"). Falha na verificação: 503 |
| 6 | Novo login após aceite | Sem pop-up e sem novo registro |
| 7 | Nova versão | O pop-up volta com a nova versão; aceite enviado para a versão antiga ou com hash divergente é recusado; histórico preservado |
| 8 | Acesso direto | As rotas voltam ao dashboard; polling recebe 403 JSON; POST sem CSRF ou com resposta inválida não grava; FingerPoint isento |
| 9 | Sem permissão | O operador não vê nem acessa a aba LGPD; a tela não permite editar o termo (POST → 405); UPDATE, DELETE e TRUNCATE no banco são recusados; usuário com resposta não pode ser excluído |
| 10 | Atualização preservando dados | Reaplicar o up com 405 respostas não mudou nenhuma contagem; o down foi recusado com respostas; a migração de termo gerada pela ferramenta foi aplicada e o hash bate com o Python; a ferramenta recusa o MODELO com `[PREENCHER]` |
| 11 | Carga | Verificação do aceite: +1,8 ms por requisição; 399 aceites registrados em poucos segundos; Configurações → LGPD abre rápido |

Itens que **não** foram testados aqui e devem ser validados em QAS:
- aparência do pop-up no navegador, inclusive no celular;
- aplicação das migrações com o `psql` do Windows;
- execução sob o serviço NSSM.

## Pontos de atenção

1. **Texto do termo:** antes de publicar, a academia precisa fornecer os dados dos `[PREENCHER]` e
   aprovar o texto.
2. **Superusuário do banco** pode desativar triggers. Veja `migrations/opcional_role_app.sql`.
3. **IP no log de login:** o `registrar_log` existente já gravava o IP no login e no logout. Isso não foi
   alterado e permite, por horário, associar o IP a um aceite.
4. **Perfil na sessão:** quem for rebaixado de admin mantém o acesso até o próximo login (no máximo 1 hora).
5. **Fora do escopo:** exposições de dados encontradas na análise (fotos e documentos em `/static`,
   `POST /acessos/biometria` sem autenticação, senha em `bio_config.json`, logs versionados) foram
   tratadas em outro branch, `claude/nostalgic-mestorf-7ac4ec`, ainda não mesclado.
6. **Alunos e biometria:** o aceite dos alunos e o consentimento para a biometria (dado sensível)
   ficam para uma próxima versão.
