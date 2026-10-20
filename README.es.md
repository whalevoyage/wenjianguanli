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

**¿El disco C: está lleno? Primero mira qué hay dentro.**

Una herramienta de limpieza de disco y gestión de archivos para Windows: análisis por escaneo, detección de duplicados, limpieza de archivos basura, migración entre discos y mapa de espacio.

Las operaciones de escritura muestran primero la lista de archivos afectados y esperan tu confirmación. Por defecto, la limpieza mueve los archivos a cuarentena, desde donde se pueden restaurar. Las acciones irreversibles se indican claramente y requieren una confirmación adicional.
La IA solo entiende lo que pides y redacta un plan; quien toca los archivos es el código local.
Salvo las solicitudes al modelo del Agente y las explicaciones de IA que pidas expresamente, el análisis, la búsqueda de duplicados, la limpieza, la migración y las copias de seguridad se ejecutan localmente. No hace falta una cuenta.

> No decide por ti. Te muestra exactamente qué archivos va a tocar cada acción.

![Pantalla de inicio de Atlas](docs/images/overview.png)

## Inicio rápido

1. Descarga [Atlas-2.1.0-win64-portable.zip](https://github.com/whalevoyage/wenjianguanli/releases/latest) y descomprímelo donde quieras (por ejemplo `D:\Atlas`)
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
| Papelera | La limpieza va a cuarentena y se puede restaurar en su sitio |
| Registro de operaciones | Consulta de auditoría y exportación |

## Modelo de seguridad

- La limpieza genera una lista; tras la confirmación los archivos van a cuarentena, nunca se borran físicamente de inmediato.
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
