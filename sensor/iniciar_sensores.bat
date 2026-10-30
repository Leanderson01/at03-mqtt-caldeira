@echo off
REM Sobe N sensores simulados de uma vez, cada um em uma janela propria.
REM
REM Uso: iniciar_sensores.bat [N] [modo] [intervalo] [prefixo]
REM   N          quantidade de sensores        (padrao 3)
REM   modo       normal ^| pico ^| alta          (padrao normal)
REM   intervalo  segundos entre leituras       (padrao 60; use 5 na demonstracao)
REM   prefixo    prefixo dos ids               (padrao s -^> s1, s2, s3...)
REM
REM Exemplo da demonstracao: iniciar_sensores.bat 3 normal 5
REM Para encerrar um sensor, feche a janela dele (ou Ctrl+C nela).

setlocal
set N=%1
if "%N%"=="" set N=3
set MODO=%2
if "%MODO%"=="" set MODO=normal
set INTERVALO=%3
if "%INTERVALO%"=="" set INTERVALO=60
set PREFIXO=%4
if "%PREFIXO%"=="" set PREFIXO=s

for /L %%i in (1,1,%N%) do (
  start "sensor %PREFIXO%%%i" python "%~dp0sensor.py" --id %PREFIXO%%%i --modo %MODO% --intervalo %INTERVALO%
)

echo %N% sensor(es) em modo %MODO%, intervalo %INTERVALO%s.
endlocal
