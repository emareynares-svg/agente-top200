# Agente Top 200

Programa propio que analiza automáticamente las 200 empresas con mayor capitalización del mundo
y publica los resultados en un dashboard web.

| Frecuencia | Qué hace | Cuándo (hora Argentina) |
|---|---|---|
| **Mensual** | Toma las 200 de mayor capitalización, las puntúa por **calidad + valor**, elige las **50** mejores y Claude escribe una tesis para cada una (fortalezas, riesgos, catalizadores, veredicto) | Día 1, 09:00 |
| **Semanal** | Elige **20** de esas 50 (hasta 5 en *seguimiento* + el resto por *rotación*), hace el análisis técnico y Claude cruza tesis y precio: **entrar ahora / de a poco / esperar / evitar** | Domingo, 10:00 |

**Rotación:** cada semana entran primero las empresas que hace más tiempo que no se analizan
(a igualdad, las de mejor score). Las que la semana anterior estaban para "entrar ahora" o en
"zona de compra" se repiten como *seguimiento* (máximo 5). En unas 3 semanas se recorren las 50.

Corre gratis en **GitHub Actions** y el dashboard se publica con **GitHub Pages**.

---

## Instalación (una sola vez, ~20 minutos)

### 1. Cuenta de GitHub
Creá una cuenta gratis en https://github.com/signup si no tenés.

### 2. Crear el repositorio
1. Entrá a https://github.com/new
2. Nombre: `agente-top200` · Visibilidad: **Public** (necesario para Pages gratis; ver "Privacidad" abajo).
3. Tildá **Add a README file** y tocá **Create repository**.

### 3. Subir los archivos
1. Descomprimí el ZIP en tu Mac.
2. En el repo: **Add file → Upload files** y arrastrá **todo el contenido** de la carpeta
   (`agente/`, `docs/`, `tests/`, `config.yaml`, `requirements.txt`, `README.md`). Tocá **Commit changes**.
3. La carpeta `.github` está oculta en el Finder, así que el workflow se crea a mano:
   **Add file → Create new file**, nombre `.github/workflows/agente.yml`, pegá el contenido del archivo
   `agente.yml` del ZIP (está en `.github/workflows/`; en el Finder, `Cmd + Shift + .` muestra los ocultos)
   y tocá **Commit changes**.

### 4. API key de Claude
1. En https://console.anthropic.com creá una API key (**API Keys → Create Key**) y cargá crédito
   (con US$5 alcanza para más de un mes).
2. En el repo: **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `ANTHROPIC_API_KEY`
   - Secret: la key (empieza con `sk-ant-`)

### 5. Permisos y dashboard
1. **Settings → Actions → General → Workflow permissions** → **Read and write permissions** → Save.
2. **Settings → Pages** → Source: **Deploy from a branch** → Branch: `main`, carpeta `/docs` → Save.

### 6. Primera ejecución
**Actions → Agente Top 200 → Run workflow** → modo `todo` → **Run workflow**.
Tarda 15–30 minutos. Cuando termine, el dashboard queda en:

`https://TU-USUARIO.github.io/agente-top200/`

Guardalo en la pantalla de inicio del iPhone (Safari → Compartir → **Agregar a inicio**).

Desde ahí corre solo según el calendario de arriba.

---

## Personalizar
Todo se ajusta en `config.yaml` (se puede editar desde GitHub con el lápiz):
- Cantidades (200 / 50 / 20), pesos de **calidad** y **valor**.
- `solo_operables`: si las 20 semanales deben ser solo las que se pueden comprar desde EE.UU.
- `seguimiento_max`: cuántos lugares se reservan para repetir oportunidades de la semana anterior.
- `excluir` / `incluir_extra`: sacar o agregar tickers a mano (ej. sumar `CAT` aunque no esté en el top 200).
- Modelo de Claude y tu perfil inversor (la IA lo usa como contexto).

Los horarios se cambian en `.github/workflows/agente.yml` (formato cron, en hora UTC = Argentina + 3).

## Cómo se calcula el score mensual
Cada métrica se convierte en percentil (0–100) contra las 200, o dentro del sector cuando
depende mucho de la industria (márgenes, PE, EV/EBITDA). Después se promedian por peso:

- **Calidad (55%)**: ROIC, margen operativo, margen bruto, años con flujo de caja libre positivo,
  deuda neta/EBITDA (menos es mejor), crecimiento de ventas a 3 años.
- **Valor (45%)**: FCF yield, earnings yield (1/PE forward), EV/EBITDA, PEG, libro/precio (solo
  financieras) y upside del precio objetivo de analistas.

Bancos y aseguradoras usan ROE y precio/libro en lugar de ROIC, FCF y deuda/EBITDA.

## Costos
- GitHub Actions y Pages: **gratis** en repositorios públicos.
- Datos: **gratis** (companiesmarketcap.com + Yahoo Finance).
- Claude (Sonnet 5.5): ~US$0,60 por mes por las 50 tesis + ~US$0,05 por semana por el análisis de entrada
  = **~US$1 por mes**. El costo real de cada corrida aparece en el resumen de la ejecución en GitHub.
- Correr a mano en modo `todo` gasta un mensual + un semanal (~US$0,65). Para probar, usá `semanal`.

## Privacidad
Con el repo público, cualquiera que tenga el link puede ver el dashboard y `config.yaml` (incluido
el texto de `perfil_inversor`). No hay datos de tu cartera ni claves: la API key queda en
*Secrets*. Si preferís todo privado, necesitás GitHub Pro para usar Pages con un repo privado.

## Probar en tu computadora (opcional)
```bash
pip install -r requirements.txt
python tests/test_offline.py          # prueba completa con datos simulados, sin Internet
python -m agente todo --demo          # genera datos simulados en docs/data
export ANTHROPIC_API_KEY=sk-ant-...
python -m agente todo                 # corrida real
cd docs && python -m http.server      # ver el dashboard en http://localhost:8000
```

## Límites a tener en cuenta
- Yahoo Finance no es una API oficial: a veces falta algún dato o cambia el formato. El programa
  tolera faltantes (no puntúa a una empresa con menos del 50% de las métricas).
- "Comprable desde EE.UU." es una heurística (ticker sin sufijo de mercado). Confirmá en Hapi antes de operar.
- Es una herramienta de análisis, no asesoramiento financiero. Las decisiones son tuyas.
