# Agentic loops con Claude Code

Guía escrita para el trabajo del Departamento de Planeación Hídrica: el reporte
pluvial de este repositorio, el informe mensual en LaTeX, la auditoría previa a
que un documento salga del área y los paquetes de Avesta/Neptune.

---

## 1. Lo primero: un loop sin verificador es un temporizador

Repetir un prompt cada media hora no es un agentic loop. Un agentic loop tiene
tres partes, y la que casi siempre falta es la tercera:

1. **Un disparador** que decide cuándo corre.
2. **Una tarea** con instrucciones estables.
3. **Un verificador** que responde sí o no, y con eso el loop sabe si ya
   terminó o si debe volver a intentar.

Sin la tercera pieza el loop no converge: se queda dando vueltas o se detiene
en el primer intento sin que nadie sepa si el resultado sirve. El verificador
tiene que ser un comando que sale con código 0 o 1, no una opinión.

En este repositorio ese comando es:

```bash
python3 scripts/verificar.py        # 0 = aprobado, 1 = hay errores
```

Y ya tienes otro para Avesta, dentro de tu propia skill:

```bash
python3 ~/.claude/skills/synced/avesta-audit/scripts/audit_zip.py <paquete.zip>
```

También sale con 1 cuando encuentra un BLOCKER. Cualquier cosa que sepas
comprobar con un comando sirve: `latexmk` que compile, un `grep` que no
encuentre nada, un script que valide un CSV.

**Antes de automatizar una actividad, escribe su verificador.** Es el 80 % del
trabajo y lo que decide si el loop te sirve o te da lata.

---

## 2. Los cinco mecanismos, y cuál te toca en cada caso

| Mecanismo | Dónde vive | ¿Sobrevive si cierras Claude? | Para qué sirve |
| --- | --- | --- | --- |
| `/loop` | La sesión abierta | No | Vigilar algo en curso mientras trabajas |
| `/cron` | La sesión abierta, en memoria | No, y expira a los 7 días | Recordatorios dentro de una sesión larga |
| Routines | Servidor de Anthropic | Sí | Trabajo desatendido de verdad |
| `claude -p` | Tu máquina o un runner | Sí | El loop de producción, acotado |
| Hooks | Archivo de configuración | Sí | Los rieles que siempre se ejecutan |

### 2.1 `/loop` — el más fácil, y el que menos compromiso pide

```
/loop 15m corre python3 scripts/verificar.py; si falla, arregla los ERROR y vuelve a correrlo
```

Sin intervalo, Claude decide él mismo cada cuándo despertar según lo que esté
esperando:

```
/loop vigila el despliegue en Streamlit Cloud y avísame en cuanto quede arriba
```

Sirve mientras tú estás ahí. Se acaba cuando cierras la sesión. Es el punto de
partida correcto para probar si una actividad tuya se deja automatizar, antes
de montarle infraestructura.

### 2.2 Routines — para lo que debe ocurrir aunque no abras la computadora

Se crean desde Claude Code en la web. Cada disparo puede abrir una **sesión
nueva** —que empieza sin memoria de las anteriores— o entrar a una sesión que
ya existe. Tres cosas que conviene tener claras de entrada:

- El cron se evalúa **en UTC**, no en horario de León. Guanajuato dejó el
  horario de verano en 2022, así que es UTC−6 todo el año: las 7:00 de la
  mañana en León son `5 13 * * *` en UTC, siempre. (El minuto 5 en lugar del 0
  es a propósito: todo el mundo programa en punto.)
- El intervalo mínimo es de una hora.
- En sesión nueva, el prompt tiene que ser **autosuficiente**: no puede
  suponer nada de lo que platicaste ayer. Ahí es donde `CLAUDE.md` y tus skills
  hacen el trabajo pesado.

Este es el mecanismo para el reporte pluvial diario en temporada de lluvias.

### 2.3 `claude -p` — el loop de producción

Modo no interactivo. Se mete en el cron de tu equipo, en un `for` de bash o en
GitHub Actions. Es donde el loop se cierra de verdad, y este repositorio ya
trae uno armado en `scripts/corregir-hasta-aprobar.sh`. Su parte medular:

