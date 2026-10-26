# Flow Execution Debugger – Guía de uso

Herramienta de escritorio para localizar rápidamente ejecuciones de flujos de Power Automate, filtrarlas por los parámetros con los que se dispararon, saltar a la iteración exacta de un *Apply to each* y seguir las llamadas a flujos hijos.

Se distribuye como **código fuente Python**: puedes revisar todo lo que hace antes de ejecutarlo. No necesitas XrmToolBox.

**Autor:** [samcall22](https://github.com/samcall22) · **Licencia:** [GPL-3.0-or-later](LICENSE)

---

## 1. Requisitos

- Windows 10 u 11.
- **Python 3.10 o superior** (ver abajo cómo instalarlo).
- Tu cuenta corporativa de Microsoft (la misma con la que entras en Power Automate). Se admite MFA.
- Permiso para ver el historial de ejecuciones de los flujos que quieras depurar.

---

## 2. Instalación

### 2.1 Instalar Python (solo una vez)
Elige una opción:
- **Web oficial:** descarga el instalador de <https://www.python.org/downloads/> y, en la primera pantalla, marca **"Add python.exe to PATH"** antes de pulsar *Install Now*.
- **Terminal:** abre PowerShell y ejecuta `winget install Python.Python.3.12`.
- Si tu empresa usa un portal de software (Centro de software, Portal de empresa…), instálalo desde ahí.

Para comprobarlo, abre una terminal **nueva** y escribe `py --version` o `python --version`.

### 2.2 Instalar la herramienta
1. Descomprime el ZIP en una carpeta tuya, por ejemplo `Documentos\FlowExecutionDebugger`.
2. Haz doble clic en **`iniciar.bat`**.
3. La **primera vez** se abrirá una ventana negra que prepara un entorno de Python aislado dentro de la carpeta (`.venv`) y descarga las librerías necesarias (`msal`, `msal-extensions`, `requests`, `truststore`). Tarda uno o dos minutos. Las siguientes veces la aplicación se abre directamente.

> Si Windows avisa al abrir `iniciar.bat` porque el archivo viene de Internet o de Teams: clic derecho en el ZIP → **Propiedades** → marca **Desbloquear** → Aceptar, y vuelve a descomprimirlo.

No se instala nada fuera de la carpeta salvo Python. Puedes abrir `iniciar.bat` con el Bloc de notas para ver exactamente qué hace.

### 2.3 Actualizar
Sustituye los archivos por los de la versión nueva (puedes conservar la carpeta `.venv`). Si cambian las librerías, `iniciar.bat` las actualiza solo. Tu configuración y tu sesión se conservan.

---

## 3. Primer inicio de sesión

1. Deja **Tenant (optional)** vacío. Rellénalo solo si eres invitado en otro tenant (pon su dominio, p. ej. `contoso.onmicrosoft.com`, o su id).
2. Pulsa **Sign in**. Se abrirá tu navegador con el inicio de sesión de Microsoft. Completa el MFA.
3. Cuando el navegador diga *"Authentication complete"*, vuelve a la aplicación.
4. En **Environment** elige el entorno. Se cargarán automáticamente las soluciones.

La sesión se recuerda: las siguientes veces no tendrás que volver a introducir la contraseña mientras el token siga siendo válido. Para cerrar la sesión pulsa **Sign out**.

---

## 4. Uso paso a paso

### 4.1 Elegir el flujo
1. Pulsa **Load flows**.
2. Filtra por **Name**, **Solution** o **Status** y selecciona el flujo en la lista de la izquierda.

> Si eres administrador del entorno verás todos los flujos. Si no, solo los tuyos y los compartidos contigo, y aparecerá el aviso **⚠ PARTIAL LIST**.

### 4.2 Buscar ejecuciones
1. Ajusta **From** y **Until** (fecha **y hora**, formato `dd/mm/aaaa hh:mm`).
   - Botón **▾**: abre un calendario para elegir el día.
   - Teclas **↑ / ↓**: cambian la parte donde está el cursor (día, mes, año, hora o minuto).
2. Opcional: **Run status** (Succeeded, Failed…) y **Max. runs**.
3. Pulsa **Load runs** o **Last hour** (última hora directamente).

> El rango se aplica en el servidor: si lo cambias, vuelve a pulsar **Load runs**. Usa ventanas cortas (minutos u horas). Si aparece **⚠ LIMIT REACHED**, el resultado está incompleto: acota el rango.

### 4.3 Filtrar por los parámetros del desencadenador (opcional)
1. En **Filters by body parameter** aparece una casilla por cada parámetro, con su nombre amigable y su nombre técnico entre paréntesis (pasa el ratón por encima para ver la descripción).
2. Escribe uno o varios valores (búsqueda parcial, sin distinguir mayúsculas).
3. Elige **And (all)** o **Or (any)** y pulsa **Filter by parameters** (o Enter).

> Este filtro analiza cada ejecución una a una: puede tardar. Verás el progreso y el tiempo estimado abajo, y puedes pulsar **Cancel** en cualquier momento.

### 4.4 Acciones sobre una ejecución
Selecciona una fila y usa la barra de botones, el **clic derecho** o el **doble clic** (= Open):

| Botón | Qué hace |
|---|---|
| **Open** | Abre la ejecución en el navegador y perfil elegidos arriba (**Browser** / **Profile**). |
| **Parameters** | Muestra los valores con los que se disparó. Doble clic o Ctrl+C copia el valor. |
| **Child flows** | Lista las llamadas a otros flujos, en qué iteración se hicieron y el enlace directo a la ejecución hija. |
| **Copy link** | Copia la URL de la ejecución al portapapeles. |
| **Download JSON** | Descarga la ejecución completa en el modo elegido en **JSON mode**. |

### 4.5 Modos de JSON

| Modo | Contenido |
|---|---|
| **Full** | Todo, tal como lo devuelve la API. |
| **Clean (loops only)** | Lista plana de bucles con sus iteraciones, estados y salidas. |
| **Tree (nested loops)** ⭐ | Jerarquía real: cada iteración lleva su `item`, sus `actions` y, dentro, los bucles anidados con sus propias iteraciones. |
| **Raw (run data only)** | Solo los datos de la ejecución, sin transformar. Útil para pasárselo a una IA u otras herramientas. |

Cada iteración incluye **`portalIndex`** (el mismo número que muestra el portal de Power Automate) y, si está anidada, **`path`**, por ejemplo `Apply_to_each[2] > Apply_to_each_2[3]`.
Para agilizar tu trabajo, puedes pasar cualquiera de estos JSON a una IA. Esta te ayudará a identificar errores o a localizar la iteración exacta del bucle que necesitas.
---

## 5. Casos típicos

**"Ha fallado la iteración X y no sé por qué"**
1. Carga las ejecuciones del momento en que ocurrió.
2. **Download JSON** en modo **Tree**.
3. Busca en el fichero el valor, id de registro o mensaje de error.
4. Lee `portalIndex` (y `path` si es anidado).
5. **Open** y ve directamente a esa iteración en el portal.

**"El error está en un flujo hijo"**
1. **Child flows** sobre la ejecución padre.
2. Localiza la llamada (la columna *Iteration* indica en qué vuelta del bucle se hizo).
3. **Open** o **Copy link** para ir a la ejecución hija.

---

## 6. Problemas frecuentes

| Síntoma | Solución |
|---|---|
| `iniciar.bat` dice que no encuentra Python | Instala Python (apartado 2.1) marcando **Add python.exe to PATH** y abre `iniciar.bat` de nuevo. |
| Error al **instalar dependencias** | Normalmente es el proxy corporativo. Abre una terminal en la carpeta y ejecuta `set HTTPS_PROXY=http://tu-proxy:puerto` y después `iniciar.bat`. Pide la dirección del proxy a IT. |
| Error `CERTIFICATE_VERIFY_FAILED` (Zscaler u otro proxy con inspección SSL) | La herramienta ya usa los certificados de Windows, así que no debería pasar. Si ocurre, comprueba que tienes la última versión y Python 3.10 o superior, y borra la carpeta `.venv` para que se reinstalen las librerías. Si persiste, pide a IT el certificado raíz del proxy en formato `.pem` y ejecuta `set REQUESTS_CA_BUNDLE=C:\ruta\certificado.pem` antes de `iniciar.bat`. |
| Hago doble clic y no se abre nada | Abre una terminal en la carpeta y ejecuta `iniciar.bat debug` para ver el error. |
| Algo se quedó a medias en la primera instalación | Borra la carpeta `.venv` y vuelve a ejecutar `iniciar.bat`. |
| El navegador no se abre al pulsar **Sign in** | Comprueba que tienes un navegador predeterminado configurado y vuelve a intentarlo. |
| No aparece el entorno | Revisa el **Tenant**: si el entorno está en otro tenant, escríbelo y vuelve a pulsar **Sign in**. |
| ⚠ PARTIAL LIST | No eres administrador del entorno: solo ves tus flujos y los compartidos. |
| El filtro **Solution** está vacío | No se pudo leer la información de soluciones de Dataverse (permisos). El resto funciona igual. |
| Una llamada a flujo hijo no tiene enlace | El hijo se invocó por HTTP en lugar de con *Run a child flow*; la API no expone el id de la ejecución hija. |
| "Busy" al pulsar un botón | Hay una operación en curso: espera o pulsa **Cancel**. |

---

## 7. Datos y privacidad

- La configuración y la sesión se guardan en `%APPDATA%\FlowExecutionDebugger`. La caché de sesión está **cifrada** con tu usuario de Windows.
- Los JSON descargados pueden contener **datos sensibles** de las ejecuciones (datos de clientes, correos, etc.). Guárdalos en un sitio seguro y no los compartas por canales públicos.
- Para borrar todo: **Sign out**, cierra la aplicación y elimina la carpeta del programa (incluida `.venv`) y `%APPDATA%\FlowExecutionDebugger`.

---

## 8. Autoría, licencia y exención de responsabilidad

**Copyright © 2026 [samcall22](https://github.com/samcall22).** Todos los derechos no concedidos expresamente por la licencia quedan reservados.

Este programa es software libre distribuido bajo la **GNU General Public License v3.0 o posterior** (archivo [`LICENSE`](LICENSE)). Puedes usarlo, copiarlo, modificarlo y redistribuirlo **siempre que**:
- mantengas el aviso de copyright y la autoría original;
- incluyas la licencia completa;
- indiques los cambios que hayas hecho;
- distribuyas cualquier versión modificada también bajo GPL-3.0, con su código fuente.

No está permitido distribuir este programa ni versiones derivadas como software cerrado ni presentarlo como obra propia.

**Sin garantía.** El programa se proporciona **"TAL CUAL", SIN GARANTÍA DE NINGÚN TIPO**, expresa o implícita, incluidas las de comerciabilidad o idoneidad para un fin concreto. **El autor no será responsable** de ningún daño, pérdida de datos, interrupción del servicio ni de cualquier otro perjuicio derivado del uso o de la imposibilidad de uso del programa, en la medida máxima permitida por la ley aplicable (secciones 15 y 16 de la licencia). Cada usuario es responsable de verificar los resultados y de cumplir las políticas de su organización sobre el acceso y el tratamiento de los datos.

Esta herramienta **no está afiliada, patrocinada ni respaldada por Microsoft**. *Microsoft*, *Power Automate*, *Power Platform* y *Dataverse* son marcas de Microsoft Corporation.
