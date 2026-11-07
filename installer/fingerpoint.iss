; =============================================================================
; FingerPoint (leitor biometrico) - instalador e atualizador (Inno Setup 6)
; O GymFlow Web tem instalador proprio: installer\gymflow.iss
;
; Compilar com installer\build.ps1 -Produto fingerpoint (recomendado) ou:
;   ISCC.exe /DSrcDir=<pasta do codigo> [/DAppVersion=x.y.z] [/DSufixo=-teste] installer\fingerpoint.iss
;
; Exige o GymFlow Web ja instalado: usa a pasta dele (registro de desinstalacao do
; AppId do gymflow.iss, InstallLocation; reserva C:\GymFlow) e o venv dele.
; Instala em <pasta>\biometria:
;   - antes da copia: exige o FingerPoint fechado (oferece encerrar) e faz backup
;     da pasta biometria atual (zip, sem logs) em <pasta>\backups_instalacao;
;   - depois: confere as dependencias no venv (pip so se faltar) e roda o
;     installer\config_inicial.py ja instalado (api_token = BIOMETRIA_API_TOKEN).
; Python e driver do leitor nao sao instalados.
;
; Nunca copia nem remove: bio_config.json (senha do banco e token), logs\,
; backups. Nao entram no pacote: o .rar do SDK, o .cpp e o iniciar_biometria.bat
; antigo (substituido por installer\iniciar_biometria.bat, que usa o venv).
; =============================================================================

#ifndef SrcDir
  #define SrcDir AddBackslash(SourcePath) + ".."
#endif
#ifndef Sufixo
  #define Sufixo ""
#endif

; --- Versao: passada pelo build.ps1 ou lida de biometria\app.py (APP_VERSAO = "x.y.z")
#ifndef AppVersion
  #define AppVersion ""
  #define VerLinha ""
  #define VerArq FileOpen(SrcDir + "\biometria\app.py")
  #if !VerArq
    #error Nao foi possivel abrir biometria\app.py em SrcDir
  #endif
  #define EntreAspas(str L) Copy(Copy(L, Pos('"', L) + 1), 1, Pos('"', Copy(L, Pos('"', L) + 1)) - 1)
  #for {VerLinha = ""; AppVersion == "" && !FileEof(VerArq); VerLinha = FileRead(VerArq)} \
    AppVersion = (Pos("APP_VERSAO", VerLinha) == 1 ? EntreAspas(VerLinha) : "")
  #expr FileClose(VerArq)
#endif
#if AppVersion == ""
  #error APP_VERSAO nao encontrado em biometria\app.py
#endif

