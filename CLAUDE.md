# GymFlow

Sistema de gestão de academia (cliente: Academia AMF, Fortaleza). Desenvolvedor único: Victor.
Idioma de trabalho: português (Brasil).

## Stack
- **Web (v1.5.0):** Flask, SQLAlchemy, PostgreSQL 16, Bootstrap 5, ReportLab (PDF), APScheduler (jobs)
- **Desktop FingerPoint (v1.2.2):** CustomTkinter + leitor Digital Persona U.are.U 4500 (`uareu4500.dll`)
- **Pagamentos:** Cielo Link de Pagamento (produção) e Asaas (sandbox validado)
- **Infra:** Cloudflare (DNS + Tunnel `gymflow-producao`), NSSM (serviços Windows), pg_dump/pg_restore

## Ambientes
- `D:\Projetos\Python\GymFlow` = desenvolvimento (onde o Claude Code trabalha)
- `C:\Academia_teste` = QAS
- `C:\Sistema_controle_acesso` = **PRODUÇÃO. Nunca ler nem alterar.**
- Deploy é cópia manual de arquivos para o servidor Windows. Não há CI/CD nem testes automatizados; a validação é manual em QAS.

## Ambiente Docker (desenvolvimento)
- `docker compose up -d --build` sobe `gymflow-web` (Flask, porta 5000) e `gymflow-db` (PostgreSQL 16, porta 5433 no host).
- Login inicial: admin / admin123. Dados de teste: `docker compose exec web python seed.py`.
- Banco: `docker compose exec db psql -U gymflow -d gymflow`.
- Dependencias do container ficam em `requirements-docker.txt` (sem pacotes exclusivos do Windows). O FingerPoint (`biometria/`) roda fora do Docker, no Windows, por depender do leitor USB e das DLLs.

## Papéis (subagentes)
Delegue cada tarefa ao subagente correspondente e **nunca misture responsabilidades**:
- `dev` → implementação
- `arquiteto` → análise estrutural (somente leitura)
- `dba` → modelagem e banco de dados

## Regras do projeto
- Inativação de aluno usa a data de `vencimento`, nunca o histórico de passagens.
- Nomes de campos de webhook devem ser validados com payload real, não com a documentação.
- `tecnologia_ativa()` é a fonte única de verdade; o bloqueio de tecnologia é na API (ex.: `POST /acessos/biometria` → 403), não só na UI.
- Cuidado com o autoflush do SQLAlchemy em verificações de duplicidade antes do commit.
- Nunca fazer exclusão física direta: usar o fluxo `lgpd_delete_request` (solicitação/aprovação).
- Termos LGPD: apenas `consentimento` e `privacidade` participam do gate `before_request`; `uso` é só versionado/exibido.
- Webhook Cielo não possui autenticação (limitação do produto).
- Regras de pagamento: estorno é decisão manual; cancelamento vale no fim do ciclo pago; cartão recusado tem 4 tentativas com 1 dia de intervalo e bloqueio no 5º dia.

## Convenções
- Versionamento semântico com tags `vX.Y.Z-web` / `vX.Y.Z-desktop` e atualização do `CHANGELOG.md`.
- Entregar arquivos completos em vez de patches parciais em mudanças estruturais.
- Respostas diretas e objetivas.

## Pendências conhecidas
- FingerPoint `bio_db.py` grava direto no PostgreSQL e ignora a API — será corrigido na camada `/api/v1` (v1.6.0, JWT com o model `Usuario`, abstração por campo `tecnologia`).
- Roadmap LGPD v1.7.0 em 4 fases (consentimento/auditoria → direitos do titular → segurança → interface admin).
