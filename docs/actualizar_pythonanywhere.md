# Actualizar Merk-888 en PythonAnywhere

El `git push` actualiza GitHub, no la aplicación de PythonAnywhere. Después
hay que actualizar el proyecto, recopilar los archivos estáticos y recargar
la aplicación web.

## 1. Revisar el proyecto en una consola Bash de PythonAnywhere

```bash
cd /home/Merk888/Merk-888
git status --short
git branch --show-current
```

La rama debe ser `main`. Si aparecen cambios en código o configuración que
hiciste directamente en el servidor, revísalos antes de continuar. No uses
`git reset --hard` para resolver conflictos.

## 2. Actualizar

Este bloque guarda únicamente posibles cambios de archivos compilados Python
que antes estaban versionados. No guarda ni descarta cambios en el código,
la configuración o las bases de datos. Esos archivos compilados dejan de
formar parte del repositorio y Python los vuelve a generar cuando los necesita.

```bash
(
  set -e
  cd /home/Merk888/Merk-888
  source /home/Merk888/.virtualenvs/env/bin/activate

  if git ls-files -- '*.pyc' '*.pyo' | grep -q .; then
    git stash push -m "Respaldo de caches Python antes de actualizar" -- '*.pyc' '*.pyo'
  fi

  git pull --ff-only origin main
  python manage.py check
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
  git log -1 --oneline
)
```

Si cualquier comando falla, el bloque se detiene: revisa el error antes de
recargar la aplicación. Si `pull` indica un conflicto en archivos fuente, no
lo fuerces ni apliques automáticamente el respaldo de caches.

Estos cambios del visor y de la notificación de carritos vaciados no agregan
dependencias ni migraciones. El comando `migrate` aplica solo migraciones ya
incluidas en el repositorio; no ejecutes `makemigrations` en el servidor para
este despliegue. Si una actualización futura modifica `requirements.txt`,
instala sus dependencias en el entorno virtual antes de ejecutar `check`.

Los archivos privados, como `/home/Merk888/.telegram_bot.env` y
`/home/Merk888/.price_sync.env`, permanecen fuera del repositorio. No cambies
sus contraseñas ni los vuelvas a copiar a archivos versionados.

## 3. Recargar y comprobar

En PythonAnywhere abre **Web**, selecciona `merk888.pythonanywhere.com` y
pulsa **Reload**. Después actualiza el navegador con `Ctrl+F5`.

- `/v` busca exclusivamente por código de barras, con o sin sesión.
- El enlace del navbar, con sesión iniciada, abre `/visor/cajeros/`, donde se
  permite buscar por nombre, ID y código de barras.
- Al vaciar un carrito no facturado con una cuenta de trabajador, se muestra
  durante dos segundos: `Venta no facturada #1 del día`, usando su conteo real.
  Los Web Master siguen excluidos de ese registro.

No registres ventas ficticias para comprobar la interfaz. Ten presente que
vaciar un carrito con una cuenta de trabajador genera un registro de auditoría.
