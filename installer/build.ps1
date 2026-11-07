<#
.SYNOPSIS
  Gera o instalador do GymFlow Web (GymFlow-Setup-<versao><sufixo>.exe) ou do FingerPoint
  (FingerPoint-Setup-<versao><sufixo>.exe) com o Inno Setup.

.EXAMPLE
  .\installer\build.ps1
  .\installer\build.ps1 -Origem D:\Projetos\Python\GymFlow -Sufixo -teste -Saida D:\builds
  .\installer\build.ps1 -Produto fingerpoint

.PARAMETER Produto
  web (padrao): GymFlow Web, installer\gymflow.iss, versao de <Origem>\app\version.py.
  fingerpoint : FingerPoint, installer\fingerpoint.iss, versao de <Origem>\biometria\app.py.
.PARAMETER Origem
  Pasta do codigo que vai para o instalador (padrao: a raiz deste repositorio).
  O instalador web usa os scripts de <Origem>\installer (atualizar_banco.py, config_inicial.py);
  o do FingerPoint usa <Origem>\biometria e <Origem>\installer\iniciar_biometria.bat.
.PARAMETER Sufixo
  Texto apos a versao no nome do arquivo (ex.: -teste). Padrao: nenhum.
.PARAMETER PythonSetup
  (so web) Instalador oficial do Python (python-3.x.y-amd64.exe, de python.org) embutido no setup e usado
  so se o servidor nao tiver Python 64 bits >= 3.10. Padrao: o mais novo em installer\python\.
  Use o instalador COMPLETO (~28 MB) para instalar sem internet. Sem ele, o setup so avisa.
.PARAMETER Saida
  Pasta do .exe gerado (padrao: installer\saida).
.PARAMETER Iscc
  Caminho do ISCC.exe (padrao: procura nas pastas de instalacao do Inno Setup 6).
#>
param(
    [ValidateSet('web', 'fingerpoint')]
    [string]$Produto = 'web',
    [string]$Origem = (Join-Path $PSScriptRoot '..'),
    [string]$Sufixo = '',
    [string]$PythonSetup = '',
    [string]$Saida  = (Join-Path $PSScriptRoot 'saida'),
    [string]$Iscc   = ''
)
$ErrorActionPreference = 'Stop'

$Origem = (Resolve-Path $Origem).Path

if ($Produto -eq 'fingerpoint') {
    $nome      = 'FingerPoint'
    $script    = 'fingerpoint.iss'
    $arqVersao = Join-Path $Origem 'biometria\app.py'
    $obrigatorios = 'biometria\app.py', 'biometria\bio_config.example.json', 'biometria\CHANGELOG.md',
                    'installer\iniciar_biometria.bat'
    $padrao    = '^APP_VERSAO\s*=\s*"([^"]+)"'
} else {
    $nome      = 'GymFlow'
    $script    = 'gymflow.iss'
    $arqVersao = Join-Path $Origem 'app\version.py'
    $obrigatorios = 'serve.py', 'requirements.txt', 'installer\atualizar_banco.py', 'installer\config_inicial.py'
    $padrao    = '^APP_VERSAO\s*=\s*"([^"]+)"'
}

if (-not (Test-Path $arqVersao)) { throw "Nao encontrei $arqVersao. -Origem aponta para a pasta do GymFlow?" }
$m = Select-String -Path $arqVersao -Pattern $padrao | Select-Object -First 1
if (-not $m) { throw "APP_VERSAO nao encontrado em $arqVersao" }
$versao = $m.Matches[0].Groups[1].Value

foreach ($f in $obrigatorios) {
    if (-not (Test-Path (Join-Path $Origem $f))) { throw "Falta $f em $Origem." }
}

$defPython = @()
if ($Produto -eq 'web') {
    if (-not $PythonSetup) {
        $PythonSetup = Get-ChildItem (Join-Path $PSScriptRoot 'python') -Filter 'python-3*-amd64.exe' -ErrorAction SilentlyContinue |
            Sort-Object { [version]($_.Name -replace '^python-([0-9.]+)-amd64\.exe$', '$1') } -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if ($PythonSetup) {
        $PythonSetup = (Resolve-Path $PythonSetup).Path
        $assinatura = Get-AuthenticodeSignature -LiteralPath $PythonSetup
        if ($assinatura.Status -ne 'Valid' -or $assinatura.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
            throw "Assinatura invalida em $PythonSetup (esperado: Python Software Foundation)."
        }
        $defPython = @("/DPythonSetup=$PythonSetup")
    } else {
        Write-Warning 'Sem instalador do Python em installer\python\: o setup nao conseguira instalar o Python sozinho.'
    }
} elseif ($PythonSetup) {
    Write-Warning '-PythonSetup e ignorado no FingerPoint: ele usa o venv do GymFlow Web.'
}

if (-not $Iscc) {
    $Iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) { throw 'ISCC.exe nao encontrado. Instale o Inno Setup 6 ou use -Iscc.' }
}

# Aviso (nao bloqueia): o instalador leva os arquivos da pasta como estao, commitados ou nao
if (Test-Path (Join-Path $Origem '.git')) {
    $pendentes = git -C $Origem status --porcelain 2>$null
    if ($pendentes) {
        Write-Warning "Ha alteracoes sem commit em $Origem. O instalador vai incluir os arquivos como estao no disco."
    }
}

New-Item -ItemType Directory -Force -Path $Saida | Out-Null
$Saida = (Resolve-Path $Saida).Path

Write-Host "$nome $versao$Sufixo"
Write-Host "  origem: $Origem"
if ($Produto -eq 'web') {
    Write-Host "  python: $(if ($PythonSetup) { $PythonSetup } else { '(nao embutido)' })"
}
Write-Host "  saida : $Saida"

if ($Produto -eq 'fingerpoint') {
    # Sem /Q: a lista curta de arquivos (linhas "Compressing:") serve para conferir o pacote
    & $Iscc "/DSrcDir=$Origem" "/DAppVersion=$versao" "/DSufixo=$Sufixo" "/O$Saida" (Join-Path $PSScriptRoot $script)
} else {
    & $Iscc /Q "/DSrcDir=$Origem" "/DSufixo=$Sufixo" @defPython "/O$Saida" (Join-Path $PSScriptRoot $script)
}
if ($LASTEXITCODE -ne 0) { throw "ISCC falhou (codigo $LASTEXITCODE)." }

$exe = Join-Path $Saida "$nome-Setup-$versao$Sufixo.exe"
Write-Host "Gerado: $exe"
