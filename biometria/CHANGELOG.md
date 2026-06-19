# Changelog — FingerPoint

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versionamento segue [SemVer](https://semver.org/lang/pt-BR/): `MAJOR.MINOR.PATCH`.

## [1.2.1] — 2026-06-19

### Corrigido
- **Travamento na busca do Cadastro:** o campo de busca disparava uma query
  no banco a cada tecla digitada, reconstruindo a lista inteira de widgets
  a cada vez — com centenas de alunos, isso travava a interface por mais
  de 10 segundos. Substituído por busca explícita via botão "🔍 Buscar"
  ou tecla Enter, executada em thread separada para não bloquear a UI.
- **Biometria aparecia como "Pendente" na tela web mesmo após a coleta:**
  o app salvava a digital na tabela `biometrias`, mas nunca atualizava os
  campos `aluno.biometria_status` / `biometria_2_status` na tabela
  `alunos` — que são os campos que a tela web de fato lê para mostrar
  "Cadastrada" ou "Pendente". `salvar_templates` agora sincroniza esses
  campos automaticamente. Inclui também uma rotina de **reconciliação
  retroativa** (`reconciliar_status_biometria`), executada a cada login,
  que corrige o status de alunos cadastrados antes desta correção.

## [1.2.0] — 2026-06-18

### Mudado
- **Rebranding:** o app passa a se chamar **FingerPoint** — produto de
  controle de acesso por biometria, independente de cliente específico.
- Visual da sidebar e tela de login limpos, sem referências à Academia AMF.
- Ícone do braço substituído por ícone de impressão digital (🫆).

### Adicionado
- Sistema de log em arquivo (`biometria/logs/YYYY-MM-DD.txt`), separado por dia,
  com categorias: `RECONHECIMENTO`, `ACESSO`, `ERRO`, `LEITOR`, `CADASTRO`, `SISTEMA`.
- Reconhecimento automático ao abrir a aba Portaria — não precisa mais clicar
  no botão de "Iniciar reconhecimento" a cada sessão.
- Pausa automática ao entrar no Cadastro e retomada ao voltar para a Portaria.
- Flag de intenção do usuário preservada entre trocas de aba: pausa manual
  é respeitada mesmo após navegar pelo app.
- Versionamento e identificação do app na janela e sidebar.

### Corrigido
- **Falso-positivo grave** de comparação biométrica: quando `compare_fmds`
  falhava no DLL, `score` continuava 0 e o threshold `0 < 21474` aprovava
  qualquer comparação errada como match positivo do último aluno testado.
  Agora usa sentinela `0xFFFFFFFF` que nunca passa no threshold em caso
  de falha.
- App não trava mais por *access violation* do DLL nativo quando o leitor
  USB desconecta ou não está presente — `OSError` é capturado e o estado
  do dispositivo é atualizado.
- Status do leitor na sidebar reflete o estado real em tempo de execução,
  não só na inicialização.
- Loop de reconhecimento tem retry de até 5 falhas consecutivas antes de
  parar (antes, 1 erro derrubava o loop e exigia reiniciar o app).

## [1.1.0] — 2026-06-12

### Adicionado
- Fechamento limpo da janela: `WM_DELETE_WINDOW` + `os._exit(0)` para
  encerrar o processo completamente (antes, o processo continuava em
  background e o cliente precisava reiniciar o computador para reabrir).

## [1.0.0] — 2026-XX-XX (versão de base)

- Cadastro de digitais com 3 capturas, mantendo o melhor template.
- Reconhecimento 1:N eficiente via `compare_fmds` em memória.
- Integração com o sistema Flask via POST em `/acessos/biometria`.
- Fallback de gravação direta no banco quando o Flask está offline.