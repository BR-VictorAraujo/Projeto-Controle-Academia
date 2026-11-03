; =============================================================================
; GymFlow Web - instalador e atualizador (Inno Setup 6)
; O FingerPoint (leitor biometrico) tem instalador proprio: installer\fingerpoint.iss
;
; Compilar com installer\build.ps1 (recomendado) ou:
;   ISCC.exe /DSrcDir=<pasta do codigo> [/DSufixo=-teste] installer\gymflow.iss
;
; Um unico instalador para os dois casos, detectados pela pasta escolhida:
;   - pasta com .env e app\  -> ATUALIZAR: para o servico, backup (zip + pg_dump),
;     troca o codigo, pip install, migracoes, registra e inicia o servico de novo.
;   - pasta vazia            -> INSTALACAO NOVA: pagina do PostgreSQL (com teste),
;     gera o .env (SECRET_KEY e BIOMETRIA_API_TOKEN), cria tabelas + admin e o servico.
; O PostgreSQL nao e instalado nem alterado (apenas usado).
;
; Servico Windows "GymFlow": registrado e controlado pelo proprio serve.py (pywin32),
; como o Nway Control faz com o NwayControl.exe. Sem NSSM.
;
; Senha do banco: so viaja por variavel de ambiente do processo (PGPASSWORD /
; GYMFLOW_DB_PASS), nunca em argumento, arquivo temporario ou log.
;
; Nunca sobrescreve: .env, app\static\fotos, app\static\documentos,
; app\static\img (so adiciona o que faltar), biometria\, logs, venv.
; =============================================================================

#ifndef SrcDir
  #define SrcDir AddBackslash(SourcePath) + ".."
#endif
#ifndef Sufixo
  #define Sufixo ""
#endif
; Instalador oficial do Python (python-3.x.y-amd64.exe) embutido: usado so se a maquina
; nao tiver um Python 64 bits >= 3.10 para todos os usuarios. Passado pelo build.ps1.
#ifdef PythonSetup
  #define PythonSetupNome ExtractFileName(PythonSetup)
#else
  #define PythonSetupNome ""
#endif

; --- Versao lida de app\version.py (linha APP_VERSAO = "x.y.z") -------------
#define AppVersion ""
#define VerLinha ""
#define VerArq FileOpen(SrcDir + "\app\version.py")
#if !VerArq
  #error Nao foi possivel abrir app\version.py em SrcDir
#endif
#define EntreAspas(str L) Copy(Copy(L, Pos('"', L) + 1), 1, Pos('"', Copy(L, Pos('"', L) + 1)) - 1)
#for {VerLinha = ""; AppVersion == "" && !FileEof(VerArq); VerLinha = FileRead(VerArq)} \
  AppVersion = (Pos("APP_VERSAO", VerLinha) == 1 ? EntreAspas(VerLinha) : "")
#expr FileClose(VerArq)
#if AppVersion == ""
  #error APP_VERSAO nao encontrado em app\version.py
#endif

