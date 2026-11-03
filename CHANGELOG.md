# Changelog — GymFlow

## [Não lançado] — Segurança: exposição de dados pessoais de alunos (LGPD)

### Segurança
- **Fotos e documentos de alunos só com login**: novas rotas `/arquivos/fotos/<arquivo>` e `/arquivos/documentos/<aluno_id>/<arquivo>` com `@login_required` (`send_from_directory`, `Cache-Control: private, no-store`; documentos com `nosniff`). O acesso direto por `/static/fotos/` e `/static/documentos/` agora retorna 404, inclusive para variações (`../`, `./`, `%2f`, maiúsculas). Os arquivos continuam em `app/static/` e o banco continua gravando `/static/...` (sem migração); o filtro Jinja `arquivo_url` e o helper `url_arquivo()` convertem para a URL protegida nas telas e no JSON de `/acessos/biometria` e `/monitoramento/ultimo`.
- **`POST /acessos/biometria` exige segredo compartilhado**: header `X-GymFlow-Token` comparado com `BIOMETRIA_API_TOKEN` (`.env`) via `hmac.compare_digest`. Sem token configurado no servidor → 503; token ausente/inválido → 401, registrado em auditoria. Restringir a localhost não protegeria, porque o Cloudflare Tunnel conecta a partir de 127.0.0.1.
- **Segredos e logs fora do Git**: `biometria/bio_config.json` (senha do PostgreSQL) e `biometria/logs/` (nomes de alunos) removidos do versionamento e incluídos no `.gitignore`. Novo `biometria/bio_config.example.json`. Os arquivos continuam no histórico do Git: a senha do banco deve ser trocada.

### Alterado
- **FingerPoint 1.2.3** (ver `biometria/CHANGELOG.md`): envia `X-GymFlow-Token` com o valor de `api_token` do `bio_config.json`; 401/503 e status inesperados mostram erro em vez de "Acesso liberado"; o login do app não apaga mais `flask_port`/`api_token` do JSON.

### Deploy (obrigatório na mesma janela, web + FingerPoint)
1. Gerar o token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Servidor web: adicionar `BIOMETRIA_API_TOKEN=<token>` ao `.env` e reiniciar o serviço NSSM.
3. FingerPoint: adicionar `"api_token": "<token>"` ao `bio_config.json` (modelo em `bio_config.example.json`) e reiniciar o app.
4. Sem os dois passos, toda passagem por biometria é recusada (503/401).

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