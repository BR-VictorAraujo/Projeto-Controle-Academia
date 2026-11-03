# Instalador do GymFlow (Inno Setup)

Um único `GymFlow-Setup-<versao>.exe` instala e atualiza o GymFlow Web num servidor Windows e o
deixa rodando como **serviço Windows "GymFlow"**. Ele segue o modelo do instalador do Nway Control:
o próprio app (`serve.py`, com pywin32) registra e controla o serviço, **sem NSSM**.

O PostgreSQL não é instalado nem alterado (só usado). O FingerPoint (`biometria/`) **não** faz parte
deste instalador: a pasta `biometria/` nunca é copiada nem apagada por ele.

| Arquivo | Para quê |
|---|---|
| `gymflow.iss` | Script do Inno Setup |
| `build.ps1` | Gera o `.exe` (chama o `ISCC.exe`) |
| `atualizar_banco.py` | Cria o banco se faltar, `db.create_all()` + registros padrão e as migrações |
| `config_inicial.py` | Instalação nova: gera o `.env`. Sempre: sincroniza o `BIOMETRIA_API_TOKEN` |
| `../serve.py` | Servidor (waitress) e comandos do serviço Windows |

## 1. Gerar o instalador (máquina de desenvolvimento)

Pré-requisito: Inno Setup 6.

```powershell
.\installer\build.ps1
```

Com outra pasta de origem e um sufixo no nome (ex.: build de teste):

```powershell
.\installer\build.ps1 -Origem C:\caminho\da\origem -Sufixo -teste -Saida C:\builds
```

- A versão vem de `<Origem>\app\version.py` (`APP_VERSAO`).
- O instalador leva os arquivos **como estão no disco**. Se houver alteração sem commit, o build só
  avisa, sem bloquear.
- Nunca entram no pacote: `.env`, `venv`, `biometria/`, `app/static/fotos`, `app/static/documentos`,
  `logs`, `backups_instalacao`, `.git`, `.claude`, `tests`, `docker`, `*.log`/`*.dump`/`*.backup`/`*.zip`.
  As imagens de `app/static/img` entram, mas só são gravadas onde ainda não existem.
- Para conferir o conteúdo, compile sem `/Q` e veja as linhas `Compressing:` do ISCC.

## 2. Instalar ou atualizar (no servidor, como administrador)

Execute `GymFlow-Setup-<versao>.exe` e escolha a pasta do sistema. O modo é detectado pela pasta:

