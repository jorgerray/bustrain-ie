# Actualización automática de autobuses y NI Railways

Esta carpeta convierte los horarios de **Transport for Ireland** (sur) y de
**Translink** (norte: Metro, Glider, Ulsterbus, Goldliner y NI Railways) en un
paquete `bus.json.gz` (0,56 MB) y lo publica en una URL fija. La Web App lo
descarga **cada 2 días** y sustituye el paquete embebido sin que toques nada.

El paquete embebido en `BusData.html` sigue ahí como respaldo: si la descarga
falla o el visitante no tiene red, la app funciona igual con los horarios del
día en que se generó.

## Cómo montarlo (una sola vez, 5 minutos)

1. Crea un repositorio en GitHub, vacío y **público** (por ejemplo
   `bustrain-ie`).
2. Sube el contenido de esta carpeta `feed/` a la raíz del repositorio. Debe
   quedar así:

   ```
   bustrain-ie/
   ├── .github/workflows/update-bus.yml
   ├── fetch_inputs.py
   ├── build_data.py
   ├── txc_to_gtfs.py
   ├── merge_gtfs.py
   └── README.md
   ```

   Desde la web de GitHub: **Add file → Upload files**, arrastra los archivos y
   confirma. El flujo de trabajo se activa solo al detectarlo.
3. Ve a la pestaña **Actions → Update bus feed → Run workflow** para lanzar la
   primera ejecución (tarda 8–10 minutos). Al terminar se crea la rama `feed`
   con `bus.json.gz`.
4. Copia la URL del paquete:

   ```
   https://raw.githubusercontent.com/TU-USUARIO/bustrain-ie/feed/bus.json.gz
   ```

5. Pega esa URL en `Code.gs`, en la constante `BUS_MODEL_URL`:

   ```js
   const BUS_MODEL_URL = 'https://raw.githubusercontent.com/TU-USUARIO/bustrain-ie/feed/bus.json.gz';
   ```

6. Guarda y crea una **nueva implementación** de la Web App. Ejecuta
   `diagnostico()` en el editor: debe mostrar `BUS MODEL: HTTP 200, 564380 bytes`.

A partir de ahí, cada día a las 04:17 UTC el robot descarga el GTFS, reconstruye
el paquete y lo publica. La app lo recoge en la siguiente visita pasados 2 días.

## Qué hace el flujo

| Paso | Detalle |
|---|---|
| Descarga | `fetch_inputs.py`: GTFS nacional de la NTA (`mdb-2364`, 187 MB), los tres ZIP de Translink desde la API de OpenDataNI y el callejero de GeoNames |
| Convierte | `txc_to_gtfs.py` pasa el TransXchange de Translink a GTFS; `merge_gtfs.py` fusiona los tres feeds |
| Genera | `python3 build_data.py --busfeed feed-out` → `bus.json.gz` |
| Publica | Rama huérfana `feed`, con un solo commit (se fuerza el push: el repo no crece) |

Los ZIP de Translink se localizan por la API de OpenDataNI en cada ejecución,
así que no hay que actualizar ninguna URL cuando publican una revisión nueva.
Su licencia es Open Government Licence v3.0 y exige atribución: la app la lleva
en el pie.

Como la rama se reescribe en cada ejecución, el repositorio se queda siempre en
~0,6 MB, no 0,6 MB por día.

## Si el feed no cambia

La NTA publica versiones nuevas cada cierto tiempo, no a diario. Cuando el GTFS
no ha cambiado, `bus.json.gz` sale idéntico y la app **no** lo aplica: compara
la fecha `meta.built` del paquete con la que ya tiene y, si coincide, ni siquiera
vuelve a descargarlo hasta pasados 2 días. El commit sí se crea (con la misma
fecha), lo cual sirve de latido para ver que el robot sigue vivo.

## Aviso de antigüedad

Si el paquete supera **45 días**, la app muestra bajo el panel de datos:

> Bus timetables are 63 days old: they may have changed.

Ese aviso es la señal de que el robot ha dejado de funcionar (o de que la NTA
lleva mucho sin publicar). Se revisa en la pestaña Actions del repositorio.

## Alternativas de alojamiento

La app solo necesita una URL HTTPS que devuelva el fichero con
`Access-Control-Allow-Origin: *`. Sirven también:

- **GitHub Pages**: activa Pages en la rama `feed` y usa
  `https://TU-USUARIO.github.io/bustrain-ie/bus.json.gz`.
- **jsDelivr** (CDN de GitHub, con caché): 
  `https://cdn.jsdelivr.net/gh/TU-USUARIO/bustrain-ie@feed/bus.json.gz`.
- **Cloudflare R2, S3, Netlify, Vercel**: sube `bus.json.gz` con el mismo
  flujo y pega la URL pública.

`raw.githubusercontent.com` y jsDelivr se comprobaron con CORS abierto
(`access-control-allow-origin: *`), que es lo que necesita la descarga desde el
navegador.

## Probar en local

```bash
python3 fetch_inputs.py --out inputs
python3 build_data.py --gtfs inputs/gtfs --geonames inputs/IE_NI.txt --out out --busfeed feed-out
python3 -m http.server 8000 --directory feed-out   # http://localhost:8000/bus.json.gz
```

Y en `Code.gs`, apunta `BUS_MODEL_URL` a esa dirección mientras pruebas.

## Ejecutar a mano sin GitHub

Si prefieres no usar GitHub Actions, cualquier máquina con Python 3 y `curl`
puede hacerlo con una tarea programada:

```bash
python3 fetch_inputs.py --out inputs
python3 build_data.py --gtfs inputs/gtfs --geonames inputs/IE_NI.txt --out out --busfeed /var/www/bustrain
```

y sirve `/var/www/bustrain/bus.json.gz` por HTTPS.