[Setup]
; GUID fixo: e ele que identifica uma instalacao anterior (atualizacao no lugar, sem duplicar)
AppId={{58FD47C6-707F-4124-8952-2E0C1C601FDD}
AppName=GymFlow
AppVersion={#AppVersion}
AppVerName=GymFlow {#AppVersion}
AppPublisher=GymFlow
VersionInfoVersion={#AppVersion}
DefaultDirName=C:\GymFlow
UsePreviousAppDir=yes
DisableDirPage=no
DirExistsWarning=no
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=saida
OutputBaseFilename=GymFlow-Setup-{#AppVersion}{#Sufixo}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=GymFlow {#AppVersion}
; O servico e parado no PrepareToInstall; nao ha outra aplicacao a fechar
CloseApplications=no
; Log em %TEMP%\Setup Log AAAA-MM-DD #NNN.txt. Nunca contem a senha do banco.
SetupLogging=yes

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Dirs]
; Pastas de dados: criadas se faltarem e nunca removidas sem confirmacao
Name: "{app}\app\static\fotos"; Flags: uninsneveruninstall
Name: "{app}\app\static\documentos"; Flags: uninsneveruninstall
Name: "{app}\app\static\img"; Flags: uninsneveruninstall
Name: "{app}\logs"; Flags: uninsneveruninstall
Name: "{app}\backups_instalacao"; Flags: uninsneveruninstall

[Files]
; Codigo web. Os Excludes protegem os dados do cliente: nada disso e copiado.
Source: "{#SrcDir}\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: "\.git,\.gitattributes,\.claude,\venv,\.venv,\.env,__pycache__,*.pyc,*.log,\logs,\backups_instalacao,\biometria,\installer,\app\static\fotos,\app\static\documentos,\app\static\img,\tests,\stop,\docker,\Dockerfile,\docker-compose.yml,\.dockerignore,\requirements-docker.txt,\CLAUDE.md,\seed.py,*.dump,*.backup,*.zip"
; Imagens padrao: so as que ainda nao existem (logo do cliente nunca e trocado)
Source: "{#SrcDir}\app\static\img\*"; DestDir: "{app}\app\static\img"; \
  Flags: onlyifdoesntexist uninsneveruninstall recursesubdirs createallsubdirs skipifsourcedoesntexist
; Scripts do instalador usados depois da copia
Source: "{#SrcDir}\installer\atualizar_banco.py"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "{#SrcDir}\installer\config_inicial.py"; DestDir: "{app}\installer"; Flags: ignoreversion
#ifdef PythonSetup
; Extraido para {tmp} so quando precisa instalar o Python
Source: "{#PythonSetup}"; Flags: dontcopy
#endif

[Run]
Filename: "http://localhost:5000"; Description: "Abrir o GymFlow no navegador"; \
  Flags: postinstall shellexec skipifsilent unchecked

[Code]
const
  ServicoNome = 'GymFlow';
  CHAVE_SERVICOS = 'SYSTEM\CurrentControlSet\Services';
  PythonEmbutido = '{#PythonSetupNome}';

var
  ModoAtualizar: Boolean;
  ServicoTentado: Boolean;
  ServicoLegado: String;
  ArqLog: String;
  PastaBackup: String;
  { pagina do PostgreSQL }
  DbPage: TWizardPage;
  EdHost, EdPorta, EdBanco, EdUsuario, EdSenha: TNewEdit;
  BtnTestar: TNewButton;
  LblResultado: TNewStaticText;
  ConexaoOk: Boolean;
  { pagina do Python }
  PaginaPython: TInputFileWizardPage;
  PythonDetectado: String;
  ChkInstalarPython: TNewCheckBox;
  { ferramentas do PostgreSQL }
  PgBin: String;
  PgBinBuscado: Boolean;
  LblPostgres: TNewStaticText;

function SetEnvironmentVariableW(lpName: String; lpValue: String): Integer;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';
{ Mesma funcao da API, com valor nulo: remove a variavel do ambiente. }
function RemoveEnvironmentVariableW(lpName: String; lpValue: Cardinal): Integer;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

{ ---------------------------------------------------------------------------
  Utilitarios
  --------------------------------------------------------------------------- }

function PastaApp(): String;
begin
  Result := RemoveBackslashUnlessRoot(WizardDirValue());
end;

function VenvPython(): String;
begin
  Result := PastaApp() + '\venv\Scripts\python.exe';
end;

function ArquivoResultado(): String;
begin
  Result := ExpandConstant('{tmp}\gymflow-resultado.txt');
end;

procedure Registrar(Texto: String);
begin
  Log(Texto);
  if ArqLog <> '' then
    SaveStringToFile(ArqLog, GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + '  ' + Texto + #13#10, True);
end;

{ Roda um programa escondido, com stdout/stderr no log de instalacao.
  Variaveis de ambiente definidas antes (ex.: PGPASSWORD) passam para o programa. }
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

function DecodificarUrl(S: String): String;
var
  I: Integer;
begin
  Result := '';
  I := 1;
  while I <= Length(S) do
  begin
    if (S[I] = '%') and (I + 2 <= Length(S)) then
    begin
      Result := Result + Chr(StrToInt('$' + Copy(S, I + 1, 2)));
      I := I + 3;
    end
    else
    begin
      Result := Result + S[I];
      I := I + 1;
    end;
  end;
end;

{ Valor de uma chave do .env (sem aspas). '' se ausente ou vazia. }
function LerEnv(Chave: String): String;
var
  Linhas: TArrayOfString;
  I: Integer;
  L: String;
begin
  Result := '';
  if not LoadStringsFromFile(PastaApp() + '\.env', Linhas) then Exit;
  for I := 0 to GetArrayLength(Linhas) - 1 do
  begin
    L := Trim(Linhas[I]);
    if Pos(Chave + '=', L) = 1 then
    begin
      Result := Trim(Copy(L, Length(Chave) + 2, MaxInt));
      if (Length(Result) >= 2) and ((Result[1] = '"') or (Result[1] = '''')) then
        Result := Copy(Result, 2, Length(Result) - 2);
      Exit;
    end;
  end;
end;

{ postgresql[+driver]://usuario:senha@host:porta/banco?params }
function ParseDatabaseUrl(Url: String; var Host, Porta, Banco, Usuario, Senha: String): Boolean;
var
  P: Integer;
  Credenciais, Resto: String;
begin
  Result := False;
  P := Pos('://', Url);
  if P = 0 then Exit;
  Resto := Copy(Url, P + 3, MaxInt);
  P := Length(Resto);
  while (P > 0) and (Resto[P] <> '@') do P := P - 1;
  if P = 0 then Exit;
  Credenciais := Copy(Resto, 1, P - 1);
  Resto := Copy(Resto, P + 1, MaxInt);
  P := Pos(':', Credenciais);
  if P > 0 then
  begin
    Usuario := DecodificarUrl(Copy(Credenciais, 1, P - 1));
    Senha := DecodificarUrl(Copy(Credenciais, P + 1, MaxInt));
  end
  else
  begin
    Usuario := DecodificarUrl(Credenciais);
    Senha := '';
  end;
  P := Pos('?', Resto);
  if P > 0 then Resto := Copy(Resto, 1, P - 1);
  P := Pos('/', Resto);
  if P = 0 then Exit;
  Banco := DecodificarUrl(Copy(Resto, P + 1, MaxInt));
  Host := Copy(Resto, 1, P - 1);
  Porta := '5432';
  P := Pos(':', Host);
  if P > 0 then
  begin
    Porta := Copy(Host, P + 1, MaxInt);
    Host := Copy(Host, 1, P - 1);
  end;
  Result := (Host <> '') and (Banco <> '');
end;

{ ---------------------------------------------------------------------------
  Ferramentas do PostgreSQL (psql.exe e pg_dump.exe), detectadas automaticamente
  --------------------------------------------------------------------------- }

function BinPostgresValido(Dir: String): Boolean;
begin
  Dir := RemoveBackslashUnlessRoot(Trim(Dir));
  Result := (Dir <> '') and FileExists(Dir + '\psql.exe') and FileExists(Dir + '\pg_dump.exe');
end;

{ Pasta do executavel no ImagePath de um servico ("C:\...\bin\pg_ctl.exe" runservice ...) }
function PastaDoImagePath(ImagePath: String): String;
var
  P: Integer;
begin
  ImagePath := Trim(ImagePath);
  if (ImagePath <> '') and (ImagePath[1] = '"') then
  begin
    ImagePath := Copy(ImagePath, 2, MaxInt);
    P := Pos('"', ImagePath);
  end
  else
    P := Pos(' ', ImagePath);
  if P > 0 then ImagePath := Copy(ImagePath, 1, P - 1);
  Result := ExtractFileDir(ImagePath);
end;

{ Maior versao em <Base>\PostgreSQL\<versao>\bin }
function ProcurarEmPasta(Base: String): String;
var
  FR: TFindRec;
  Melhor: Integer;
begin
  Result := '';
  Melhor := -1;
  if FindFirst(Base + '\PostgreSQL\*', FR) then
    try
      repeat
        if ((FR.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and (FR.Name <> '.') and (FR.Name <> '..') then
          if BinPostgresValido(Base + '\PostgreSQL\' + FR.Name + '\bin') and
             (StrToIntDef(FR.Name, 0) > Melhor) then
          begin
            Melhor := StrToIntDef(FR.Name, 0);
            Result := Base + '\PostgreSQL\' + FR.Name + '\bin';
          end;
      until not FindNext(FR);
    finally
      FindClose(FR);
    end;
end;

function LocalizarBinPostgres(): String;
var
  Nomes: TArrayOfString;
  I: Integer;
  Valor, Caminho: String;
begin
  { 1. servico do PostgreSQL instalado nesta maquina (o banco que esta rodando) }
  if RegGetSubkeyNames(HKLM, CHAVE_SERVICOS, Nomes) then
    for I := 0 to GetArrayLength(Nomes) - 1 do
      if Pos('postgresql', Lowercase(Nomes[I])) = 1 then
        if RegQueryStringValue(HKLM, CHAVE_SERVICOS + '\' + Nomes[I], 'ImagePath', Valor) then
        begin
          Result := PastaDoImagePath(Valor);
          if BinPostgresValido(Result) then Exit;
        end;
  { 2. registro do instalador oficial (EDB) }
  if RegGetSubkeyNames(HKLM64, 'SOFTWARE\PostgreSQL\Installations', Nomes) then
    for I := GetArrayLength(Nomes) - 1 downto 0 do
      if RegQueryStringValue(HKLM64, 'SOFTWARE\PostgreSQL\Installations\' + Nomes[I], 'Base Directory', Valor) then
      begin
        Result := AddBackslash(Valor) + 'bin';
        if BinPostgresValido(Result) then Exit;
      end;
  { 3. pastas padrao }
  Result := ProcurarEmPasta(ExpandConstant('{commonpf64}'));
  if Result <> '' then Exit;
  Result := ProcurarEmPasta(ExpandConstant('{commonpf32}'));
  if Result <> '' then Exit;
  Result := ProcurarEmPasta(ExpandConstant('{sd}'));
  if Result <> '' then Exit;
  { 4. PATH do sistema }
  Valor := GetEnv('PATH') + ';';
  while Pos(';', Valor) > 0 do
  begin
    Caminho := Trim(Copy(Valor, 1, Pos(';', Valor) - 1));
    Valor := Copy(Valor, Pos(';', Valor) + 1, MaxInt);
    if BinPostgresValido(Caminho) then
    begin
      Result := RemoveBackslashUnlessRoot(Caminho);
      Exit;
    end;
  end;
  Result := '';
end;

{ Detectado uma vez por execucao do instalador }
function PastaBinPostgres(): String;
begin
  if not PgBinBuscado then
  begin
    PgBin := LocalizarBinPostgres();
    PgBinBuscado := True;
    if PgBin <> '' then
      Log('PostgreSQL (psql/pg_dump): ' + PgBin)
    else
      Log('PostgreSQL (psql/pg_dump): nao encontrado');
  end;
  Result := PgBin;
end;

{ python.exe serve para o servico? 64 bits e versao >= 3.10 (exigencia das dependencias).
  Fora de pasta de usuario: o servico roda como SYSTEM. }
function PythonValido(Caminho: String): Boolean;
var
  Codigo: Integer;
begin
  Result := False;
  if (Caminho = '') or not FileExists(Caminho) then Exit;
  if Pos(Lowercase(ExpandConstant('{sd}') + '\users\'), Lowercase(Caminho)) = 1 then
  begin
    Log('Python ignorado (instalado so para um usuario): ' + Caminho);
    Exit;
  end;
  Result := Exec(Caminho, '-I -c "import sys,struct; sys.exit(0 if struct.calcsize(''P'') == 8 and sys.version_info >= (3, 10) else 1)"',
                 '', SW_HIDE, ewWaitUntilTerminated, Codigo) and (Codigo = 0);
  if not Result then
    Log('Python ignorado (nao e 64 bits >= 3.10): ' + Caminho);
end;

{ Procura um Python valido, do mais novo para o mais antigo. '' se nenhum. }
function LocalizarPython(): String;
var
  Versoes: TArrayOfString;
  I, V: Integer;
  Caminho: String;
begin
  { 1. o usado na instalacao anterior }
  Result := GetPreviousData('Python', '');
  if PythonValido(Result) then Exit;
  { 2. pastas padrao do instalador oficial "para todos os usuarios" }
  for V := 20 downto 10 do
  begin
    Result := ExpandConstant('{commonpf64}') + '\Python3' + IntToStr(V) + '\python.exe';
    if PythonValido(Result) then Exit;
    Result := ExpandConstant('{sd}') + '\Python3' + IntToStr(V) + '\python.exe';
    if PythonValido(Result) then Exit;
  end;
  { 3. registro do Windows (instalacoes para todos os usuarios) }
  if RegGetSubkeyNames(HKLM64, 'SOFTWARE\Python\PythonCore', Versoes) then
    for I := GetArrayLength(Versoes) - 1 downto 0 do
    begin
      Result := '';
      if not RegQueryStringValue(HKLM64, 'SOFTWARE\Python\PythonCore\' + Versoes[I] + '\InstallPath',
                                 'ExecutablePath', Result) then
        if RegQueryStringValue(HKLM64, 'SOFTWARE\Python\PythonCore\' + Versoes[I] + '\InstallPath', '', Caminho) then
          Result := AddBackslash(Caminho) + 'python.exe';
      if PythonValido(Result) then Exit;
    end;
  Result := '';
end;

{ ---------------------------------------------------------------------------
  Servico do Windows (comandos do proprio serve.py; sc.exe como reserva)
  --------------------------------------------------------------------------- }

function ServicoExisteNome(Nome: String): Boolean;
var
  Codigo: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\sc.exe'), 'query "' + Nome + '"', '', SW_HIDE,
                 ewWaitUntilTerminated, Codigo) and (Codigo = 0);
end;

function ServicoExiste(): Boolean;
begin
  Result := ServicoExisteNome(ServicoNome);
end;

{ Servico antigo (instalacao manual com NSSM) cujo AppDirectory e esta pasta e
  que tem outro nome: e substituido pelo servico GymFlow. }
function DetectarServicoLegado(): String;
var
  Nomes: TArrayOfString;
  I: Integer;
  Dir: String;
begin
  Result := '';
  if not RegGetSubkeyNames(HKLM, CHAVE_SERVICOS, Nomes) then Exit;
  for I := 0 to GetArrayLength(Nomes) - 1 do
    if CompareText(Nomes[I], ServicoNome) <> 0 then
      if RegQueryStringValue(HKLM, CHAVE_SERVICOS + '\' + Nomes[I] + '\Parameters', 'AppDirectory', Dir) then
        if CompareText(RemoveBackslashUnlessRoot(Trim(Dir)), PastaApp()) = 0 then
        begin
          Result := Nomes[I];
          Exit;
        end;
end;

{ Para pelo sc.exe e espera ate 30 s. Funciona com qualquer versao instalada. }
procedure PararPeloSc(Nome: String);
var
  Codigo, I: Integer;
begin
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop "' + Nome + '"', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  for I := 1 to 15 do
  begin
    if Exec(ExpandConstant('{cmd}'), '/c sc query "' + Nome + '" | find "STOPPED" > nul', '', SW_HIDE,
            ewWaitUntilTerminated, Codigo) and (Codigo = 0) then Exit;
    Sleep(2000);
  end;
end;

{ Texto que o serve.py deixou sobre a falha (nunca contem senha). }
function DetalheDoComando(): String;
var
  Linhas: TArrayOfString;
  I: Integer;
begin
  Result := '';
  if LoadStringsFromFile(ArquivoResultado(), Linhas) then
    for I := 0 to GetArrayLength(Linhas) - 1 do
      if Pos('DETALHE=', Linhas[I]) = 1 then
        Result := Copy(Linhas[I], 9, Length(Linhas[I]));
end;

{ python serve.py --<comando> (venv do sistema). Saida no log de instalacao. }
function ExecutarServe(Comando: String): Integer;
begin
  DeleteFile(ArquivoResultado());
  SetEnvironmentVariableW('GYMFLOW_RESULT_FILE', ArquivoResultado());
  try
    if FileExists(VenvPython()) and FileExists(PastaApp() + '\serve.py') then
      Result := Executar('serve.py ' + Comando, VenvPython(), '"' + PastaApp() + '\serve.py" ' + Comando)
    else
      Result := -1;
  finally
    RemoveEnvironmentVariableW('GYMFLOW_RESULT_FILE', 0);
  end;
end;

procedure RegistrarServico();
var
  Codigo: Integer;
begin
  ServicoTentado := True;
  if ServicoLegado <> '' then
  begin
    Registrar('Removendo o servico antigo ' + ServicoLegado + ' (substituido pelo servico ' + ServicoNome + ')');
    PararPeloSc(ServicoLegado);
    Exec(ExpandConstant('{sys}\sc.exe'), 'delete "' + ServicoLegado + '"', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  end;

  Codigo := ExecutarServe('--instalar-servico');
  if Codigo <> 0 then
  begin
    SuppressibleMsgBox('Nao foi possivel registrar o servico do Windows (codigo ' + IntToStr(Codigo) + ').' + #13#10 +
      DetalheDoComando() + #13#10 + #13#10 + 'Log: ' + ArqLog, mbError, MB_OK, IDOK);
    Exit;
  end;
  Codigo := ExecutarServe('--iniciar-servico');
  if Codigo <> 0 then
    SuppressibleMsgBox('O servico do Windows foi registrado, mas nao iniciou (codigo ' + IntToStr(Codigo) + ').' + #13#10 +
      DetalheDoComando() + #13#10 + #13#10 +
      'Veja ' + PastaApp() + '\logs\servico.log. Ele tenta iniciar sozinho na proxima vez que o Windows ligar.',
      mbError, MB_OK, IDOK)
  else
    Registrar('Servico ' + ServicoNome + ' em execucao.');
end;

{ ---------------------------------------------------------------------------
  Pagina do PostgreSQL (so instalacao nova)
  --------------------------------------------------------------------------- }

procedure DefinirAmbienteBanco(Banco: String);
begin
  SetEnvironmentVariableW('PGHOST', Trim(EdHost.Text));
  SetEnvironmentVariableW('PGPORT', Trim(EdPorta.Text));
  SetEnvironmentVariableW('PGUSER', Trim(EdUsuario.Text));
  SetEnvironmentVariableW('PGPASSWORD', EdSenha.Text);
  SetEnvironmentVariableW('PGDATABASE', Banco);
  SetEnvironmentVariableW('PGCONNECT_TIMEOUT', '5');
  SetEnvironmentVariableW('PGCLIENTENCODING', 'UTF8');
  SetEnvironmentVariableW('LC_MESSAGES', 'C');
end;

procedure LimparAmbienteBanco();
begin
  RemoveEnvironmentVariableW('PGPASSWORD', 0);
  RemoveEnvironmentVariableW('PGHOST', 0);
  RemoveEnvironmentVariableW('PGPORT', 0);
  RemoveEnvironmentVariableW('PGUSER', 0);
  RemoveEnvironmentVariableW('PGDATABASE', 0);
  RemoveEnvironmentVariableW('PGCONNECT_TIMEOUT', 0);
  RemoveEnvironmentVariableW('PGCLIENTENCODING', 0);
  RemoveEnvironmentVariableW('LC_MESSAGES', 0);
end;

{ Conecta com o psql (dados so no ambiente). Devolve a 1a linha de erro em Saida. }
function TentarConexao(Banco: String; var Saida: String): Integer;
var
  Psql, Arq: String;
  Linhas: TArrayOfString;
begin
  Saida := '';
  Psql := PastaBinPostgres() + '\psql.exe';
  if not FileExists(Psql) then
  begin
    Result := 30;
    Exit;
  end;
  Arq := ExpandConstant('{tmp}\teste-conexao.txt');
  DeleteFile(Arq);
  DefinirAmbienteBanco(Banco);
  try
    if not Exec(ExpandConstant('{cmd}'), '/c ""' + Psql + '" -w -X -q -t -c "select 1" > "' + Arq + '" 2>&1"',
                '', SW_HIDE, ewWaitUntilTerminated, Result) then
      Result := 99;
  finally
    LimparAmbienteBanco();
  end;
  if LoadStringsFromFile(Arq, Linhas) and (GetArrayLength(Linhas) > 0) then
    Saida := Trim(Linhas[0]);
  DeleteFile(Arq);
end;

function Contem(Texto: String; Trechos: array of String): Boolean;
var
  I: Integer;
begin
  Result := False;
  Texto := Lowercase(Texto);
  for I := 0 to GetArrayLength(Trechos) - 1 do
    if Pos(Trechos[I], Texto) > 0 then
    begin
      Result := True;
      Exit;
    end;
end;

{ 0 ok | 12 banco nao existe (sera criado) | 10 servidor inacessivel |
  11 acesso recusado | 30 psql nao encontrado | 19 outro }
function TestarBanco(var Detalhe: String): Integer;
var
  Saida, Saida2: String;
begin
  Detalhe := '';
  Result := TentarConexao(Trim(EdBanco.Text), Saida);
  if Result = 0 then Exit;
  if Result = 30 then Exit;
  Detalhe := Saida;
  if Contem(Saida, ['password', 'senha', 'autentica', 'authentication', 'pg_hba', 'role ', 'papel']) then
    Result := 11
  else if Contem(Saida, ['exist']) then
  begin
    { banco ausente? confere as credenciais no banco postgres }
    if TentarConexao('postgres', Saida2) = 0 then
      Result := 12
    else
    begin
      Detalhe := Saida2;
      Result := 11;
    end;
  end
  else if Contem(Saida, ['could not connect', 'connection refused', 'could not translate', 'timeout',
                         'timed out', 'conectar', 'recusad', 'host']) then
    Result := 10
  else
    Result := 19;
end;

function MensagemCodigo(Codigo: Integer): String;
begin
  case Codigo of
    0:  Result := 'Conexao estabelecida com sucesso.';
    12: Result := 'Conexao ok. O banco ainda nao existe: sera criado na instalacao.';
    10: Result := 'Nao foi possivel alcancar o servidor. Verifique o endereco, a porta e o firewall.';
    11: Result := 'O servidor recusou o acesso. Verifique o usuario, a senha e o pg_hba.conf.';
    30: Result := 'Ferramentas do PostgreSQL (psql.exe e pg_dump.exe) nao encontradas neste computador: ' +
                  'nao da para testar aqui, e as migracoes vao falhar. Instale as ferramentas de linha de comando ' +
                  'do PostgreSQL (Command Line Tools) ou adicione a pasta bin ao PATH.';
  else
    Result := 'Nao foi possivel testar a conexao (codigo ' + IntToStr(Codigo) + ').';
  end;
end;

function AddLabel(Page: TWizardPage; const Texto: String; Topo: Integer): TNewStaticText;
begin
  Result := TNewStaticText.Create(Page);
  Result.Parent := Page.Surface;
  Result.Caption := Texto;
  Result.Left := 0;
  Result.Top := ScaleY(Topo + 3);
  Result.AutoSize := True;
end;

function AddEdit(Page: TWizardPage; Topo, Largura: Integer): TNewEdit;
begin
  Result := TNewEdit.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := ScaleX(170);
  Result.Top := ScaleY(Topo);
  Result.Width := ScaleX(Largura);
end;

procedure CampoAlterado(Sender: TObject);
begin
  ConexaoOk := False;
  LblResultado.Caption := '';
end;

function CamposPreenchidos(): Boolean;
begin
  Result := (Trim(EdHost.Text) <> '') and (StrToIntDef(Trim(EdPorta.Text), 0) > 0) and
            (Trim(EdBanco.Text) <> '') and (Trim(EdUsuario.Text) <> '');
  if not Result then
    MsgBox('Preencha servidor, porta, banco e usuario.', mbError, MB_OK);
end;

procedure TestarClick(Sender: TObject);
var
  Codigo: Integer;
  Detalhe: String;
begin
  if not CamposPreenchidos() then Exit;
  BtnTestar.Enabled := False;
  LblResultado.Font.Color := clWindowText;
  LblResultado.Caption := 'Testando a conexao...';
  LblResultado.Update;
  try
    Codigo := TestarBanco(Detalhe);
  finally
    BtnTestar.Enabled := True;
  end;
  Log('Teste de conexao: codigo ' + IntToStr(Codigo) + ' ' + Detalhe);
  ConexaoOk := (Codigo = 0) or (Codigo = 12);
  if ConexaoOk then
    LblResultado.Font.Color := clGreen
  else
    LblResultado.Font.Color := clRed;
  LblResultado.Caption := MensagemCodigo(Codigo);
  if (not ConexaoOk) and (Detalhe <> '') then
    LblResultado.Caption := LblResultado.Caption + #13#10 + Detalhe;
end;

procedure CriarPaginaBanco();
begin
  DbPage := CreateCustomPage(wpSelectDir, 'Configuracao do PostgreSQL',
    'Informe a conexao com o banco do GymFlow e teste antes de continuar.');

  AddLabel(DbPage, 'Servidor (IP ou nome):', 0);
  EdHost := AddEdit(DbPage, 0, 250);
  EdHost.Text := 'localhost';
  AddLabel(DbPage, 'Porta:', 26);
  EdPorta := AddEdit(DbPage, 26, 70);
  EdPorta.Text := '5432';
  AddLabel(DbPage, 'Banco de dados:', 52);
  EdBanco := AddEdit(DbPage, 52, 250);
  EdBanco.Text := 'academia_db';
  AddLabel(DbPage, 'Usuario:', 78);
  EdUsuario := AddEdit(DbPage, 78, 250);
  EdUsuario.Text := 'postgres';
  AddLabel(DbPage, 'Senha:', 104);
  EdSenha := AddEdit(DbPage, 104, 250);
  EdSenha.PasswordChar := '*';

  BtnTestar := TNewButton.Create(DbPage);
  BtnTestar.Parent := DbPage.Surface;
  BtnTestar.Left := 0;
  BtnTestar.Top := ScaleY(136);
  BtnTestar.Width := ScaleX(130);
  BtnTestar.Height := ScaleY(24);
  BtnTestar.Caption := 'Testar conexao';
  BtnTestar.OnClick := @TestarClick;

  LblResultado := TNewStaticText.Create(DbPage);
  LblResultado.Parent := DbPage.Surface;
  LblResultado.Left := ScaleX(140);
  LblResultado.Top := ScaleY(136);
  LblResultado.Width := DbPage.SurfaceWidth - ScaleX(140);
  LblResultado.AutoSize := False;
  LblResultado.WordWrap := True;
  LblResultado.Height := ScaleY(80);
  LblResultado.Caption := '';

  AddLabel(DbPage, 'O banco e criado se ainda nao existir. Os dados vao para o .env da pasta do sistema.', 222);
  LblPostgres := AddLabel(DbPage, '', 240);

  EdHost.OnChange := @CampoAlterado;
  EdPorta.OnChange := @CampoAlterado;
  EdBanco.OnChange := @CampoAlterado;
  EdUsuario.OnChange := @CampoAlterado;
  EdSenha.OnChange := @CampoAlterado;
end;

{ ---------------------------------------------------------------------------
  Ciclo de vida do assistente
  --------------------------------------------------------------------------- }

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = DbPage.ID then
    if PastaBinPostgres() <> '' then
      LblPostgres.Caption := 'PostgreSQL detectado: ' + PastaBinPostgres()
    else
      LblPostgres.Caption := 'PostgreSQL (psql.exe/pg_dump.exe) nao encontrado neste computador.';
end;

procedure InitializeWizard();
begin
  CriarPaginaBanco();

  { Python identificado automaticamente; a pagina so aparece se nenhum valido for encontrado }
  PythonDetectado := LocalizarPython();
  if PythonDetectado <> '' then
    Log('Python detectado: ' + PythonDetectado)
  else
    Log('Nenhum Python 64 bits >= 3.10 para todos os usuarios encontrado.');

  PaginaPython := CreateInputFilePage(DbPage.ID,
    'Python',
    'Python 64 bits nao encontrado',
    'Nao foi encontrado um Python 3.10 ou mais novo, 64 bits, instalado para todos os usuarios ' +
    '(ex.: C:\Program Files\Python314). O servico roda como SYSTEM e usa este Python.');
  PaginaPython.Add('Ou indique um python.exe ja instalado:', 'Python|python.exe', '.exe');
  PaginaPython.Values[0] := PythonDetectado;

  if PythonEmbutido <> '' then
  begin
    ChkInstalarPython := TNewCheckBox.Create(PaginaPython);
    ChkInstalarPython.Parent := PaginaPython.Surface;
    ChkInstalarPython.Left := 0;
    ChkInstalarPython.Top := PaginaPython.Edits[0].Top + PaginaPython.Edits[0].Height + ScaleY(16);
    ChkInstalarPython.Width := PaginaPython.SurfaceWidth;
    ChkInstalarPython.Height := ScaleY(34);
    ChkInstalarPython.Caption := 'Instalar o Python automaticamente (' + PythonEmbutido + ', para todos os ' +
      'usuarios, em Program Files, no PATH) - recomendado';
    ChkInstalarPython.Checked := True;
  end;
end;

{ Instalar o Python embutido? So quando nao ha Python valido nem venv e a opcao esta marcada. }
function UsarPythonEmbutido(): Boolean;
begin
  Result := False;
  if (ChkInstalarPython = nil) or (PythonDetectado <> '') or FileExists(VenvPython()) then Exit;
  Result := ChkInstalarPython.Checked;
end;

{ Python do venv a criar: o detectado ou o indicado na pagina }
function PythonBase(): String;
begin
  Result := Trim(PaginaPython.Values[0]);
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'Python', PythonBase());
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  { Atualizacao: a configuracao e a senha existentes (.env) nao sao tocadas. }
  if PageID = DbPage.ID then
    Result := ModoAtualizar or WizardSilent
  else if PageID = PaginaPython.ID then
    Result := FileExists(VenvPython()) or (PythonDetectado <> '');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    ModoAtualizar := FileExists(PastaApp() + '\.env') and DirExists(PastaApp() + '\app');
    ServicoLegado := DetectarServicoLegado();
    if ModoAtualizar then
      Log('Modo: ATUALIZAR ' + PastaApp())
    else
      Log('Modo: INSTALACAO NOVA em ' + PastaApp());
    if ServicoLegado <> '' then
      Log('Servico antigo para esta pasta: ' + ServicoLegado);
  end
  else if CurPageID = DbPage.ID then
  begin
    if not CamposPreenchidos() then
    begin
      Result := False;
      Exit;
    end;
    if not ConexaoOk then
      if SuppressibleMsgBox('A conexao ainda nao foi testada com sucesso.' + #13#10 + #13#10 +
           'Continuar mesmo assim?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDNO then
        Result := False;
  end
  else if CurPageID = PaginaPython.ID then
  begin
    if UsarPythonEmbutido() then Exit;
    if not PythonValido(PythonBase()) then
    begin
      MsgBox('Este python.exe nao serve: precisa ser Python 3.10 ou mais novo, 64 bits, ' +
        'instalado para todos os usuarios (fora de C:\Users).', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  if ModoAtualizar then
    Result := 'ATUALIZACAO da instalacao existente' + NewLine + NewLine +
      'Antes de copiar: o servico sera parado e sera feito backup da pasta (zip) e do banco (pg_dump) em' +
      NewLine + Space + PastaApp() + '\backups_instalacao' + NewLine +
      'Preservados: .env, fotos, documentos, img, biometria, logs, venv.' + NewLine + NewLine
  else
    Result := 'INSTALACAO NOVA' + NewLine + NewLine +
      'Banco: ' + Trim(EdUsuario.Text) + '@' + Trim(EdHost.Text) + ':' + Trim(EdPorta.Text) + '/' +
      Trim(EdBanco.Text) + NewLine + NewLine;
  if UsarPythonEmbutido() then
    Result := Result + 'Python (venv e servico):' + NewLine + Space + 'sera instalado automaticamente (' +
      PythonEmbutido + ', para todos os usuarios)' + NewLine + NewLine
  else if not FileExists(VenvPython()) then
    Result := Result + 'Python (venv e servico):' + NewLine + Space + PythonBase() + NewLine + NewLine;
  Result := Result + MemoDirInfo + NewLine + NewLine +
    'Servico Windows:' + NewLine + Space + ServicoNome + ' (inicio automatico, reinicio em falha)' + NewLine;
  if ServicoLegado <> '' then
    Result := Result + Space + 'O servico antigo "' + ServicoLegado + '" (NSSM) sera removido e substituido.' + NewLine;
end;

{ ---------------------------------------------------------------------------
  Antes da copia: parar o servico e fazer backup (somente atualizacao)
  --------------------------------------------------------------------------- }

function FazerBackupBanco(Carimbo: String; var Erro: String): Boolean;
var
  Host, Porta, Banco, Usuario, Senha, Bin, Destino: String;
  Codigo: Integer;
begin
  Result := False;
  if not ParseDatabaseUrl(LerEnv('DATABASE_URL'), Host, Porta, Banco, Usuario, Senha) then
  begin
    Erro := 'DATABASE_URL ausente ou em formato inesperado no .env.';
    Exit;
  end;
  Bin := PastaBinPostgres();
  if Bin = '' then
  begin
    Erro := 'pg_dump.exe nao encontrado em ' + ExpandConstant('{commonpf64}') + '\PostgreSQL\<versao>\bin.';
    Exit;
  end;
  Destino := PastaBackup + '\banco_' + Banco + '_' + Carimbo + '.dump';
  { senha so no ambiente do processo (PGPASSWORD) }
  SetEnvironmentVariableW('PGPASSWORD', Senha);
  try
    Codigo := Executar('Backup do banco (pg_dump)...', Bin + '\pg_dump.exe',
      '-h ' + Host + ' -p ' + Porta + ' -U "' + Usuario + '" -w -Fc -f "' + Destino + '" "' + Banco + '"');
  finally
    RemoveEnvironmentVariableW('PGPASSWORD', 0);
  end;
  if (Codigo <> 0) or not FileExists(Destino) then
  begin
    Erro := 'pg_dump falhou (codigo ' + IntToStr(Codigo) + ').';
    Exit;
  end;
  Registrar('   backup do banco: ' + Destino);
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Carimbo, Zip, Erro: String;
  Codigo: Integer;
  Parou: Boolean;
begin
  Result := '';
  ForceDirectories(PastaApp() + '\logs');
  ArqLog := PastaApp() + '\logs\instalacao.log';
  Registrar('===== GymFlow {#AppVersion}: ' + PastaApp());

  { 1. Parar o servico: pelo serve.py instalado; se for versao antiga (sem o comando), pelo sc.exe }
  Parou := False;
  if ServicoExiste() then
  begin
    Parou := True;
    if ExecutarServe('--parar-servico') <> 0 then
      PararPeloSc(ServicoNome);
  end;
  if ServicoLegado <> '' then
  begin
    Registrar('Parando o servico antigo ' + ServicoLegado);
    PararPeloSc(ServicoLegado);
  end;

  if not ModoAtualizar then Exit;

  { 2. Backup da pasta (sem venv, sem os backups anteriores e sem este log) }
  Carimbo := GetDateTimeString('yyyy-mm-dd_hh-nn-ss', '-', '-');
  PastaBackup := PastaApp() + '\backups_instalacao';
  ForceDirectories(PastaBackup);
  Zip := PastaBackup + '\pasta_' + Carimbo + '.zip';
  Codigo := Executar('Backup da pasta do sistema (zip)...', ExpandConstant('{sys}\tar.exe'),
    '-a -c -f "' + Zip + '" --exclude=./venv --exclude=./backups_instalacao --exclude=__pycache__ ' +
    '--exclude=instalacao.log -C "' + PastaApp() + '" .');
  if (Codigo <> 0) or not FileExists(Zip) then
    Result := 'Falha no backup da pasta (tar codigo ' + IntToStr(Codigo) + '). Feche o FingerPoint e tente de novo.'
  { 3. Backup do banco }
  else if not FazerBackupBanco(Carimbo, Erro) then
    Result := 'Falha no backup do banco: ' + Erro;

  if Result <> '' then
  begin
    Result := Result + #13#10 + 'Nada foi alterado. Detalhes em ' + ArqLog;
    Registrar('ABORTADO: ' + Result);
    if Parou then
      if ExecutarServe('--iniciar-servico') <> 0 then
        Exec(ExpandConstant('{sys}\sc.exe'), 'start "' + ServicoNome + '"', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
    if ServicoLegado <> '' then
      Exec(ExpandConstant('{sys}\sc.exe'), 'start "' + ServicoLegado + '"', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  end;
end;

{ ---------------------------------------------------------------------------
  Depois da copia: venv, .env/token, pip, banco, servico, verificacoes.
  Feito em codigo (nao em [Run]) para conferir o codigo de saida de cada passo.
  --------------------------------------------------------------------------- }

function GerarConfiguracao(): Integer;
begin
  if ModoAtualizar then
    Result := Executar('Conferindo o token do FingerPoint...', VenvPython(),
                       '"' + PastaApp() + '\installer\config_inicial.py"')
  else
  begin
    { dados do banco so por variavel de ambiente }
    SetEnvironmentVariableW('GYMFLOW_DB_HOST', Trim(EdHost.Text));
    SetEnvironmentVariableW('GYMFLOW_DB_PORT', Trim(EdPorta.Text));
    SetEnvironmentVariableW('GYMFLOW_DB_NAME', Trim(EdBanco.Text));
    SetEnvironmentVariableW('GYMFLOW_DB_USER', Trim(EdUsuario.Text));
    SetEnvironmentVariableW('GYMFLOW_DB_PASS', EdSenha.Text);
    try
      Result := Executar('Gerando o .env e o token do FingerPoint...', VenvPython(),
                         '"' + PastaApp() + '\installer\config_inicial.py" --novo');
    finally
      RemoveEnvironmentVariableW('GYMFLOW_DB_PASS', 0);
      RemoveEnvironmentVariableW('GYMFLOW_DB_HOST', 0);
      RemoveEnvironmentVariableW('GYMFLOW_DB_PORT', 0);
      RemoveEnvironmentVariableW('GYMFLOW_DB_NAME', 0);
      RemoveEnvironmentVariableW('GYMFLOW_DB_USER', 0);
    end;
  end;
end;

function SiteResponde(Url: String; Tentativas: Integer): Boolean;
var
  Http: Variant;
  I: Integer;
begin
  Result := False;
  for I := 1 to Tentativas do
  begin
    try
      Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
      Http.SetTimeouts(3000, 3000, 5000, 5000);
      Http.Open('GET', Url, False);
      Http.Send('');
      if Http.Status < 500 then
      begin
        Registrar('   health check: HTTP ' + IntToStr(Http.Status));
        Result := True;
        Exit;
      end;
    except
    end;
    Sleep(2000);
  end;
end;

{ Instala o Python embutido em modo silencioso (todos os usuarios, PATH) e o localiza. }
function InstalarPythonEmbutido(): Boolean;
var
  Codigo: Integer;
begin
  ExtractTemporaryFile(PythonEmbutido);
  { /log: o instalador do Python grava o motivo de qualquer falha em logs\python_instalacao*.log }
  Codigo := Executar('Instalando o Python (' + PythonEmbutido + ')... pode levar alguns minutos',
    ExpandConstant('{tmp}\') + PythonEmbutido,
    '/quiet /log "' + PastaApp() + '\logs\python_instalacao.log" InstallAllUsers=1 PrependPath=1 ' +
    'Include_test=0 Include_doc=0 Include_launcher=1 InstallLauncherAllUsers=1');
  { 3010 = instalado, pede reinicio (nao necessario para o venv) }
  Result := (Codigo = 0) or (Codigo = 3010);
  if not Result then
    Registrar('   Python nao instalado. Motivo em ' + PastaApp() + '\logs\python_instalacao*.log ' +
      '(sem internet? use o instalador completo do Python no build).');
  if Result then
  begin
    PythonDetectado := LocalizarPython();
    PaginaPython.Values[0] := PythonDetectado;
    Result := PythonDetectado <> '';
    if Result then
      Registrar('   Python instalado: ' + PythonDetectado)
    else
      Registrar('   Python instalado, mas nao localizado como 64 bits para todos os usuarios. Se ja existir ' +
        'o mesmo Python instalado so para um usuario, remova-o e execute de novo.');
  end;
end;

{ Devolve '' se tudo certo, ou o passo que falhou. }
function PassosAntesDoServico(): String;
var
  Codigo: Integer;
begin
  Result := '';
  if UsarPythonEmbutido() then
    if not InstalarPythonEmbutido() then
    begin
      Result := 'instalar o Python';
      Exit;
    end;

  if not FileExists(VenvPython()) then
    if Executar('Criando o ambiente virtual (venv) com ' + PythonBase() + '...', PythonBase(),
                '-m venv "' + PastaApp() + '\venv"') <> 0 then
    begin
      Result := 'criar o venv';
      Exit;
    end;

  { 2a tentativa com os certificados do Windows (truststore): rede com proxy/antivirus que
    inspeciona HTTPS faz o pip recusar o certificado; o Windows confia nele, o pip nao. }
  if Executar('Instalando dependencias (pip install -r requirements.txt)...', VenvPython(),
              '-m pip install --disable-pip-version-check -r "' + PastaApp() + '\requirements.txt"') <> 0 then
    if Executar('Instalando dependencias (nova tentativa com os certificados do Windows)...', VenvPython(),
                '-m pip install --disable-pip-version-check --use-feature=truststore -r "' +
                PastaApp() + '\requirements.txt"') <> 0 then
    begin
      Result := 'instalar as dependencias (pip). Confira o acesso a internet (pypi.org) no log';
      Exit;
    end;

  { Nova: gera o .env. Sempre: BIOMETRIA_API_TOKEN igual no .env e no biometria\bio_config.json }
  Codigo := GerarConfiguracao();
  if Codigo = 3 then
    SuppressibleMsgBox('Atencao: BIOMETRIA_API_TOKEN do .env e api_token do biometria\bio_config.json sao diferentes.' + #13#10 +
      'Nada foi alterado. Deixe os dois iguais e reinicie o servico e o FingerPoint, ' +
      'senao o FingerPoint recebe 401.', mbInformation, MB_OK, IDOK)
  else if Codigo <> 0 then
  begin
    Result := 'gerar o .env / token do FingerPoint';
    Exit;
  end;

  { psql das migracoes: o mesmo detectado aqui }
  SetEnvironmentVariableW('GYMFLOW_PG_BIN', PastaBinPostgres());
  try
    if Executar('Atualizando o banco (tabelas e migracoes)...', VenvPython(),
                '"' + PastaApp() + '\installer\atualizar_banco.py"') <> 0 then
      Result := 'atualizar o banco (migracoes)';
  finally
    RemoveEnvironmentVariableW('GYMFLOW_PG_BIN', 0);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Falha: String;
begin
  if CurStep = ssPostInstall then
  begin
    Falha := PassosAntesDoServico();
    if Falha <> '' then
    begin
      { Sem dependencias ou com migracao falha, o servico nao sobe com o codigo novo }
      ServicoTentado := True;
      Registrar('FALHA: ' + Falha);
      SuppressibleMsgBox('Os arquivos foram copiados, mas a instalacao falhou ao ' + Falha + '.' + #13#10 +
        'O servico NAO foi iniciado.' + #13#10 + #13#10 +
        'Log: ' + ArqLog + #13#10 +
        'Backup (atualizacao): ' + PastaApp() + '\backups_instalacao', mbCriticalError, MB_OK, IDOK);
      Exit;
    end;

    RegistrarServico();

    WizardForm.StatusLabel.Caption := 'Verificando http://localhost:5000 ...';
    if not SiteResponde('http://localhost:5000/', 30) then
      SuppressibleMsgBox('O site nao respondeu em http://localhost:5000 em 60 s.' + #13#10 +
        'Veja ' + PastaApp() + '\logs\servico.log', mbError, MB_OK, IDOK)
    else if LerEnv('BIOMETRIA_API_TOKEN') = '' then
      SuppressibleMsgBox('Atencao: BIOMETRIA_API_TOKEN esta ausente ou vazio no .env.' + #13#10 +
        'O FingerPoint nao conseguira registrar passagens (POST /acessos/biometria).', mbInformation, MB_OK, IDOK);
    Registrar('===== fim');
  end;

  { Rede de seguranca: se por algum motivo o passo acima nao rodou, tenta de novo ao final }
  if (CurStep = ssDone) and (not ServicoTentado) then
    RegistrarServico();
end;

{ ---------------------------------------------------------------------------
  Desinstalacao: remove o servico; dados so saem com confirmacao
  --------------------------------------------------------------------------- }

function RemoverServicoDesinstalacao(): Boolean;
var
  Codigo: Integer;
  Py, App: String;
begin
  Result := True;
  if not ServicoExiste() then Exit;
  App := ExpandConstant('{app}');
  Py := App + '\venv\Scripts\python.exe';
  if FileExists(Py) and FileExists(App + '\serve.py') then
    if Exec(Py, '"' + App + '\serve.py" --remover-servico', App, SW_HIDE, ewWaitUntilTerminated, Codigo)
       and (Codigo = 0) then Exit;
  { reserva: sc.exe }
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop ' + ServicoNome, '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  Sleep(4000);
  Exec(ExpandConstant('{sys}\sc.exe'), 'delete ' + ServicoNome, '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  Result := not ServicoExiste();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  App: String;
begin
  App := ExpandConstant('{app}');
  if CurUninstallStep = usUninstall then
  begin
    if not RemoverServicoDesinstalacao() then
      SuppressibleMsgBox('Nao foi possivel parar e remover o servico ' + ServicoNome + '. ' +
        'Remova-o depois em Servicos do Windows (sc delete ' + ServicoNome + ').', mbError, MB_OK, IDOK);
  end;

  if CurUninstallStep = usPostUninstall then
  begin
    { Configuracao e arquivos de trabalho (o .env tem a senha do banco) }
    if SuppressibleMsgBox('Excluir tambem a configuracao (.env), os logs, os backups de instalacao e o venv?' + #13#10 + #13#10 +
         'O banco de dados nao e alterado.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
    begin
      DeleteFile(App + '\.env');
      DelTree(App + '\logs', True, True, True);
      DelTree(App + '\backups_instalacao', True, True, True);
      DelTree(App + '\venv', True, True, True);
    end;
    { Fotos e documentos dos alunos: pergunta separada, padrao NAO }
    if DirExists(App + '\app\static\fotos') or DirExists(App + '\app\static\documentos') then
      if SuppressibleMsgBox('Excluir as FOTOS e os DOCUMENTOS dos alunos?' + #13#10 + #13#10 +
           App + '\app\static\fotos' + #13#10 + App + '\app\static\documentos' + #13#10 + #13#10 +
           'Esta acao nao pode ser desfeita. Em caso de duvida, responda Nao.',
           mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      begin
        DelTree(App + '\app\static\fotos', True, True, True);
        DelTree(App + '\app\static\documentos', True, True, True);
      end;
  end;
end;
