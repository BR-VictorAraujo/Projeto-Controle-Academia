# Changelog — GymFlow

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