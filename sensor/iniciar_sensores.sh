#!/usr/bin/env bash
# Sobe N sensores simulados de uma vez, cada um como processo independente.
#
# Uso: ./iniciar_sensores.sh [N] [modo] [intervalo] [prefixo]
#   N          quantidade de sensores        (padrão 3)
#   modo       normal | pico | alta          (padrão normal)
#   intervalo  segundos entre leituras       (padrão 60; use 5 na demonstração)
#   prefixo    prefixo dos ids               (padrão s -> s1, s2, s3...)
#
# Exemplo da demonstração: ./iniciar_sensores.sh 3 normal 5
# Ctrl+C encerra todos os sensores iniciados por este script.

N=${1:-3}
MODO=${2:-normal}
INTERVALO=${3:-60}
PREFIXO=${4:-s}
PYTHON=${PYTHON:-python3}
DIR="$(cd "$(dirname "$0")" && pwd)"

PIDS=()
trap 'echo; echo "Encerrando sensores..."; kill "${PIDS[@]}" 2>/dev/null; exit 0' INT TERM

for i in $(seq 1 "$N"); do
  "$PYTHON" -u "$DIR/sensor.py" --id "$PREFIXO$i" --modo "$MODO" --intervalo "$INTERVALO" &
  PIDS+=($!)
  sleep 0.5
done

echo "$N sensor(es) em modo $MODO, intervalo ${INTERVALO}s. Ctrl+C para encerrar."
wait