```bash
for intento in 1 2 3 4; do
  if python3 scripts/verificar.py --json > /tmp/verificacion.json; then
    echo "Aprobado en el intento $intento."
    exit 0
  fi

  echo "Intento $intento: hay errores, mandando a corregir."
  claude -p "Lee /tmp/verificacion.json. Corrige únicamente los renglones de
             nivel ERROR. No toques los AVISO ni refactorices nada más.
             Al terminar corre python3 scripts/verificar.py y confirma." \
    --allowedTools "Bash(python3 *) Read Edit" \
    --permission-mode acceptEdits \
    --max-budget-usd 2
done

echo "Cuatro intentos sin aprobar. Requiere revisión humana."
exit 1
```

Las tres banderas que hacen que esto sea seguro dejarlo solo:

- `--allowedTools` limita lo que puede tocar. Sin esto, un loop desatendido
  tiene permiso de hacer cualquier cosa.
- `--max-budget-usd` le pone techo al gasto por intento.
- El `for` con tope pone techo a los intentos. **Nunca escribas un `while true`
  alrededor de un modelo.**

Añade `--output-format json` cuando necesites leer el resultado desde otro
script, y `--json-schema` cuando quieras que la respuesta venga con estructura
fija.

### 2.4 Hooks — para lo que no debe depender del criterio del modelo

Un hook es un comando que el arnés ejecuta siempre, en un momento fijo del
ciclo: al abrir sesión (`SessionStart`), al enviar un prompt
(`UserPromptSubmit`), después de una herramienta (`PostToolUse`), al terminar
(`Stop`). No es Claude quien decide correrlo.

El caso que te aplica: un hook `Stop` que corra `scripts/verificar.py` y no
deje cerrar el turno si sale 1. Así el verificador deja de depender de que
alguien se acuerde de correrlo.

Para escribirlo, en una sesión pide `/update-config` y describe el
comportamiento; esa skill edita `settings.json` por ti.

### 2.5 Skills — la tarea, empacada

Ya tienes esta parte resuelta mejor que la mayoría: `reporte-mensual-ph`,
`higiene-datos-auditoria`, `avesta-workflow`, `avesta-audit`, `xlsx-data-first`,
`visual-slides`. Una skill es el "qué hacer" del loop, escrito una vez y
estable. El disparador solo tiene que invocarla.

Esa es la razón por la que tus loops pueden ser prompts de dos renglones: el
detalle ya vive en la skill.

---

## 3. Tus actividades, una por una

### Caso A · Vigilancia pluvial diaria en temporada

**Verificador:** `python3 scripts/verificar.py --red` (comprueba que SAPAL y
CONAGUA respondan).

**Mecanismo:** Routine con sesión nueva, de junio a octubre.

**Prompt de la Routine** (autosuficiente, porque arranca en frío):

> Corre `python3 scripts/verificar.py --red`. Si algún endpoint no responde,
> repórtalo y detente. Si responden, revisa con `get_latest_conagua_date` si
> hay corte nuevo desde ayer. Si no lo hay, no hagas nada y no me escribas. Si
> lo hay, dime qué estaciones reportaron lluvia y cuál fue el acumulado máximo.

Nota el "si no lo hay, no me escribas". Un loop diario que te manda un mensaje
aunque no haya pasado nada se vuelve ruido en dos semanas y lo terminas
ignorando justo el día que sí importaba.

**Cómo empezar:** pruébalo primero con `/loop 2h <ese mismo prompt>` en una
sesión abierta. Si el resultado te sirve tres días seguidos, conviértelo en
Routine.

### Caso B · Reporte mensual de Planeación Hídrica

Aquí hay **dos loops distintos**, y mezclarlos sería un error.

**Loop 1 — compilación, sí se automatiza.** Corregir un error de LaTeX no tiene
juicio de por medio.

