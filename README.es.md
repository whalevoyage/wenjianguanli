# Atlas

<p align="center">
  <a href="README.md">简体中文</a> ·
  <a href="README.en.md">English</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.de.md">Deutsch</a> ·
  <a href="README.ru.md">Русский</a> ·
  <b>Español</b>
</p>

**Gestión de archivos y limpieza de disco local y controlada para Windows: vista previa primero, confirmación explícita, operaciones trazables, reversión cuando esté disponible y un Agente de IA.**

El análisis, la detección de duplicados, la limpieza de archivos basura, la migración entre discos y el mapa de espacio te ayudan a ver antes de actuar.

Las operaciones de escritura muestran primero la lista de archivos afectados y esperan tu confirmación. La limpieza manual mueve los archivos a cuarentena por defecto; podrán restaurarse si la ruta original y el estado del sistema de archivos lo permiten. La limpieza permanente del Agente y el vaciado de la cuarentena son irreversibles, se indican claramente y requieren confirmación adicional.
La IA solo entiende lo que pides y redacta un plan; quien toca los archivos es el código local.
Salvo las solicitudes al modelo del Agente y las explicaciones de IA que pidas expresamente, el análisis, la búsqueda de duplicados, la limpieza, la migración y las copias de seguridad se ejecutan localmente. No hace falta una cuenta.

> No decide por ti. Te muestra exactamente qué archivos va a tocar cada acción.

![Pantalla de inicio de Atlas](docs/images/overview.png)

## Inicio rápido

1. Descarga [Atlas-2.1.1-win64-portable.zip](https://github.com/whalevoyage/wenjianguanli/releases/latest) y descomprímelo donde quieras (por ejemplo `D:\Atlas`)
2. Haz doble clic en `Atlas\Atlas.exe`
3. El navegador se abre solo. No aparece ninguna ventana de consola.

No necesitas Python, Node ni ningún runtime. El programa escucha únicamente en `127.0.0.1` y guarda los datos en `%LOCALAPPDATA%\Atlas`.
Desinstalar es borrar esa carpeta.

**Un segundo doble clic no abre otra instancia** — apunta a la que ya está en marcha.
**Si el puerto 8000 está ocupado, elige uno libre** y escribe la dirección real en el registro.

## Funciones

| Página | Para qué sirve |
| --- | --- |
| Inicio | Capacidad del disco, espacio recuperable, estadísticas y tareas recientes |
| Agente Atlas | Interacción en lenguaje natural; prioriza tareas de archivos y también responde preguntas generales |
| Mapa de espacio | Mapa de árbol del uso por carpeta, con exploración por niveles |
| Vista previa de organización | Previsualiza por tipo, fecha, tamaño o proyecto; solo lectura |
| Radar de proyectos | Detecta carpetas de proyecto: tipo, tamaño, número de archivos y proyectos inactivos |
| Análisis por escaneo | Recuentos, tamaños, distribución por tipo, archivos grandes y sin tocar; snapshots para comparar antes/después |
| Duplicados | Detección byte a byte; incluye «imágenes similares» (redimensionadas, recomprimidas o guardadas de nuevo, con tolerancia a rotación) |
| Archivos basura | Temporales, registros, cachés, carpetas vacías y archivos de 0 bytes, con tres niveles de riesgo |
| Búsqueda de texto completo | Índice FTS5 local por carpeta, busca en nombres y contenido |
| Migración entre discos | Copia y verifica elemento por elemento; el origen solo se toca si la verificación pasa; permite revertir |
| Copia de proyecto | Snapshots ZIP excluyendo dependencias y carpetas de compilación; verificación de restauración |
| Centro de tareas | Progreso en vivo, errores, cancelación y estado de reversión |
| Papelera | La limpieza manual va a cuarentena; se puede intentar restaurar si la ruta y el sistema de archivos lo permiten |
| Registro de operaciones | Consulta de auditoría y exportación |

## Modelo de seguridad

- La limpieza manual muestra primero una lista y, tras la confirmación, mueve los archivos a cuarentena por defecto. Se podrán restaurar si la ruta original y el estado del sistema de archivos lo permiten. La limpieza permanente del Agente y el vaciado de la cuarentena son irreversibles y requieren confirmación aparte.
- La migración copia y verifica primero; el origen se procesa solo si la verificación pasa.
- Las operaciones largas se ejecutan como tareas en segundo plano con progreso, cancelación y reversión.
- La carpeta del propio proyecto, la carpeta de estado y la cuarentena están protegidas.
- Las carpetas del sistema no se escanean por defecto. El modo estándar bloquea las escrituras allí; el modo de acceso completo permite relajar expresamente esa protección. Los datos propios de Atlas y la cuarentena siguen protegidos.
- Se excluyen por defecto `node_modules`, `.git`, `__pycache__`, `.venv`, `dist`, `build` y similares.
- Antes de migrar o copiar se comprueba el espacio libre en el destino.
- Escucha solo en `127.0.0.1`, no se expone a la red local.

## Sobre la documentación

Esta página es un **resumen**. Para detalles (arquitectura, configuración, pruebas, límites):

- Versión en inglés (16 capítulos completos): [README.en.md](README.en.md)
- Original en chino: [README.md](README.md)

Seguridad y privacidad: [SECURITY.md](SECURITY.md) / [PRIVACY.md](PRIVACY.md) (en chino).

## Licencia

[MIT License](LICENSE). Uso, modificación y redistribución libres siempre que se conserve el aviso de copyright y de licencia.
