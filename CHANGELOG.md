# Changelog — GymFlow

## [1.7.0] — 2026-10-23

### Adicionado
- **LGPD — ciência e consentimento dos usuários do sistema (funcionários)**. Alunos ficam fora desta versão.
  - Tabelas novas (`lgpd_termo`, `lgpd_termo_versao`, `lgpd_manifestacao`, `lgpd_termo_auditoria`, `schema_migracao`) criadas por script SQL numerado em `migrations/` (aplicado com `psql`, sem Alembic). Nenhuma tabela existente é alterada; as configurações `lgpd_gate_ativo` (padrão `0`) e `lgpd_contato_privacidade` são inseridas em `configuracoes`.
  - Manifestações e auditoria dos termos são append-only (triggers no banco); versão publicada não pode ser alterada nem excluída.
  - Hash canônico SHA-256 do texto (`app/lgpd.py`: `canonicalizar`/`hash_conteudo`), conferido no registro da manifestação.
  - Gate após o login (`before_request`): com a exigência ligada, termo pendente leva a `/privacidade/manifestacao`; consentimento exigido recusado leva a `/privacidade/recusado`; requisições XHR/JSON recebem 403 JSON. Erro de banco na verificação falha fechado (503). Webhooks de pagamento e `POST /acessos/biometria` (FingerPoint) continuam isentos.
  - Telas do titular: resposta aos termos (ciência: "Li e estou ciente"; consentimento: "Concordo"/"Não concordo"), consentimento não concedido e "Privacidade" (histórico e revogação/alteração de consentimento).
  - Administração (`/privacidade/admin`, perfil admin): termos, versões (rascunho, cópia da vigente, publicação com vigência imediata ou agendada, bloqueio de publicação com `[PREENCHER`), manifestações, situação por usuário, auditoria e configuração. Sem exportação e sem edição/exclusão de manifestações.
  - Decorator `@requer_consentimento('codigo')` disponível para rotas que dependam de consentimento específico (nenhuma rota usa nesta versão).
  - `perfil_required(*perfis)` e `registrar_log_sem_commit()` em `auth.py`; CSRF local mínimo nos POST do blueprint `lgpd`.
- Sem a migração aplicada, ou com `lgpd_gate_ativo = 0`, o sistema se comporta como na versão anterior.

### Alterado
- **Gerenciamento de usuários restrito ao perfil admin**: `GET /usuarios`, `POST /usuarios/novo`, `POST /usuarios/<id>/editar` e `GET /usuarios/<id>/toggle` passam a usar `@perfil_required('admin')` (antes bastava estar logado). Proteção contra autobloqueio: o admin logado não pode desativar o próprio usuário nem remover o próprio perfil admin se for o último admin ativo (nada é gravado e uma mensagem é exibida).
  - Limitação conhecida: o perfil é lido da sessão, definido no login. Um usuário rebaixado de admin para operador mantém o acesso de admin até o próximo login (sessão de até 1 hora).

### Observações
- A camada `/api/v1` com JWT (model `Usuario`, abstração por campo `tecnologia`) e a correção do FingerPoint `bio_db.py` gravando direto no PostgreSQL **seguem pendentes**.
- O `app/version.py` já indicava 1.6.0 (build 2026-08-07) sem entrada correspondente neste changelog.

---

## [1.5.0] — 2026-06-30

### Adicionado
- **Aba Tecnologias de Acesso** (`/configuracoes/tecnologias`): controle centralizado das tecnologias do sistema (biometria, RFID, facial). Introdução de `tecnologia_ativa(nome)` como fonte única de verdade em `auth.py`, injetada via context processor. Desabilitar biometria bloqueia ingestão no `POST /acessos/biometria` (retorna 403) e oculta filtros e colunas relacionados — não apenas na UI, mas no backend. Registros históricos permanecem visíveis.
- **Paginação backend**: helper `paginar()` em `auth.py` usando SQLAlchemy `.paginate()`. Seletor de itens por página com opções 20, 50 e 100 aplicado nas telas de Alunos, Financeiro e Relatórios. Macro `_paginacao.html` reutilizável com estilo unificado (botões com borda, página ativa em laranja, reticências).
- **Paginação na tela de Relatórios**: classe `PaginacaoManual` em `relatorios.py` para listas filtradas em Python (passagens, por aluno, vencimentos, auditoria, aniversariantes). Relatório financeiro usa `paginar()` SQL direto. Heatmap continua calculado sobre todos os dados independente da página.
- **Campo Observação nos relatórios financeiros**: coluna "Observação" disponível como opção nos relatórios diário e mensal (PDF e CSV), para todas as seções (Dinheiro, PIX, Cartão). Coluna recebe largura proporcional no PDF. Uso principal: identificar pagamentos realizados por terceiros (ex: "Pagamento realizado pelo usuário X").

### Corrigido
- **`financeiro.html`**: variável de loop `pag` sobrescrevia o objeto de paginação vindo do backend, quebrando o seletor "Por página" e os contadores da tabela. Renomeada para `pagto` dentro do `{% for %}`.
- **`financeiro.html`**: CSS `.page-link-custom` ausente causava paginação com estilo simples (sem botões destacados), diferente da tela de Alunos. Estilos adicionados no bloco `{% block estilos %}`.

---

## [1.4.0] — 2026-06-XX

### Adicionado
- Pagamento dividido (dois registros por transação, formas diferentes)
- Filtro de relatório diário por turno (`hora_ini`/`hora_fim`) lendo `criado_em`
- PDF com quebra de linha automática em nomes longos via ReportLab `Paragraph`
- Popup global de novas passagens (polling `/monitoramento/ultimo` a cada 2s no `base.html`, exceto na tela Monitoramento)
- Versionamento da web como GymFlow via `app/version.py` com context processor
- Sistema de backup do banco (`pg_dump`/`pg_restore`, APScheduler, rotatividade, trigger manual)
- Filtros de biometria e status na tela de Alunos

### Corrigido
- Vencimento não avançava após pagamento: bug de autoflush do SQLAlchemy contando o próprio registro recém-inserido na checagem de duplicidade

---

## [1.3.0] — 2026-XX-XX

### Adicionado
- Branding GymFlow (substituindo "Academia AMF")
- Tela de Monitoramento em tempo real com polling de 1 segundo
- Dashboard com métricas de alunos ativos, vencimentos e passagens

---

## [1.2.1] — FingerPoint

### Corrigido
- Busca travando: substituído debounce por botão explícito + thread separada
- Status biometria pendente: `salvar_templates` agora sincroniza `biometria_status` / `biometria_2_status`; `reconciliar_status_biometria()` executada no login

### Adicionado
- Logs diários em `.txt` via `bio_logger.py`
- Auto-start do reconhecimento na aba Portaria
- Pausa/retomada entre abas com flag de intenção manual