| A pasta tem... | Modo | O que acontece |
|---|---|---|
| `.env` e `app\` | **Atualizar** | Para o serviço e faz backup da pasta (zip, sem o `venv`) e do banco (`pg_dump -Fc`) em `backups_instalacao\`. Depois troca o código, roda `pip install` e as migrações e registra e inicia o serviço de novo. Se o backup falhar, nada é alterado e o serviço volta a rodar. A página do banco não aparece: o `.env` existente não é tocado. |
| nada | **Instalação nova** | Página "Configuração do PostgreSQL" com **Testar conexão**. Gera o `.env` com `SECRET_KEY` e `BIOMETRIA_API_TOKEN` aleatórios, cria o banco se faltar, as tabelas e o admin (`admin`/`admin123`, **troque no primeiro acesso**) e registra o serviço. |

Telas do assistente:

1. **Pasta.** Na primeira vez com o cliente atual (instalado à mão), aponte a pasta existente
   (ex.: `C:\Sistema_controle_acesso`). Nas próximas, o instalador sugere a mesma pasta.
2. **Configuração do PostgreSQL** (só instalação nova). O teste usa o `psql.exe` do PostgreSQL
   instalado, com os dados só em variáveis de ambiente. A pasta das ferramentas (`psql.exe` +
   `pg_dump.exe`) é detectada automaticamente e aparece na página. A ordem de busca é:
   1. o serviço `postgresql*` desta máquina, pelo caminho do `pg_ctl.exe`;
   2. o registro do instalador oficial;
   3. `Program Files\PostgreSQL\<versão>\bin` (64 e 32 bits) e `C:\PostgreSQL\<versão>\bin`;
   4. o PATH.

   A mesma pasta é usada no backup (`pg_dump`) e nas migrações (`psql`). Se o banco estiver em
   outro computador (ou no Docker), instale nesta máquina as "Command Line Tools" do PostgreSQL;
   sem elas, as migrações falham. Resultados possíveis do teste:

   | Resultado | Significado |
   |---|---|
   | ok | Conectou |
   | ok, banco será criado | As credenciais valem, mas o banco ainda não existe |
   | servidor inacessível | Endereço, porta ou firewall |
   | acesso recusado | Usuário, senha ou `pg_hba.conf` |
   | `psql` não encontrado | Não dá para testar; é possível continuar mesmo assim |

3. **Python.** É identificado automaticamente, e esta página só aparece se nenhum Python válido for
   encontrado. A busca segue esta ordem:
   1. o Python usado na instalação anterior;
   2. `C:\Program Files\Python3XX` e `C:\Python3XX`, do mais novo ao mais antigo;
   3. o registro do Windows (`HKLM\SOFTWARE\Python\PythonCore`).

   Só vale um `python.exe` **64 bits, versão 3.10 ou mais nova, fora de `C:\Users`** (instalado para
   todos os usuários), porque o serviço roda como SYSTEM com esse Python. O Python escolhido aparece
   na tela "Pronto para instalar" e no log do Inno. Se já existir `venv` na pasta, ele é reaproveitado.

   **Sem Python válido:** a página oferece "Instalar o Python automaticamente", já marcada. O
   instalador oficial embutido roda em modo silencioso, com
   `/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_doc=0 Include_launcher=1`, e
   instala em `C:\Program Files\Python3XX`, no PATH. Depois, o Python é localizado de novo e o processo
   segue. Se a máquina já tiver o **mesmo** Python instalado só para um usuário, o instalador do Python
   pode só "modificar" essa instalação. Nesse caso, remova-a e execute de novo.

   Para embutir o Python, coloque o instalador oficial de python.org em `installer\python\`
   (ex.: `python-3.14.4-amd64.exe`; a pasta não vai para o Git). O `build.ps1` pega o mais novo e
   confere a assinatura digital da Python Software Foundation. Prefira o instalador **completo**
   (~28 MB), que instala sem internet. A cópia de ~1 MB que fica em `%LOCALAPPDATA%\Package Cache`
   baixa os componentes do python.org durante a instalação.

**A senha do banco** só viaja por variável de ambiente do processo (`PGPASSWORD` para `psql` e
`pg_dump`; `GYMFLOW_DB_PASS` para o `config_inicial.py`). Ela nunca vai em argumento, arquivo
temporário ou log. Depois da instalação, ela fica apenas no `.env`.

Depois da cópia, cada passo abaixo confere o código de saída. Se falhar, o instalador para e mostra o
log:

1. Cria o `venv`, se ainda não existir.
2. `venv\Scripts\python -m pip install -r requirements.txt`. Se falhar, tenta de novo com
   `--use-feature=truststore`, que usa os certificados do Windows. Isso cobre redes com proxy ou
   antivírus que inspecionam HTTPS.
3. `installer\config_inicial.py`:
   - **instalação nova:** gera o `.env`;
   - **sempre:** garante o **mesmo** `BIOMETRIA_API_TOKEN` no `.env` e no `api_token` de
     `biometria\bio_config.json`, se o arquivo existir. Quando falta num dos dois, copia o valor do
     outro; quando falta nos dois, gera um novo. Os demais campos ficam como estão. Se os dois
     tiverem valores diferentes, nada é alterado e o instalador avisa.
4. `installer\atualizar_banco.py`: `db.create_all()`, registros padrão e as migrações de `migrations\`.
   Se um passo falhar até aqui, o serviço **não** é iniciado com o código novo.
5. Serviço:
   - se houver um serviço antigo do NSSM com outro nome apontando para a pasta, ele é parado e removido;
   - depois rodam `serve.py --instalar-servico` e `serve.py --iniciar-servico` (nova tentativa no
     fim do assistente, como no Nway Control).
6. Health check em `http://localhost:5000` (até 60 s) e aviso se o token faltar.

Logs:
- `logs\instalacao.log`: saída de cada passo (pip, migrações, serviço);
- `logs\servico.log`: o serviço (waitress), com rotação de 10 MB × 5;
- log do Inno: `%TEMP%\Setup Log *.txt`.

### Serviço Windows "GymFlow" (pywin32, sem NSSM)

Comandos (rodar como administrador, na pasta do sistema):

```powershell
venv\Scripts\python.exe serve.py --instalar-servico
venv\Scripts\python.exe serve.py --iniciar-servico
venv\Scripts\python.exe serve.py --parar-servico
venv\Scripts\python.exe serve.py --remover-servico
venv\Scripts\python.exe serve.py
```

O último roda em primeiro plano, no console, para diagnóstico.

- Início automático e conta **LocalSystem**.
- Reinício em falha via `sc failure` (5 s, 5 s e depois a cada 30 s) e `sc failureflag 1`. Vale
  também quando o processo é morto ou quando o servidor web para (por exemplo, com o PostgreSQL
  ainda subindo no boot).