[Setup]
; GUID fixo e proprio do FingerPoint (o do GymFlow Web e outro)
AppId={{104FB6A5-6520-42AF-B2E4-DBDB43CCCFB2}
AppName=FingerPoint
AppVersion={#AppVersion}
AppVerName=FingerPoint {#AppVersion}
AppPublisher=GymFlow
VersionInfoVersion={#AppVersion}
; {app} = pasta do GymFlow Web; os arquivos vao para {app}\biometria
DefaultDirName={code:PastaGymFlowInstalado}
UsePreviousAppDir=no
AppendDefaultDirName=no
DisableDirPage=no
DirExistsWarning=no
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=saida
OutputBaseFilename=FingerPoint-Setup-{#AppVersion}{#Sufixo}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=FingerPoint {#AppVersion}
; Desinstalador dentro de biometria\: nao se mistura com o unins000.exe do GymFlow Web
UninstallFilesDir={app}\biometria
; O FingerPoint e fechado no PrepareToInstall
CloseApplications=no
; Log em %TEMP%\Setup Log AAAA-MM-DD #NNN.txt. Nunca contem a senha do banco.
SetupLogging=yes

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Messages]
ptbr.FinishedLabelNoIcons=O FingerPoint {#AppVersion} foi instalado.%n%nConfira o titulo da janela: deve mostrar v{#AppVersion}. Se aparecer ERRO DE AUTENTICACAO, o api_token esta diferente do BIOMETRIA_API_TOKEN.
ptbr.FinishedLabel=O FingerPoint {#AppVersion} foi instalado.%n%nConfira o titulo da janela: deve mostrar v{#AppVersion}. Se aparecer ERRO DE AUTENTICACAO, o api_token esta diferente do BIOMETRIA_API_TOKEN.

[Tasks]
Name: "desktopicon"; Description: "Atalho ""FingerPoint"" na Area de Trabalho"; GroupDescription: "Atalhos:"
Name: "iniciarwindows"; Description: "Iniciar com o Windows"; GroupDescription: "Atalhos:"; Flags: unchecked

[Dirs]
; Pastas de dados: criadas se faltarem e nunca removidas
Name: "{app}\biometria\logs"; Flags: uninsneveruninstall
Name: "{app}\backups_instalacao"; Flags: uninsneveruninstall

[Files]
; Lista fechada: bio_config.json, logs\, o .rar, o .cpp e o .bat antigo nunca entram
Source: "{#SrcDir}\biometria\app.py"; DestDir: "{app}\biometria"; Flags: ignoreversion
Source: "{#SrcDir}\biometria\bio_*.py"; DestDir: "{app}\biometria"; Flags: ignoreversion
Source: "{#SrcDir}\biometria\bio_config.example.json"; DestDir: "{app}\biometria"; Flags: ignoreversion
Source: "{#SrcDir}\biometria\*.dll"; DestDir: "{app}\biometria"; Flags: ignoreversion
Source: "{#SrcDir}\biometria\CHANGELOG.md"; DestDir: "{app}\biometria"; Flags: ignoreversion
; Inicio com console (diagnostico), pelo python do venv do GymFlow
Source: "{#SrcDir}\installer\iniciar_biometria.bat"; DestDir: "{app}\biometria"; Flags: ignoreversion

[Icons]
Name: "{commondesktop}\FingerPoint"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "app.py"; \
  WorkingDir: "{app}\biometria"; Comment: "FingerPoint {#AppVersion}"; Tasks: desktopicon
Name: "{commonstartup}\FingerPoint"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "app.py"; \
  WorkingDir: "{app}\biometria"; Comment: "FingerPoint {#AppVersion}"; Tasks: iniciarwindows

[Run]
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "app.py"; WorkingDir: "{app}\biometria"; \
  Description: "Abrir o FingerPoint agora"; Flags: postinstall nowait skipifsilent runasoriginaluser

[Code]
const
  AppIdGymFlow = '{58FD47C6-707F-4124-8952-2E0C1C601FDD}';
  CHAVE_DESINSTALAR = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\';
  PastaPadrao = 'C:\GymFlow';
  TITULO_JANELA = 'fingerpoint';

var
  ArqLog: String;
  { PIDs no formato ' 123 456 ' }
  PidsJanelas: String;

function EnumWindows(lpEnumFunc: Longword; lParam: Longint): Integer;
  external 'EnumWindows@user32.dll stdcall';
function GetWindowTextW(hWnd: HWND; lpString: String; nMaxCount: Integer): Integer;
  external 'GetWindowTextW@user32.dll stdcall';
function GetWindowThreadProcessId(hWnd: HWND; var lpdwProcessId: Longword): Longword;
  external 'GetWindowThreadProcessId@user32.dll stdcall';
function IsWindowVisible(hWnd: HWND): Integer;
  external 'IsWindowVisible@user32.dll stdcall';

{ ---------------------------------------------------------------------------
  Utilitarios
  --------------------------------------------------------------------------- }

function PastaApp(): String;
begin
  Result := RemoveBackslashUnlessRoot(WizardDirValue());
end;

function PastaBio(): String;
begin
  Result := PastaApp() + '\biometria';
end;

function VenvPython(): String;
begin
  Result := PastaApp() + '\venv\Scripts\python.exe';
end;

procedure Registrar(Texto: String);
begin
  Log(Texto);
  if ArqLog <> '' then
    SaveStringToFile(ArqLog, GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + '  ' + Texto + #13#10, True);
end;

{ Roda um programa escondido, com stdout/stderr no log de instalacao. }
function Executar(Descricao, Programa, Parametros: String): Integer;
begin
  WizardForm.StatusLabel.Caption := Descricao;
  Registrar('== ' + Descricao);
  if not Exec(ExpandConstant('{cmd}'),
              '/c ""' + Programa + '" ' + Parametros + ' >> "' + ArqLog + '" 2>&1"',
              PastaApp(), SW_HIDE, ewWaitUntilTerminated, Result) then
    Result := -1;
  Registrar('   codigo de saida: ' + IntToStr(Result));
end;

{ Pasta do GymFlow Web: InstallLocation do registro de desinstalacao do gymflow.iss }
function PastaGymFlowInstalado(Param: String): String;
var
  Chave: String;
begin
  Chave := CHAVE_DESINSTALAR + AppIdGymFlow + '_is1';
  Result := '';
  if not RegQueryStringValue(HKLM64, Chave, 'InstallLocation', Result) then
    if not RegQueryStringValue(HKLM32, Chave, 'InstallLocation', Result) then
      Result := '';
  Result := RemoveBackslashUnlessRoot(Trim(Result));
  if Result = '' then
  begin
    Result := PastaPadrao;
    Log('GymFlow Web nao encontrado no registro; pasta sugerida: ' + Result);
  end
  else
    Log('GymFlow Web instalado em: ' + Result);
end;

{ '' se a pasta tem o GymFlow Web instalado; senao, a mensagem de erro }
function ValidarPasta(Pasta: String): String;
var
  Faltam: String;
begin
  Result := '';
  Faltam := '';
  if not FileExists(Pasta + '\venv\Scripts\pythonw.exe') then Faltam := Faltam + #13#10 + '  venv\Scripts\pythonw.exe';
  if not FileExists(Pasta + '\venv\Scripts\python.exe') then Faltam := Faltam + #13#10 + '  venv\Scripts\python.exe';
  if not FileExists(Pasta + '\.env') then Faltam := Faltam + #13#10 + '  .env';
  if not DirExists(Pasta + '\app') then Faltam := Faltam + #13#10 + '  app\';
  if not FileExists(Pasta + '\installer\config_inicial.py') then Faltam := Faltam + #13#10 + '  installer\config_inicial.py';
  if Faltam <> '' then
    Result := 'A pasta ' + Pasta + ' nao tem o GymFlow Web instalado. Faltam:' + Faltam + #13#10 + #13#10 +
      'Instale ou atualize primeiro o GymFlow Web (GymFlow-Setup-<versao>.exe) e depois execute ' +
      'este instalador de novo, escolhendo a pasta do sistema.';
end;

{ ---------------------------------------------------------------------------
  FingerPoint aberto? (janela "FingerPoint..." ou python/pythonw rodando app.py)
  --------------------------------------------------------------------------- }

function TemPid(Lista: String; Pid: String): Boolean;
begin
  Result := Pos(' ' + Pid + ' ', Lista) > 0;
end;

function AoEnumerarJanela(Wnd: HWND; Param: Longint): Integer;
var
  Titulo: String;
  Tam: Integer;
  Pid: Longword;
begin
  Result := 1;
  if IsWindowVisible(Wnd) = 0 then Exit;
  Titulo := StringOfChar(' ', 256);
  Tam := GetWindowTextW(Wnd, Titulo, 256);
  if Tam <= 0 then Exit;
  Titulo := Copy(Titulo, 1, Tam);
  if Pos(TITULO_JANELA, Lowercase(Titulo)) = 1 then
  begin
    Pid := 0;
    GetWindowThreadProcessId(Wnd, Pid);
    if (Pid <> 0) and not TemPid(PidsJanelas, IntToStr(Pid)) then
      PidsJanelas := PidsJanelas + ' ' + IntToStr(Pid) + ' ';
  end;
end;

{ Linha de comando de python que roda o app.py desta pasta (caminho completo)
  ou um app.py relativo (atalho/bat: pasta inicial = biometria). }
function ComandoDoFingerPoint(Cmd, Bio: String): Boolean;
var
  P: Integer;
  Depois: String;
begin
  Result := False;
  Cmd := Lowercase(Cmd);
  if Pos(Lowercase(Bio) + '\app.py', Cmd) > 0 then
  begin
    Result := True;
    Exit;
  end;
  P := Pos('app.py', Cmd);
  if P = 0 then Exit;
  Depois := Copy(Cmd, P + 6, 1);
  if (Depois <> '') and (Depois <> ' ') and (Depois <> '"') then Exit;
  Result := (P = 1) or (Cmd[P - 1] = ' ') or (Cmd[P - 1] = '"');
end;

{ PIDs (' 123 456 ') dos processos do FingerPoint. Bio = pasta biometria. }
function ProcessosFingerPoint(Bio: String): String;
var
  Locator, Servico, Lista, Item: Variant;
  I: Integer;
  Pid, Cmd, PidsPython, Resto: String;
begin
  Result := '';
  PidsPython := '';
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Servico := Locator.ConnectServer('.', 'root\CIMV2');
    Lista := Servico.ExecQuery('SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name LIKE ''python%''');
    for I := 0 to Lista.Count - 1 do
    begin
      Item := Lista.ItemIndex(I);
      Pid := IntToStr(Item.ProcessId);
      PidsPython := PidsPython + ' ' + Pid + ' ';
      if VarIsNull(Item.CommandLine) then
        Cmd := ''
      else
        Cmd := Item.CommandLine;
      if ComandoDoFingerPoint(Cmd, Bio) then
      begin
        Log('FingerPoint em execucao: PID ' + Pid + ' ' + Cmd);
        Result := Result + ' ' + Pid + ' ';
      end;
    end;
  except
    Log('Consulta de processos (WMI) falhou: ' + GetExceptionMessage());
  end;

  { Janelas com titulo "FingerPoint..." de um processo python }
  PidsJanelas := '';
  EnumWindows(CreateCallback(@AoEnumerarJanela), 0);
  Resto := Trim(PidsJanelas);
  while Resto <> '' do
  begin
    if Pos(' ', Resto) > 0 then
    begin
      Pid := Copy(Resto, 1, Pos(' ', Resto) - 1);
      Resto := Trim(Copy(Resto, Pos(' ', Resto) + 1, MaxInt));
    end
    else
    begin
      Pid := Resto;
      Resto := '';
    end;
    if TemPid(PidsPython, Pid) and not TemPid(Result, Pid) then
    begin
      Log('Janela do FingerPoint aberta: PID ' + Pid);
      Result := Result + ' ' + Pid + ' ';
    end;
  end;
end;

{ Fecha com pedido normal (WM_CLOSE) e, se nao fechar, a forca. }
procedure EncerrarProcessos(Pids: String);
var
  Resto, Pid: String;
  Codigo, Rodada: Integer;
begin
  for Rodada := 1 to 2 do
  begin
    Resto := Trim(Pids);
    while Resto <> '' do
    begin
      if Pos(' ', Resto) > 0 then
      begin
        Pid := Copy(Resto, 1, Pos(' ', Resto) - 1);
        Resto := Trim(Copy(Resto, Pos(' ', Resto) + 1, MaxInt));
      end
      else
      begin
        Pid := Resto;
        Resto := '';
      end;
      if Rodada = 1 then
        Exec(ExpandConstant('{sys}\taskkill.exe'), '/PID ' + Pid + ' /T', '', SW_HIDE, ewWaitUntilTerminated, Codigo)
      else
        Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /PID ' + Pid + ' /T', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
    end;
    Sleep(3000);
  end;
end;

{ True se o FingerPoint esta fechado (ou foi encerrado). Pergunta enquanto estiver aberto. }
function GarantirFingerPointFechado(Bio: String): Boolean;
var
  Pids, Legivel: String;
begin
  Result := False;
  Pids := ProcessosFingerPoint(Bio);
  while Pids <> '' do
  begin
    Legivel := Trim(Pids);
    StringChangeEx(Legivel, '  ', ', ', True);
    case SuppressibleMsgBox('O FingerPoint esta aberto (PID ' + Legivel + ').' + #13#10 + #13#10 +
           'Sim: o instalador encerra o FingerPoint agora.' + #13#10 +
           'Nao: ja fechei, verificar de novo.' + #13#10 +
           'Cancelar: nao continuar.', mbConfirmation, MB_YESNOCANCEL or MB_DEFBUTTON3, IDCANCEL) of
      IDYES: EncerrarProcessos(Pids);
      IDNO: Log('Usuario informou que fechou o FingerPoint; verificando de novo.');
    else
      Exit;
    end;
    Pids := ProcessosFingerPoint(Bio);
  end;
  Result := True;
end;

{ ---------------------------------------------------------------------------
  Ciclo de vida do assistente
  --------------------------------------------------------------------------- }

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Erro: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Erro := ValidarPasta(PastaApp());
    if Erro <> '' then
    begin
      SuppressibleMsgBox(Erro, mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := 'FingerPoint {#AppVersion} em:' + NewLine + Space + PastaBio() + NewLine + NewLine;
  if DirExists(PastaBio()) then
    Result := Result + 'Antes de copiar: o FingerPoint precisa estar fechado e sera feito backup da pasta ' +
      'biometria (zip, sem logs) em' + NewLine + Space + PastaApp() + '\backups_instalacao' + NewLine +
      'Preservados: bio_config.json e logs.' + NewLine + NewLine;
  Result := Result + 'Python: venv do GymFlow Web (' + PastaApp() + '\venv)' + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + MemoTasksInfo + NewLine;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Carimbo, Zip: String;
  Codigo: Integer;
begin
  { Silencioso ou pagina pulada: valida aqui tambem }
  Result := ValidarPasta(PastaApp());
  if Result <> '' then
  begin
    Log('ABORTADO: ' + Result);
    Exit;
  end;

  ForceDirectories(PastaApp() + '\logs');
  ArqLog := PastaApp() + '\logs\instalacao.log';
  Registrar('===== FingerPoint {#AppVersion}: ' + PastaBio());

  { 1. FingerPoint fechado }
  if not GarantirFingerPointFechado(PastaBio()) then
  begin
    Result := 'O FingerPoint continua aberto. Feche-o e execute o instalador de novo. Nada foi alterado.';
    Registrar('ABORTADO: ' + Result);
    Exit;
  end;

  { 2. Backup da pasta biometria atual (sem logs e sem o desinstalador) }
  if not DirExists(PastaBio()) then
  begin
    Registrar('Pasta biometria ainda nao existe: sem backup (instalacao nova).');
    Exit;
  end;
  Carimbo := GetDateTimeString('yyyy-mm-dd_hh-nn-ss', '-', '-');
  ForceDirectories(PastaApp() + '\backups_instalacao');
  Zip := PastaApp() + '\backups_instalacao\biometria-' + Carimbo + '.zip';
  Codigo := Executar('Backup da pasta biometria (zip)...', ExpandConstant('{sys}\tar.exe'),
    '-a -c -f "' + Zip + '" --exclude=./logs --exclude=__pycache__ --exclude=unins*.exe --exclude=unins*.dat ' +
    '-C "' + PastaBio() + '" .');
  if (Codigo <> 0) or not FileExists(Zip) then
  begin
    Result := 'Falha no backup da pasta biometria (tar codigo ' + IntToStr(Codigo) + ').' + #13#10 +
      'Nada foi alterado. Detalhes em ' + ArqLog;
    Registrar('ABORTADO: ' + Result);
    Exit;
  end;
  Registrar('   backup: ' + Zip);
end;

{ ---------------------------------------------------------------------------
  Depois da copia: dependencias no venv e token (config_inicial.py)
  --------------------------------------------------------------------------- }

function DependenciasOk(): Boolean;
begin
  Result := Executar('Conferindo as dependencias do FingerPoint no venv...', VenvPython(),
    '-I -c "import tkinter, customtkinter, psycopg, requests"') = 0;
end;

procedure GarantirDependencias();
var
  Pacotes: String;
begin
  if DependenciasOk() then Exit;

  if FileExists(PastaApp() + '\requirements.txt') then
    Pacotes := '-r "' + PastaApp() + '\requirements.txt"'
  else
    Pacotes := 'customtkinter "psycopg[binary]" requests pillow';
  { 2a tentativa com os certificados do Windows (proxy/antivirus que inspeciona HTTPS) }
  if Executar('Instalando dependencias do FingerPoint (pip install)...', VenvPython(),
              '-m pip install --disable-pip-version-check ' + Pacotes) <> 0 then
    Executar('Instalando dependencias (nova tentativa com os certificados do Windows)...', VenvPython(),
             '-m pip install --disable-pip-version-check --use-feature=truststore ' + Pacotes);

  if not DependenciasOk() then
    SuppressibleMsgBox('Faltam dependencias do FingerPoint no venv do GymFlow (tkinter, customtkinter, psycopg ' +
      'ou requests). O FingerPoint nao vai abrir.' + #13#10 + #13#10 +
      'Se faltar o tkinter, o Python foi instalado sem "tcl/tk and IDLE": modifique a instalacao do ' +
      'Python e marque essa opcao. Para os demais, confira o acesso a internet (pypi.org).' + #13#10 + #13#10 +
      'Log: ' + ArqLog, mbError, MB_OK, IDOK);
end;

procedure SincronizarToken();
var
  Codigo: Integer;
begin
  { Mesmo passo da atualizacao do gymflow.iss. Sem bio_config.json, ele o cria a partir
    do bio_config.example.json (banco do DATABASE_URL) e grava o api_token. }
  Codigo := Executar('Conferindo o token do FingerPoint...', VenvPython(),
                     '"' + PastaApp() + '\installer\config_inicial.py"');
  if Codigo = 3 then
    SuppressibleMsgBox('Atencao: BIOMETRIA_API_TOKEN do .env e api_token do biometria\bio_config.json sao diferentes.' + #13#10 +
      'Nada foi alterado. Deixe os dois iguais e reinicie o servico GymFlow e o FingerPoint, ' +
      'senao o FingerPoint mostra ERRO DE AUTENTICACAO.', mbInformation, MB_OK, IDOK)
  else if Codigo <> 0 then
    SuppressibleMsgBox('Nao foi possivel conferir o token do FingerPoint (codigo ' + IntToStr(Codigo) + ').' + #13#10 +
      'Confira se api_token do biometria\bio_config.json e igual ao BIOMETRIA_API_TOKEN do .env.' + #13#10 + #13#10 +
      'Log: ' + ArqLog, mbError, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    GarantirDependencias();
    SincronizarToken();
    Registrar('Confira o titulo da janela: deve mostrar v{#AppVersion}. Se aparecer ERRO DE AUTENTICACAO, ' +
      'o api_token esta diferente do BIOMETRIA_API_TOKEN.');
    Registrar('===== fim');
  end;
end;

{ ---------------------------------------------------------------------------
  Desinstalacao: so atalhos e arquivos instalados (bio_config.json, logs e
  backups ficam). O FingerPoint precisa estar fechado.
  --------------------------------------------------------------------------- }

function InitializeUninstall(): Boolean;
begin
  Result := GarantirFingerPointFechado(ExpandConstant('{app}') + '\biometria');
  if not Result then
    SuppressibleMsgBox('O FingerPoint continua aberto. Feche-o e desinstale de novo.', mbError, MB_OK, IDOK);
end;
