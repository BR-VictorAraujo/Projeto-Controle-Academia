@echo off
rem FingerPoint - inicio com console, para diagnostico (gerado pelo instalador do FingerPoint).
rem Uso normal: atalho "FingerPoint" na Area de Trabalho (pythonw, sem console).
title FingerPoint - diagnostico
cd /d "%~dp0"
if not exist "..\venv\Scripts\python.exe" (
  echo Nao encontrei ..\venv\Scripts\python.exe. Instale ou atualize primeiro o GymFlow Web.
  pause
  exit /b 1
)
echo Iniciando o FingerPoint com o Python do venv do GymFlow...
"..\venv\Scripts\python.exe" app.py
pause
