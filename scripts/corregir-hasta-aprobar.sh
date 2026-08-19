#!/usr/bin/env bash
#
# Loop cerrado: verifica, y mientras no apruebe manda a Claude a corregir.
# Tope de intentos y presupuesto acotado, porque corre sin nadie viéndolo.
#
# Uso:
#   ./scripts/corregir-hasta-aprobar.sh          # 4 intentos
#   INTENTOS=2 ./scripts/corregir-hasta-aprobar.sh
#
# Sale 0 si aprueba, 1 si se agotaron los intentos.

set -uo pipefail

INTENTOS="${INTENTOS:-4}"
PRESUPUESTO="${PRESUPUESTO:-2}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SALIDA="${TMPDIR:-/tmp}/verificacion-pluvial.json"

cd "$RAIZ" || exit 1

for (( intento = 1; intento <= INTENTOS; intento++ )); do
  if python3 scripts/verificar.py --json > "$SALIDA"; then
    echo "Aprobado en el intento ${intento}/${INTENTOS}."
    exit 0
  fi

  echo "Intento ${intento}/${INTENTOS}: la verificación no pasó, mandando a corregir."

  claude -p "Lee ${SALIDA}. Corrige únicamente los renglones de nivel ERROR.
             No toques los de nivel AVISO y no refactorices nada que no esté
             señalado. Al terminar corre 'python3 scripts/verificar.py' y
             confirma el resultado." \
    --allowedTools "Read Edit Bash(python3 *)" \
    --permission-mode acceptEdits \
    --max-budget-usd "$PRESUPUESTO" \
    || echo "El intento ${intento} terminó con error; se reintenta."
done

echo "Se agotaron los ${INTENTOS} intentos sin aprobar. Requiere revisión humana."
python3 scripts/verificar.py
exit 1
