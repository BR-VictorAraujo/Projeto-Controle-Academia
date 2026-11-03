<#
.SYNOPSIS
  Gera o instalador do GymFlow (GymFlow-Setup-<versao><sufixo>.exe) com o Inno Setup.

.EXAMPLE
  .\installer\build.ps1
  .\installer\build.ps1 -Origem D:\Projetos\Python\GymFlow -Sufixo -teste -Saida D:\builds

.PARAMETER Origem
  Pasta do codigo que vai para o instalador (padrao: a raiz deste repositorio).
  A versao vem de <Origem>\app\version.py. O instalador usa os scripts de
  <Origem>\installer (atualizar_banco.py, config_inicial.py).
.PARAMETER Sufixo
  Texto apos a versao no nome do arquivo (ex.: -teste). Padrao: nenhum.
.PARAMETER PythonSetup
  Instalador oficial do Python (python-3.x.y-amd64.exe, de python.org) embutido no setup e usado
  so se o servidor nao tiver Python 64 bits >= 3.10. Padrao: o mais novo em installer\python\.
  Use o instalador COMPLETO (~28 MB) para instalar sem internet. Sem ele, o setup so avisa.
.PARAMETER Saida
  Pasta do .exe gerado (padrao: installer\saida).
.PARAMETER Iscc
  Caminho do ISCC.exe (padrao: procura nas pastas de instalacao do Inno Setup 6).
#>
param(
    [string]$Origem = (Join-Path $PSScriptRoot '..'),
    [string]$Sufixo = '',
    [string]$PythonSetup = '',
    [string]$Saida  = (Join-Path $PSScriptRoot 'saida'),
    [string]$Iscc   = ''
)
$ErrorActionPreference = 'Stop'

$Origem = (Resolve-Path $Origem).Path
$arqVersao = Join-Path $Origem 'app\version.py'
if (-not (Test-Path $arqVersao)) { throw "Nao encontrei $arqVersao. -Origem aponta para a pasta do GymFlow?" }
$m = Select-String -Path $arqVersao -Pattern '^APP_VERSAO\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $m) { throw "APP_VERSAO nao encontrado em $arqVersao" }
$versao = $m.Matches[0].Groups[1].Value

foreach ($f in 'serve.py', 'requirements.txt', 'installer\atualizar_banco.py', 'installer\config_inicial.py') {
    if (-not (Test-Path (Join-Path $Origem $f))) { throw "Falta $f em $Origem." }
}

if (-not $PythonSetup) {
    $PythonSetup = Get-ChildItem (Join-Path $PSScriptRoot 'python') -Filter 'python-3*-amd64.exe' -ErrorAction SilentlyContinue |
        Sort-Object { [version]($_.Name -replace '^python-([0-9.]+)-amd64\.exe$', '$1') } -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
$defPython = @()
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

Write-Host "GymFlow $versao$Sufixo"
Write-Host "  origem: $Origem"
Write-Host "  python: $(if ($PythonSetup) { $PythonSetup } else { '(nao embutido)' })"
Write-Host "  saida : $Saida"

& $Iscc /Q "/DSrcDir=$Origem" "/DSufixo=$Sufixo" @defPython "/O$Saida" (Join-Path $PSScriptRoot 'gymflow.iss')
if ($LASTEXITCODE -ne 0) { throw "ISCC falhou (codigo $LASTEXITCODE)." }

$exe = Join-Path $Saida "GymFlow-Setup-$versao$Sufixo.exe"
Write-Host "Gerado: $exe"