- **Como o Python é usado.** O serviço executa o **python.exe base** do venv com `-S`, assim:
  `"C:\Program Files\Python314\python.exe" -S "<pasta>\serve.py" --servico`.
  - O `venv\Scripts\python.exe` é só um lançador que abre outro processo, e o Windows não conseguiria
    falar com ele.
  - Com `-S`, o site-packages global (inclusive outro pywin32) não é carregado. O `serve.py` põe o
    `venv\Lib\site-packages` no caminho, e assim o pywin32, as DLLs dele e o waitress vêm do venv.
  - Não é usado o `pythonservice.exe`. Por isso o Python base precisa continuar instalado no mesmo
    caminho e ser acessível pela conta SYSTEM (`Program Files` serve).
  - Para conferir, o comando registrado aparece em `sc qc GymFlow`.
- **Suspensão.** `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` vale enquanto o
  serviço roda e não altera o `powercfg`. Ele não impede a suspensão pedida pelo usuário (menu, botão
  de energia, tampa).

### Migrações

`atualizar_banco.py` aplica, em ordem de nome, os arquivos `migrations\NNN_*.sql`, sem os `*_down.sql`
e sem os que não começam com número. A aplicação usa `psql -v ON_ERROR_STOP=1`. Uma migração é pulada
quando o nome do arquivo (sem `.sql` e sem `_up`) já está em `schema_migracao.versao`. As demais rodam
de novo, o que exige que **toda migração seja idempotente e rode numa transação**.

### Desinstalar

Painel de Controle → Programas → GymFlow. O desinstalador remove o serviço (`serve.py --remover-servico`,
com `sc stop/delete` como reserva) e os arquivos de código que ele instalou. Depois faz duas perguntas,
as duas com resposta padrão **Não**:

1. Excluir a configuração (`.env`), os logs, os backups de instalação e o `venv`?
2. Excluir as **fotos e documentos** dos alunos?

O banco de dados nunca é alterado.

### Voltar à versão anterior

1. `venv\Scripts\python.exe serve.py --parar-servico`.
2. Restaure a pasta a partir de `backups_instalacao\pasta_<data>.zip`.
3. Inicie o serviço da versão restaurada. Se ela ainda não tiver o `serve.py` com os comandos (por
   exemplo, a versão manual com NSSM), remova o serviço `GymFlow` (`sc delete GymFlow`) e reinstale
   o serviço antigo.

Só em último caso, restaure também o banco com `pg_restore` do `banco_<data>.dump`: isso desfaz tudo o
que foi gravado depois do backup. Os backups não são apagados automaticamente.

## 3. Validação no QAS (`C:\Academia_teste`)

- [ ] Instalar **por cima** da versão anterior: `.env`, `app\static\fotos`, `app\static\documentos`,
      `app\static\img` e `biometria\bio_config.json` ficam iguais (compare data e tamanho).
- [ ] O `.env` e o `bio_config.json` têm o mesmo token (`BIOMETRIA_API_TOKEN` / `api_token`), e o
      FingerPoint registra uma passagem.
- [ ] `backups_instalacao\` tem o `.zip` da pasta e o `.dump` do banco.
- [ ] `sc qc GymFlow` mostra `AUTO_START`, `LocalSystem` e o comando `python.exe -S ...serve.py --servico`.
      O serviço NSSM antigo, se existia com outro nome, sumiu.
- [ ] Reiniciar o Windows: o site volta sozinho, sem ninguém logado.
- [ ] Matar o processo (`taskkill /F /PID <pid do python do serviço>`): o site volta em cerca de 5 s.
- [ ] `powercfg /requests`, num prompt de admin, mostra o `python.exe` na seção **SYSTEM**.
- [ ] Instalação nova numa pasta vazia: "Testar conexão" com senha errada, porta errada e banco
      inexistente mostra a mensagem certa.
- [ ] Desinstalar: o serviço some e, respondendo "Não", os dados ficam.

## Apêndice (opcional): outros ajustes de energia do servidor

Nada disso é feito pelo instalador. São ajustes a considerar se ainda houver quedas.

- **USB do leitor (U.are.U 4500):** no Gerenciador de Dispositivos, abra cada "Hub raiz USB" →
  Gerenciamento de Energia e desmarque "O computador pode desligar este dispositivo para economizar
  energia". No plano de energia: Configurações avançadas → USB → Suspensão seletiva de USB →
  Desabilitado.
- **Placa de rede:** no Gerenciador de Dispositivos → adaptador → Gerenciamento de Energia, desmarque
  o desligamento para economizar energia. Em Avançado, desligue "Energy Efficient Ethernet" / "Green
  Ethernet", se existir. Isso evita que o Cloudflare Tunnel caia.
- **BIOS/UEFI:** ative "Restore on AC Power Loss" = Power On, para religar sozinho depois de uma queda
  de energia. Se houver suspensão que o Windows não explica, desative também ErP/EuP e os estados
  profundos (Deep Sleep).
- **Windows Update:** defina o "Horário ativo" cobrindo o horário da academia. Depois de um reinício
  pelo Update, o serviço volta sozinho.