```bash
for intento in 1 2 3; do
  latexmk -xelatex REPORTE_MENSUAL_2026-08.tex > /tmp/latex.log 2>&1 && break
  claude -p "Lee /tmp/latex.log y corrige el error de compilación en
             REPORTE_MENSUAL_2026-08.tex. No cambies el contenido de las
             secciones, solo lo que impide compilar. Respeta la skill
             reporte-mensual-ph: la plantilla no se toca." \
    --allowedTools "Read Edit Bash(latexmk *)" --permission-mode acceptEdits
done
```

**Loop 2 — auditoría, no se automatiza la corrección.** Tu propia skill
`higiene-datos-auditoria` dice, textual: *"No arregles nada. Señala y
devuelve."* Y con razón: SAPAL es sujeto obligado y la responsabilidad por
revelar información reservada es personal. Un loop que corrige hallazgos en
silencio te quita justo la señal que necesitas.

El loop correcto aquí es de **repetición hasta que el autor limpie**, no de
autocorrección:

> Corre `/higiene-datos-auditoria` sobre el reporte. Devuélveme los hallazgos
> con su ubicación exacta. No corrijas nada, salvo ortografía.

Y esa es la regla general: **automatiza el bucle donde el criterio no aporta;
deja al humano donde la firma es suya.**

### Caso C · Paquetes de Avesta / Neptune

Este es el caso donde más ganas, porque el verificador ya existe y ya sale con
código 1 cuando hay BLOCKER.

```bash
PAQUETE=tarea.zip
for intento in 1 2 3; do
  python3 ~/.claude/skills/synced/avesta-audit/scripts/audit_zip.py "$PAQUETE" > /tmp/audit.txt && break
  claude -p "Lee /tmp/audit.txt. Corrige los BLOCKER siguiendo la skill
             avesta-workflow, vuelve a armar $PAQUETE y vuelve a auditar." \
    --allowedTools "Read Edit Write Bash(python3 *) Bash(zip *)" \
    --permission-mode acceptEdits --max-budget-usd 3
done
```

Ojo con lo que el propio script advierte de sí mismo: *"Catches the
deterministic failure classes only. Coherence, traceability, self-contradiction
and trap design still need a human read."* El loop te quita el trabajo
mecánico. La lectura completa antes de entregar sigue siendo tuya.

### Caso D · Mantenimiento de este repositorio

`RAINFALL_MAPS_STREAMLIT.py` tiene 1626 líneas en un solo archivo, con
importaciones repetidas y bloques de comentarios duplicados. Es el candidato
natural para un loop de refactor, **pero solo porque ahora existe el
verificador**: sin él, no hay forma de que un loop sepa que no rompió el mapa.

Empieza en chico y con tope:

```
/loop quita una duplicación de RAINFALL_MAPS_STREAMLIT.py, corre
      python3 scripts/verificar.py, y si aprueba haz commit. Una por vuelta.
```

Una mejora por vuelta, verificada, con commit propio. Si algo sale mal, el
`git revert` es de una línea.

---

## 4. Reglas para dejar un loop solo

1. **Techo de intentos siempre.** Un `for 1 2 3`, nunca un `while true`.
2. **`--max-budget-usd` en todo `claude -p` desatendido.**
3. **`--allowedTools` acotado.** Un loop de compilación no necesita permiso de
   red; uno de lectura de datos no necesita permiso de escritura.
4. **Rama propia.** Un loop que empuja a `main` te deja sin punto de regreso.
5. **Silencio cuando no hay novedad.** Instrúyelo explícitamente.
6. **Nada de credenciales ni datos reservados en el prompt.** Este repositorio
   es público y las Routines se guardan del lado del servidor.
7. **Revisa la bitácora una vez por semana.** Un loop que lleva veinte días
   fallando igual y no te avisó es peor que no tener loop.

---

## 5. Por dónde empezar esta semana

1. **Hoy:** corre `python3 scripts/verificar.py` y arregla los dos avisos que
   reporta. Así compruebas que el verificador dice la verdad.
2. **Esta semana:** un `/loop` en sesión abierta para la vigilancia pluvial.
   Sin infraestructura, sin compromiso. Si a los tres días te sirvió, sigue.
3. **Cuando ya te haya servido:** conviértelo en Routine y escribe el
   verificador de tu siguiente actividad. Siempre en ese orden: verificador
   primero, loop después.
